"""Tests for project closure validation logic and API endpoint guards.

Covers the service layer (project_service.py) which checks:
1. Active assignments (HARD)
2. Open timesheets - Submitted/Flagged (HARD)
3. Unresolved escalations (HARD)
4. Pending partner confirmations (HARD)
5. Unfilled roles (SOFT)
6. Pending recommendations (SOFT)
7. Missing skill breakdowns (SOFT)

And the API endpoint guards:
- GET   /api/projects/{id}/close-check  (dry-run validation)
- POST  /api/projects/{id}/close        (actual closure with guard)
- PATCH /api/projects/{id}/status       (status update with close guard)
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, date, timedelta
import uuid

from app.services.project_service import (
    check_active_assignments,
    check_open_timesheets,
    check_unresolved_escalations,
    check_pending_confirmations,
    check_unfilled_roles,
    check_pending_recommendations,
    check_missing_skill_breakdowns,
    check_project_closure,
    close_project,
    auto_dismiss_pending_recommendations,
    get_project_role_ids,
    get_resolution_hints,
    ProjectClosureBlockedError,
    ProjectNotFoundError,
    InvalidProjectStateError,
    ClosureCheckResult,
)
from app.models.project import ProjectStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Create a mock database session with chainable query API."""
    db = MagicMock()
    return db


@pytest.fixture
def project_id():
    return uuid.uuid4()


@pytest.fixture
def role_ids():
    return [uuid.uuid4(), uuid.uuid4()]


# ---------------------------------------------------------------------------
# Unit tests: get_project_role_ids
# ---------------------------------------------------------------------------

class TestGetProjectRoleIds:
    def test_returns_role_ids_for_project(self, mock_db, project_id):
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        mock_db.query.return_value.filter.return_value.all.return_value = [
            (r1,), (r2,),
        ]
        result = get_project_role_ids(mock_db, project_id)
        assert result == [r1, r2]

    def test_returns_empty_when_no_roles(self, mock_db, project_id):
        mock_db.query.return_value.filter.return_value.all.return_value = []
        result = get_project_role_ids(mock_db, project_id)
        assert result == []


# ---------------------------------------------------------------------------
# Unit tests: check_active_assignments
# ---------------------------------------------------------------------------

class TestCheckActiveAssignments:
    def test_returns_empty_when_no_role_ids(self, mock_db):
        result = check_active_assignments(mock_db, [])
        assert result == []
        mock_db.query.assert_not_called()

    def test_returns_empty_when_no_active_assignments(self, mock_db, role_ids):
        mock_db.query.return_value.filter.return_value.all.return_value = []
        result = check_active_assignments(mock_db, role_ids)
        assert result == []

    def test_finds_active_assignments(self, mock_db, role_ids):
        a_id = uuid.uuid4()
        t_id = uuid.uuid4()
        mock_assignment = MagicMock()
        mock_assignment.id = a_id
        mock_assignment.technician_id = t_id
        mock_assignment.role_id = role_ids[0]
        mock_assignment.status = "Active"
        mock_assignment.start_date = date(2026, 1, 1)
        mock_assignment.end_date = date(2026, 6, 30)

        mock_db.query.return_value.filter.return_value.all.return_value = [mock_assignment]
        result = check_active_assignments(mock_db, role_ids)

        assert len(result) == 1
        assert result[0]["assignment_id"] == str(a_id)
        assert result[0]["status"] == "Active"
        assert result[0]["technician_id"] == str(t_id)

    def test_finds_pre_booked_assignments(self, mock_db, role_ids):
        mock_assignment = MagicMock()
        mock_assignment.id = uuid.uuid4()
        mock_assignment.technician_id = uuid.uuid4()
        mock_assignment.role_id = role_ids[0]
        mock_assignment.status = "Pre-Booked"
        mock_assignment.start_date = date(2026, 4, 1)
        mock_assignment.end_date = None

        mock_db.query.return_value.filter.return_value.all.return_value = [mock_assignment]
        result = check_active_assignments(mock_db, role_ids)

        assert len(result) == 1
        assert result[0]["status"] == "Pre-Booked"


# ---------------------------------------------------------------------------
# Unit tests: check_open_timesheets
# ---------------------------------------------------------------------------

class TestCheckOpenTimesheets:
    def test_returns_empty_when_no_role_ids(self, mock_db):
        result = check_open_timesheets(mock_db, [])
        assert result == []

    def test_returns_empty_when_no_assignments(self, mock_db, role_ids):
        # First query returns assignment IDs, which is empty
        mock_db.query.return_value.filter.return_value.all.return_value = []
        result = check_open_timesheets(mock_db, role_ids)
        assert result == []

    def test_finds_submitted_timesheets(self, mock_db, role_ids):
        from app.models.timesheet import TimesheetStatus

        a_id = uuid.uuid4()
        t_id = uuid.uuid4()

        # First call: get assignment IDs
        # Second call: get timesheets
        assignment_query = MagicMock()
        assignment_query.all.return_value = [(a_id,)]

        timesheet = MagicMock()
        timesheet.id = uuid.uuid4()
        timesheet.assignment_id = a_id
        timesheet.technician_id = t_id
        timesheet.week_start = date(2026, 3, 9)
        timesheet.hours = 40.0
        timesheet.status = TimesheetStatus.SUBMITTED

        timesheet_query = MagicMock()
        timesheet_query.all.return_value = [timesheet]

        # Set up chained query returns
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                return assignment_query
            return timesheet_query

        mock_db.query.return_value.filter.side_effect = side_effect

        result = check_open_timesheets(mock_db, role_ids)
        assert len(result) == 1
        assert result[0]["status"] == "Submitted"
        assert result[0]["hours"] == 40.0

    def test_finds_flagged_timesheets(self, mock_db, role_ids):
        from app.models.timesheet import TimesheetStatus

        a_id = uuid.uuid4()
        assignment_query = MagicMock()
        assignment_query.all.return_value = [(a_id,)]

        timesheet = MagicMock()
        timesheet.id = uuid.uuid4()
        timesheet.assignment_id = a_id
        timesheet.technician_id = uuid.uuid4()
        timesheet.week_start = date(2026, 3, 2)
        timesheet.hours = 45.0
        timesheet.status = TimesheetStatus.FLAGGED

        timesheet_query = MagicMock()
        timesheet_query.all.return_value = [timesheet]

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                return assignment_query
            return timesheet_query

        mock_db.query.return_value.filter.side_effect = side_effect

        result = check_open_timesheets(mock_db, role_ids)
        assert len(result) == 1
        assert result[0]["status"] == "Flagged"


# ---------------------------------------------------------------------------
# Unit tests: check_unresolved_escalations
# ---------------------------------------------------------------------------

class TestCheckUnresolvedEscalations:
    def test_returns_empty_when_no_role_ids(self, mock_db):
        result = check_unresolved_escalations(mock_db, [])
        assert result == []

    def test_returns_empty_when_no_assignments(self, mock_db, role_ids):
        mock_db.query.return_value.filter.return_value.all.return_value = []
        result = check_unresolved_escalations(mock_db, role_ids)
        assert result == []

    def test_finds_escalated_confirmations(self, mock_db, role_ids):
        from app.models.assignment_confirmation import EscalationStatus

        a_id = uuid.uuid4()
        p_id = uuid.uuid4()

        assignment_query = MagicMock()
        assignment_query.all.return_value = [(a_id,)]

        escalation = MagicMock()
        escalation.id = uuid.uuid4()
        escalation.assignment_id = a_id
        escalation.partner_id = p_id
        escalation.escalation_status = EscalationStatus.ESCALATED
        escalation.escalated_at = datetime.utcnow() - timedelta(hours=5)
        escalation.hours_waiting = 29.0

        escalation_query = MagicMock()
        escalation_query.all.return_value = [escalation]

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                return assignment_query
            return escalation_query

        mock_db.query.return_value.filter.side_effect = side_effect

        result = check_unresolved_escalations(mock_db, role_ids)
        assert len(result) == 1
        assert result[0]["escalation_status"] == "escalated"
        assert result[0]["hours_waiting"] == 29.0


# ---------------------------------------------------------------------------
# Integration tests: check_project_closure composite
# ---------------------------------------------------------------------------

class TestCheckPendingConfirmations:
    def test_returns_empty_when_no_role_ids(self, mock_db):
        result = check_pending_confirmations(mock_db, [])
        assert result == []

    def test_returns_empty_when_no_assignments(self, mock_db, role_ids):
        mock_db.query.return_value.filter.return_value.all.return_value = []
        result = check_pending_confirmations(mock_db, role_ids)
        assert result == []

    def test_finds_pending_confirmations(self, mock_db, role_ids):
        from app.models.assignment_confirmation import ConfirmationStatus, ConfirmationType

        a_id = uuid.uuid4()
        p_id = uuid.uuid4()

        assignment_query = MagicMock()
        assignment_query.all.return_value = [(a_id,)]

        conf = MagicMock()
        conf.id = uuid.uuid4()
        conf.assignment_id = a_id
        conf.partner_id = p_id
        conf.confirmation_type = ConfirmationType.START_DATE
        conf.requested_date = date(2026, 4, 1)
        conf.hours_waiting = 12.5
        conf.status = ConfirmationStatus.PENDING

        conf_query = MagicMock()
        conf_query.all.return_value = [conf]

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                return assignment_query
            return conf_query

        mock_db.query.return_value.filter.side_effect = side_effect

        result = check_pending_confirmations(mock_db, role_ids)
        assert len(result) == 1
        assert result[0]["confirmation_type"] == "start_date"
        assert result[0]["hours_waiting"] == 12.5


# ---------------------------------------------------------------------------
# Unit tests: check_unfilled_roles
# ---------------------------------------------------------------------------

class TestCheckUnfilledRoles:
    def test_returns_empty_when_all_filled(self, mock_db, project_id):
        role = MagicMock()
        role.id = uuid.uuid4()
        role.role_name = "Lead Splicer"
        role.quantity = 2
        role.filled = 2
        role.open_slots = 0

        mock_db.query.return_value.filter.return_value.all.return_value = [role]
        result = check_unfilled_roles(mock_db, project_id)
        assert result == []

    def test_finds_unfilled_roles(self, mock_db, project_id):
        role = MagicMock()
        role.id = uuid.uuid4()
        role.role_name = "Cable Puller"
        role.quantity = 3
        role.filled = 1
        role.open_slots = 2

        mock_db.query.return_value.filter.return_value.all.return_value = [role]
        result = check_unfilled_roles(mock_db, project_id)
        assert len(result) == 1
        assert result[0]["role_name"] == "Cable Puller"
        assert result[0]["open_slots"] == 2


# ---------------------------------------------------------------------------
# Unit tests: check_pending_recommendations
# ---------------------------------------------------------------------------

class TestCheckPendingRecommendations:
    def test_returns_empty_when_no_role_ids(self, mock_db):
        result = check_pending_recommendations(mock_db, [])
        assert result == []

    def test_finds_pending_recommendations(self, mock_db, role_ids):
        rec = MagicMock()
        rec.id = uuid.uuid4()
        rec.role_id = str(role_ids[0])
        rec.technician_id = str(uuid.uuid4())
        rec.overall_score = 0.85
        rec.status = "Pending"

        mock_db.query.return_value.filter.return_value.all.return_value = [rec]
        result = check_pending_recommendations(mock_db, role_ids)
        assert len(result) == 1
        assert result[0]["overall_score"] == 0.85


# ---------------------------------------------------------------------------
# Unit tests: check_missing_skill_breakdowns
# ---------------------------------------------------------------------------

class TestCheckMissingSkillBreakdowns:
    def test_returns_empty_when_no_role_ids(self, mock_db):
        result = check_missing_skill_breakdowns(mock_db, [])
        assert result == []

    def test_finds_assignments_without_breakdowns(self, mock_db, role_ids):
        a_id = uuid.uuid4()
        t_id = uuid.uuid4()

        assignment = MagicMock()
        assignment.id = a_id
        assignment.technician_id = t_id
        assignment.role_id = role_ids[0]
        assignment.status = "Completed"
        assignment.start_date = date(2026, 1, 1)
        assignment.end_date = date(2026, 3, 1)

        # First query: completed assignments
        # Second query: skill breakdowns (returns None)
        completed_query = MagicMock()
        completed_query.all.return_value = [assignment]

        breakdown_query = MagicMock()
        breakdown_query.first.return_value = None

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                return completed_query
            mock_result = MagicMock()
            mock_result.first.return_value = None
            return mock_result

        mock_db.query.return_value.filter.side_effect = side_effect

        result = check_missing_skill_breakdowns(mock_db, role_ids)
        assert len(result) == 1
        assert result[0]["assignment_id"] == str(a_id)


# ---------------------------------------------------------------------------
# Integration tests: check_project_closure composite
# ---------------------------------------------------------------------------

class TestCheckProjectClosure:
    @patch("app.services.project_service.check_missing_skill_breakdowns")
    @patch("app.services.project_service.check_pending_recommendations")
    @patch("app.services.project_service.check_unfilled_roles")
    @patch("app.services.project_service.check_pending_confirmations")
    @patch("app.services.project_service.check_unresolved_escalations")
    @patch("app.services.project_service.check_open_timesheets")
    @patch("app.services.project_service.check_active_assignments")
    @patch("app.services.project_service.get_project_role_ids")
    def test_can_close_when_all_clear(
        self, mock_roles, mock_assign, mock_ts, mock_esc,
        mock_conf, mock_unfilled, mock_recs, mock_breakdowns,
        mock_db, project_id
    ):
        mock_roles.return_value = [uuid.uuid4()]
        mock_assign.return_value = []
        mock_ts.return_value = []
        mock_esc.return_value = []
        mock_conf.return_value = []
        mock_unfilled.return_value = []
        mock_recs.return_value = []
        mock_breakdowns.return_value = []

        result = check_project_closure(mock_db, project_id)
        assert result.can_close is True
        assert result.has_warnings is False
        assert result.active_assignments == []
        assert result.open_timesheets == []
        assert result.unresolved_escalations == []
        assert result.pending_confirmations == []
        assert result.blocking_reasons == []
        assert result.hard_blocker_count == 0
        assert result.soft_blocker_count == 0

    @patch("app.services.project_service.check_missing_skill_breakdowns")
    @patch("app.services.project_service.check_pending_recommendations")
    @patch("app.services.project_service.check_unfilled_roles")
    @patch("app.services.project_service.check_pending_confirmations")
    @patch("app.services.project_service.check_unresolved_escalations")
    @patch("app.services.project_service.check_open_timesheets")
    @patch("app.services.project_service.check_active_assignments")
    @patch("app.services.project_service.get_project_role_ids")
    def test_blocked_by_active_assignments(
        self, mock_roles, mock_assign, mock_ts, mock_esc,
        mock_conf, mock_unfilled, mock_recs, mock_breakdowns,
        mock_db, project_id
    ):
        mock_roles.return_value = [uuid.uuid4()]
        mock_assign.return_value = [{"assignment_id": "a1", "status": "Active"}]
        mock_ts.return_value = []
        mock_esc.return_value = []
        mock_conf.return_value = []
        mock_unfilled.return_value = []
        mock_recs.return_value = []
        mock_breakdowns.return_value = []

        result = check_project_closure(mock_db, project_id)
        assert result.can_close is False
        assert len(result.active_assignments) == 1
        assert "1 active assignment" in result.blocking_reasons[0]

    @patch("app.services.project_service.check_missing_skill_breakdowns")
    @patch("app.services.project_service.check_pending_recommendations")
    @patch("app.services.project_service.check_unfilled_roles")
    @patch("app.services.project_service.check_pending_confirmations")
    @patch("app.services.project_service.check_unresolved_escalations")
    @patch("app.services.project_service.check_open_timesheets")
    @patch("app.services.project_service.check_active_assignments")
    @patch("app.services.project_service.get_project_role_ids")
    def test_blocked_by_open_timesheets(
        self, mock_roles, mock_assign, mock_ts, mock_esc,
        mock_conf, mock_unfilled, mock_recs, mock_breakdowns,
        mock_db, project_id
    ):
        mock_roles.return_value = [uuid.uuid4()]
        mock_assign.return_value = []
        mock_ts.return_value = [
            {"timesheet_id": "t1", "status": "Submitted"},
            {"timesheet_id": "t2", "status": "Flagged"},
        ]
        mock_esc.return_value = []
        mock_conf.return_value = []
        mock_unfilled.return_value = []
        mock_recs.return_value = []
        mock_breakdowns.return_value = []

        result = check_project_closure(mock_db, project_id)
        assert result.can_close is False
        assert len(result.open_timesheets) == 2
        assert "2 open timesheet" in result.blocking_reasons[0]

    @patch("app.services.project_service.check_missing_skill_breakdowns")
    @patch("app.services.project_service.check_pending_recommendations")
    @patch("app.services.project_service.check_unfilled_roles")
    @patch("app.services.project_service.check_pending_confirmations")
    @patch("app.services.project_service.check_unresolved_escalations")
    @patch("app.services.project_service.check_open_timesheets")
    @patch("app.services.project_service.check_active_assignments")
    @patch("app.services.project_service.get_project_role_ids")
    def test_blocked_by_unresolved_escalations(
        self, mock_roles, mock_assign, mock_ts, mock_esc,
        mock_conf, mock_unfilled, mock_recs, mock_breakdowns,
        mock_db, project_id
    ):
        mock_roles.return_value = [uuid.uuid4()]
        mock_assign.return_value = []
        mock_ts.return_value = []
        mock_esc.return_value = [{"escalation_id": "e1", "escalation_status": "escalated"}]
        mock_conf.return_value = []
        mock_unfilled.return_value = []
        mock_recs.return_value = []
        mock_breakdowns.return_value = []

        result = check_project_closure(mock_db, project_id)
        assert result.can_close is False
        assert len(result.unresolved_escalations) == 1
        assert "1 unresolved escalation" in result.blocking_reasons[0]

    @patch("app.services.project_service.check_missing_skill_breakdowns")
    @patch("app.services.project_service.check_pending_recommendations")
    @patch("app.services.project_service.check_unfilled_roles")
    @patch("app.services.project_service.check_pending_confirmations")
    @patch("app.services.project_service.check_unresolved_escalations")
    @patch("app.services.project_service.check_open_timesheets")
    @patch("app.services.project_service.check_active_assignments")
    @patch("app.services.project_service.get_project_role_ids")
    def test_blocked_by_pending_confirmations(
        self, mock_roles, mock_assign, mock_ts, mock_esc,
        mock_conf, mock_unfilled, mock_recs, mock_breakdowns,
        mock_db, project_id
    ):
        mock_roles.return_value = [uuid.uuid4()]
        mock_assign.return_value = []
        mock_ts.return_value = []
        mock_esc.return_value = []
        mock_conf.return_value = [{"confirmation_id": "c1", "confirmation_type": "start_date"}]
        mock_unfilled.return_value = []
        mock_recs.return_value = []
        mock_breakdowns.return_value = []

        result = check_project_closure(mock_db, project_id)
        assert result.can_close is False
        assert len(result.pending_confirmations) == 1
        assert "1 pending partner confirmation" in result.blocking_reasons[0]

    @patch("app.services.project_service.check_missing_skill_breakdowns")
    @patch("app.services.project_service.check_pending_recommendations")
    @patch("app.services.project_service.check_unfilled_roles")
    @patch("app.services.project_service.check_pending_confirmations")
    @patch("app.services.project_service.check_unresolved_escalations")
    @patch("app.services.project_service.check_open_timesheets")
    @patch("app.services.project_service.check_active_assignments")
    @patch("app.services.project_service.get_project_role_ids")
    def test_soft_blockers_allow_close_with_warnings(
        self, mock_roles, mock_assign, mock_ts, mock_esc,
        mock_conf, mock_unfilled, mock_recs, mock_breakdowns,
        mock_db, project_id
    ):
        """Soft blockers should set has_warnings=True but can_close=True."""
        mock_roles.return_value = [uuid.uuid4()]
        mock_assign.return_value = []
        mock_ts.return_value = []
        mock_esc.return_value = []
        mock_conf.return_value = []
        mock_unfilled.return_value = [
            {"role_id": "r1", "role_name": "Splicer", "open_slots": 2, "quantity": 3, "filled": 1}
        ]
        mock_recs.return_value = [
            {"recommendation_id": "rec1", "role_id": "r1", "overall_score": 0.85, "status": "Pending"}
        ]
        mock_breakdowns.return_value = [
            {"assignment_id": "a1", "technician_id": "t1"}
        ]

        result = check_project_closure(mock_db, project_id)
        assert result.can_close is True
        assert result.has_warnings is True
        assert result.soft_blocker_count == 3
        assert result.hard_blocker_count == 0
        assert len(result.warning_reasons) == 3

    @patch("app.services.project_service.check_missing_skill_breakdowns")
    @patch("app.services.project_service.check_pending_recommendations")
    @patch("app.services.project_service.check_unfilled_roles")
    @patch("app.services.project_service.check_pending_confirmations")
    @patch("app.services.project_service.check_unresolved_escalations")
    @patch("app.services.project_service.check_open_timesheets")
    @patch("app.services.project_service.check_active_assignments")
    @patch("app.services.project_service.get_project_role_ids")
    def test_blocked_by_multiple_conditions(
        self, mock_roles, mock_assign, mock_ts, mock_esc,
        mock_conf, mock_unfilled, mock_recs, mock_breakdowns,
        mock_db, project_id
    ):
        mock_roles.return_value = [uuid.uuid4()]
        mock_assign.return_value = [{"assignment_id": "a1", "status": "Active"}]
        mock_ts.return_value = [{"timesheet_id": "t1", "status": "Submitted"}]
        mock_esc.return_value = [{"escalation_id": "e1", "escalation_status": "escalated"}]
        mock_conf.return_value = [{"confirmation_id": "c1", "confirmation_type": "start_date"}]
        mock_unfilled.return_value = [
            {"role_id": "r1", "role_name": "Splicer", "open_slots": 1, "quantity": 2, "filled": 1}
        ]
        mock_recs.return_value = []
        mock_breakdowns.return_value = []

        result = check_project_closure(mock_db, project_id)
        assert result.can_close is False
        assert result.has_warnings is True
        assert len(result.blocking_reasons) == 4  # 4 hard blocker types
        assert result.hard_blocker_count == 4
        assert result.soft_blocker_count == 1

    @patch("app.services.project_service.check_missing_skill_breakdowns")
    @patch("app.services.project_service.check_pending_recommendations")
    @patch("app.services.project_service.check_unfilled_roles")
    @patch("app.services.project_service.check_pending_confirmations")
    @patch("app.services.project_service.check_unresolved_escalations")
    @patch("app.services.project_service.check_open_timesheets")
    @patch("app.services.project_service.check_active_assignments")
    @patch("app.services.project_service.get_project_role_ids")
    def test_project_with_no_roles_can_close(
        self, mock_roles, mock_assign, mock_ts, mock_esc,
        mock_conf, mock_unfilled, mock_recs, mock_breakdowns,
        mock_db, project_id
    ):
        mock_roles.return_value = []
        mock_assign.return_value = []
        mock_ts.return_value = []
        mock_esc.return_value = []
        mock_conf.return_value = []
        mock_unfilled.return_value = []
        mock_recs.return_value = []
        mock_breakdowns.return_value = []

        result = check_project_closure(mock_db, project_id)
        assert result.can_close is True


# ---------------------------------------------------------------------------
# Integration tests: close_project
# ---------------------------------------------------------------------------

class TestCloseProject:
    @patch("app.services.project_service.check_project_closure")
    def test_close_succeeds_when_all_clear(self, mock_check, mock_db, project_id):
        project = MagicMock()
        project.id = project_id
        project.status = ProjectStatus.ACTIVE
        project.name = "Test Project"
        mock_db.query.return_value.filter.return_value.first.return_value = project

        mock_check.return_value = ClosureCheckResult(can_close=True)

        result = close_project(mock_db, project_id)
        assert result.status == ProjectStatus.CLOSED
        mock_db.flush.assert_called_once()

    def test_raises_not_found(self, mock_db, project_id):
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ProjectNotFoundError, match="not found"):
            close_project(mock_db, project_id)

    def test_raises_already_closed(self, mock_db, project_id):
        project = MagicMock()
        project.id = project_id
        project.status = ProjectStatus.CLOSED
        mock_db.query.return_value.filter.return_value.first.return_value = project

        with pytest.raises(InvalidProjectStateError, match="already closed"):
            close_project(mock_db, project_id)

    def test_raises_draft_cannot_close(self, mock_db, project_id):
        project = MagicMock()
        project.id = project_id
        project.status = ProjectStatus.DRAFT
        mock_db.query.return_value.filter.return_value.first.return_value = project

        with pytest.raises(InvalidProjectStateError, match="Draft"):
            close_project(mock_db, project_id)

    @patch("app.services.project_service.check_project_closure")
    def test_raises_blocked_with_details(self, mock_check, mock_db, project_id):
        project = MagicMock()
        project.id = project_id
        project.status = ProjectStatus.ACTIVE
        mock_db.query.return_value.filter.return_value.first.return_value = project

        mock_check.return_value = ClosureCheckResult(
            can_close=False,
            active_assignments=[{"assignment_id": "a1", "status": "Active"}],
            open_timesheets=[{"timesheet_id": "t1", "status": "Submitted"}],
            unresolved_escalations=[],
        )

        with pytest.raises(ProjectClosureBlockedError) as exc_info:
            close_project(mock_db, project_id)

        err = exc_info.value
        assert len(err.active_assignments) == 1
        assert len(err.open_timesheets) == 1
        assert len(err.unresolved_escalations) == 0
        assert "Cannot close project" in err.message

    @patch("app.services.project_service.check_project_closure")
    def test_wrapping_up_project_can_close(self, mock_check, mock_db, project_id):
        """Projects in 'Wrapping Up' status should be closable."""
        project = MagicMock()
        project.id = project_id
        project.status = ProjectStatus.WRAPPING_UP
        project.name = "Wrapping Up Project"
        mock_db.query.return_value.filter.return_value.first.return_value = project

        mock_check.return_value = ClosureCheckResult(can_close=True)

        result = close_project(mock_db, project_id)
        assert result.status == ProjectStatus.CLOSED

    @patch("app.services.project_service.check_project_closure")
    def test_on_hold_project_can_close(self, mock_check, mock_db, project_id):
        """Projects on hold should be closable if no blockers."""
        project = MagicMock()
        project.id = project_id
        project.status = ProjectStatus.ON_HOLD
        project.name = "On Hold Project"
        mock_db.query.return_value.filter.return_value.first.return_value = project

        mock_check.return_value = ClosureCheckResult(can_close=True)

        result = close_project(mock_db, project_id)
        assert result.status == ProjectStatus.CLOSED


# ---------------------------------------------------------------------------
# ClosureCheckResult dataclass tests
# ---------------------------------------------------------------------------

class TestClosureCheckResult:
    def test_no_blockers(self):
        result = ClosureCheckResult()
        assert result.can_close is True
        assert result.has_warnings is False
        assert result.blocking_reasons == []
        assert result.warning_reasons == []
        assert result.hard_blocker_count == 0
        assert result.soft_blocker_count == 0

    def test_blocking_reasons_formatting(self):
        result = ClosureCheckResult(
            can_close=False,
            active_assignments=[{"id": "1"}, {"id": "2"}],
            open_timesheets=[{"id": "3"}],
            unresolved_escalations=[{"id": "4"}, {"id": "5"}, {"id": "6"}],
            pending_confirmations=[{"id": "7"}],
        )
        reasons = result.blocking_reasons
        assert len(reasons) == 4
        assert "2 active assignment(s)" in reasons[0]
        assert "1 open timesheet(s)" in reasons[1]
        assert "3 unresolved escalation(s)" in reasons[2]
        assert "1 pending partner confirmation" in reasons[3]
        assert result.hard_blocker_count == 7

    def test_warning_reasons_formatting(self):
        result = ClosureCheckResult(
            can_close=True,
            has_warnings=True,
            unfilled_roles=[
                {"role_id": "r1", "open_slots": 2},
                {"role_id": "r2", "open_slots": 1},
            ],
            pending_recommendations=[{"id": "rec1"}],
            missing_skill_breakdowns=[{"id": "sb1"}, {"id": "sb2"}],
        )
        warnings = result.warning_reasons
        assert len(warnings) == 3
        assert "2 role(s) with 3 total unfilled" in warnings[0]
        assert "1 pending staffing recommendation" in warnings[1]
        assert "2 completed assignment(s) missing" in warnings[2]
        assert result.soft_blocker_count == 5


# ---------------------------------------------------------------------------
# Resolution hints tests
# ---------------------------------------------------------------------------

class TestResolutionHints:
    def test_hints_for_all_hard_blockers(self):
        result = ClosureCheckResult(
            can_close=False,
            active_assignments=[{"assignment_id": "a1", "status": "Active"}],
            open_timesheets=[
                {"timesheet_id": "t1", "status": "Submitted"},
                {"timesheet_id": "t2", "status": "Flagged"},
            ],
            unresolved_escalations=[{"escalation_id": "e1"}],
            pending_confirmations=[{"confirmation_id": "c1"}],
        )
        hints = get_resolution_hints(result)
        assert len(hints) == 4
        assert any("active assignment" in h.lower() for h in hints)
        assert any("timesheet" in h.lower() for h in hints)
        assert any("escalated" in h.lower() for h in hints)
        assert any("partner confirmation" in h.lower() for h in hints)

    def test_hints_for_soft_blockers(self):
        result = ClosureCheckResult(
            can_close=True,
            has_warnings=True,
            unfilled_roles=[{"role_id": "r1", "open_slots": 2}],
            pending_recommendations=[{"recommendation_id": "rec1"}],
            missing_skill_breakdowns=[{"assignment_id": "a1"}],
        )
        hints = get_resolution_hints(result)
        assert len(hints) == 3
        assert any("unfilled" in h.lower() for h in hints)
        assert any("recommendation" in h.lower() for h in hints)
        assert any("skill breakdown" in h.lower() for h in hints)

    def test_no_hints_when_clean(self):
        result = ClosureCheckResult()
        hints = get_resolution_hints(result)
        assert hints == []

    def test_timesheet_hints_differentiate_submitted_and_flagged(self):
        result = ClosureCheckResult(
            can_close=False,
            open_timesheets=[
                {"timesheet_id": "t1", "status": "Submitted"},
                {"timesheet_id": "t2", "status": "Submitted"},
                {"timesheet_id": "t3", "status": "Flagged"},
            ],
        )
        hints = get_resolution_hints(result)
        # Should have a single hint mentioning both submitted and flagged
        ts_hint = [h for h in hints if "timesheet" in h.lower()][0]
        assert "2 pending" in ts_hint
        assert "1 flagged" in ts_hint


# ---------------------------------------------------------------------------
# Auto-dismiss pending recommendations tests
# ---------------------------------------------------------------------------

class TestAutoDismissPendingRecommendations:
    def test_returns_zero_when_no_role_ids(self, mock_db):
        result = auto_dismiss_pending_recommendations(mock_db, [])
        assert result == 0

    def test_dismisses_pending_recommendations(self, mock_db, role_ids):
        rec1 = MagicMock()
        rec1.status = "Pending"
        rec2 = MagicMock()
        rec2.status = "Pending"

        mock_db.query.return_value.filter.return_value.all.return_value = [rec1, rec2]

        result = auto_dismiss_pending_recommendations(mock_db, role_ids)
        assert result == 2
        assert rec1.status == "Dismissed"
        assert rec2.status == "Dismissed"
        mock_db.flush.assert_called_once()


# ---------------------------------------------------------------------------
# Exception tests
# ---------------------------------------------------------------------------

class TestProjectClosureBlockedError:
    def test_stores_all_blocking_details(self):
        err = ProjectClosureBlockedError(
            message="Cannot close",
            active_assignments=[{"id": "a1"}],
            open_timesheets=[{"id": "t1"}, {"id": "t2"}],
            unresolved_escalations=[{"id": "e1"}],
        )
        assert err.message == "Cannot close"
        assert len(err.active_assignments) == 1
        assert len(err.open_timesheets) == 2
        assert len(err.unresolved_escalations) == 1

    def test_defaults_to_empty_lists(self):
        err = ProjectClosureBlockedError(message="Blocked")
        assert err.active_assignments == []
        assert err.open_timesheets == []
        assert err.unresolved_escalations == []


# ---------------------------------------------------------------------------
# API endpoint guard tests (router-level)
# ---------------------------------------------------------------------------

class TestProjectCloseEndpointGuard:
    """Tests for the API endpoint close-validation guards.

    These test the _build_blocking_items function used by the router
    to convert ClosureCheckResult into structured API responses.
    """

    def test_build_blocking_items_with_all_types(self):
        """Verify _build_blocking_items produces correct BlockingItem types."""
        from app.routers.projects import _build_blocking_items
        from app.schemas.project import BlockingSeverity, BlockingItemType

        result = ClosureCheckResult(
            can_close=False,
            has_warnings=True,
            active_assignments=[{
                "assignment_id": "a1",
                "technician_id": "tech12345678",
                "status": "Active",
                "start_date": "2026-01-01",
                "end_date": "2026-06-30",
            }],
            open_timesheets=[{
                "timesheet_id": "t1",
                "assignment_id": "a1",
                "technician_id": "tech12345678",
                "week_start": "2026-03-09",
                "hours": 40.0,
                "status": "Submitted",
            }],
            unresolved_escalations=[{
                "escalation_id": "e1",
                "assignment_id": "a1",
                "partner_id": "part12345678",
                "escalation_status": "escalated",
                "hours_waiting": 30.0,
            }],
            pending_confirmations=[{
                "confirmation_id": "c1",
                "assignment_id": "a1",
                "partner_id": "part12345678",
                "confirmation_type": "start_date",
                "requested_date": "2026-04-01",
                "hours_waiting": 12.0,
            }],
            unfilled_roles=[{
                "role_id": "r1",
                "role_name": "Lead Splicer",
                "quantity": 3,
                "filled": 1,
                "open_slots": 2,
            }],
            pending_recommendations=[{
                "recommendation_id": "rec1",
                "role_id": "r1",
                "technician_id": "tech12345678",
                "overall_score": 0.85,
                "status": "Pending",
            }],
            missing_skill_breakdowns=[{
                "assignment_id": "a2",
                "technician_id": "tech12345678",
                "role_id": "r1",
                "start_date": "2026-01-01",
                "end_date": "2026-03-01",
            }],
        )

        items = _build_blocking_items(result)

        # Should have 7 items total (1 of each type)
        assert len(items) == 7

        # Check hard blockers
        hard_items = [i for i in items if i.severity == BlockingSeverity.HARD]
        assert len(hard_items) == 4
        hard_types = {i.type for i in hard_items}
        assert BlockingItemType.ACTIVE_ASSIGNMENT in hard_types
        assert BlockingItemType.PENDING_TIMESHEET in hard_types
        assert BlockingItemType.ESCALATED_CONFIRMATION in hard_types
        assert BlockingItemType.PENDING_CONFIRMATION in hard_types

        # Check soft blockers
        soft_items = [i for i in items if i.severity == BlockingSeverity.SOFT]
        assert len(soft_items) == 3
        soft_types = {i.type for i in soft_items}
        assert BlockingItemType.UNFILLED_ROLE in soft_types
        assert BlockingItemType.PENDING_RECOMMENDATION in soft_types
        assert BlockingItemType.PENDING_SKILL_BREAKDOWN in soft_types

    def test_build_blocking_items_differentiates_flagged_vs_submitted(self):
        """Verify flagged timesheets get FLAGGED_TIMESHEET type, submitted get PENDING."""
        from app.routers.projects import _build_blocking_items
        from app.schemas.project import BlockingItemType

        result = ClosureCheckResult(
            can_close=False,
            open_timesheets=[
                {
                    "timesheet_id": "t1",
                    "assignment_id": "a1",
                    "technician_id": "tech12345678",
                    "week_start": "2026-03-09",
                    "hours": 40.0,
                    "status": "Flagged",
                },
                {
                    "timesheet_id": "t2",
                    "assignment_id": "a1",
                    "technician_id": "tech12345678",
                    "week_start": "2026-03-16",
                    "hours": 35.0,
                    "status": "Submitted",
                },
            ],
        )

        items = _build_blocking_items(result)
        assert len(items) == 2
        types = {i.type for i in items}
        assert BlockingItemType.FLAGGED_TIMESHEET in types
        assert BlockingItemType.PENDING_TIMESHEET in types

    def test_build_blocking_items_empty_result(self):
        """No items when result is clean."""
        from app.routers.projects import _build_blocking_items

        result = ClosureCheckResult(can_close=True)
        items = _build_blocking_items(result)
        assert items == []

    def test_blocking_items_have_required_fields(self):
        """All blocking items should have entity_id, entity_type, summary."""
        from app.routers.projects import _build_blocking_items

        result = ClosureCheckResult(
            can_close=False,
            active_assignments=[{
                "assignment_id": "a1",
                "technician_id": "tech12345678",
                "status": "Active",
                "start_date": "2026-01-01",
                "end_date": None,
            }],
        )

        items = _build_blocking_items(result)
        for item in items:
            assert item.entity_id is not None
            assert item.entity_type is not None
            assert item.summary is not None
            assert len(item.summary) > 0
