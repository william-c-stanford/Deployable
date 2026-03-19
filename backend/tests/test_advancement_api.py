"""Tests for advancement API endpoints.

Tests cover:
1. GET /api/advancement/{tech_id}/status — query advancement status
2. GET /api/advancement/status — list advancement statuses with filters
3. GET /api/advancement/cert-gates — list cert gate configs
4. GET /api/advancement/cert-gates/{skill_id} — get specific cert gate
5. PUT /api/advancement/cert-gates/{skill_id} — update cert gate config
6. POST /api/advancement/re-evaluate — manual re-evaluation (dry run + apply)
7. Role-based access control (ops-only for admin endpoints)
8. Career stage transitions during re-evaluation
"""

import uuid
from datetime import date

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
    TechnicianSkill,
    TechnicianCertification,
    CareerStage,
    DeployabilityStatus,
    ProficiencyLevel,
    CertStatus,
)
from app.models.skill import Skill, SkillCategory


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
def db(db_engine):
    TestSession = sessionmaker(bind=db_engine)
    session = TestSession()
    yield session
    session.close()


@pytest.fixture
def client(db):
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def ops_headers():
    return {"X-Demo-Role": "ops", "X-Demo-User-Id": "ops-user-1"}


@pytest.fixture
def tech_headers():
    return {"X-Demo-Role": "technician", "X-Demo-User-Id": "tech-user-1"}


@pytest.fixture
def partner_headers():
    return {"X-Demo-Role": "partner", "X-Demo-User-Id": "partner-user-1"}


@pytest.fixture
def skill_category(db: Session):
    cat = SkillCategory(
        id=uuid.uuid4(),
        name="Fiber Optic",
        description="Fiber optic skills",
    )
    db.add(cat)
    db.commit()
    return cat


@pytest.fixture
def skills(db: Session, skill_category):
    splicing = Skill(
        id=uuid.uuid4(),
        name="Fiber Splicing",
        slug="fiber-splicing",
        category_id=skill_category.id,
        intermediate_hours_threshold=100,
        advanced_hours_threshold=300,
        cert_gate_intermediate=None,
        cert_gate_advanced="FOA CFOT",
    )
    otdr = Skill(
        id=uuid.uuid4(),
        name="OTDR Testing",
        slug="otdr-testing",
        category_id=skill_category.id,
        intermediate_hours_threshold=80,
        advanced_hours_threshold=250,
        cert_gate_intermediate="FOA CFOT",
        cert_gate_advanced=None,
    )
    db.add_all([splicing, otdr])
    db.commit()
    return {"splicing": splicing, "otdr": otdr}


@pytest.fixture
def tech_beginner(db: Session):
    """Technician with beginner skills, insufficient hours."""
    tech = Technician(
        id=uuid.uuid4(),
        first_name="Alice",
        last_name="Smith",
        email="alice@test.com",
        career_stage=CareerStage.IN_TRAINING,
        deployability_status=DeployabilityStatus.IN_TRAINING,
    )
    db.add(tech)
    db.flush()
    db.add_all([
        TechnicianSkill(
            id=uuid.uuid4(),
            technician_id=tech.id,
            skill_name="Fiber Splicing",
            proficiency_level=ProficiencyLevel.APPRENTICE,
            training_hours_accumulated=50.0,
        ),
        TechnicianSkill(
            id=uuid.uuid4(),
            technician_id=tech.id,
            skill_name="OTDR Testing",
            proficiency_level=ProficiencyLevel.APPRENTICE,
            training_hours_accumulated=30.0,
        ),
    ])
    db.commit()
    db.refresh(tech)
    return tech


@pytest.fixture
def tech_ready(db: Session):
    """Technician meeting hours thresholds with required certs."""
    tech = Technician(
        id=uuid.uuid4(),
        first_name="Bob",
        last_name="Jones",
        email="bob@test.com",
        career_stage=CareerStage.IN_TRAINING,
        deployability_status=DeployabilityStatus.IN_TRAINING,
    )
    db.add(tech)
    db.flush()
    db.add_all([
        TechnicianSkill(
            id=uuid.uuid4(),
            technician_id=tech.id,
            skill_name="Fiber Splicing",
            proficiency_level=ProficiencyLevel.APPRENTICE,
            training_hours_accumulated=150.0,
        ),
        TechnicianSkill(
            id=uuid.uuid4(),
            technician_id=tech.id,
            skill_name="OTDR Testing",
            proficiency_level=ProficiencyLevel.APPRENTICE,
            training_hours_accumulated=100.0,
        ),
        TechnicianCertification(
            id=uuid.uuid4(),
            technician_id=tech.id,
            cert_name="FOA CFOT",
            status=CertStatus.ACTIVE,
            issue_date=date(2025, 1, 1),
        ),
    ])
    db.commit()
    db.refresh(tech)
    return tech


@pytest.fixture
def tech_cert_blocked(db: Session):
    """Technician with hours met but missing required cert."""
    tech = Technician(
        id=uuid.uuid4(),
        first_name="Carol",
        last_name="White",
        email="carol@test.com",
        career_stage=CareerStage.IN_TRAINING,
        deployability_status=DeployabilityStatus.IN_TRAINING,
    )
    db.add(tech)
    db.flush()
    db.add(TechnicianSkill(
        id=uuid.uuid4(),
        technician_id=tech.id,
        skill_name="OTDR Testing",
        proficiency_level=ProficiencyLevel.APPRENTICE,
        training_hours_accumulated=120.0,
    ))
    db.commit()
    db.refresh(tech)
    return tech


# ---------------------------------------------------------------------------
# GET /api/advancement/{tech_id}/status
# ---------------------------------------------------------------------------

class TestGetAdvancementStatus:
    def test_success(self, client, db, skills, tech_beginner, ops_headers):
        resp = client.get(
            f"/api/advancement/{tech_beginner.id}/status",
            headers=ops_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["technician_id"] == str(tech_beginner.id)
        assert data["technician_name"] == "Alice Smith"
        assert data["career_stage"] == "In Training"
        assert data["total_skills"] == 2
        assert data["skills_at_apprentice"] == 2
        assert data["skills_at_intermediate"] == 0
        assert data["skills_at_advanced"] == 0
        assert data["overall_training_complete"] is False
        assert len(data["skills"]) == 2

    def test_skill_details(self, client, db, skills, tech_beginner, ops_headers):
        resp = client.get(
            f"/api/advancement/{tech_beginner.id}/status",
            headers=ops_headers,
        )
        data = resp.json()
        splicing_skill = next(
            (s for s in data["skills"] if s["skill_name"] == "Fiber Splicing"), None
        )
        assert splicing_skill is not None
        assert splicing_skill["current_level"] == "Apprentice"
        assert splicing_skill["training_hours_accumulated"] == 50.0
        assert splicing_skill["eligible_for_advancement"] is False
        assert splicing_skill["hours_to_next_level"] == 50.0
        assert splicing_skill["next_level"] in ("Intermediate",)

    def test_eligible_status(self, client, db, skills, tech_ready, ops_headers):
        resp = client.get(
            f"/api/advancement/{tech_ready.id}/status",
            headers=ops_headers,
        )
        data = resp.json()
        eligible = [s for s in data["skills"] if s["eligible_for_advancement"]]
        assert len(eligible) == 2

    def test_cert_blocked_status(self, client, db, skills, tech_cert_blocked, ops_headers):
        resp = client.get(
            f"/api/advancement/{tech_cert_blocked.id}/status",
            headers=ops_headers,
        )
        data = resp.json()
        otdr_skill = data["skills"][0]
        assert otdr_skill["eligible_for_advancement"] is False
        assert otdr_skill["cert_gate_required"] == "FOA CFOT"
        assert otdr_skill["cert_gate_met"] is False

    def test_not_found(self, client, ops_headers):
        resp = client.get(
            f"/api/advancement/{uuid.uuid4()}/status",
            headers=ops_headers,
        )
        assert resp.status_code == 404

    def test_technician_can_view(self, client, db, skills, tech_beginner, tech_headers):
        resp = client.get(
            f"/api/advancement/{tech_beginner.id}/status",
            headers=tech_headers,
        )
        assert resp.status_code == 200

    def test_no_auth_fails(self, client, db, skills, tech_beginner):
        resp = client.get(f"/api/advancement/{tech_beginner.id}/status")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/advancement/status
# ---------------------------------------------------------------------------

class TestListAdvancementStatus:
    def test_ops_can_list(self, client, db, skills, tech_beginner, tech_ready, ops_headers):
        resp = client.get("/api/advancement/status", headers=ops_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

    def test_technician_forbidden(self, client, tech_headers):
        resp = client.get("/api/advancement/status", headers=tech_headers)
        assert resp.status_code == 403

    def test_partner_forbidden(self, client, partner_headers):
        resp = client.get("/api/advancement/status", headers=partner_headers)
        assert resp.status_code == 403

    def test_eligible_only_filter(self, client, db, skills, tech_beginner, tech_ready, ops_headers):
        resp = client.get(
            "/api/advancement/status?eligible_only=true",
            headers=ops_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Only Bob should have eligible advancements
        names = [d["technician_name"] for d in data]
        assert "Bob Jones" in names
        assert "Alice Smith" not in names

    def test_career_stage_filter(self, client, db, skills, tech_beginner, ops_headers):
        resp = client.get(
            "/api/advancement/status?career_stage=In Training",
            headers=ops_headers,
        )
        assert resp.status_code == 200
        for item in resp.json():
            assert item["career_stage"] == "In Training"

    def test_pagination(self, client, db, skills, tech_beginner, tech_ready, ops_headers):
        resp = client.get(
            "/api/advancement/status?skip=0&limit=1",
            headers=ops_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1


# ---------------------------------------------------------------------------
# Cert Gate Endpoints
# ---------------------------------------------------------------------------

class TestCertGateEndpoints:
    def test_list_cert_gates(self, client, db, skills, ops_headers):
        resp = client.get("/api/advancement/cert-gates", headers=ops_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        names = {item["skill_name"] for item in data["items"]}
        assert "Fiber Splicing" in names
        assert "OTDR Testing" in names

    def test_list_forbidden_for_technician(self, client, tech_headers):
        resp = client.get("/api/advancement/cert-gates", headers=tech_headers)
        assert resp.status_code == 403

    def test_get_cert_gate_detail(self, client, db, skills, ops_headers):
        resp = client.get(
            f"/api/advancement/cert-gates/{skills['splicing'].id}",
            headers=ops_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skill_name"] == "Fiber Splicing"
        assert data["intermediate_hours_threshold"] == 100
        assert data["advanced_hours_threshold"] == 300
        assert data["cert_gate_intermediate"] is None
        assert data["cert_gate_advanced"] == "FOA CFOT"

    def test_get_cert_gate_not_found(self, client, ops_headers):
        resp = client.get(
            f"/api/advancement/cert-gates/{uuid.uuid4()}",
            headers=ops_headers,
        )
        assert resp.status_code == 404

    def test_update_thresholds(self, client, db, skills, ops_headers):
        resp = client.put(
            f"/api/advancement/cert-gates/{skills['splicing'].id}",
            headers=ops_headers,
            json={
                "intermediate_hours_threshold": 120,
                "advanced_hours_threshold": 350,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intermediate_hours_threshold"] == 120
        assert data["advanced_hours_threshold"] == 350

        # Verify persisted
        db.refresh(skills["splicing"])
        assert skills["splicing"].intermediate_hours_threshold == 120

    def test_update_add_cert_gate(self, client, db, skills, ops_headers):
        resp = client.put(
            f"/api/advancement/cert-gates/{skills['splicing'].id}",
            headers=ops_headers,
            json={"cert_gate_intermediate": "BICSI Installer"},
        )
        assert resp.status_code == 200
        assert resp.json()["cert_gate_intermediate"] == "BICSI Installer"

    def test_update_clear_cert_gate(self, client, db, skills, ops_headers):
        resp = client.put(
            f"/api/advancement/cert-gates/{skills['splicing'].id}",
            headers=ops_headers,
            json={"cert_gate_advanced": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["cert_gate_advanced"] is None

    def test_update_not_found(self, client, ops_headers):
        resp = client.put(
            f"/api/advancement/cert-gates/{uuid.uuid4()}",
            headers=ops_headers,
            json={"intermediate_hours_threshold": 150},
        )
        assert resp.status_code == 404

    def test_update_forbidden_for_partner(self, client, db, skills, partner_headers):
        resp = client.put(
            f"/api/advancement/cert-gates/{skills['splicing'].id}",
            headers=partner_headers,
            json={"intermediate_hours_threshold": 150},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/advancement/re-evaluate
# ---------------------------------------------------------------------------

class TestReEvaluation:
    def test_dry_run_no_changes(self, client, db, skills, tech_ready, ops_headers):
        resp = client.post(
            "/api/advancement/re-evaluate",
            headers=ops_headers,
            json={"dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["technicians_evaluated"] >= 1
        assert data["total_advancements"] >= 1

        # Verify no actual changes
        db.refresh(tech_ready)
        for ts in tech_ready.skills:
            level = ts.proficiency_level.value if hasattr(ts.proficiency_level, "value") else str(ts.proficiency_level)
            assert level == "Apprentice"

    def test_apply_advancements(self, client, db, skills, tech_ready, ops_headers):
        resp = client.post(
            "/api/advancement/re-evaluate",
            headers=ops_headers,
            json={"dry_run": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is False
        assert data["total_advancements"] >= 2
        assert data["technicians_with_changes"] >= 1

        # Find Bob's result
        bob = next(
            (r for r in data["results"] if r["technician_name"] == "Bob Jones"), None
        )
        assert bob is not None
        assert len(bob["advancements"]) == 2
        for adv in bob["advancements"]:
            assert adv["old_level"] == "Apprentice"
            assert adv["new_level"] == "Intermediate"

        # Verify DB
        db.refresh(tech_ready)
        for ts in tech_ready.skills:
            level = ts.proficiency_level.value if hasattr(ts.proficiency_level, "value") else str(ts.proficiency_level)
            assert level == "Intermediate"

    def test_career_stage_transition(self, client, db, skills, tech_ready, ops_headers):
        resp = client.post(
            "/api/advancement/re-evaluate",
            headers=ops_headers,
            json={"dry_run": False, "technician_ids": [str(tech_ready.id)]},
        )
        data = resp.json()
        bob = data["results"][0]
        assert bob["career_stage_changed"] is True
        assert bob["old_career_stage"] == "In Training"
        assert bob["new_career_stage"] == "Training Completed"

        db.refresh(tech_ready)
        cs = tech_ready.career_stage
        assert (cs.value if hasattr(cs, "value") else str(cs)) == "Training Completed"

    def test_specific_ids_filter(self, client, db, skills, tech_beginner, tech_ready, ops_headers):
        resp = client.post(
            "/api/advancement/re-evaluate",
            headers=ops_headers,
            json={"dry_run": True, "technician_ids": [str(tech_beginner.id)]},
        )
        data = resp.json()
        assert data["technicians_evaluated"] == 1
        assert data["results"][0]["technician_name"] == "Alice Smith"
        assert data["total_advancements"] == 0

    def test_cert_blocked_no_advance(self, client, db, skills, tech_cert_blocked, ops_headers):
        resp = client.post(
            "/api/advancement/re-evaluate",
            headers=ops_headers,
            json={"dry_run": False, "technician_ids": [str(tech_cert_blocked.id)]},
        )
        data = resp.json()
        assert data["total_advancements"] == 0

    def test_forbidden_for_technician(self, client, tech_headers):
        resp = client.post(
            "/api/advancement/re-evaluate",
            headers=tech_headers,
            json={"dry_run": True},
        )
        assert resp.status_code == 403

    def test_forbidden_for_partner(self, client, partner_headers):
        resp = client.post(
            "/api/advancement/re-evaluate",
            headers=partner_headers,
            json={"dry_run": True},
        )
        assert resp.status_code == 403

    def test_empty_body_defaults(self, client, db, skills, tech_ready, ops_headers):
        resp = client.post(
            "/api/advancement/re-evaluate",
            headers=ops_headers,
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is False
