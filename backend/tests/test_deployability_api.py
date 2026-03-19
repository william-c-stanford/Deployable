"""Tests for the deployability status API endpoints.

Tests cover:
  - GET /api/deployability/{id}/status — read computed status with readiness
  - POST /api/deployability/{id}/override — apply manual status override
  - POST /api/deployability/{id}/unlock — unlock locked status
  - GET /api/deployability/{id}/history — view status change history
  - GET /api/deployability/summary — aggregate summary
  - Role-based access control
  - Manual override creates audit trail in history table
  - Visual indicator flags (auto vs manual, divergent status)
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

import app.models as _models  # noqa: F401 — ensure all models registered with Base
from app.database import Base, get_db
from app.main import app
from app.models.technician import (
    Technician,
    CareerStage,
    DeployabilityStatus,
)
from app.models.deployability_history import (
    DeployabilityStatusHistory,
    StatusChangeSource,
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


OPS_HEADERS = {"X-Demo-Role": "ops", "X-Demo-User-Id": "ops-user-1"}
TECH_HEADERS = {"X-Demo-Role": "technician", "X-Demo-User-Id": "tech-user-1"}
PARTNER_HEADERS = {"X-Demo-Role": "partner", "X-Demo-User-Id": "partner-user-1"}


@pytest.fixture
def sample_tech(db) -> Technician:
    tech = Technician(
        id=uuid.uuid4(),
        first_name="Jane",
        last_name="Doe",
        email="jane.doe@example.com",
        career_stage=CareerStage.DEPLOYED,
        deployability_status=DeployabilityStatus.READY_NOW,
        deployability_locked=False,
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    return tech


@pytest.fixture
def locked_tech(db) -> Technician:
    tech = Technician(
        id=uuid.uuid4(),
        first_name="Bob",
        last_name="Locked",
        email="bob.locked@example.com",
        career_stage=CareerStage.AWAITING_ASSIGNMENT,
        deployability_status=DeployabilityStatus.INACTIVE,
        deployability_locked=True,
        inactive_locked_by="ops-admin",
        inactive_lock_reason="Compliance hold",
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    return tech


@pytest.fixture
def tech_with_history(db) -> Technician:
    tech = Technician(
        id=uuid.uuid4(),
        first_name="Alice",
        last_name="History",
        email="alice.h@example.com",
        career_stage=CareerStage.DEPLOYED,
        deployability_status=DeployabilityStatus.READY_NOW,
        deployability_locked=False,
    )
    db.add(tech)
    db.flush()

    # Add some history entries
    for i, (old, new, source) in enumerate([
        (None, "In Training", StatusChangeSource.SYSTEM),
        ("In Training", "Missing Cert", StatusChangeSource.EVENT_TRIGGERED),
        ("Missing Cert", "In Training", StatusChangeSource.AUTO_COMPUTED),
        ("In Training", "Ready Now", StatusChangeSource.MANUAL_OVERRIDE),
    ]):
        entry = DeployabilityStatusHistory(
            technician_id=tech.id,
            old_status=old,
            new_status=new,
            source=source,
            reason=f"Test entry {i}",
            readiness_score_at_change=50.0 + i * 10,
        )
        db.add(entry)

    db.commit()
    db.refresh(tech)
    return tech


# ---------------------------------------------------------------------------
# GET /api/deployability/{id}/status
# ---------------------------------------------------------------------------

class TestGetDeployabilityStatus:
    @patch("app.routers.deployability.evaluate_technician_readiness")
    def test_returns_status_with_readiness(self, mock_eval, client, sample_tech):
        mock_result = MagicMock()
        mock_result.overall_score = 78.5
        mock_result.suggested_status = "Ready Now"
        mock_result.status_change_recommended = False
        mock_result.status_change_reason = None
        mock_result.dimension_scores = {
            "certification": 85.0,
            "training": 72.0,
            "assignment_history": 65.0,
            "documentation": 90.0,
        }
        mock_result.certification.summary = "3/3 active"
        mock_result.training.summary = "2 advanced"
        mock_result.assignment_history.summary = "5 total"
        mock_result.documentation.summary = "4/4 verified"
        mock_eval.return_value = mock_result

        resp = client.get(
            f"/api/deployability/{sample_tech.id}/status",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["current_status"] == "Ready Now"
        assert data["is_locked"] is False
        assert data["is_manual_override"] is False
        assert data["readiness"]["overall_score"] == 78.5
        assert data["readiness"]["dimension_scores"]["certification"] == 85.0
        assert data["status_divergent"] is False
        assert data["technician_name"] == "Jane Doe"

    @patch("app.routers.deployability.evaluate_technician_readiness")
    def test_flags_divergent_status(self, mock_eval, client, sample_tech, db):
        # Set status to In Training while readiness suggests Ready Now
        sample_tech.deployability_status = DeployabilityStatus.IN_TRAINING
        db.commit()

        mock_result = MagicMock()
        mock_result.overall_score = 82.0
        mock_result.suggested_status = "Ready Now"
        mock_result.status_change_recommended = True
        mock_result.status_change_reason = "All requirements met"
        mock_result.dimension_scores = {
            "certification": 90.0, "training": 80.0,
            "assignment_history": 70.0, "documentation": 85.0,
        }
        mock_result.certification.summary = "OK"
        mock_result.training.summary = "OK"
        mock_result.assignment_history.summary = "OK"
        mock_result.documentation.summary = "OK"
        mock_eval.return_value = mock_result

        resp = client.get(
            f"/api/deployability/{sample_tech.id}/status",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status_divergent"] is True
        assert data["auto_computed_status"] == "Ready Now"
        assert data["current_status"] == "In Training"

    def test_404_for_missing_technician(self, client):
        resp = client.get(
            f"/api/deployability/{uuid.uuid4()}/status",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404

    @patch("app.routers.deployability.evaluate_technician_readiness")
    def test_shows_manual_override_indicator(self, mock_eval, client, locked_tech):
        mock_eval.side_effect = Exception("skip readiness")

        resp = client.get(
            f"/api/deployability/{locked_tech.id}/status",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_locked"] is True
        assert data["is_manual_override"] is True
        assert data["locked_by"] == "ops-admin"
        assert data["lock_reason"] == "Compliance hold"

    @patch("app.routers.deployability.evaluate_technician_readiness")
    def test_includes_last_change(self, mock_eval, client, tech_with_history):
        mock_eval.side_effect = Exception("skip")

        resp = client.get(
            f"/api/deployability/{tech_with_history.id}/status",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["last_change"] is not None
        assert data["last_change"]["new_status"] == "Ready Now"
        assert data["last_change"]["source"] == "manual_override"

    @patch("app.routers.deployability.evaluate_technician_readiness")
    def test_technician_can_read_own_status(self, mock_eval, client, sample_tech):
        mock_eval.side_effect = Exception("skip")

        resp = client.get(
            f"/api/deployability/{sample_tech.id}/status",
            headers=TECH_HEADERS,
        )
        # Technicians can read status (not write)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/deployability/{id}/override
# ---------------------------------------------------------------------------

class TestManualOverride:
    @patch("app.routers.deployability.dispatch_event_safe")
    @patch("app.routers.deployability.evaluate_technician_readiness")
    def test_override_changes_status_and_creates_history(
        self, mock_eval, mock_dispatch, client, sample_tech, db
    ):
        mock_eval.return_value = MagicMock(
            overall_score=75.0,
            dimension_scores={"certification": 90.0},
        )

        resp = client.post(
            f"/api/deployability/{sample_tech.id}/override",
            headers=OPS_HEADERS,
            json={
                "new_status": "Missing Cert",
                "reason": "Cert expired during review",
                "lock_status": True,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is True
        assert data["old_status"] == "Ready Now"
        assert data["new_status"] == "Missing Cert"
        assert data["locked"] is True

        # Verify DB was updated
        db.refresh(sample_tech)
        assert sample_tech.deployability_status == DeployabilityStatus.MISSING_CERT
        assert sample_tech.deployability_locked is True

        # Verify history entry was created
        history = (
            db.query(DeployabilityStatusHistory)
            .filter(DeployabilityStatusHistory.technician_id == sample_tech.id)
            .all()
        )
        assert len(history) == 1
        assert history[0].old_status == "Ready Now"
        assert history[0].new_status == "Missing Cert"
        assert history[0].source == StatusChangeSource.MANUAL_OVERRIDE
        assert history[0].reason == "Cert expired during review"
        assert history[0].actor_id == "ops-user-1"

        # Verify event dispatched
        mock_dispatch.assert_called_once()

    def test_override_rejects_invalid_status(self, client, sample_tech):
        resp = client.post(
            f"/api/deployability/{sample_tech.id}/override",
            headers=OPS_HEADERS,
            json={"new_status": "Not A Real Status", "reason": "test"},
        )
        assert resp.status_code == 400

    def test_override_requires_ops_role(self, client, sample_tech):
        resp = client.post(
            f"/api/deployability/{sample_tech.id}/override",
            headers=TECH_HEADERS,
            json={"new_status": "Ready Now", "reason": "test"},
        )
        assert resp.status_code == 403

    def test_partner_cannot_override(self, client, sample_tech):
        resp = client.post(
            f"/api/deployability/{sample_tech.id}/override",
            headers=PARTNER_HEADERS,
            json={"new_status": "Ready Now", "reason": "test"},
        )
        assert resp.status_code == 403

    @patch("app.routers.deployability.evaluate_technician_readiness")
    def test_override_same_status_noop(self, mock_eval, client, sample_tech):
        resp = client.post(
            f"/api/deployability/{sample_tech.id}/override",
            headers=OPS_HEADERS,
            json={"new_status": "Ready Now", "reason": "No change needed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is False

    def test_override_404_for_missing_tech(self, client):
        resp = client.post(
            f"/api/deployability/{uuid.uuid4()}/override",
            headers=OPS_HEADERS,
            json={"new_status": "Ready Now", "reason": "test"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/deployability/{id}/unlock
# ---------------------------------------------------------------------------

class TestUnlock:
    def test_unlock_removes_lock(self, client, locked_tech, db):
        resp = client.post(
            f"/api/deployability/{locked_tech.id}/unlock",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["unlocked"] is True

        db.refresh(locked_tech)
        assert locked_tech.deployability_locked is False
        assert locked_tech.inactive_locked_by is None

    def test_unlock_noop_if_not_locked(self, client, sample_tech):
        resp = client.post(
            f"/api/deployability/{sample_tech.id}/unlock",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        assert resp.json()["unlocked"] is False

    def test_unlock_requires_ops_role(self, client, locked_tech):
        resp = client.post(
            f"/api/deployability/{locked_tech.id}/unlock",
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/deployability/{id}/history
# ---------------------------------------------------------------------------

class TestStatusHistory:
    def test_returns_paginated_history(self, client, tech_with_history):
        resp = client.get(
            f"/api/deployability/{tech_with_history.id}/history",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert len(data["history"]) == 4
        # Should be newest first
        assert data["history"][0]["new_status"] == "Ready Now"
        assert data["history"][0]["source"] == "manual_override"
        assert data["history"][-1]["new_status"] == "In Training"
        assert data["history"][-1]["source"] == "system"

    def test_history_pagination_with_limit(self, client, tech_with_history):
        resp = client.get(
            f"/api/deployability/{tech_with_history.id}/history?limit=2&offset=0",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert len(data["history"]) == 2

    def test_history_404_for_missing_tech(self, client):
        resp = client.get(
            f"/api/deployability/{uuid.uuid4()}/history",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/deployability/summary
# ---------------------------------------------------------------------------

class TestDeployabilitySummary:
    def test_summary_returns_correct_counts(self, client, sample_tech, locked_tech, db):
        # We have 2 techs: sample_tech (Ready Now, unlocked) and locked_tech (Inactive, locked)
        resp = client.get(
            "/api/deployability/summary",
            headers=OPS_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_technicians"] == 2
        assert data["status_counts"]["Ready Now"] == 1
        assert data["status_counts"]["Inactive"] == 1
        assert data["locked_count"] == 1

    def test_summary_requires_ops_role(self, client):
        resp = client.get(
            "/api/deployability/summary",
            headers=TECH_HEADERS,
        )
        assert resp.status_code == 403

    def test_partner_cannot_access_summary(self, client):
        resp = client.get(
            "/api/deployability/summary",
            headers=PARTNER_HEADERS,
        )
        assert resp.status_code == 403
