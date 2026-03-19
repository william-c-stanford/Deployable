"""Tests for the ops dispute section API endpoints.

Tests cover:
1. GET /api/disputes — list flagged timesheets with project staffing context
2. GET /api/disputes/{id} — single flagged timesheet detail
3. POST /api/disputes/{id}/resolve — resolve a flagged timesheet
4. Filtering by status, project, technician, partner
5. Role-based access control (ops-only)
6. Pagination
"""

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models as _models  # noqa: F401 — ensure all models registered with Base
from app.database import Base, get_db
from app.main import app
from app.models.technician import (
    Technician,
    CareerStage,
    DeployabilityStatus,
)
from app.models.user import Partner
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.assignment import Assignment, AssignmentType
from app.models.timesheet import Timesheet, TimesheetStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="function")
def db(db_engine):
    SessionLocal = sessionmaker(bind=db_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(db):
    """FastAPI test client with in-memory SQLite DB override."""

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


OPS_HEADERS = {"X-Demo-Role": "ops", "X-Demo-User-Id": "ops-user-1"}
TECH_HEADERS = {"X-Demo-Role": "technician", "X-Demo-User-Id": "tech-user-1"}
PARTNER_HEADERS = {"X-Demo-Role": "partner", "X-Demo-User-Id": "partner-user-1"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _create_partner(db: Session, name: str = "TestCo") -> Partner:
    p = Partner(id=uuid.uuid4(), name=name, contact_email=f"{name.lower()}@example.com")
    db.add(p)
    db.flush()
    return p


def _create_project(db: Session, partner: Partner, name: str = "Fiber Build Alpha") -> Project:
    proj = Project(
        id=uuid.uuid4(),
        name=name,
        partner_id=partner.id,
        status=ProjectStatus.ACTIVE,
        location_region="Southeast",
        start_date=date(2026, 1, 1),
    )
    db.add(proj)
    db.flush()
    return proj


def _create_role(db: Session, project: Project, role_name: str = "Lead Splicer") -> ProjectRole:
    role = ProjectRole(
        id=uuid.uuid4(),
        project_id=project.id,
        role_name=role_name,
        quantity=2,
    )
    db.add(role)
    db.flush()
    return role


def _create_technician(db: Session, first: str = "Jane", last: str = "Doe") -> Technician:
    tech = Technician(
        id=uuid.uuid4(),
        first_name=first,
        last_name=last,
        email=f"{first.lower()}.{last.lower()}.{uuid.uuid4().hex[:4]}@example.com",
        career_stage=CareerStage.DEPLOYED,
        deployability_status=DeployabilityStatus.CURRENTLY_ASSIGNED,
    )
    db.add(tech)
    db.flush()
    return tech


def _create_assignment(db: Session, technician: Technician, role: ProjectRole) -> Assignment:
    a = Assignment(
        id=uuid.uuid4(),
        technician_id=technician.id,
        role_id=role.id,
        start_date=date(2026, 1, 15),
        assignment_type=AssignmentType.ACTIVE,
    )
    db.add(a)
    db.flush()
    return a


def _create_timesheet(
    db: Session,
    assignment: Assignment,
    technician_id: uuid.UUID = None,
    status: TimesheetStatus = TimesheetStatus.FLAGGED,
    hours: float = 40.0,
    flag_comment: str = "Hours seem high for this scope",
    week_start: date = None,
) -> Timesheet:
    ts = Timesheet(
        id=uuid.uuid4(),
        technician_id=technician_id or assignment.technician_id,
        assignment_id=assignment.id,
        week_start=week_start or date(2026, 2, 2),
        hours=hours,
        status=status,
        flag_comment=flag_comment if status in (TimesheetStatus.FLAGGED, TimesheetStatus.RESOLVED) else None,
        submitted_at=datetime.utcnow() - timedelta(days=3),
        reviewed_at=datetime.utcnow() if status != TimesheetStatus.SUBMITTED else None,
    )
    db.add(ts)
    db.flush()
    return ts


def _seed_full_dispute(db: Session, tech_name=("Jane", "Doe"), project_name="Fiber Alpha",
                        ts_status=TimesheetStatus.FLAGGED, hours=40.0):
    """Create a full chain: partner → project → role → tech → assignment → flagged timesheet."""
    partner = _create_partner(db, "TestPartner")
    project = _create_project(db, partner, project_name)
    role = _create_role(db, project)
    tech = _create_technician(db, *tech_name)
    assignment = _create_assignment(db, tech, role)
    ts = _create_timesheet(db, assignment, status=ts_status, hours=hours)
    db.commit()
    return {
        "partner": partner,
        "project": project,
        "role": role,
        "technician": tech,
        "assignment": assignment,
        "timesheet": ts,
    }


# ---------------------------------------------------------------------------
# Tests: GET /api/disputes
# ---------------------------------------------------------------------------

class TestListDisputes:
    """GET /api/disputes — list flagged timesheets."""

    def test_list_returns_flagged_timesheets(self, client, db):
        data = _seed_full_dispute(db)
        resp = client.get("/api/disputes", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert body["flagged_count"] >= 1
        item = body["items"][0]
        assert item["status"] == "Flagged"
        assert item["assignment"]["technician"]["first_name"] == "Jane"
        assert item["assignment"]["project"]["name"] == "Fiber Alpha"

    def test_list_includes_resolved(self, client, db):
        _seed_full_dispute(db, ts_status=TimesheetStatus.RESOLVED, tech_name=("Bob", "Smith"))
        resp = client.get("/api/disputes", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert body["resolved_count"] >= 1

    def test_list_excludes_submitted_and_approved(self, client, db):
        _seed_full_dispute(db, ts_status=TimesheetStatus.SUBMITTED, tech_name=("Al", "Pha"))
        _seed_full_dispute(db, ts_status=TimesheetStatus.APPROVED, tech_name=("Be", "Ta"))
        resp = client.get("/api/disputes", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        # Neither submitted nor approved should appear
        for item in body["items"]:
            assert item["status"] in ("Flagged", "Resolved")

    def test_filter_by_status_flagged(self, client, db):
        _seed_full_dispute(db, ts_status=TimesheetStatus.FLAGGED, tech_name=("F1", "Test"))
        _seed_full_dispute(db, ts_status=TimesheetStatus.RESOLVED, tech_name=("R1", "Test"))
        resp = client.get("/api/disputes?status=Flagged", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["status"] == "Flagged"

    def test_filter_by_status_resolved(self, client, db):
        _seed_full_dispute(db, ts_status=TimesheetStatus.FLAGGED, tech_name=("F2", "Test"))
        _seed_full_dispute(db, ts_status=TimesheetStatus.RESOLVED, tech_name=("R2", "Test"))
        resp = client.get("/api/disputes?status=Resolved", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["status"] == "Resolved"

    def test_filter_by_invalid_status_returns_400(self, client, db):
        resp = client.get("/api/disputes?status=Approved", headers=OPS_HEADERS)
        assert resp.status_code == 400

    def test_filter_by_project_id(self, client, db):
        d1 = _seed_full_dispute(db, project_name="Project A", tech_name=("PA", "Test"))
        _seed_full_dispute(db, project_name="Project B", tech_name=("PB", "Test"))
        resp = client.get(
            f"/api/disputes?project_id={d1['project'].id}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["assignment"]["project"]["name"] == "Project A"

    def test_filter_by_technician_id(self, client, db):
        d1 = _seed_full_dispute(db, tech_name=("Target", "Tech"))
        _seed_full_dispute(db, tech_name=("Other", "Tech"))
        resp = client.get(
            f"/api/disputes?technician_id={d1['technician'].id}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["assignment"]["technician"]["first_name"] == "Target"

    def test_filter_by_partner_id(self, client, db):
        d1 = _seed_full_dispute(db, tech_name=("P1", "Tech"))
        resp = client.get(
            f"/api/disputes?partner_id={d1['partner'].id}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1

    def test_pagination(self, client, db):
        # Create 3 flagged timesheets
        for i in range(3):
            _seed_full_dispute(db, tech_name=(f"Tech{i}", "Page"), hours=30 + i)
        resp = client.get("/api/disputes?skip=0&limit=2", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] >= 3

    def test_empty_result(self, client, db):
        resp = client.get("/api/disputes", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_staffing_context_in_response(self, client, db):
        """Verify that the response includes full staffing context."""
        data = _seed_full_dispute(db)
        resp = client.get("/api/disputes", headers=OPS_HEADERS)
        body = resp.json()
        item = body["items"][0]

        # Assignment context
        assert "assignment" in item
        asgn = item["assignment"]
        assert "technician" in asgn
        assert "role" in asgn
        assert "project" in asgn

        # Technician fields
        tech = asgn["technician"]
        assert "full_name" in tech
        assert "email" in tech
        assert "deployability_status" in tech

        # Project fields
        proj = asgn["project"]
        assert "name" in proj
        assert "partner_id" in proj
        assert "location_region" in proj

        # Role fields
        assert "role_name" in asgn["role"]


# ---------------------------------------------------------------------------
# Tests: GET /api/disputes/{id}
# ---------------------------------------------------------------------------

class TestGetDispute:
    """GET /api/disputes/{id} — single dispute detail."""

    def test_get_by_id(self, client, db):
        data = _seed_full_dispute(db)
        ts_id = str(data["timesheet"].id)
        resp = client.get(f"/api/disputes/{ts_id}", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == ts_id
        assert body["status"] == "Flagged"
        assert body["assignment"]["technician"]["full_name"] == "Jane Doe"

    def test_get_not_found(self, client, db):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/disputes/{fake_id}", headers=OPS_HEADERS)
        assert resp.status_code == 404

    def test_get_non_flagged_returns_400(self, client, db):
        data = _seed_full_dispute(db, ts_status=TimesheetStatus.SUBMITTED, tech_name=("Sub", "Test"))
        ts_id = str(data["timesheet"].id)
        resp = client.get(f"/api/disputes/{ts_id}", headers=OPS_HEADERS)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: POST /api/disputes/{id}/resolve
# ---------------------------------------------------------------------------

class TestResolveDispute:
    """POST /api/disputes/{id}/resolve — resolve a flagged dispute."""

    def test_resolve_flagged(self, client, db):
        data = _seed_full_dispute(db, tech_name=("Resolving", "Test"))
        ts_id = str(data["timesheet"].id)
        resp = client.post(f"/api/disputes/{ts_id}/resolve", headers=OPS_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "Resolved"
        assert body["id"] == ts_id

    def test_resolve_non_flagged_returns_400(self, client, db):
        data = _seed_full_dispute(db, ts_status=TimesheetStatus.RESOLVED, tech_name=("Already", "Resolved"))
        ts_id = str(data["timesheet"].id)
        resp = client.post(f"/api/disputes/{ts_id}/resolve", headers=OPS_HEADERS)
        assert resp.status_code == 400

    def test_resolve_not_found(self, client, db):
        fake_id = str(uuid.uuid4())
        resp = client.post(f"/api/disputes/{fake_id}/resolve", headers=OPS_HEADERS)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Role-based access control
# ---------------------------------------------------------------------------

class TestDisputeRBAC:
    """Verify only ops users can access dispute endpoints."""

    def test_technician_cannot_list_disputes(self, client, db):
        resp = client.get("/api/disputes", headers=TECH_HEADERS)
        assert resp.status_code == 403

    def test_partner_cannot_list_disputes(self, client, db):
        resp = client.get("/api/disputes", headers=PARTNER_HEADERS)
        assert resp.status_code == 403

    def test_technician_cannot_resolve(self, client, db):
        data = _seed_full_dispute(db, tech_name=("RBAC", "Test"))
        ts_id = str(data["timesheet"].id)
        resp = client.post(f"/api/disputes/{ts_id}/resolve", headers=TECH_HEADERS)
        assert resp.status_code == 403

    def test_no_auth_returns_401(self, client, db):
        resp = client.get("/api/disputes")
        assert resp.status_code == 401
