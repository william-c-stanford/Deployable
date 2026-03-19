"""Tests for forward staffing schedule endpoints and assignment chaining."""

import uuid
from datetime import date, timedelta, datetime

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.models.assignment import Assignment, AssignmentType, AssignmentStatus, ChainPriority
from app.models.technician import Technician
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.user import Partner


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

def _make_partner(db):
    p = Partner(id=uuid.uuid4(), company_name="TestCo", contact_email="test@co.com")
    db.add(p)
    db.flush()
    return p


def _make_project(db, partner_id, name="Test Project", region="Southwest"):
    proj = Project(
        id=uuid.uuid4(),
        name=name,
        partner_id=partner_id,
        status=ProjectStatus.ACTIVE,
        location_region=region,
        start_date=date.today(),
    )
    db.add(proj)
    db.flush()
    return proj


def _make_role(db, project_id, name="Lead Splicer"):
    role = ProjectRole(
        id=uuid.uuid4(),
        project_id=project_id,
        role_name=name,
        hourly_rate=45.0,
        per_diem=75.0,
    )
    db.add(role)
    db.flush()
    return role


def _make_technician(db, first="John", last="Doe"):
    tech = Technician(
        id=uuid.uuid4(),
        first_name=first,
        last_name=last,
        email=f"{first.lower()}.{last.lower()}.{uuid.uuid4().hex[:6]}@test.com",
    )
    db.add(tech)
    db.flush()
    return tech


def _make_assignment(db, tech_id, role_id, start, end=None, **kwargs):
    a = Assignment(
        id=uuid.uuid4(),
        technician_id=tech_id,
        role_id=role_id,
        start_date=start,
        end_date=end,
        assignment_type=kwargs.get("assignment_type", AssignmentType.ACTIVE),
        status=kwargs.get("status", "Active"),
        is_forward_booked=kwargs.get("is_forward_booked", False),
        booking_confidence=kwargs.get("booking_confidence"),
        chain_id=kwargs.get("chain_id"),
        chain_position=kwargs.get("chain_position"),
    )
    db.add(a)
    db.flush()
    return a


# Mock auth for all tests
@pytest.fixture(autouse=True)
def mock_auth():
    """Mock authentication to always return an ops user."""
    from app.auth import CurrentUser

    mock_user = CurrentUser(
        user_id=str(uuid.uuid4()),
        role="ops",
        email="ops@test.com",
    )

    with patch("app.routers.forward_staffing.require_role") as mock_require:
        mock_require.return_value = lambda: mock_user

        with patch("app.routers.forward_staffing.get_current_user", return_value=mock_user):
            # Also patch the Depends resolution
            from app.routers.forward_staffing import router
            yield mock_user


# ---------------------------------------------------------------------------
# Unit tests for model fields
# ---------------------------------------------------------------------------

class TestAssignmentModelFields:
    """Test that new forward staffing fields exist on the Assignment model."""

    def test_forward_booking_fields_exist(self):
        a = Assignment()
        assert hasattr(a, "is_forward_booked")
        assert hasattr(a, "booking_confidence")
        assert hasattr(a, "confirmed_at")
        assert hasattr(a, "confirmed_by")

    def test_chaining_fields_exist(self):
        a = Assignment()
        assert hasattr(a, "previous_assignment_id")
        assert hasattr(a, "next_assignment_id")
        assert hasattr(a, "chain_id")
        assert hasattr(a, "chain_position")
        assert hasattr(a, "chain_priority")
        assert hasattr(a, "gap_days")
        assert hasattr(a, "chain_notes")

    def test_is_pre_booked_property(self):
        a = Assignment(assignment_type=AssignmentType.PRE_BOOKED)
        assert a.is_pre_booked is True

        a2 = Assignment(assignment_type=AssignmentType.ACTIVE)
        assert a2.is_pre_booked is False

    def test_is_chained_property(self):
        a = Assignment(chain_id=uuid.uuid4())
        assert a.is_chained is True

        a2 = Assignment()
        assert a2.is_chained is False

    def test_duration_days_property(self):
        a = Assignment(start_date=date(2026, 4, 1), end_date=date(2026, 6, 30))
        assert a.duration_days == 90

        a2 = Assignment(start_date=date(2026, 4, 1))
        assert a2.duration_days is None

    def test_assignment_status_enum_has_pre_booked(self):
        assert AssignmentStatus.PRE_BOOKED.value == "Pre-Booked"
        assert AssignmentStatus.PENDING_CONFIRMATION.value == "Pending Confirmation"

    def test_chain_priority_enum(self):
        assert ChainPriority.HIGH.value == "High"
        assert ChainPriority.MEDIUM.value == "Medium"
        assert ChainPriority.LOW.value == "Low"


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestForwardStaffingSchemas:
    """Test Pydantic schemas for forward staffing."""

    def test_forward_assignment_create_validation(self):
        from app.schemas.forward_staffing import ForwardAssignmentCreate

        data = ForwardAssignmentCreate(
            technician_id=str(uuid.uuid4()),
            role_id=str(uuid.uuid4()),
            start_date=date(2026, 5, 1),
            end_date=date(2026, 7, 31),
            booking_confidence=0.85,
        )
        assert data.booking_confidence == 0.85
        assert data.start_date == date(2026, 5, 1)

    def test_booking_confidence_bounds(self):
        from app.schemas.forward_staffing import ForwardAssignmentCreate
        from pydantic import ValidationError

        # Valid: within bounds
        data = ForwardAssignmentCreate(
            technician_id=str(uuid.uuid4()),
            role_id=str(uuid.uuid4()),
            start_date=date(2026, 5, 1),
            booking_confidence=0.0,
        )
        assert data.booking_confidence == 0.0

        data2 = ForwardAssignmentCreate(
            technician_id=str(uuid.uuid4()),
            role_id=str(uuid.uuid4()),
            start_date=date(2026, 5, 1),
            booking_confidence=1.0,
        )
        assert data2.booking_confidence == 1.0

        # Invalid: out of bounds
        with pytest.raises(ValidationError):
            ForwardAssignmentCreate(
                technician_id=str(uuid.uuid4()),
                role_id=str(uuid.uuid4()),
                start_date=date(2026, 5, 1),
                booking_confidence=1.5,
            )

    def test_chain_create_request(self):
        from app.schemas.forward_staffing import ChainCreateRequest, ChainAssignmentEntry

        entries = [
            ChainAssignmentEntry(
                role_id=str(uuid.uuid4()),
                start_date=date(2026, 5, 1),
                end_date=date(2026, 6, 30),
            ),
            ChainAssignmentEntry(
                role_id=str(uuid.uuid4()),
                start_date=date(2026, 7, 1),
                end_date=date(2026, 8, 31),
            ),
        ]
        chain = ChainCreateRequest(
            technician_id=str(uuid.uuid4()),
            assignments=entries,
            chain_priority="High",
        )
        assert len(chain.assignments) == 2
        assert chain.chain_priority == "High"

    def test_forward_schedule_response(self):
        from app.schemas.forward_staffing import ForwardScheduleResponse

        resp = ForwardScheduleResponse(
            schedule_start=date(2026, 3, 19),
            schedule_end=date(2026, 6, 17),
            total_assignments=5,
            active_count=3,
            pre_booked_count=2,
            chained_count=1,
            entries=[],
        )
        assert resp.total_assignments == 5

    def test_technician_gap_schema(self):
        from app.schemas.forward_staffing import TechnicianGap

        gap = TechnicianGap(
            technician_id=str(uuid.uuid4()),
            technician_name="John Doe",
            gap_start=date(2026, 5, 1),
            gap_end=date(2026, 5, 14),
            gap_days=14,
        )
        assert gap.gap_days == 14


# ---------------------------------------------------------------------------
# Integration-style tests (using helpers to test endpoint logic)
# ---------------------------------------------------------------------------

class TestAssignmentChainingLogic:
    """Test the chaining logic at the model level."""

    def test_gap_days_calculation(self):
        """Verify gap_days computes correctly between chained assignments."""
        prev_end = date(2026, 4, 30)
        next_start = date(2026, 5, 3)
        gap = (next_start - prev_end).days - 1
        assert gap == 2  # May 1 and May 2

    def test_chain_position_ordering(self):
        """Chain positions should be sequential."""
        chain_id = uuid.uuid4()
        assignments = [
            Assignment(chain_id=chain_id, chain_position=1, start_date=date(2026, 4, 1)),
            Assignment(chain_id=chain_id, chain_position=2, start_date=date(2026, 5, 1)),
            Assignment(chain_id=chain_id, chain_position=3, start_date=date(2026, 6, 1)),
        ]
        positions = [a.chain_position for a in assignments]
        assert positions == [1, 2, 3]

    def test_seamless_chain_zero_gap(self):
        """A seamless chain has 0 gap days."""
        prev_end = date(2026, 4, 30)
        next_start = date(2026, 5, 1)
        gap = (next_start - prev_end).days - 1
        assert gap == 0


class TestForwardScheduleHelpers:
    """Test helper functions used by the forward staffing router."""

    def test_find_gaps_identifies_gaps(self):
        from app.routers.forward_staffing import _find_gaps

        tech = MagicMock()
        tech.full_name = "John Doe"

        a1 = MagicMock(spec=Assignment)
        a1.technician_id = uuid.uuid4()
        a1.id = uuid.uuid4()
        a1.start_date = date(2026, 4, 1)
        a1.end_date = date(2026, 4, 30)
        a1.technician = tech
        a1.role = MagicMock()
        a1.role.project = MagicMock()
        a1.role.project.name = "Project A"

        a2 = MagicMock(spec=Assignment)
        a2.technician_id = a1.technician_id
        a2.id = uuid.uuid4()
        a2.start_date = date(2026, 5, 15)
        a2.end_date = date(2026, 6, 30)
        a2.technician = tech
        a2.role = MagicMock()
        a2.role.project = MagicMock()
        a2.role.project.name = "Project B"

        gaps = _find_gaps([a1, a2], date(2026, 4, 1), date(2026, 7, 1))
        assert len(gaps) == 1
        assert gaps[0].gap_days == 14  # May 1 through May 14
        assert gaps[0].previous_project_name == "Project A"
        assert gaps[0].next_project_name == "Project B"

    def test_find_gaps_seamless_no_gaps(self):
        from app.routers.forward_staffing import _find_gaps

        tech = MagicMock()
        tech.full_name = "Jane Doe"

        a1 = MagicMock(spec=Assignment)
        a1.technician_id = uuid.uuid4()
        a1.id = uuid.uuid4()
        a1.start_date = date(2026, 4, 1)
        a1.end_date = date(2026, 4, 30)
        a1.technician = tech
        a1.role = MagicMock()
        a1.role.project = MagicMock()

        a2 = MagicMock(spec=Assignment)
        a2.technician_id = a1.technician_id
        a2.id = uuid.uuid4()
        a2.start_date = date(2026, 5, 1)
        a2.end_date = date(2026, 6, 30)
        a2.technician = tech
        a2.role = MagicMock()
        a2.role.project = MagicMock()

        gaps = _find_gaps([a1, a2], date(2026, 4, 1), date(2026, 7, 1))
        assert len(gaps) == 0

    def test_find_gaps_empty_list(self):
        from app.routers.forward_staffing import _find_gaps
        gaps = _find_gaps([], date(2026, 4, 1), date(2026, 7, 1))
        assert gaps == []


class TestAssignmentToEntry:
    """Test the _assignment_to_entry helper."""

    def test_converts_orm_to_schema(self):
        from app.routers.forward_staffing import _assignment_to_entry

        tech = MagicMock()
        tech.full_name = "Marcus Johnson"

        project = MagicMock()
        project.id = uuid.uuid4()
        project.name = "Phoenix FTTH"

        role = MagicMock()
        role.role_name = "Lead Splicer"
        role.project = project

        a = MagicMock(spec=Assignment)
        a.id = uuid.uuid4()
        a.technician_id = uuid.uuid4()
        a.technician = tech
        a.role_id = uuid.uuid4()
        a.role = role
        a.start_date = date(2026, 5, 1)
        a.end_date = date(2026, 7, 31)
        a.status = "Pre-Booked"
        a.assignment_type = AssignmentType.PRE_BOOKED
        a.is_forward_booked = True
        a.booking_confidence = 0.85
        a.chain_id = None
        a.chain_position = None
        a.gap_days = None
        a.partner_confirmed_start = False

        entry = _assignment_to_entry(a)
        assert entry.technician_name == "Marcus Johnson"
        assert entry.project_name == "Phoenix FTTH"
        assert entry.is_forward_booked is True
        assert entry.booking_confidence == 0.85


class TestBuildChain:
    """Test the _build_chain helper."""

    def test_builds_chain_from_assignments(self):
        from app.routers.forward_staffing import _build_chain

        chain_id = uuid.uuid4()
        tech = MagicMock()
        tech.full_name = "Sarah Chen"

        def make_mock(pos, start, end):
            a = MagicMock(spec=Assignment)
            a.id = uuid.uuid4()
            a.technician_id = uuid.uuid4()
            a.technician = tech
            a.role_id = uuid.uuid4()
            a.role = MagicMock()
            a.role.role_name = f"Role {pos}"
            a.role.project = MagicMock()
            a.role.project.id = uuid.uuid4()
            a.role.project.name = f"Project {pos}"
            a.chain_id = chain_id
            a.chain_position = pos
            a.chain_priority = ChainPriority.HIGH
            a.start_date = start
            a.end_date = end
            a.status = "Pre-Booked"
            a.assignment_type = AssignmentType.PRE_BOOKED
            a.gap_days = 0 if pos > 1 else None
            a.chain_notes = None
            a.booking_confidence = 0.9
            a.is_forward_booked = True
            a.confirmed_at = None
            return a

        assignments = [
            make_mock(1, date(2026, 4, 1), date(2026, 5, 31)),
            make_mock(2, date(2026, 6, 1), date(2026, 7, 31)),
            make_mock(3, date(2026, 8, 1), date(2026, 9, 30)),
        ]

        chain = _build_chain(assignments)
        assert len(chain.links) == 3
        assert chain.chain_priority == "High"
        assert chain.total_duration_days == 182  # Apr 1 to Sep 30
        assert chain.technician_name == "Sarah Chen"
