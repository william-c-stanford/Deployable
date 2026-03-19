"""Tests for headcount request post-approval workflow.

Tests that:
1. Approving a headcount request creates ProjectRole slot records
2. WebSocket notifications are published to the partner topic
3. Cascade events trigger staffing recommendations for new roles
4. Audit trail is created for the approval workflow
5. Edge cases: no project, already non-pending status, etc.
"""

import uuid
from datetime import datetime, date
from unittest.mock import MagicMock, call, patch

import pytest

from app.workers.events import EventPayload, EventType
from app.services.headcount_approval import execute_headcount_approval


@pytest.fixture
def mock_session():
    """Create a mock SQLAlchemy session."""
    session = MagicMock()
    session.get = MagicMock()
    session.query = MagicMock()
    session.add = MagicMock()
    session.flush = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.close = MagicMock()
    return session


@pytest.fixture
def mock_ws():
    """Create a mock WebSocket broadcaster."""
    return MagicMock()


@pytest.fixture
def sample_headcount_request():
    """Create a sample approved headcount request."""
    hr = MagicMock()
    hr.id = uuid.uuid4()
    hr.partner_id = uuid.uuid4()
    hr.project_id = uuid.uuid4()
    hr.role_name = "Lead Fiber Splicer"
    hr.quantity = 3
    hr.priority = "high"
    hr.status = "Approved"
    hr.start_date = date(2026, 4, 1)
    hr.end_date = date(2026, 9, 30)
    hr.required_skills = [{"skill": "Fiber Splicing", "min_level": "Advanced"}]
    hr.required_certs = ["FOA CFOT", "OSHA 10"]
    hr.constraints = "Must be available for travel"
    hr.notes = None
    return hr


@pytest.fixture
def sample_project():
    """Create a sample project."""
    project = MagicMock()
    project.id = uuid.uuid4()
    project.name = "Metro Fiber Build - Dallas"
    project.status = "Draft"
    project.partner_id = uuid.uuid4()
    return project


@pytest.fixture
def sample_partner():
    """Create a sample partner."""
    partner = MagicMock()
    partner.id = uuid.uuid4()
    partner.name = "FiberTech Solutions"
    return partner


def _make_payload(hr_id: str, actor_id: str = "ops-user-1", **data_kwargs) -> EventPayload:
    """Create an EventPayload for the headcount approval task."""
    base_data = {
        "partner_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "role_name": "Lead Fiber Splicer",
        "quantity": 3,
        "priority": "high",
    }
    base_data.update(data_kwargs)
    return EventPayload(
        event_type=EventType.HEADCOUNT_APPROVED,
        entity_type="headcount_request",
        entity_id=str(hr_id),
        actor_id=actor_id,
        data=base_data,
    )


def _setup_session_gets(session, hr, project, partner):
    """Wire up session.get to return the correct mocks based on model class."""
    def get_side_effect(model, id_val):
        name = model.__name__ if hasattr(model, '__name__') else str(model)
        if name == "PendingHeadcountRequest":
            return hr
        if name == "Project":
            return project
        if name == "Partner":
            return partner
        return None
    session.get = MagicMock(side_effect=get_side_effect)


class TestExecuteHeadcountApproval:
    """Tests for the core execute_headcount_approval service function."""

    def test_creates_project_role_on_approval(
        self, mock_session, mock_ws,
        sample_headcount_request, sample_project, sample_partner,
    ):
        """Approved headcount request should create ProjectRole records."""
        sample_project.partner_id = sample_headcount_request.partner_id
        sample_headcount_request.project_id = sample_project.id
        _setup_session_gets(mock_session, sample_headcount_request, sample_project, sample_partner)

        payload = _make_payload(sample_headcount_request.id)
        result = execute_headcount_approval(mock_session, payload, ws_broadcaster=mock_ws)

        assert result["status"] == "completed"
        assert result["roles_created"]
        assert len(result["roles_created"]) == 1
        assert result["roles_created"][0]["role_name"] == "Lead Fiber Splicer"
        assert result["roles_created"][0]["quantity"] == 3

        # Verify session.add was called (for role, audit log, suggested action)
        assert mock_session.add.call_count >= 3
        mock_session.commit.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_emits_partner_websocket_notification(
        self, mock_session, mock_ws,
        sample_headcount_request, sample_project, sample_partner,
    ):
        """Approved headcount should broadcast WS notification to partner topic."""
        sample_project.partner_id = sample_headcount_request.partner_id
        sample_headcount_request.project_id = sample_project.id
        _setup_session_gets(mock_session, sample_headcount_request, sample_project, sample_partner)

        payload = _make_payload(sample_headcount_request.id)
        result = execute_headcount_approval(mock_session, payload, ws_broadcaster=mock_ws)

        # Should broadcast to both 'partner' and 'dashboard' topics
        assert mock_ws.call_count == 2

        # First call: partner notification
        partner_call = mock_ws.call_args_list[0]
        assert partner_call[0][0] == "partner"
        partner_event = partner_call[0][1]
        assert partner_event["event_type"] == "headcount.approved"
        assert partner_event["partner_id"] == str(sample_headcount_request.partner_id)
        assert "approved" in partner_event["message"].lower()
        assert partner_event["role_name"] == "Lead Fiber Splicer"
        assert partner_event["quantity"] == 3

        # Second call: dashboard notification
        dashboard_call = mock_ws.call_args_list[1]
        assert dashboard_call[0][0] == "dashboard"
        assert dashboard_call[0][1]["event_type"] == "headcount.roles_created"

    def test_cascades_role_unfilled_events(
        self, mock_session, mock_ws,
        sample_headcount_request, sample_project, sample_partner,
    ):
        """Approved headcount should cascade ROLE_UNFILLED events for staffing."""
        sample_project.partner_id = sample_headcount_request.partner_id
        sample_headcount_request.project_id = sample_project.id
        _setup_session_gets(mock_session, sample_headcount_request, sample_project, sample_partner)

        payload = _make_payload(sample_headcount_request.id)
        result = execute_headcount_approval(mock_session, payload, ws_broadcaster=mock_ws)

        # Should return cascade_events for the new role
        assert "cascade_events" in result
        assert len(result["cascade_events"]) == 1
        cascade = result["cascade_events"][0]
        assert cascade["event_type"] == EventType.ROLE_UNFILLED.value
        assert cascade["data"]["reason"] == "headcount_approved"
        assert cascade["data"]["role_name"] == "Lead Fiber Splicer"
        assert cascade["data"]["quantity"] == 3
        assert cascade["data"]["headcount_request_id"] == str(sample_headcount_request.id)

    def test_skips_non_approved_request(self, mock_session, mock_ws):
        """Non-approved headcount requests should be skipped."""
        hr = MagicMock()
        hr.id = uuid.uuid4()
        hr.status = "Pending"
        mock_session.get.return_value = hr

        payload = _make_payload(hr.id)
        result = execute_headcount_approval(mock_session, payload, ws_broadcaster=mock_ws)

        assert result["status"] == "skipped"
        assert "not 'Approved'" in result["reason"]
        mock_ws.assert_not_called()

    def test_skips_missing_request(self, mock_session, mock_ws):
        """Missing headcount request should be skipped."""
        mock_session.get.return_value = None

        payload = _make_payload(str(uuid.uuid4()))
        result = execute_headcount_approval(mock_session, payload, ws_broadcaster=mock_ws)

        assert result["status"] == "skipped"
        assert "not found" in result["reason"]
        mock_ws.assert_not_called()

    def test_updates_draft_project_to_staffing(
        self, mock_session, mock_ws,
        sample_headcount_request, sample_project, sample_partner,
    ):
        """Project in Draft status should be updated to Staffing on approval."""
        sample_project.partner_id = sample_headcount_request.partner_id
        sample_project.status = "Draft"
        sample_headcount_request.project_id = sample_project.id
        _setup_session_gets(mock_session, sample_headcount_request, sample_project, sample_partner)

        payload = _make_payload(sample_headcount_request.id)
        result = execute_headcount_approval(mock_session, payload, ws_broadcaster=mock_ws)

        assert result["status"] == "completed"
        assert result["project_status_updated"] is True

    def test_includes_partner_name_in_result(
        self, mock_session, mock_ws,
        sample_headcount_request, sample_project, sample_partner,
    ):
        """Result should include the partner name for downstream consumers."""
        sample_project.partner_id = sample_headcount_request.partner_id
        sample_headcount_request.project_id = sample_project.id
        _setup_session_gets(mock_session, sample_headcount_request, sample_project, sample_partner)

        payload = _make_payload(sample_headcount_request.id)
        result = execute_headcount_approval(mock_session, payload, ws_broadcaster=mock_ws)

        assert result["partner_name"] == "FiberTech Solutions"
        assert result["project_name"] == "Metro Fiber Build - Dallas"

    def test_ws_event_contains_roles_created(
        self, mock_session, mock_ws,
        sample_headcount_request, sample_project, sample_partner,
    ):
        """WebSocket partner event should include the roles_created list."""
        sample_project.partner_id = sample_headcount_request.partner_id
        sample_headcount_request.project_id = sample_project.id
        _setup_session_gets(mock_session, sample_headcount_request, sample_project, sample_partner)

        payload = _make_payload(sample_headcount_request.id)
        execute_headcount_approval(mock_session, payload, ws_broadcaster=mock_ws)

        partner_event = mock_ws.call_args_list[0][0][1]
        assert "roles_created" in partner_event
        assert len(partner_event["roles_created"]) == 1
        assert partner_event["roles_created"][0]["role_name"] == "Lead Fiber Splicer"
        assert partner_event["roles_created"][0]["open_slots"] == 3


class TestHeadcountApprovalEventRegistration:
    """Tests that HEADCOUNT_APPROVED event type is properly registered."""

    def test_event_type_exists(self):
        """HEADCOUNT_APPROVED should be defined in EventType enum."""
        assert hasattr(EventType, "HEADCOUNT_APPROVED")
        assert EventType.HEADCOUNT_APPROVED.value == "project.headcount_approved"

    def test_event_category_mapped(self):
        """HEADCOUNT_APPROVED should be mapped to PROJECT category."""
        from app.workers.events import EVENT_CATEGORY_MAP, EventCategory
        assert EventType.HEADCOUNT_APPROVED in EVENT_CATEGORY_MAP
        assert EVENT_CATEGORY_MAP[EventType.HEADCOUNT_APPROVED] == EventCategory.PROJECT

    def test_dispatcher_routes_event(self):
        """HEADCOUNT_APPROVED should be routed to the headcount task."""
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        assert EventType.HEADCOUNT_APPROVED in EVENT_TASK_ROUTING
        tasks = EVENT_TASK_ROUTING[EventType.HEADCOUNT_APPROVED]
        assert "app.workers.tasks.headcount.process_approved_headcount" in tasks

    def test_celery_task_routes_includes_headcount(self):
        """Celery task routes should include headcount tasks."""
        from app.workers.celery_app import celery_app
        routes = celery_app.conf.task_routes
        assert "app.workers.tasks.headcount.*" in routes
        assert routes["app.workers.tasks.headcount.*"]["queue"] == "project"

    def test_headcount_task_importable(self):
        """The headcount task module should be importable and have the task function."""
        from app.workers.tasks.headcount import process_approved_headcount
        assert process_approved_headcount.name == "app.workers.tasks.headcount.process_approved_headcount"
