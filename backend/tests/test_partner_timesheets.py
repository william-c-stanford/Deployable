"""Tests for partner timesheet approve/flag/resolve endpoints.

Verifies:
  - Partner-scoped query filtering (partners only see their projects' timesheets)
  - PUT approve: Submitted → Approved
  - PUT flag: Submitted → Flagged
  - PUT resolve: Flagged → Resolved
  - Invalid status transitions are rejected
  - Cross-partner access is blocked (404)
  - Event dispatch on each action
"""

import uuid
from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, get_db
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.assignment import Assignment, AssignmentType
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.user import Partner
from app.models.technician import Technician, DeployabilityStatus

# ---------------------------------------------------------------------------
# In-memory SQLite setup
# ---------------------------------------------------------------------------
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Enable foreign keys for SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

# ---------------------------------------------------------------------------
# Fixtures — IDs
# ---------------------------------------------------------------------------

PARTNER_A_ID = uuid.uuid4()
PARTNER_B_ID = uuid.uuid4()
TECH_ID = uuid.uuid4()
PROJECT_A_ID = uuid.uuid4()
PROJECT_B_ID = uuid.uuid4()
ROLE_A_ID = uuid.uuid4()
ROLE_B_ID = uuid.uuid4()
ASSIGNMENT_A_ID = uuid.uuid4()
ASSIGNMENT_B_ID = uuid.uuid4()
TS_SUBMITTED_ID = uuid.uuid4()
TS_APPROVED_ID = uuid.uuid4()
TS_FLAGGED_ID = uuid.uuid4()
TS_OTHER_PARTNER_ID = uuid.uuid4()


def _partner_headers(partner_id: uuid.UUID) -> dict:
    return {
        "X-Demo-Role": "partner",
        "X-Demo-User-Id": str(partner_id),
    }


def _ops_headers() -> dict:
    return {
        "X-Demo-Role": "ops",
        "X-Demo-User-Id": "ops-admin-1",
    }


def _tech_headers() -> dict:
    return {
        "X-Demo-Role": "technician",
        "X-Demo-User-Id": str(TECH_ID),
    }


# ---------------------------------------------------------------------------
# DB setup / teardown
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_database():
    """Create tables and seed test data before each test, drop after."""
    # Use strings for UUID PKs since SQLite doesn't have native UUID
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    # Partners
    partner_a = Partner(id=PARTNER_A_ID, name="Acme Fiber Co")
    partner_b = Partner(id=PARTNER_B_ID, name="Beta Networks")
    db.add_all([partner_a, partner_b])

    # Technician
    tech = Technician(
        id=TECH_ID,
        first_name="John",
        last_name="Smith",
        email="john@example.com",
        phone="555-0100",
        home_base_state="GA",
        deployability_status=DeployabilityStatus.READY_NOW,
    )
    db.add(tech)

    # Project A (partner A)
    project_a = Project(
        id=PROJECT_A_ID,
        name="Project Alpha",
        partner_id=PARTNER_A_ID,
        status=ProjectStatus.ACTIVE,
        location_region="Southeast",
        start_date=date(2026, 1, 1),
    )
    db.add(project_a)

    role_a = ProjectRole(
        id=ROLE_A_ID,
        project_id=PROJECT_A_ID,
        role_name="Fiber Splicer",
        quantity=2,
        filled=1,
    )
    db.add(role_a)

    assignment_a = Assignment(
        id=ASSIGNMENT_A_ID,
        technician_id=TECH_ID,
        role_id=ROLE_A_ID,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 6, 15),
        status="Active",
        assignment_type=AssignmentType.ACTIVE,
    )
    db.add(assignment_a)

    # Project B (partner B) — for cross-partner scoping tests
    project_b = Project(
        id=PROJECT_B_ID,
        name="Project Beta",
        partner_id=PARTNER_B_ID,
        status=ProjectStatus.ACTIVE,
        location_region="Northeast",
        start_date=date(2026, 2, 1),
    )
    db.add(project_b)

    role_b = ProjectRole(
        id=ROLE_B_ID,
        project_id=PROJECT_B_ID,
        role_name="Cable Tech",
        quantity=1,
        filled=1,
    )
    db.add(role_b)

    assignment_b = Assignment(
        id=ASSIGNMENT_B_ID,
        technician_id=TECH_ID,
        role_id=ROLE_B_ID,
        start_date=date(2026, 2, 1),
        status="Active",
        assignment_type=AssignmentType.ACTIVE,
    )
    db.add(assignment_b)

    # Timesheets
    ts_submitted = Timesheet(
        id=TS_SUBMITTED_ID,
        technician_id=TECH_ID,
        assignment_id=ASSIGNMENT_A_ID,
        week_start=date(2026, 3, 2),
        hours=40.0,
        status=TimesheetStatus.SUBMITTED,
        submitted_at=datetime(2026, 3, 9),
    )
    ts_approved = Timesheet(
        id=TS_APPROVED_ID,
        technician_id=TECH_ID,
        assignment_id=ASSIGNMENT_A_ID,
        week_start=date(2026, 2, 23),
        hours=38.0,
        status=TimesheetStatus.APPROVED,
        submitted_at=datetime(2026, 3, 2),
        reviewed_at=datetime(2026, 3, 3),
    )
    ts_flagged = Timesheet(
        id=TS_FLAGGED_ID,
        technician_id=TECH_ID,
        assignment_id=ASSIGNMENT_A_ID,
        week_start=date(2026, 2, 16),
        hours=60.0,
        status=TimesheetStatus.FLAGGED,
        flag_comment="Hours seem too high for this week",
        submitted_at=datetime(2026, 2, 23),
        reviewed_at=datetime(2026, 2, 24),
    )
    # Timesheet on partner B's project
    ts_other = Timesheet(
        id=TS_OTHER_PARTNER_ID,
        technician_id=TECH_ID,
        assignment_id=ASSIGNMENT_B_ID,
        week_start=date(2026, 3, 2),
        hours=35.0,
        status=TimesheetStatus.SUBMITTED,
        submitted_at=datetime(2026, 3, 9),
    )
    db.add_all([ts_submitted, ts_approved, ts_flagged, ts_other])
    db.commit()
    db.close()

    yield

    Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# Tests: GET list (partner-scoped filtering)
# ---------------------------------------------------------------------------

class TestListPartnerTimesheets:
    """GET /api/partner/timesheets — partner-scoped list."""

    def test_partner_sees_only_own_timesheets(self):
        """Partner A sees only timesheets from their projects, not partner B's."""
        resp = client.get(
            "/api/partner/timesheets",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3  # submitted + approved + flagged
        ids = {item["id"] for item in data["items"]}
        assert str(TS_SUBMITTED_ID) in ids
        assert str(TS_APPROVED_ID) in ids
        assert str(TS_FLAGGED_ID) in ids
        # Partner B's timesheet should NOT be visible
        assert str(TS_OTHER_PARTNER_ID) not in ids

    def test_partner_b_sees_only_own_timesheets(self):
        resp = client.get(
            "/api/partner/timesheets",
            headers=_partner_headers(PARTNER_B_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == str(TS_OTHER_PARTNER_ID)

    def test_status_filter(self):
        resp = client.get(
            "/api/partner/timesheets?status=Submitted",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "Submitted"

    def test_includes_summary_counts(self):
        resp = client.get(
            "/api/partner/timesheets",
            headers=_partner_headers(PARTNER_A_ID),
        )
        data = resp.json()
        assert data["pending_count"] == 1  # one Submitted
        assert data["flagged_count"] == 1  # one Flagged

    def test_ops_cannot_access_partner_endpoint(self):
        """Ops role is blocked from partner-only endpoints."""
        resp = client.get(
            "/api/partner/timesheets",
            headers=_ops_headers(),
        )
        assert resp.status_code == 403

    def test_technician_cannot_access_partner_endpoint(self):
        resp = client.get(
            "/api/partner/timesheets",
            headers=_tech_headers(),
        )
        assert resp.status_code == 403

    def test_enriched_context_fields(self):
        """Response includes technician_name, project_name, role_name."""
        resp = client.get(
            "/api/partner/timesheets",
            headers=_partner_headers(PARTNER_A_ID),
        )
        data = resp.json()
        item = data["items"][0]
        assert item["technician_name"] == "John Smith"
        assert item["project_name"] == "Project Alpha"
        assert item["role_name"] == "Fiber Splicer"


# ---------------------------------------------------------------------------
# Tests: GET single timesheet (partner-scoped)
# ---------------------------------------------------------------------------

class TestGetPartnerTimesheet:
    def test_get_own_timesheet(self):
        resp = client.get(
            f"/api/partner/timesheets/{TS_SUBMITTED_ID}",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == str(TS_SUBMITTED_ID)

    def test_cross_partner_blocked(self):
        """Partner A cannot see partner B's timesheet."""
        resp = client.get(
            f"/api/partner/timesheets/{TS_OTHER_PARTNER_ID}",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 404

    def test_nonexistent_timesheet(self):
        resp = client.get(
            f"/api/partner/timesheets/{uuid.uuid4()}",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: PUT approve (partner)
# ---------------------------------------------------------------------------

class TestPartnerApproveTimesheet:
    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_approve_submitted_timesheet(self, mock_dispatch):
        """Partner can approve a Submitted timesheet → Approved."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_SUBMITTED_ID}/approve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Approved"
        assert data["reviewed_by_role"] == "partner"

        # Verify event was dispatched
        mock_dispatch.assert_called_once()
        payload = mock_dispatch.call_args[0][0]
        assert payload.event_type.value == "timesheet.partner_approved"
        assert payload.data["partner_id"] == str(PARTNER_A_ID)

    def test_cannot_approve_already_approved(self):
        """Cannot approve a timesheet that's already approved."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_APPROVED_ID}/approve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 400
        assert "Cannot approve" in resp.json()["detail"]

    def test_cannot_approve_flagged_as_partner(self):
        """Partners cannot approve flagged timesheets (ops override only)."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_FLAGGED_ID}/approve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 400

    def test_cross_partner_approve_blocked(self):
        """Partner A cannot approve partner B's timesheet."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_OTHER_PARTNER_ID}/approve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: PUT flag (partner)
# ---------------------------------------------------------------------------

class TestPartnerFlagTimesheet:
    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_flag_submitted_timesheet(self, mock_dispatch):
        """Partner can flag a Submitted timesheet → Flagged."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_SUBMITTED_ID}/flag",
            headers=_partner_headers(PARTNER_A_ID),
            json={"flag_comment": "Hours don't match our records"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Flagged"
        assert data["flag_comment"] == "Hours don't match our records"
        assert data["reviewed_by_role"] == "partner"

        # Verify event
        mock_dispatch.assert_called_once()
        payload = mock_dispatch.call_args[0][0]
        assert payload.event_type.value == "timesheet.partner_flagged"

    def test_flag_requires_comment(self):
        """Flag comment is mandatory."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_SUBMITTED_ID}/flag",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 422  # Validation error

    def test_cannot_flag_approved(self):
        """Cannot flag an already-approved timesheet."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_APPROVED_ID}/flag",
            headers=_partner_headers(PARTNER_A_ID),
            json={"flag_comment": "Wrong hours"},
        )
        assert resp.status_code == 400

    def test_cannot_flag_already_flagged(self):
        """Cannot double-flag a timesheet."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_FLAGGED_ID}/flag",
            headers=_partner_headers(PARTNER_A_ID),
            json={"flag_comment": "Still wrong"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: PUT resolve (partner)
# ---------------------------------------------------------------------------

class TestPartnerResolveTimesheet:
    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_resolve_flagged_timesheet(self, mock_dispatch):
        """Partner can resolve a Flagged timesheet → Resolved."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_FLAGGED_ID}/resolve",
            headers=_partner_headers(PARTNER_A_ID),
            json={"corrected_hours": 45.0, "resolution_note": "Corrected after review"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Resolved"
        assert data["hours"] == 45.0  # Corrected

        mock_dispatch.assert_called_once()
        payload = mock_dispatch.call_args[0][0]
        assert payload.event_type.value == "timesheet.resolved"
        assert payload.data["corrected_hours"] == 45.0

    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_resolve_without_corrected_hours(self, mock_dispatch):
        """Can resolve keeping original hours."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_FLAGGED_ID}/resolve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["hours"] == 60.0  # Original kept

    def test_cannot_resolve_submitted(self):
        """Cannot resolve a timesheet that's not flagged."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_SUBMITTED_ID}/resolve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 400

    def test_cannot_resolve_approved(self):
        resp = client.put(
            f"/api/partner/timesheets/{TS_APPROVED_ID}/resolve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: Status transition matrix
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    """Verify the complete status transition matrix for partner actions."""

    def test_submitted_approve_ok(self):
        resp = client.put(
            f"/api/partner/timesheets/{TS_SUBMITTED_ID}/approve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "Approved"

    def test_submitted_flag_ok(self):
        resp = client.put(
            f"/api/partner/timesheets/{TS_SUBMITTED_ID}/flag",
            headers=_partner_headers(PARTNER_A_ID),
            json={"flag_comment": "Incorrect hours"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "Flagged"

    def test_submitted_resolve_rejected(self):
        resp = client.put(
            f"/api/partner/timesheets/{TS_SUBMITTED_ID}/resolve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 400

    def test_flagged_resolve_ok(self):
        resp = client.put(
            f"/api/partner/timesheets/{TS_FLAGGED_ID}/resolve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "Resolved"

    def test_flagged_approve_rejected_for_partner(self):
        resp = client.put(
            f"/api/partner/timesheets/{TS_FLAGGED_ID}/approve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 400

    def test_approved_flag_rejected(self):
        resp = client.put(
            f"/api/partner/timesheets/{TS_APPROVED_ID}/flag",
            headers=_partner_headers(PARTNER_A_ID),
            json={"flag_comment": "Trying to flag approved"},
        )
        assert resp.status_code == 400

    def test_approved_resolve_rejected(self):
        resp = client.put(
            f"/api/partner/timesheets/{TS_APPROVED_ID}/resolve",
            headers=_partner_headers(PARTNER_A_ID),
            json={},
        )
        assert resp.status_code == 400
