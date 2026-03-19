"""Tests for partner confirmation flow — models and API endpoints."""

import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.assignment_confirmation import (
    AssignmentConfirmation,
    ConfirmationStatus,
    ConfirmationType,
    EscalationStatus,
)
from app.models.assignment import Assignment, AssignmentType
from app.models.user import Partner
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.technician import Technician, DeployabilityStatus, CareerStage

# ---------------------------------------------------------------------------
# In-memory SQLite test database
# ---------------------------------------------------------------------------

SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

# Common headers
OPS_HEADERS = {"X-Demo-Role": "ops", "X-Demo-User-Id": "ops-user-1"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_db():
    """Create tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def seed_data(db_session):
    """Create partner, project, role, technician, and assignment for testing."""
    partner = Partner(
        id=uuid.uuid4(),
        name="Test Partner Corp",
        contact_email="partner@test.com",
    )
    db_session.add(partner)
    db_session.flush()

    project = Project(
        id=uuid.uuid4(),
        name="Test Fiber Project",
        partner_id=partner.id,
        status=ProjectStatus.ACTIVE,
        location_region="Northeast",
        start_date=date(2026, 4, 1),
    )
    db_session.add(project)
    db_session.flush()

    role = ProjectRole(
        id=uuid.uuid4(),
        project_id=project.id,
        role_name="Lead Splicer",
        quantity=2,
    )
    db_session.add(role)
    db_session.flush()

    technician = Technician(
        id=uuid.uuid4(),
        first_name="John",
        last_name="Smith",
        email="john.smith@test.com",
        phone="555-0100",
        deployability_status=DeployabilityStatus.READY_NOW,
        career_stage=CareerStage.DEPLOYED,
        home_base_state="NY",
    )
    db_session.add(technician)
    db_session.flush()

    assignment = Assignment(
        id=uuid.uuid4(),
        technician_id=technician.id,
        role_id=role.id,
        start_date=date(2026, 4, 15),
        end_date=date(2026, 7, 15),
        assignment_type=AssignmentType.ACTIVE,
    )
    db_session.add(assignment)
    db_session.commit()

    return {
        "partner": partner,
        "project": project,
        "role": role,
        "technician": technician,
        "assignment": assignment,
    }


# ---------------------------------------------------------------------------
# Model unit tests
# ---------------------------------------------------------------------------

class TestAssignmentConfirmationModel:
    """Unit tests for the AssignmentConfirmation SQLAlchemy model."""

    def test_create_confirmation(self, db_session, seed_data):
        conf = AssignmentConfirmation(
            assignment_id=seed_data["assignment"].id,
            partner_id=seed_data["partner"].id,
            confirmation_type=ConfirmationType.START_DATE,
            status=ConfirmationStatus.PENDING,
            requested_date=date(2026, 4, 15),
        )
        db_session.add(conf)
        db_session.commit()
        db_session.refresh(conf)

        assert conf.id is not None
        assert conf.status == ConfirmationStatus.PENDING
        assert conf.confirmation_type == ConfirmationType.START_DATE
        assert conf.requested_date == date(2026, 4, 15)
        assert conf.escalated is False
        assert conf.escalation_status == EscalationStatus.NONE

    def test_confirm_status_transition(self, db_session, seed_data):
        conf = AssignmentConfirmation(
            assignment_id=seed_data["assignment"].id,
            partner_id=seed_data["partner"].id,
            confirmation_type=ConfirmationType.START_DATE,
            status=ConfirmationStatus.PENDING,
            requested_date=date(2026, 4, 15),
        )
        db_session.add(conf)
        db_session.commit()

        conf.status = ConfirmationStatus.CONFIRMED
        conf.responded_at = datetime.utcnow()
        db_session.commit()
        db_session.refresh(conf)

        assert conf.status == ConfirmationStatus.CONFIRMED
        assert conf.responded_at is not None

    def test_decline_with_proposed_date(self, db_session, seed_data):
        conf = AssignmentConfirmation(
            assignment_id=seed_data["assignment"].id,
            partner_id=seed_data["partner"].id,
            confirmation_type=ConfirmationType.END_DATE,
            status=ConfirmationStatus.PENDING,
            requested_date=date(2026, 7, 15),
        )
        db_session.add(conf)
        db_session.commit()

        conf.status = ConfirmationStatus.DECLINED
        conf.proposed_date = date(2026, 8, 1)
        conf.response_note = "Need extra two weeks."
        conf.responded_at = datetime.utcnow()
        db_session.commit()
        db_session.refresh(conf)

        assert conf.status == ConfirmationStatus.DECLINED
        assert conf.proposed_date == date(2026, 8, 1)
        assert conf.response_note == "Need extra two weeks."

    def test_is_overdue_property(self, db_session, seed_data):
        conf = AssignmentConfirmation(
            assignment_id=seed_data["assignment"].id,
            partner_id=seed_data["partner"].id,
            confirmation_type=ConfirmationType.START_DATE,
            status=ConfirmationStatus.PENDING,
            requested_date=date(2026, 4, 15),
            requested_at=datetime.utcnow() - timedelta(hours=25),
        )
        db_session.add(conf)
        db_session.commit()

        assert conf.is_overdue is True

    def test_is_not_overdue_property(self, db_session, seed_data):
        conf = AssignmentConfirmation(
            assignment_id=seed_data["assignment"].id,
            partner_id=seed_data["partner"].id,
            confirmation_type=ConfirmationType.START_DATE,
            status=ConfirmationStatus.PENDING,
            requested_date=date(2026, 4, 15),
            requested_at=datetime.utcnow() - timedelta(hours=12),
        )
        db_session.add(conf)
        db_session.commit()

        assert conf.is_overdue is False

    def test_hours_waiting_property(self, db_session, seed_data):
        conf = AssignmentConfirmation(
            assignment_id=seed_data["assignment"].id,
            partner_id=seed_data["partner"].id,
            confirmation_type=ConfirmationType.START_DATE,
            status=ConfirmationStatus.PENDING,
            requested_date=date(2026, 4, 15),
            requested_at=datetime.utcnow() - timedelta(hours=6),
        )
        db_session.add(conf)
        db_session.commit()

        assert 5.5 <= conf.hours_waiting <= 6.5

    def test_escalation_fields(self, db_session, seed_data):
        conf = AssignmentConfirmation(
            assignment_id=seed_data["assignment"].id,
            partner_id=seed_data["partner"].id,
            confirmation_type=ConfirmationType.START_DATE,
            status=ConfirmationStatus.ESCALATED,
            requested_date=date(2026, 4, 15),
            escalated=True,
            escalated_at=datetime.utcnow(),
            escalation_status=EscalationStatus.ESCALATED,
        )
        db_session.add(conf)
        db_session.commit()
        db_session.refresh(conf)

        assert conf.escalated is True
        assert conf.escalation_status == EscalationStatus.ESCALATED


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestPartnerConfirmationAPI:
    """Integration tests for partner confirmation API endpoints."""

    def test_create_confirmation_as_ops(self, seed_data):
        resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["confirmation_type"] == "start_date"
        assert data["requested_date"] == "2026-04-15"
        assert data["escalated"] is False

    def test_create_confirmation_partner_forbidden(self, seed_data):
        resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers={"X-Demo-Role": "partner", "X-Demo-User-Id": str(seed_data["partner"].id)},
        )
        assert resp.status_code == 403

    def test_duplicate_pending_conflict(self, seed_data):
        payload = {
            "assignment_id": str(seed_data["assignment"].id),
            "partner_id": str(seed_data["partner"].id),
            "confirmation_type": "start_date",
            "requested_date": "2026-04-15",
        }
        resp1 = client.post("/api/partner-confirmations", json=payload, headers=OPS_HEADERS)
        assert resp1.status_code == 201

        resp2 = client.post("/api/partner-confirmations", json=payload, headers=OPS_HEADERS)
        assert resp2.status_code == 409

    def test_list_confirmations_ops(self, seed_data):
        # Create one
        client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers=OPS_HEADERS,
        )
        resp = client.get("/api/partner-confirmations", headers=OPS_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["pending_count"] >= 1

    def test_list_confirmations_partner_scoped(self, seed_data):
        partner_headers = {
            "X-Demo-Role": "partner",
            "X-Demo-User-Id": str(seed_data["partner"].id),
        }
        # Create as ops
        client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers=OPS_HEADERS,
        )
        resp = client.get("/api/partner-confirmations", headers=partner_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_get_single_confirmation(self, seed_data):
        create_resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers=OPS_HEADERS,
        )
        conf_id = create_resp.json()["id"]

        resp = client.get(f"/api/partner-confirmations/{conf_id}", headers=OPS_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["id"] == conf_id

    def test_confirm_assignment_date(self, seed_data):
        partner_headers = {
            "X-Demo-Role": "partner",
            "X-Demo-User-Id": str(seed_data["partner"].id),
        }
        create_resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers=OPS_HEADERS,
        )
        conf_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/partner-confirmations/{conf_id}/respond",
            json={"action": "confirm", "response_note": "All good."},
            headers=partner_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confirmation"]["status"] == "confirmed"
        assert data["assignment_updated"] is True
        assert "confirmed" in data["message"].lower()

    def test_decline_assignment_date(self, seed_data):
        partner_headers = {
            "X-Demo-Role": "partner",
            "X-Demo-User-Id": str(seed_data["partner"].id),
        }
        create_resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "end_date",
                "requested_date": "2026-07-15",
            },
            headers=OPS_HEADERS,
        )
        conf_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/partner-confirmations/{conf_id}/respond",
            json={
                "action": "decline",
                "proposed_date": "2026-08-01",
                "response_note": "Need extra time.",
            },
            headers=partner_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confirmation"]["status"] == "declined"
        assert data["confirmation"]["proposed_date"] == "2026-08-01"

    def test_decline_without_proposed_date_fails(self, seed_data):
        partner_headers = {
            "X-Demo-Role": "partner",
            "X-Demo-User-Id": str(seed_data["partner"].id),
        }
        create_resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers=OPS_HEADERS,
        )
        conf_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/partner-confirmations/{conf_id}/respond",
            json={"action": "decline"},
            headers=partner_headers,
        )
        assert resp.status_code == 422

    def test_respond_to_already_confirmed_fails(self, seed_data):
        partner_headers = {
            "X-Demo-Role": "partner",
            "X-Demo-User-Id": str(seed_data["partner"].id),
        }
        create_resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers=OPS_HEADERS,
        )
        conf_id = create_resp.json()["id"]

        # Confirm
        client.post(
            f"/api/partner-confirmations/{conf_id}/respond",
            json={"action": "confirm"},
            headers=partner_headers,
        )

        # Try to confirm again
        resp = client.post(
            f"/api/partner-confirmations/{conf_id}/respond",
            json={"action": "confirm"},
            headers=partner_headers,
        )
        assert resp.status_code == 409

    def test_technician_role_forbidden(self, seed_data):
        tech_headers = {"X-Demo-Role": "technician", "X-Demo-User-Id": "tech-1"}
        resp = client.get("/api/partner-confirmations", headers=tech_headers)
        assert resp.status_code == 403

    def test_get_pending_for_partner(self, seed_data):
        # Create two confirmations
        for ct in ["start_date", "end_date"]:
            client.post(
                "/api/partner-confirmations",
                json={
                    "assignment_id": str(seed_data["assignment"].id),
                    "partner_id": str(seed_data["partner"].id),
                    "confirmation_type": ct,
                    "requested_date": "2026-04-15",
                },
                headers=OPS_HEADERS,
            )

        resp = client.get(
            f"/api/partner-confirmations/partner/{seed_data['partner'].id}/pending",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["pending_count"] == 2

    def test_nonexistent_assignment_404(self):
        resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(uuid.uuid4()),
                "partner_id": str(uuid.uuid4()),
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            },
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404

    def test_nonexistent_confirmation_404(self):
        resp = client.get(
            f"/api/partner-confirmations/{uuid.uuid4()}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404

    def test_end_date_confirmation_updates_assignment(self, seed_data):
        partner_headers = {
            "X-Demo-Role": "partner",
            "X-Demo-User-Id": str(seed_data["partner"].id),
        }
        create_resp = client.post(
            "/api/partner-confirmations",
            json={
                "assignment_id": str(seed_data["assignment"].id),
                "partner_id": str(seed_data["partner"].id),
                "confirmation_type": "end_date",
                "requested_date": "2026-07-15",
            },
            headers=OPS_HEADERS,
        )
        conf_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/partner-confirmations/{conf_id}/respond",
            json={"action": "confirm"},
            headers=partner_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["assignment_updated"] is True
