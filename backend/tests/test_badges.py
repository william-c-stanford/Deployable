"""Tests for badge API endpoints.

Tests cover:
1. GET /api/technicians/{tech_id}/badges — list all badges
2. GET /api/technicians/{tech_id}/badges?badge_type=site — filtered listing
3. POST /api/technicians/{tech_id}/badges/grant — grant manual badge (ops only)
4. DELETE /api/technicians/{tech_id}/badges/{badge_id} — revoke badge (ops only)
5. GET /api/technicians/{tech_id}/badges/milestones — milestone badges
6. POST /api/technicians/{tech_id}/badges/milestones/sync — sync milestones
7. Role-based access control (ops-only for grant/revoke/sync)
8. Error cases (duplicate badges, milestone grant rejection, 404s)
"""

import uuid
from datetime import date, datetime

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
    TechnicianBadge,
    TechnicianCertification,
    TechnicianSkill,
    CareerStage,
    DeployabilityStatus,
    ProficiencyLevel,
    CertStatus,
    BadgeType,
)
from app.models.assignment import Assignment, AssignmentType
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.training import (
    TrainingProgram,
    TrainingEnrollment,
    EnrollmentStatus,
    AdvancementLevel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
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
def db_session(db_engine):
    TestSession = sessionmaker(bind=db_engine)
    session = TestSession()
    yield session
    session.close()


@pytest.fixture(scope="function")
def client(db_session):
    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


OPS_HEADERS = {"X-Demo-Role": "ops", "X-Demo-User-Id": "ops-user-1"}
TECH_HEADERS = {"X-Demo-Role": "technician", "X-Demo-User-Id": "tech-user-1"}
PARTNER_HEADERS = {"X-Demo-Role": "partner", "X-Demo-User-Id": "partner-user-1"}


@pytest.fixture
def technician(db_session):
    tech = Technician(
        first_name="Jane",
        last_name="Fiber",
        email="jane.fiber@example.com",
        career_stage=CareerStage.DEPLOYED,
        deployability_status=DeployabilityStatus.READY_NOW,
        hire_date=date(2020, 1, 1),
        years_experience=5.0,
    )
    db_session.add(tech)
    db_session.commit()
    db_session.refresh(tech)
    return tech


@pytest.fixture
def technician_with_badges(db_session, technician):
    """Technician with pre-existing badges of different types."""
    badges = [
        TechnicianBadge(
            technician_id=technician.id,
            badge_type=BadgeType.SITE,
            badge_name="AT&T Approved",
            description="Approved for AT&T sites",
        ),
        TechnicianBadge(
            technician_id=technician.id,
            badge_type=BadgeType.CLIENT,
            badge_name="Verizon Preferred",
            description="Preferred by Verizon",
        ),
        TechnicianBadge(
            technician_id=technician.id,
            badge_type=BadgeType.MILESTONE,
            badge_name="First Cert Earned",
            description="Earned first active industry certification",
        ),
    ]
    for b in badges:
        db_session.add(b)
    db_session.commit()
    return technician


@pytest.fixture
def technician_with_achievements(db_session, technician):
    """Technician with certs and skills to trigger milestone badges."""
    # Add 3 active certifications
    for i in range(3):
        cert = TechnicianCertification(
            technician_id=technician.id,
            cert_name=f"Cert {i+1}",
            status=CertStatus.ACTIVE,
            issue_date=date(2023, 1, 1),
        )
        db_session.add(cert)

    # Add 1 advanced skill
    skill = TechnicianSkill(
        technician_id=technician.id,
        skill_name="Fiber Splicing",
        proficiency_level=ProficiencyLevel.ADVANCED,
        training_hours_accumulated=200,
    )
    db_session.add(skill)

    db_session.commit()
    return technician


# ---------------------------------------------------------------------------
# Tests: List badges
# ---------------------------------------------------------------------------

class TestListBadges:
    def test_list_all_badges(self, client, technician_with_badges):
        tech_id = technician_with_badges.id
        resp = client.get(f"/api/technicians/{tech_id}/badges", headers=OPS_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3
        assert data["badge_type_filter"] is None

    def test_list_badges_filtered_by_type(self, client, technician_with_badges):
        tech_id = technician_with_badges.id
        resp = client.get(
            f"/api/technicians/{tech_id}/badges?badge_type=site",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["badge_name"] == "AT&T Approved"
        assert data["badge_type_filter"] == "site"

    def test_list_badges_empty(self, client, technician):
        resp = client.get(
            f"/api/technicians/{technician.id}/badges",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_badges_technician_not_found(self, client):
        fake_id = uuid.uuid4()
        resp = client.get(f"/api/technicians/{fake_id}/badges", headers=OPS_HEADERS)
        assert resp.status_code == 404

    def test_list_badges_technician_role_can_view(self, client, technician_with_badges):
        tech_id = technician_with_badges.id
        resp = client.get(f"/api/technicians/{tech_id}/badges", headers=TECH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] == 3

    def test_list_badges_partner_role_can_view(self, client, technician_with_badges):
        tech_id = technician_with_badges.id
        resp = client.get(f"/api/technicians/{tech_id}/badges", headers=PARTNER_HEADERS)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Grant manual badge
# ---------------------------------------------------------------------------

class TestGrantBadge:
    def test_grant_site_badge(self, client, technician):
        resp = client.post(
            f"/api/technicians/{technician.id}/badges/grant",
            json={
                "badge_type": "site",
                "badge_name": "Google Data Center Cleared",
                "description": "Cleared for Google DC access",
            },
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["badge_name"] == "Google Data Center Cleared"
        assert data["badge_type"] == "site"
        assert data["description"] == "Cleared for Google DC access"
        assert data["technician_id"] == str(technician.id)

    def test_grant_client_badge(self, client, technician):
        resp = client.post(
            f"/api/technicians/{technician.id}/badges/grant",
            json={"badge_type": "client", "badge_name": "AWS Preferred"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["badge_type"] == "client"

    def test_grant_milestone_badge_rejected(self, client, technician):
        resp = client.post(
            f"/api/technicians/{technician.id}/badges/grant",
            json={"badge_type": "milestone", "badge_name": "Fake Milestone"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 400
        assert "auto-generated" in resp.json()["detail"].lower()

    def test_grant_duplicate_badge_rejected(self, client, technician):
        payload = {"badge_type": "site", "badge_name": "Same Badge"}
        resp1 = client.post(
            f"/api/technicians/{technician.id}/badges/grant",
            json=payload,
            headers=OPS_HEADERS,
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            f"/api/technicians/{technician.id}/badges/grant",
            json=payload,
            headers=OPS_HEADERS,
        )
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["detail"]

    def test_grant_badge_technician_role_forbidden(self, client, technician):
        resp = client.post(
            f"/api/technicians/{technician.id}/badges/grant",
            json={"badge_type": "site", "badge_name": "Nope"},
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 403

    def test_grant_badge_partner_role_forbidden(self, client, technician):
        resp = client.post(
            f"/api/technicians/{technician.id}/badges/grant",
            json={"badge_type": "site", "badge_name": "Nope"},
            headers=PARTNER_HEADERS,
        )
        assert resp.status_code == 403

    def test_grant_badge_technician_not_found(self, client):
        fake_id = uuid.uuid4()
        resp = client.post(
            f"/api/technicians/{fake_id}/badges/grant",
            json={"badge_type": "site", "badge_name": "Test"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Revoke badge
# ---------------------------------------------------------------------------

class TestRevokeBadge:
    def test_revoke_badge(self, client, technician_with_badges, db_session):
        tech_id = technician_with_badges.id
        badge = db_session.query(TechnicianBadge).filter(
            TechnicianBadge.technician_id == tech_id,
            TechnicianBadge.badge_name == "AT&T Approved",
        ).first()

        resp = client.delete(
            f"/api/technicians/{tech_id}/badges/{badge.id}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["badge_name"] == "AT&T Approved"

        # Verify it's gone
        list_resp = client.get(f"/api/technicians/{tech_id}/badges", headers=OPS_HEADERS)
        assert list_resp.json()["total"] == 2

    def test_revoke_nonexistent_badge(self, client, technician):
        fake_badge_id = uuid.uuid4()
        resp = client.delete(
            f"/api/technicians/{technician.id}/badges/{fake_badge_id}",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404

    def test_revoke_badge_technician_role_forbidden(self, client, technician_with_badges, db_session):
        tech_id = technician_with_badges.id
        badge = db_session.query(TechnicianBadge).filter(
            TechnicianBadge.technician_id == tech_id,
        ).first()

        resp = client.delete(
            f"/api/technicians/{tech_id}/badges/{badge.id}",
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: Milestone badges
# ---------------------------------------------------------------------------

class TestMilestoneBadges:
    def test_get_milestones_basic(self, client, technician):
        resp = client.get(
            f"/api/technicians/{technician.id}/badges/milestones",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["technician_id"] == str(technician.id)
        assert isinstance(data["earned"], list)
        assert isinstance(data["available"], list)
        # With hire_date 2020-01-01 and today being 2026, should earn tenure milestones
        earned_names = {e["badge_name"] for e in data["earned"]}
        assert "1 Year Anniversary" in earned_names
        assert "3 Year Veteran" in earned_names
        assert "5 Year Stalwart" in earned_names

    def test_get_milestones_with_certs(self, client, technician_with_achievements):
        tech_id = technician_with_achievements.id
        resp = client.get(
            f"/api/technicians/{tech_id}/badges/milestones",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        earned_names = {e["badge_name"] for e in data["earned"]}
        assert "First Cert Earned" in earned_names
        assert "Triple Certified" in earned_names
        assert "Advanced Specialist" in earned_names

    def test_milestones_technician_not_found(self, client):
        fake_id = uuid.uuid4()
        resp = client.get(f"/api/technicians/{fake_id}/badges/milestones", headers=OPS_HEADERS)
        assert resp.status_code == 404

    def test_milestones_all_roles_can_view(self, client, technician):
        tech_id = technician.id
        for headers in [OPS_HEADERS, TECH_HEADERS, PARTNER_HEADERS]:
            resp = client.get(
                f"/api/technicians/{tech_id}/badges/milestones",
                headers=headers,
            )
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Sync milestone badges
# ---------------------------------------------------------------------------

class TestSyncMilestones:
    def test_sync_creates_new_badges(self, client, technician_with_achievements, db_session):
        tech_id = technician_with_achievements.id

        # Before sync — no milestone badges persisted
        count_before = db_session.query(TechnicianBadge).filter(
            TechnicianBadge.technician_id == tech_id,
            TechnicianBadge.badge_type == BadgeType.MILESTONE,
        ).count()
        assert count_before == 0

        resp = client.post(
            f"/api/technicians/{tech_id}/badges/milestones/sync",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        new_badges = resp.json()
        assert len(new_badges) > 0

        # Verify persisted
        badge_names = {b["badge_name"] for b in new_badges}
        assert "First Cert Earned" in badge_names
        assert "Triple Certified" in badge_names

    def test_sync_is_idempotent(self, client, technician_with_achievements):
        tech_id = technician_with_achievements.id

        resp1 = client.post(
            f"/api/technicians/{tech_id}/badges/milestones/sync",
            headers=OPS_HEADERS,
        )
        count1 = len(resp1.json())

        resp2 = client.post(
            f"/api/technicians/{tech_id}/badges/milestones/sync",
            headers=OPS_HEADERS,
        )
        count2 = len(resp2.json())

        assert count1 > 0
        assert count2 == 0  # No new badges on second sync

    def test_sync_technician_role_forbidden(self, client, technician):
        resp = client.post(
            f"/api/technicians/{technician.id}/badges/milestones/sync",
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 403

    def test_sync_partner_role_forbidden(self, client, technician):
        resp = client.post(
            f"/api/technicians/{technician.id}/badges/milestones/sync",
            headers=PARTNER_HEADERS,
        )
        assert resp.status_code == 403
