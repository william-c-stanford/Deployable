"""Tests for manual Inactive override lock/unlock logic.

Tests cover:
1. POST /{tech_id}/override/inactive — lock technician as Inactive
2. POST /{tech_id}/override/reactivate — unlock and reactivate
3. GET /{tech_id}/override/status — query override status and audit trail
4. Role-based access control (ops-only for lock/unlock)
5. Idempotent locking behavior
6. Readiness engine respects the lock (no auto-status changes)
7. Reactivation with custom target status
8. Audit log entries created for both lock and unlock
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from unittest.mock import patch

import app.models as _models  # noqa: F401 — ensure all models registered with Base
from app.database import Base, get_db
from app.main import app
from app.models.technician import (
    Technician,
    CareerStage,
    DeployabilityStatus,
)
from app.models.audit import AuditLog


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
def sample_technician(db) -> Technician:
    tech = Technician(
        id=uuid.uuid4(),
        first_name="Jane",
        last_name="Smith",
        email="jane.smith@example.com",
        career_stage=CareerStage.AWAITING_ASSIGNMENT,
        deployability_status=DeployabilityStatus.READY_NOW,
        deployability_locked=False,
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    return tech


# ---------------------------------------------------------------------------
# Tests: Set Inactive Override
# ---------------------------------------------------------------------------

class TestSetInactiveOverride:
    """Tests for POST /api/technicians/{tech_id}/override/inactive"""

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_lock_inactive_success(self, mock_dispatch, client, sample_technician, db):
        """Ops user can lock a technician as Inactive."""
        resp = client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "Extended leave of absence"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "locked_inactive"
        assert data["previous_status"] == "Ready Now"
        assert data["new_status"] == "Inactive"
        assert data["deployability_locked"] is True
        assert data["inactive_locked_by"] == "ops-user-1"
        assert data["inactive_lock_reason"] == "Extended leave of absence"
        assert data["inactive_locked_at"] is not None

        # Verify DB state
        db.refresh(sample_technician)
        assert sample_technician.deployability_status == DeployabilityStatus.INACTIVE
        assert sample_technician.deployability_locked is True
        assert sample_technician.inactive_lock_reason == "Extended leave of absence"

        # Verify event dispatched
        mock_dispatch.assert_called_once()
        payload = mock_dispatch.call_args[0][0]
        assert payload.data["source"] == "manual_inactive_override"
        assert payload.data["locked"] is True

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_lock_inactive_idempotent(self, mock_dispatch, client, sample_technician, db):
        """Locking an already-locked-inactive technician returns idempotent response."""
        # First lock
        client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "First lock"},
            headers=OPS_HEADERS,
        )
        mock_dispatch.reset_mock()

        # Second lock — should be idempotent
        resp = client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "Second lock attempt"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "already_locked_inactive"
        assert data["inactive_lock_reason"] == "First lock"  # Original reason preserved

        # No new event dispatched for idempotent call
        mock_dispatch.assert_not_called()

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_lock_inactive_creates_audit_log(self, mock_dispatch, client, sample_technician, db):
        """Locking creates an audit log entry."""
        client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "Performance concern"},
            headers=OPS_HEADERS,
        )

        audits = db.query(AuditLog).filter(
            AuditLog.entity_id == str(sample_technician.id),
            AuditLog.action == "manual_inactive_override",
        ).all()
        assert len(audits) == 1
        assert audits[0].user_id == "ops-user-1"
        assert audits[0].details["reason"] == "Performance concern"
        assert audits[0].details["previous_status"] == "Ready Now"
        assert audits[0].details["locked"] is True

    def test_lock_inactive_requires_ops_role(self, client, sample_technician):
        """Technicians and partners cannot lock Inactive."""
        for headers in [TECH_HEADERS, PARTNER_HEADERS]:
            resp = client.post(
                f"/api/technicians/{sample_technician.id}/override/inactive",
                json={"reason": "Should fail"},
                headers=headers,
            )
            assert resp.status_code == 403

    def test_lock_inactive_requires_reason(self, client, sample_technician):
        """Lock request must include a reason."""
        resp = client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 422

    def test_lock_inactive_reason_min_length(self, client, sample_technician):
        """Reason must be at least 3 characters."""
        resp = client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "ab"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 422

    def test_lock_nonexistent_technician(self, client):
        """Locking a non-existent technician returns 404."""
        fake_id = uuid.uuid4()
        resp = client.post(
            f"/api/technicians/{fake_id}/override/inactive",
            json={"reason": "Test reason"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Reactivate / Unlock
# ---------------------------------------------------------------------------

class TestReactivate:
    """Tests for POST /api/technicians/{tech_id}/override/reactivate"""

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_reactivate_success_default_status(self, mock_dispatch, client, sample_technician, db):
        """Reactivating defaults to Ready Now status."""
        # Lock first
        client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "Temp deactivation"},
            headers=OPS_HEADERS,
        )
        mock_dispatch.reset_mock()

        # Reactivate
        resp = client.post(
            f"/api/technicians/{sample_technician.id}/override/reactivate",
            json={},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "unlocked_reactivated"
        assert data["previous_status"] == "Inactive"
        assert data["new_status"] == "Ready Now"
        assert data["deployability_locked"] is False
        assert data["inactive_locked_at"] is None
        assert data["inactive_locked_by"] is None
        assert data["inactive_lock_reason"] is None

        # Verify DB state
        db.refresh(sample_technician)
        assert sample_technician.deployability_status == DeployabilityStatus.READY_NOW
        assert sample_technician.deployability_locked is False
        assert sample_technician.inactive_locked_at is None

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_reactivate_with_custom_status(self, mock_dispatch, client, sample_technician, db):
        """Reactivation can specify a custom target status."""
        # Lock first
        client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "Was on leave"},
            headers=OPS_HEADERS,
        )
        mock_dispatch.reset_mock()

        # Reactivate with custom target
        resp = client.post(
            f"/api/technicians/{sample_technician.id}/override/reactivate",
            json={"target_status": "In Training", "reason": "Needs refresher training"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_status"] == "In Training"

        db.refresh(sample_technician)
        assert sample_technician.deployability_status == DeployabilityStatus.IN_TRAINING

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_reactivate_creates_audit_log(self, mock_dispatch, client, sample_technician, db):
        """Reactivation creates an audit log entry."""
        client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "Deactivated"},
            headers=OPS_HEADERS,
        )
        client.post(
            f"/api/technicians/{sample_technician.id}/override/reactivate",
            json={"reason": "Back from leave"},
            headers=OPS_HEADERS,
        )

        audits = db.query(AuditLog).filter(
            AuditLog.entity_id == str(sample_technician.id),
            AuditLog.action == "manual_inactive_unlock",
        ).all()
        assert len(audits) == 1
        assert audits[0].details["was_locked_reason"] == "Deactivated"
        assert audits[0].details["reactivation_reason"] == "Back from leave"

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_reactivate_not_locked_returns_400(self, mock_dispatch, client, sample_technician):
        """Reactivating a non-locked technician returns 400."""
        resp = client.post(
            f"/api/technicians/{sample_technician.id}/override/reactivate",
            json={},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 400
        assert "not manually locked" in resp.json()["detail"]

    def test_reactivate_requires_ops_role(self, client, sample_technician):
        """Only ops can reactivate."""
        for headers in [TECH_HEADERS, PARTNER_HEADERS]:
            resp = client.post(
                f"/api/technicians/{sample_technician.id}/override/reactivate",
                json={},
                headers=headers,
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: Override Status Query
# ---------------------------------------------------------------------------

class TestOverrideStatus:
    """Tests for GET /api/technicians/{tech_id}/override/status"""

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_get_override_status_locked(self, mock_dispatch, client, sample_technician, db):
        """Override status endpoint shows lock details when locked."""
        client.post(
            f"/api/technicians/{sample_technician.id}/override/inactive",
            json={"reason": "Compliance hold"},
            headers=OPS_HEADERS,
        )

        resp = client.get(
            f"/api/technicians/{sample_technician.id}/override/status",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployability_locked"] is True
        assert data["inactive_locked_by"] == "ops-user-1"
        assert data["inactive_lock_reason"] == "Compliance hold"
        assert len(data["override_history"]) == 1
        assert data["override_history"][0]["action"] == "manual_inactive_override"

    def test_get_override_status_unlocked(self, client, sample_technician):
        """Override status endpoint shows unlocked state."""
        resp = client.get(
            f"/api/technicians/{sample_technician.id}/override/status",
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployability_locked"] is False
        assert data["inactive_locked_by"] is None
        assert len(data["override_history"]) == 0


# ---------------------------------------------------------------------------
# Tests: Readiness Engine Respects Lock
# ---------------------------------------------------------------------------

class TestReadinessRespectsLock:
    """Verify that readiness evaluation does not change a locked status."""

    def test_locked_technician_no_status_suggestion(self, db, sample_technician):
        """Readiness _determine_suggested_status returns no change for locked techs."""
        from app.services.readiness import _determine_suggested_status
        from app.services.readiness import (
            CertificationReadiness,
            TrainingReadiness,
            AssignmentHistoryReadiness,
            DocumentationReadiness,
        )

        sample_technician.deployability_status = DeployabilityStatus.INACTIVE
        sample_technician.deployability_locked = True
        db.flush()

        suggested, should_change, reason = _determine_suggested_status(
            sample_technician,
            CertificationReadiness(score=90.0, active_certs=3, total_certs=3),
            TrainingReadiness(score=85.0),
            AssignmentHistoryReadiness(score=70.0),
            DocumentationReadiness(score=100.0),
            overall_score=85.0,
        )

        assert should_change is False
        assert suggested == "Inactive"
        assert reason is None


# ---------------------------------------------------------------------------
# Tests: Full Lock → Unlock Lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """End-to-end lifecycle tests for lock → query → unlock → query."""

    @patch("app.routers.technicians.dispatch_event_safe")
    def test_full_lock_unlock_lifecycle(self, mock_dispatch, client, sample_technician, db):
        """Full lifecycle: lock → check status → unlock → check status."""
        tech_id = str(sample_technician.id)

        # 1. Lock
        resp = client.post(
            f"/api/technicians/{tech_id}/override/inactive",
            json={"reason": "Company restructuring"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "locked_inactive"

        # 2. Verify locked status
        resp = client.get(f"/api/technicians/{tech_id}/override/status", headers=OPS_HEADERS)
        assert resp.json()["deployability_locked"] is True

        # 3. Verify technician detail shows locked
        resp = client.get(f"/api/technicians/{tech_id}", headers=OPS_HEADERS)
        assert resp.json()["deployability_locked"] is True
        assert resp.json()["deployability_status"] == "Inactive"

        # 4. Unlock
        resp = client.post(
            f"/api/technicians/{tech_id}/override/reactivate",
            json={"reason": "Restructuring complete"},
            headers=OPS_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "unlocked_reactivated"

        # 5. Verify unlocked status
        resp = client.get(f"/api/technicians/{tech_id}/override/status", headers=OPS_HEADERS)
        data = resp.json()
        assert data["deployability_locked"] is False
        assert data["inactive_locked_by"] is None
        assert len(data["override_history"]) == 2  # Both lock and unlock entries

        # 6. Verify technician detail shows unlocked
        resp = client.get(f"/api/technicians/{tech_id}", headers=OPS_HEADERS)
        assert resp.json()["deployability_locked"] is False
        assert resp.json()["deployability_status"] == "Ready Now"
