"""Tests for partner skill breakdown review integration with hours approval.

Verifies:
  - Skill breakdown data is included in partner timesheet responses
  - PUT approve: partner can approve skill breakdown
  - PUT reject: partner can reject skill breakdown
  - PUT request_revision: partner can request revision of skill breakdown
  - Invalid actions are rejected
  - 404 when no skill breakdown exists
  - Cross-partner access is blocked
  - Skill review is independent of hours approval
"""

import uuid
from datetime import date, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, get_db
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.assignment import Assignment, AssignmentType, AssignmentStatus
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.user import Partner
from app.models.technician import Technician, DeployabilityStatus
from app.models.skill_breakdown import SkillBreakdown, SkillBreakdownItem, SkillProficiencyRating

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
ASSIGNMENT_NO_BREAKDOWN_ID = uuid.uuid4()
TS_WITH_BREAKDOWN_ID = uuid.uuid4()
TS_NO_BREAKDOWN_ID = uuid.uuid4()
TS_OTHER_PARTNER_ID = uuid.uuid4()
BREAKDOWN_A_ID = uuid.uuid4()
ITEM_1_ID = uuid.uuid4()
ITEM_2_ID = uuid.uuid4()
ITEM_3_ID = uuid.uuid4()


def _partner_headers(partner_id: uuid.UUID) -> dict:
    return {
        "X-Demo-Role": "partner",
        "X-Demo-User-Id": str(partner_id),
    }


# ---------------------------------------------------------------------------
# DB setup / teardown
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_database():
    """Create tables and seed test data before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    # Partners
    db.add_all([
        Partner(id=PARTNER_A_ID, name="Acme Fiber Co"),
        Partner(id=PARTNER_B_ID, name="Beta Networks"),
    ])

    # Technician
    db.add(Technician(
        id=TECH_ID,
        first_name="John",
        last_name="Smith",
        email="john@example.com",
        phone="555-0100",
        home_base_state="GA",
        deployability_status=DeployabilityStatus.READY_NOW,
    ))

    # Project A (partner A)
    db.add(Project(
        id=PROJECT_A_ID,
        name="Project Alpha",
        partner_id=PARTNER_A_ID,
        status=ProjectStatus.ACTIVE,
        location_region="Southeast",
        start_date=date(2026, 1, 1),
    ))
    db.add(ProjectRole(
        id=ROLE_A_ID,
        project_id=PROJECT_A_ID,
        role_name="Fiber Splicer",
        quantity=2,
        filled=1,
    ))

    # Assignment with skill breakdown (completed)
    db.add(Assignment(
        id=ASSIGNMENT_A_ID,
        technician_id=TECH_ID,
        role_id=ROLE_A_ID,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 3, 1),
        status="Completed",
        assignment_type=AssignmentType.ACTIVE,
    ))

    # Assignment without skill breakdown
    db.add(Assignment(
        id=ASSIGNMENT_NO_BREAKDOWN_ID,
        technician_id=TECH_ID,
        role_id=ROLE_A_ID,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 6, 15),
        status="Active",
        assignment_type=AssignmentType.ACTIVE,
    ))

    # Project B (partner B)
    db.add(Project(
        id=PROJECT_B_ID,
        name="Project Beta",
        partner_id=PARTNER_B_ID,
        status=ProjectStatus.ACTIVE,
        location_region="Northeast",
        start_date=date(2026, 2, 1),
    ))
    db.add(ProjectRole(
        id=ROLE_B_ID,
        project_id=PROJECT_B_ID,
        role_name="Cable Tech",
        quantity=1,
        filled=1,
    ))
    db.add(Assignment(
        id=ASSIGNMENT_B_ID,
        technician_id=TECH_ID,
        role_id=ROLE_B_ID,
        start_date=date(2026, 2, 1),
        status="Active",
        assignment_type=AssignmentType.ACTIVE,
    ))

    # Timesheets
    db.add(Timesheet(
        id=TS_WITH_BREAKDOWN_ID,
        technician_id=TECH_ID,
        assignment_id=ASSIGNMENT_A_ID,
        week_start=date(2026, 3, 2),
        hours=40.0,
        status=TimesheetStatus.SUBMITTED,
        submitted_at=datetime(2026, 3, 9),
        skill_name="Fiber Splicing",
    ))
    db.add(Timesheet(
        id=TS_NO_BREAKDOWN_ID,
        technician_id=TECH_ID,
        assignment_id=ASSIGNMENT_NO_BREAKDOWN_ID,
        week_start=date(2026, 3, 9),
        hours=38.0,
        status=TimesheetStatus.SUBMITTED,
        submitted_at=datetime(2026, 3, 16),
    ))
    db.add(Timesheet(
        id=TS_OTHER_PARTNER_ID,
        technician_id=TECH_ID,
        assignment_id=ASSIGNMENT_B_ID,
        week_start=date(2026, 3, 2),
        hours=35.0,
        status=TimesheetStatus.SUBMITTED,
        submitted_at=datetime(2026, 3, 9),
    ))

    # Skill breakdown for assignment A
    breakdown = SkillBreakdown(
        id=BREAKDOWN_A_ID,
        assignment_id=ASSIGNMENT_A_ID,
        technician_id=TECH_ID,
        submitted_by="ops-admin-1",
        overall_notes="Good performance overall",
        overall_rating=SkillProficiencyRating.MEETS_EXPECTATIONS,
        submitted_at=datetime(2026, 3, 5),
        updated_at=datetime(2026, 3, 5),
    )
    db.add(breakdown)
    db.flush()

    # Skill breakdown items
    db.add_all([
        SkillBreakdownItem(
            id=ITEM_1_ID,
            skill_breakdown_id=BREAKDOWN_A_ID,
            skill_name="Fiber Splicing",
            hours_applied=24.0,
            proficiency_rating=SkillProficiencyRating.EXCEEDS_EXPECTATIONS,
            notes="Fast and accurate splicing",
        ),
        SkillBreakdownItem(
            id=ITEM_2_ID,
            skill_breakdown_id=BREAKDOWN_A_ID,
            skill_name="OTDR Testing",
            hours_applied=10.0,
            proficiency_rating=SkillProficiencyRating.MEETS_EXPECTATIONS,
        ),
        SkillBreakdownItem(
            id=ITEM_3_ID,
            skill_breakdown_id=BREAKDOWN_A_ID,
            skill_name="Cable Pulling",
            hours_applied=6.0,
            proficiency_rating=SkillProficiencyRating.BELOW_EXPECTATIONS,
            notes="Needs improvement in cable management",
        ),
    ])

    db.commit()
    db.close()

    yield

    Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# Tests: Skill breakdown included in timesheet response
# ---------------------------------------------------------------------------

class TestSkillBreakdownInTimesheetResponse:
    """Verify skill breakdown data is included when fetching timesheets."""

    def test_timesheet_with_breakdown_includes_summary(self):
        """Timesheet linked to an assignment with a skill breakdown includes it."""
        resp = client.get(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skill_breakdown"] is not None
        sb = data["skill_breakdown"]
        assert sb["id"] == str(BREAKDOWN_A_ID)
        assert sb["overall_rating"] == "Meets Expectations"
        assert sb["partner_review_status"] is None
        assert len(sb["items"]) == 3
        # Check items
        skill_names = {item["skill_name"] for item in sb["items"]}
        assert "Fiber Splicing" in skill_names
        assert "OTDR Testing" in skill_names
        assert "Cable Pulling" in skill_names

    def test_timesheet_without_breakdown_has_null(self):
        """Timesheet without a skill breakdown has null skill_breakdown field."""
        resp = client.get(
            f"/api/partner/timesheets/{TS_NO_BREAKDOWN_ID}",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skill_breakdown"] is None

    def test_list_includes_breakdown_per_timesheet(self):
        """List endpoint includes skill_breakdown for each timesheet."""
        resp = client.get(
            "/api/partner/timesheets",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        # Find the one with breakdown
        with_breakdown = [i for i in items if i["skill_breakdown"] is not None]
        without_breakdown = [i for i in items if i["skill_breakdown"] is None]
        assert len(with_breakdown) >= 1
        assert len(without_breakdown) >= 1


# ---------------------------------------------------------------------------
# Tests: PUT skill-breakdown/review
# ---------------------------------------------------------------------------

class TestPartnerReviewSkillBreakdown:
    """PUT /api/partner/timesheets/{id}/skill-breakdown/review"""

    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_approve_skill_breakdown(self, mock_dispatch):
        """Partner can approve the skill breakdown."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown/review",
            headers=_partner_headers(PARTNER_A_ID),
            json={"action": "approve", "note": "Looks accurate"},
        )
        assert resp.status_code == 200
        data = resp.json()
        sb = data["skill_breakdown"]
        assert sb is not None
        assert sb["partner_review_status"] == "Approved"
        assert sb["partner_review_note"] == "Looks accurate"
        mock_dispatch.assert_called_once()

    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_reject_skill_breakdown(self, mock_dispatch):
        """Partner can reject the skill breakdown."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown/review",
            headers=_partner_headers(PARTNER_A_ID),
            json={"action": "reject", "note": "Ratings seem inflated"},
        )
        assert resp.status_code == 200
        data = resp.json()
        sb = data["skill_breakdown"]
        assert sb["partner_review_status"] == "Rejected"
        assert sb["partner_review_note"] == "Ratings seem inflated"

    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_request_revision_skill_breakdown(self, mock_dispatch):
        """Partner can request revision of the skill breakdown."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown/review",
            headers=_partner_headers(PARTNER_A_ID),
            json={"action": "request_revision", "note": "Need to re-evaluate OTDR rating"},
        )
        assert resp.status_code == 200
        data = resp.json()
        sb = data["skill_breakdown"]
        assert sb["partner_review_status"] == "Revision Requested"

    def test_invalid_action_rejected(self):
        """Invalid action is rejected with 400."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown/review",
            headers=_partner_headers(PARTNER_A_ID),
            json={"action": "invalid_action"},
        )
        assert resp.status_code == 400
        assert "Invalid action" in resp.json()["detail"]

    def test_no_breakdown_returns_404(self):
        """404 when timesheet's assignment has no skill breakdown."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_NO_BREAKDOWN_ID}/skill-breakdown/review",
            headers=_partner_headers(PARTNER_A_ID),
            json={"action": "approve"},
        )
        assert resp.status_code == 404
        assert "No skill breakdown" in resp.json()["detail"]

    def test_cross_partner_blocked(self):
        """Partner B cannot review skill breakdown on partner A's timesheet."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown/review",
            headers=_partner_headers(PARTNER_B_ID),
            json={"action": "approve"},
        )
        assert resp.status_code == 404

    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_skill_review_independent_of_hours_approval(self, mock_dispatch):
        """Skill review does not change timesheet status — they're independent."""
        # Approve skill breakdown
        resp = client.put(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown/review",
            headers=_partner_headers(PARTNER_A_ID),
            json={"action": "approve"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Timesheet should still be Submitted
        assert data["status"] == "Submitted"
        # But skill breakdown should be Approved
        assert data["skill_breakdown"]["partner_review_status"] == "Approved"

    @patch("app.routers.partner_timesheets.dispatch_event_safe")
    def test_approve_note_optional(self, mock_dispatch):
        """Note is optional for skill review."""
        resp = client.put(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown/review",
            headers=_partner_headers(PARTNER_A_ID),
            json={"action": "approve"},
        )
        assert resp.status_code == 200
        sb = resp.json()["skill_breakdown"]
        assert sb["partner_review_status"] == "Approved"
        assert sb["partner_review_note"] is None


# ---------------------------------------------------------------------------
# Tests: GET skill-breakdown endpoint (partner-scoped)
# ---------------------------------------------------------------------------

class TestGetPartnerTimesheetSkillBreakdown:
    """GET /api/partner/timesheets/{id}/skill-breakdown"""

    def test_get_breakdown_for_timesheet(self):
        """Can get full skill breakdown for a timesheet's assignment."""
        resp = client.get(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(BREAKDOWN_A_ID)
        assert data["assignment_id"] == str(ASSIGNMENT_A_ID)
        assert data["technician_id"] == str(TECH_ID)
        assert len(data["items"]) == 3

    def test_404_when_no_breakdown(self):
        """Returns 404 when no breakdown exists for the timesheet's assignment."""
        resp = client.get(
            f"/api/partner/timesheets/{TS_NO_BREAKDOWN_ID}/skill-breakdown",
            headers=_partner_headers(PARTNER_A_ID),
        )
        assert resp.status_code == 404

    def test_cross_partner_blocked(self):
        """Partner B cannot view partner A's timesheet skill breakdown."""
        resp = client.get(
            f"/api/partner/timesheets/{TS_WITH_BREAKDOWN_ID}/skill-breakdown",
            headers=_partner_headers(PARTNER_B_ID),
        )
        assert resp.status_code == 404
