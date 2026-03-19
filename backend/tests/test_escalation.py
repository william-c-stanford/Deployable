"""Tests for the 24-hour escalation window logic.

Tests:
  - scan_overdue_confirmations: Identifies and escalates confirmations > 24 hours old
  - handle_escalation_triggered: Creates suggested actions and recommendations
  - handle_escalation_resolved: Handles ops resolution of escalated confirmations
  - EscalationStatus lifecycle: none → escalated → ops_reviewing → resolved_*
  - AssignmentConfirmation model properties: is_overdue, hours_waiting
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta, timezone, date
import uuid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event_dict(event_type_val, entity_type, entity_id, data=None):
    """Build a minimal event_dict as Celery tasks receive."""
    return {
        "event_type": event_type_val,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "actor_id": "system",
        "data": data or {},
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _make_mock_confirmation(
    conf_id=None,
    assignment_id=None,
    partner_id=None,
    status="pending",
    escalated=False,
    requested_at=None,
    confirmation_type="start_date",
    escalation_status="none",
):
    conf = MagicMock()
    conf.id = conf_id or uuid.uuid4()
    conf.assignment_id = assignment_id or uuid.uuid4()
    conf.partner_id = partner_id or uuid.uuid4()
    conf.status = status
    conf.escalated = escalated
    conf.requested_at = requested_at or datetime.utcnow()
    conf.confirmation_type = confirmation_type
    conf.escalation_status = escalation_status
    conf.requested_date = date.today()
    conf.escalated_at = None
    conf.escalation_resolved_at = None
    conf.escalation_resolved_by = None
    conf.escalation_resolution_note = None
    return conf


def _make_mock_assignment(assignment_id=None, tech_id=None, role_id=None):
    a = MagicMock()
    a.id = assignment_id or uuid.uuid4()
    a.technician_id = tech_id or uuid.uuid4()
    a.role_id = role_id or uuid.uuid4()
    a.start_date = date.today()
    a.end_date = None
    a.status = "Active"
    a.partner_confirmed_start = False
    a.partner_confirmed_end = False
    return a


def _make_mock_partner(partner_id=None, name="Acme Fiber Co"):
    p = MagicMock()
    p.id = partner_id or uuid.uuid4()
    p.name = name
    return p


def _make_mock_technician(tech_id=None, name="John Smith"):
    t = MagicMock()
    t.id = tech_id or uuid.uuid4()
    t.full_name = name
    t.first_name = name.split()[0]
    t.last_name = name.split()[-1]
    return t


def _make_mock_role(role_id=None, project_id=None, role_name="Lead Splicer"):
    r = MagicMock()
    r.id = role_id or uuid.uuid4()
    r.project_id = project_id or uuid.uuid4()
    r.role_name = role_name
    return r


def _make_mock_project(project_id=None, name="Atlanta Fiber Buildout"):
    p = MagicMock()
    p.id = project_id or uuid.uuid4()
    p.name = name
    return p


# ---------------------------------------------------------------------------
# Model property tests
# ---------------------------------------------------------------------------

class TestAssignmentConfirmationModel:
    """Test the AssignmentConfirmation model properties."""

    def test_is_overdue_true_after_24_hours(self):
        """Confirmation requested 25 hours ago should be overdue."""
        from app.models.assignment_confirmation import (
            AssignmentConfirmation, ConfirmationStatus,
        )
        conf = AssignmentConfirmation()
        conf.status = ConfirmationStatus.PENDING
        conf.requested_at = datetime.utcnow() - timedelta(hours=25)
        assert conf.is_overdue is True

    def test_is_overdue_false_before_24_hours(self):
        """Confirmation requested 12 hours ago should not be overdue."""
        from app.models.assignment_confirmation import (
            AssignmentConfirmation, ConfirmationStatus,
        )
        conf = AssignmentConfirmation()
        conf.status = ConfirmationStatus.PENDING
        conf.requested_at = datetime.utcnow() - timedelta(hours=12)
        assert conf.is_overdue is False

    def test_is_overdue_false_when_confirmed(self):
        """Already confirmed should not be overdue."""
        from app.models.assignment_confirmation import (
            AssignmentConfirmation, ConfirmationStatus,
        )
        conf = AssignmentConfirmation()
        conf.status = ConfirmationStatus.CONFIRMED
        conf.requested_at = datetime.utcnow() - timedelta(hours=48)
        assert conf.is_overdue is False

    def test_is_overdue_false_when_declined(self):
        """Already declined should not be overdue."""
        from app.models.assignment_confirmation import (
            AssignmentConfirmation, ConfirmationStatus,
        )
        conf = AssignmentConfirmation()
        conf.status = ConfirmationStatus.DECLINED
        conf.requested_at = datetime.utcnow() - timedelta(hours=48)
        assert conf.is_overdue is False

    def test_hours_waiting(self):
        """hours_waiting should reflect elapsed time."""
        from app.models.assignment_confirmation import AssignmentConfirmation
        conf = AssignmentConfirmation()
        conf.requested_at = datetime.utcnow() - timedelta(hours=18)
        assert 17.5 < conf.hours_waiting < 18.5

    def test_hours_waiting_zero_when_no_request(self):
        """hours_waiting should be 0 when requested_at is None."""
        from app.models.assignment_confirmation import AssignmentConfirmation
        conf = AssignmentConfirmation()
        conf.requested_at = None
        assert conf.hours_waiting == 0.0


# ---------------------------------------------------------------------------
# EscalationStatus enum tests
# ---------------------------------------------------------------------------

class TestEscalationStatus:
    """Test the EscalationStatus enum values."""

    def test_enum_values(self):
        from app.models.assignment_confirmation import EscalationStatus
        assert EscalationStatus.NONE.value == "none"
        assert EscalationStatus.ESCALATED.value == "escalated"
        assert EscalationStatus.OPS_REVIEWING.value == "ops_reviewing"
        assert EscalationStatus.RESOLVED_CONFIRMED.value == "resolved_confirmed"
        assert EscalationStatus.RESOLVED_REASSIGNED.value == "resolved_reassigned"
        assert EscalationStatus.RESOLVED_CANCELLED.value == "resolved_cancelled"

    def test_confirmation_status_includes_escalated(self):
        from app.models.assignment_confirmation import ConfirmationStatus
        assert ConfirmationStatus.ESCALATED.value == "escalated"


# ---------------------------------------------------------------------------
# Event type tests
# ---------------------------------------------------------------------------

class TestEscalationEventTypes:
    """Verify escalation event types are registered."""

    def test_event_types_exist(self):
        from app.workers.events import EventType
        assert hasattr(EventType, "CONFIRMATION_ESCALATED")
        assert hasattr(EventType, "ESCALATION_RESOLVED")
        assert hasattr(EventType, "ESCALATION_SCAN_TRIGGERED")

    def test_event_type_values(self):
        from app.workers.events import EventType
        assert EventType.CONFIRMATION_ESCALATED.value == "confirmation.escalated"
        assert EventType.ESCALATION_RESOLVED.value == "confirmation.escalation_resolved"
        assert EventType.ESCALATION_SCAN_TRIGGERED.value == "batch.escalation_scan"

    def test_events_in_category_map(self):
        from app.workers.events import EVENT_CATEGORY_MAP, EventType, EventCategory
        assert EVENT_CATEGORY_MAP[EventType.CONFIRMATION_ESCALATED] == EventCategory.ASSIGNMENT
        assert EVENT_CATEGORY_MAP[EventType.ESCALATION_RESOLVED] == EventCategory.ASSIGNMENT
        assert EVENT_CATEGORY_MAP[EventType.ESCALATION_SCAN_TRIGGERED] == EventCategory.BATCH


# ---------------------------------------------------------------------------
# Dispatcher routing tests
# ---------------------------------------------------------------------------

class TestEscalationDispatcherRouting:
    """Verify event-to-task routing for escalation events."""

    def test_escalation_triggered_routes_to_handler(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType
        tasks = EVENT_TASK_ROUTING[EventType.CONFIRMATION_ESCALATED]
        assert "app.workers.tasks.escalation.handle_escalation_triggered" in tasks

    def test_escalation_resolved_routes_to_handler(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType
        tasks = EVENT_TASK_ROUTING[EventType.ESCALATION_RESOLVED]
        assert "app.workers.tasks.escalation.handle_escalation_resolved" in tasks

    def test_escalation_scan_routes_to_scanner(self):
        from app.workers.dispatcher import EVENT_TASK_ROUTING
        from app.workers.events import EventType
        tasks = EVENT_TASK_ROUTING[EventType.ESCALATION_SCAN_TRIGGERED]
        assert "app.workers.tasks.escalation.scan_overdue_confirmations" in tasks


# ---------------------------------------------------------------------------
# Celery Beat schedule tests
# ---------------------------------------------------------------------------

class TestEscalationSchedule:
    """Verify the escalation scan is in the Celery Beat schedule."""

    def test_escalation_scan_in_beat_schedule(self):
        from app.workers.celery_app import celery_app
        beat = celery_app.conf.beat_schedule
        assert "escalation-scan" in beat
        entry = beat["escalation-scan"]
        assert entry["task"] == "app.workers.tasks.escalation.scan_overdue_confirmations"

    def test_escalation_task_routing(self):
        from app.workers.celery_app import celery_app
        routes = celery_app.conf.task_routes
        assert "app.workers.tasks.escalation.*" in routes

    def test_escalation_autodiscover(self):
        """Verify escalation tasks module is autodiscovered."""
        # The celery_app autodiscover_tasks call should include escalation
        from app.workers.celery_app import celery_app
        # The task should be importable
        from app.workers.tasks.escalation import scan_overdue_confirmations
        assert scan_overdue_confirmations is not None


# ---------------------------------------------------------------------------
# scan_overdue_confirmations task tests
# ---------------------------------------------------------------------------

class TestScanOverdueConfirmations:
    """Test the periodic escalation scan task."""

    @patch("app.workers.tasks.escalation.dispatch_event_safe")
    @patch("app.workers.tasks.escalation.SessionLocal")
    def test_escalates_overdue_confirmations(self, mock_session_local, mock_dispatch):
        """Confirmations older than 24h should be escalated."""
        from app.workers.tasks.escalation import scan_overdue_confirmations
        from app.models.assignment_confirmation import ConfirmationStatus, EscalationStatus

        # Setup
        conf_id = uuid.uuid4()
        assignment_id = uuid.uuid4()
        partner_id = uuid.uuid4()
        tech_id = uuid.uuid4()
        role_id = uuid.uuid4()
        project_id = uuid.uuid4()

        conf = _make_mock_confirmation(
            conf_id=conf_id,
            assignment_id=assignment_id,
            partner_id=partner_id,
            requested_at=datetime.utcnow() - timedelta(hours=26),
        )

        assignment = _make_mock_assignment(assignment_id, tech_id, role_id)
        partner = _make_mock_partner(partner_id)
        technician = _make_mock_technician(tech_id)
        role = _make_mock_role(role_id, project_id)
        project = _make_mock_project(project_id)

        session = MagicMock()
        mock_session_local.return_value = session
        session.query.return_value.filter.return_value.all.return_value = [conf]
        session.get.side_effect = lambda model, id: {
            assignment_id: assignment,
            partner_id: partner,
            tech_id: technician,
            role_id: role,
            project_id: project,
        }.get(id)

        # Execute
        result = scan_overdue_confirmations()

        # Verify escalation happened
        assert result["escalated"] >= 1
        assert conf.escalated is True
        assert conf.escalated_at is not None
        session.commit.assert_called()
        mock_dispatch.assert_called()

    @patch("app.workers.tasks.escalation.dispatch_event_safe")
    @patch("app.workers.tasks.escalation.SessionLocal")
    def test_skips_already_escalated(self, mock_session_local, mock_dispatch):
        """Already-escalated confirmations should not be re-escalated."""
        from app.workers.tasks.escalation import scan_overdue_confirmations

        session = MagicMock()
        mock_session_local.return_value = session
        # The query filters for escalated=False, so already-escalated won't appear
        session.query.return_value.filter.return_value.all.return_value = []

        result = scan_overdue_confirmations()

        assert result["escalated"] == 0
        mock_dispatch.assert_not_called()

    @patch("app.workers.tasks.escalation.dispatch_event_safe")
    @patch("app.workers.tasks.escalation.SessionLocal")
    def test_skips_recent_confirmations(self, mock_session_local, mock_dispatch):
        """Confirmations less than 24h old should not be escalated."""
        from app.workers.tasks.escalation import scan_overdue_confirmations

        session = MagicMock()
        mock_session_local.return_value = session
        # Query correctly filters by cutoff, so no results
        session.query.return_value.filter.return_value.all.return_value = []

        result = scan_overdue_confirmations()

        assert result["escalated"] == 0


# ---------------------------------------------------------------------------
# handle_escalation_triggered task tests
# ---------------------------------------------------------------------------

class TestHandleEscalationTriggered:
    """Test the escalation handler that creates staffing page items."""

    @patch("app.workers.tasks.escalation.SessionLocal")
    def test_creates_suggested_action(self, mock_session_local):
        """Should create a high-priority SuggestedAction for ops."""
        from app.workers.tasks.escalation import handle_escalation_triggered

        session = MagicMock()
        mock_session_local.return_value = session
        # No existing escalation rec
        session.query.return_value.filter.return_value.first.return_value = None

        conf_id = str(uuid.uuid4())
        event_dict = _make_event_dict(
            "confirmation.escalated",
            "assignment_confirmation",
            conf_id,
            data={
                "confirmation_id": conf_id,
                "assignment_id": str(uuid.uuid4()),
                "partner_id": str(uuid.uuid4()),
                "partner_name": "Acme Fiber",
                "technician_name": "John Smith",
                "role_name": "Lead Splicer",
                "project_name": "Atlanta Project",
                "project_id": str(uuid.uuid4()),
                "role_id": str(uuid.uuid4()),
                "technician_id": str(uuid.uuid4()),
                "hours_overdue": 26.5,
                "confirmation_type": "start_date",
            },
        )

        result = handle_escalation_triggered(event_dict)

        assert result["status"] == "escalation_handled"
        assert result["actions_created"] is True
        # Should have called session.add at least twice (SuggestedAction + Recommendation + AuditLog)
        assert session.add.call_count >= 2
        session.commit.assert_called()

    @patch("app.workers.tasks.escalation.SessionLocal")
    def test_skips_duplicate_recommendation(self, mock_session_local):
        """Should not create duplicate escalation recommendations for same role."""
        from app.workers.tasks.escalation import handle_escalation_triggered

        session = MagicMock()
        mock_session_local.return_value = session
        # Existing escalation rec found
        session.query.return_value.filter.return_value.first.return_value = MagicMock()

        event_dict = _make_event_dict(
            "confirmation.escalated",
            "assignment_confirmation",
            str(uuid.uuid4()),
            data={
                "confirmation_id": str(uuid.uuid4()),
                "assignment_id": str(uuid.uuid4()),
                "partner_name": "Acme",
                "technician_name": "John",
                "role_name": "Splicer",
                "project_name": "Project",
                "project_id": str(uuid.uuid4()),
                "role_id": str(uuid.uuid4()),
                "hours_overdue": 25,
                "confirmation_type": "start_date",
            },
        )

        result = handle_escalation_triggered(event_dict)

        # Should still create action and audit, but not a second recommendation
        assert result["status"] == "escalation_handled"


# ---------------------------------------------------------------------------
# handle_escalation_resolved task tests
# ---------------------------------------------------------------------------

class TestHandleEscalationResolved:
    """Test the escalation resolution handler."""

    @patch("app.workers.tasks.escalation.SessionLocal")
    def test_resolve_confirmed(self, mock_session_local):
        """Resolving with 'confirmed' updates confirmation and assignment."""
        from app.workers.tasks.escalation import handle_escalation_resolved
        from app.models.assignment_confirmation import EscalationStatus, ConfirmationStatus

        session = MagicMock()
        mock_session_local.return_value = session

        conf_id = uuid.uuid4()
        assignment_id = uuid.uuid4()
        conf = _make_mock_confirmation(conf_id=conf_id, assignment_id=assignment_id)
        conf.confirmation_type = MagicMock()
        conf.confirmation_type.value = "start_date"

        assignment = _make_mock_assignment(assignment_id)

        # session.get receives (Model, string_id) — match on the string ID
        def mock_get(model, id_val):
            id_str = str(id_val)
            lookup = {
                str(conf_id): conf,
                str(assignment_id): assignment,
            }
            return lookup.get(id_str)

        session.get.side_effect = mock_get
        session.query.return_value.filter.return_value.all.return_value = []

        event_dict = _make_event_dict(
            "confirmation.escalation_resolved",
            "assignment_confirmation",
            str(conf_id),
            data={
                "resolution": "confirmed",
                "resolved_by": "ops_user_1",
                "resolution_note": "Partner unresponsive, confirming",
            },
        )

        result = handle_escalation_resolved(event_dict)

        assert result["status"] == "resolved"
        assert result["resolution"] == "confirmed"
        assert assignment.partner_confirmed_start is True
        session.commit.assert_called()

    @patch("app.workers.tasks.escalation.SessionLocal")
    def test_resolve_not_found(self, mock_session_local):
        """Resolving a non-existent confirmation should return skipped."""
        from app.workers.tasks.escalation import handle_escalation_resolved

        session = MagicMock()
        mock_session_local.return_value = session
        session.get.return_value = None

        event_dict = _make_event_dict(
            "confirmation.escalation_resolved",
            "assignment_confirmation",
            str(uuid.uuid4()),
            data={"resolution": "confirmed"},
        )

        result = handle_escalation_resolved(event_dict)

        assert result["status"] == "skipped"

    @patch("app.workers.tasks.escalation.SessionLocal")
    def test_resolve_supersedes_pending_recs(self, mock_session_local):
        """Resolving should supersede any pending escalation recommendations."""
        from app.workers.tasks.escalation import handle_escalation_resolved
        from app.models.recommendation import RecommendationStatus

        session = MagicMock()
        mock_session_local.return_value = session

        conf_id = uuid.uuid4()
        assignment_id = uuid.uuid4()
        conf = _make_mock_confirmation(conf_id=conf_id, assignment_id=assignment_id)
        conf.confirmation_type = MagicMock()
        conf.confirmation_type.value = "start_date"

        rec = MagicMock()
        rec.status = RecommendationStatus.PENDING.value

        assignment = _make_mock_assignment(assignment_id)

        def mock_get(model, id_val):
            id_str = str(id_val)
            return {
                str(conf_id): conf,
                str(assignment_id): assignment,
            }.get(id_str)

        session.get.side_effect = mock_get
        session.query.return_value.filter.return_value.all.return_value = [rec]

        event_dict = _make_event_dict(
            "confirmation.escalation_resolved",
            "assignment_confirmation",
            str(conf_id),
            data={"resolution": "confirmed"},
        )

        handle_escalation_resolved(event_dict)

        assert rec.status == RecommendationStatus.SUPERSEDED.value


# ---------------------------------------------------------------------------
# Escalation window constant test
# ---------------------------------------------------------------------------

class TestEscalationConstants:
    """Verify the 24-hour window is correctly defined."""

    def test_window_is_24_hours(self):
        from app.workers.tasks.escalation import ESCALATION_WINDOW_HOURS
        assert ESCALATION_WINDOW_HOURS == 24
