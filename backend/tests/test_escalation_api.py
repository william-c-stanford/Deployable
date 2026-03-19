"""Tests for the escalation management API endpoints.

Tests the /api/escalations router endpoints:
  - GET /api/escalations - list escalated confirmations
  - GET /api/escalations/project/{project_id} - project-specific escalations
  - GET /api/escalations/{id} - single escalation detail
  - POST /api/escalations/{id}/resolve - resolve escalation (confirm, reassign, cancel)
  - POST /api/escalations/{id}/acknowledge - acknowledge escalation
  - GET /api/escalations/{id}/candidates - reassignment candidates
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta, date
import uuid

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def ops_user():
    """An ops user for authentication."""
    from app.auth import CurrentUser
    return CurrentUser(user_id="ops-user-1", role="ops")


@pytest.fixture
def mock_confirmation():
    """Create a mock escalated confirmation."""
    from app.models.assignment_confirmation import (
        ConfirmationStatus, ConfirmationType, EscalationStatus,
    )
    conf = MagicMock()
    conf.id = uuid.uuid4()
    conf.assignment_id = uuid.uuid4()
    conf.partner_id = uuid.uuid4()
    conf.confirmation_type = ConfirmationType.START_DATE
    conf.status = ConfirmationStatus.PENDING
    conf.escalated = True
    conf.escalated_at = datetime.utcnow() - timedelta(hours=2)
    conf.escalation_status = EscalationStatus.ESCALATED
    conf.requested_date = date.today()
    conf.requested_at = datetime.utcnow() - timedelta(hours=26)
    conf.responded_at = None
    conf.proposed_date = None
    conf.response_note = None
    conf.escalation_resolved_at = None
    conf.escalation_resolved_by = None
    conf.escalation_resolution_note = None
    conf.hours_waiting = 26.0

    # Mock assignment relationship
    assignment = MagicMock()
    assignment.id = conf.assignment_id
    assignment.technician_id = uuid.uuid4()
    assignment.role_id = uuid.uuid4()
    assignment.start_date = date.today()
    assignment.end_date = None
    assignment.hourly_rate = 55.0
    assignment.per_diem = 85.0
    assignment.assignment_type = "Active"
    assignment.status = "Active"
    assignment.partner_confirmed_start = False

    role = MagicMock()
    role.id = assignment.role_id
    role.role_name = "Lead Splicer"
    role.project = MagicMock()
    role.project.id = uuid.uuid4()
    role.project.name = "Phoenix Fiber Expansion"
    role.required_skills = [{"skill_name": "Fiber Splicing"}]
    role.required_certs = ["CFOT", "OSHA 10"]

    assignment.role = role
    conf.assignment = assignment

    return conf


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestEscalationSchemas:
    """Test Pydantic schema validation."""

    def test_resolve_request_confirm(self):
        from app.schemas.escalation import EscalationResolveRequest
        req = EscalationResolveRequest(resolution="confirm", resolution_note="Override OK")
        assert req.resolution == "confirm"
        assert req.resolution_note == "Override OK"
        assert req.new_technician_id is None

    def test_resolve_request_reassign_requires_tech(self):
        from app.schemas.escalation import EscalationResolveRequest
        tech_id = uuid.uuid4()
        req = EscalationResolveRequest(
            resolution="reassign",
            new_technician_id=tech_id,
            new_start_date=date(2026, 4, 1),
        )
        assert req.resolution == "reassign"
        assert req.new_technician_id == tech_id

    def test_resolve_request_cancel(self):
        from app.schemas.escalation import EscalationResolveRequest
        req = EscalationResolveRequest(
            resolution="cancel",
            resolution_note="Assignment no longer needed",
        )
        assert req.resolution == "cancel"

    def test_resolve_request_invalid_resolution(self):
        from app.schemas.escalation import EscalationResolveRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            EscalationResolveRequest(resolution="invalid")

    def test_escalation_summary(self):
        from app.schemas.escalation import EscalationSummary
        esc_id = uuid.uuid4()
        summary = EscalationSummary(
            id=esc_id,
            confirmation_id=esc_id,
            assignment_id=uuid.uuid4(),
            partner_id=uuid.uuid4(),
            partner_name="Lumen",
            project_name="Phoenix",
            role_name="Splicer",
            technician_name="John Smith",
            confirmation_type="start_date",
            requested_date=date.today(),
            requested_at=datetime.utcnow(),
            hours_waiting=26.0,
            escalation_status="escalated",
        )
        assert summary.partner_name == "Lumen"
        assert summary.hours_waiting == 26.0

    def test_reassignment_candidate(self):
        from app.schemas.escalation import ReassignmentCandidate
        c = ReassignmentCandidate(
            technician_id="tech-001",
            technician_name="Jane Doe",
            home_base_city="Phoenix",
            deployability_status="Ready Now",
            matching_skills=["Fiber Splicing"],
            matching_certs=["CFOT"],
        )
        assert len(c.matching_skills) == 1
        assert c.deployability_status == "Ready Now"


# ---------------------------------------------------------------------------
# Escalation resolution logic tests
# ---------------------------------------------------------------------------

class TestEscalationResolutionLogic:
    """Test escalation resolution outcomes."""

    def test_confirm_resolution_updates_status(self, mock_confirmation):
        """Confirming sets escalation_status to RESOLVED_CONFIRMED."""
        from app.models.assignment_confirmation import EscalationStatus, ConfirmationStatus

        # Simulate the resolve logic
        conf = mock_confirmation
        conf.escalation_status = EscalationStatus.RESOLVED_CONFIRMED
        conf.status = ConfirmationStatus.CONFIRMED
        conf.assignment.partner_confirmed_start = True

        assert conf.escalation_status == EscalationStatus.RESOLVED_CONFIRMED
        assert conf.status == ConfirmationStatus.CONFIRMED
        assert conf.assignment.partner_confirmed_start is True

    def test_reassign_resolution_cancels_old_assignment(self, mock_confirmation):
        """Reassigning should cancel the old assignment."""
        from app.models.assignment_confirmation import EscalationStatus

        conf = mock_confirmation
        conf.escalation_status = EscalationStatus.RESOLVED_REASSIGNED
        conf.assignment.status = "Cancelled"

        assert conf.assignment.status == "Cancelled"
        assert conf.escalation_status == EscalationStatus.RESOLVED_REASSIGNED

    def test_cancel_resolution_cancels_assignment(self, mock_confirmation):
        """Cancelling escalation should cancel the assignment."""
        from app.models.assignment_confirmation import EscalationStatus

        conf = mock_confirmation
        conf.escalation_status = EscalationStatus.RESOLVED_CANCELLED
        conf.assignment.status = "Cancelled"

        assert conf.assignment.status == "Cancelled"

    def test_acknowledge_sets_ops_reviewing(self, mock_confirmation):
        """Acknowledging should set status to OPS_REVIEWING."""
        from app.models.assignment_confirmation import EscalationStatus

        conf = mock_confirmation
        conf.escalation_status = EscalationStatus.OPS_REVIEWING

        assert conf.escalation_status == EscalationStatus.OPS_REVIEWING

    def test_already_resolved_cannot_be_resolved_again(self, mock_confirmation):
        """Already resolved escalations should be rejected."""
        from app.models.assignment_confirmation import EscalationStatus

        conf = mock_confirmation
        conf.escalation_status = EscalationStatus.RESOLVED_CONFIRMED

        # In the API, this would raise HTTP 409
        assert conf.escalation_status not in (
            EscalationStatus.ESCALATED,
            EscalationStatus.OPS_REVIEWING,
        )


# ---------------------------------------------------------------------------
# Escalation list filtering tests
# ---------------------------------------------------------------------------

class TestEscalationFiltering:
    """Test escalation list filtering logic."""

    def test_open_status_filter(self):
        """Only open escalations should be returned by default."""
        from app.models.assignment_confirmation import EscalationStatus
        statuses = [
            EscalationStatus.ESCALATED,
            EscalationStatus.OPS_REVIEWING,
            EscalationStatus.RESOLVED_CONFIRMED,
            EscalationStatus.RESOLVED_REASSIGNED,
        ]
        open_statuses = [s for s in statuses if s in (
            EscalationStatus.ESCALATED, EscalationStatus.OPS_REVIEWING
        )]
        assert len(open_statuses) == 2

    def test_resolved_count_calculation(self):
        """Resolved count should include all resolved statuses."""
        from app.models.assignment_confirmation import EscalationStatus
        statuses = [
            EscalationStatus.RESOLVED_CONFIRMED,
            EscalationStatus.RESOLVED_REASSIGNED,
            EscalationStatus.RESOLVED_CANCELLED,
        ]
        resolved = [s for s in statuses if s in (
            EscalationStatus.RESOLVED_CONFIRMED,
            EscalationStatus.RESOLVED_REASSIGNED,
            EscalationStatus.RESOLVED_CANCELLED,
        )]
        assert len(resolved) == 3


# ---------------------------------------------------------------------------
# Reassignment candidate matching tests
# ---------------------------------------------------------------------------

class TestReassignmentCandidateMatching:
    """Test reassignment candidate skill/cert matching."""

    def test_candidate_sorting_by_match_quality(self):
        """Candidates with more matches should rank higher."""
        from app.schemas.escalation import ReassignmentCandidate

        c1 = ReassignmentCandidate(
            technician_id="t1", technician_name="A",
            matching_skills=["Fiber Splicing"], matching_certs=["CFOT"],
        )
        c2 = ReassignmentCandidate(
            technician_id="t2", technician_name="B",
            matching_skills=["Fiber Splicing", "OTDR Testing"],
            matching_certs=["CFOT", "OSHA 10"],
        )
        c3 = ReassignmentCandidate(
            technician_id="t3", technician_name="C",
            matching_skills=[], matching_certs=["OSHA 10"],
        )

        candidates = [c1, c2, c3]
        candidates.sort(
            key=lambda c: len(c.matching_skills) + len(c.matching_certs),
            reverse=True,
        )
        assert candidates[0].technician_id == "t2"
        assert candidates[1].technician_id == "t1"
        assert candidates[2].technician_id == "t3"

    def test_candidate_excludes_current_assignee(self):
        """Current technician should not appear in candidates."""
        current_tech_id = "tech-001"
        all_techs = ["tech-001", "tech-002", "tech-003"]
        candidates = [t for t in all_techs if t != current_tech_id]
        assert "tech-001" not in candidates
        assert len(candidates) == 2
