"""Tests for the 48-hour partner advance visibility scheduler and API.

Covers:
  - Celery task: scan_upcoming_assignments creates correct notifications
  - Deduplication: re-running the scan doesn't create duplicate notifications
  - Partner API: list, confirm, dismiss notifications
  - Role scoping: partners only see their own notifications
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models.technician import Technician, DeployabilityStatus, CareerStage
from app.models.user import Partner
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.assignment import Assignment, AssignmentType
from app.models.partner_notification import (
    PartnerNotification,
    NotificationType,
    NotificationStatus,
)


# ---------------------------------------------------------------------------
# Fixtures (in-memory SQLite for fast tests)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite:///./test_partner_visibility.db"


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    # Import all models so they register with Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    import os
    try:
        os.remove("./test_partner_visibility.db")
    except OSError:
        pass


@pytest.fixture
def db_session(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def seed_data(db_session):
    """Create a partner, project, technician, and assignments for testing."""
    partner = Partner(
        id=uuid.uuid4(),
        name="Lumen Technologies",
        contact_email="ops@lumen.com",
    )
    db_session.add(partner)
    db_session.flush()

    project = Project(
        id=uuid.uuid4(),
        name="Metro Fiber Expansion - Denver",
        partner_id=partner.id,
        status=ProjectStatus.ACTIVE,
        location_region="Mountain West",
        start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=90),
    )
    db_session.add(project)
    db_session.flush()

    role = ProjectRole(
        id=uuid.uuid4(),
        project_id=project.id,
        role_name="Lead Splicer",
        quantity=2,
        filled=1,
    )
    db_session.add(role)
    db_session.flush()

    tech = Technician(
        id=uuid.uuid4(),
        first_name="Carlos",
        last_name="Mendez",
        email=f"carlos.mendez.{uuid.uuid4().hex[:6]}@example.com",
        career_stage=CareerStage.DEPLOYED,
        deployability_status=DeployabilityStatus.CURRENTLY_ASSIGNED,
    )
    db_session.add(tech)
    db_session.flush()

    # Assignment starting tomorrow (within 48h)
    assignment_starting = Assignment(
        id=uuid.uuid4(),
        technician_id=tech.id,
        role_id=role.id,
        start_date=date.today() + timedelta(days=1),
        end_date=date.today() + timedelta(days=30),
        status="Active",
        partner_confirmed_start=False,
        partner_confirmed_end=False,
    )
    db_session.add(assignment_starting)

    # Assignment ending tomorrow (within 48h)
    tech2 = Technician(
        id=uuid.uuid4(),
        first_name="Sarah",
        last_name="Kim",
        email=f"sarah.kim.{uuid.uuid4().hex[:6]}@example.com",
        career_stage=CareerStage.DEPLOYED,
        deployability_status=DeployabilityStatus.CURRENTLY_ASSIGNED,
    )
    db_session.add(tech2)
    db_session.flush()

    assignment_ending = Assignment(
        id=uuid.uuid4(),
        technician_id=tech2.id,
        role_id=role.id,
        start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=1),
        status="Active",
        partner_confirmed_start=True,
        partner_confirmed_end=False,
    )
    db_session.add(assignment_ending)

    # Assignment far in the future (should NOT trigger notification)
    assignment_future = Assignment(
        id=uuid.uuid4(),
        technician_id=tech.id,
        role_id=role.id,
        start_date=date.today() + timedelta(days=30),
        end_date=date.today() + timedelta(days=60),
        status="Active",
        partner_confirmed_start=False,
        partner_confirmed_end=False,
    )
    db_session.add(assignment_future)

    db_session.commit()

    return {
        "partner": partner,
        "project": project,
        "role": role,
        "tech": tech,
        "tech2": tech2,
        "assignment_starting": assignment_starting,
        "assignment_ending": assignment_ending,
        "assignment_future": assignment_future,
    }


# ---------------------------------------------------------------------------
# Celery task tests
# ---------------------------------------------------------------------------


class TestScanUpcomingAssignments:
    """Test the scan_upcoming_assignments Celery task."""

    def test_creates_starting_notification(self, db_session, seed_data):
        """Should create a notification for an assignment starting within 48h."""
        with patch("app.workers.tasks.partner_visibility.SessionLocal", return_value=db_session):
            from app.workers.tasks.partner_visibility import scan_upcoming_assignments

            result = scan_upcoming_assignments(None)

        assert result["status"] == "completed"
        assert result["starting_notifications"] >= 1

        # Verify notification was created
        notifications = db_session.query(PartnerNotification).filter(
            PartnerNotification.assignment_id == seed_data["assignment_starting"].id,
            PartnerNotification.notification_type == NotificationType.ASSIGNMENT_STARTING,
        ).all()
        assert len(notifications) == 1
        assert notifications[0].partner_id == seed_data["partner"].id
        assert notifications[0].status == NotificationStatus.PENDING

    def test_creates_ending_notification(self, db_session, seed_data):
        """Should create a notification for an assignment ending within 48h."""
        notifications = db_session.query(PartnerNotification).filter(
            PartnerNotification.assignment_id == seed_data["assignment_ending"].id,
            PartnerNotification.notification_type == NotificationType.ASSIGNMENT_ENDING,
        ).all()
        assert len(notifications) == 1
        assert notifications[0].partner_id == seed_data["partner"].id

    def test_no_notification_for_future_assignment(self, db_session, seed_data):
        """Should NOT create notifications for assignments far in the future."""
        notifications = db_session.query(PartnerNotification).filter(
            PartnerNotification.assignment_id == seed_data["assignment_future"].id,
        ).all()
        assert len(notifications) == 0

    def test_deduplication(self, db_session, seed_data):
        """Re-running the scan should not create duplicate notifications."""
        with patch("app.workers.tasks.partner_visibility.SessionLocal", return_value=db_session):
            from app.workers.tasks.partner_visibility import scan_upcoming_assignments

            result = scan_upcoming_assignments(None)

        # All should be skipped as duplicates
        assert result["skipped_duplicates"] >= 2
        assert result["starting_notifications"] == 0
        assert result["ending_notifications"] == 0

    def test_notification_message_content(self, db_session, seed_data):
        """Notification message should contain technician name and project name."""
        notification = db_session.query(PartnerNotification).filter(
            PartnerNotification.assignment_id == seed_data["assignment_starting"].id,
        ).first()
        assert notification is not None
        assert "Carlos Mendez" in notification.title
        assert "Metro Fiber Expansion" in notification.title


# ---------------------------------------------------------------------------
# Notification model tests
# ---------------------------------------------------------------------------


class TestPartnerNotificationModel:
    """Test the PartnerNotification model."""

    def test_notification_creation(self, db_session, seed_data):
        """Can create a notification record."""
        n = PartnerNotification(
            partner_id=seed_data["partner"].id,
            assignment_id=seed_data["assignment_starting"].id,
            project_id=seed_data["project"].id,
            technician_id=seed_data["tech"].id,
            notification_type=NotificationType.ASSIGNMENT_STARTING,
            status=NotificationStatus.PENDING,
            title="Test notification",
            message="Test message",
            target_date=datetime.now(timezone.utc),
        )
        db_session.add(n)
        db_session.flush()
        assert n.id is not None

    def test_notification_confirm(self, db_session, seed_data):
        """Can confirm a notification."""
        n = db_session.query(PartnerNotification).filter(
            PartnerNotification.status == NotificationStatus.PENDING,
        ).first()
        if n:
            n.status = NotificationStatus.CONFIRMED
            n.confirmed_at = datetime.now(timezone.utc)
            n.confirmed_by = "test_user"
            db_session.flush()
            assert n.status == NotificationStatus.CONFIRMED


# ---------------------------------------------------------------------------
# API endpoint tests (using FastAPI TestClient)
# ---------------------------------------------------------------------------


class TestPartnerAPI:
    """Test the partner-facing API endpoints."""

    @pytest.fixture
    def client(self, db_session):
        """Create a test client with DB session override."""
        from fastapi.testclient import TestClient
        from app.main import app

        def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()

    def test_get_upcoming_assignments_partner_role(self, client, seed_data):
        """Partner can list upcoming assignments."""
        response = client.get(
            "/api/partner/upcoming-assignments",
            headers={
                "X-Demo-Role": "partner",
                "X-Demo-User-Id": str(seed_data["partner"].id),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_notifications(self, client, seed_data):
        """Partner can list their notifications."""
        response = client.get(
            "/api/partner/notifications",
            headers={
                "X-Demo-Role": "partner",
                "X-Demo-User-Id": str(seed_data["partner"].id),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
        assert "total" in data
        assert "pending_count" in data

    def test_get_pending_notifications(self, client, seed_data):
        """Partner can list only pending notifications."""
        response = client.get(
            "/api/partner/notifications/pending",
            headers={
                "X-Demo-Role": "partner",
                "X-Demo-User-Id": str(seed_data["partner"].id),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data

    def test_confirm_notification(self, client, db_session, seed_data):
        """Partner can confirm a notification."""
        # Find a pending notification
        notification = db_session.query(PartnerNotification).filter(
            PartnerNotification.partner_id == seed_data["partner"].id,
            PartnerNotification.status == NotificationStatus.PENDING,
        ).first()

        if notification:
            response = client.post(
                f"/api/partner/notifications/{notification.id}/confirm",
                json={"confirmed_by": "Test User"},
                headers={
                    "X-Demo-Role": "partner",
                    "X-Demo-User-Id": str(seed_data["partner"].id),
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "confirmed"

    def test_dismiss_notification(self, client, db_session, seed_data):
        """Partner can dismiss a notification."""
        notification = db_session.query(PartnerNotification).filter(
            PartnerNotification.partner_id == seed_data["partner"].id,
            PartnerNotification.status == NotificationStatus.PENDING,
        ).first()

        if notification:
            response = client.post(
                f"/api/partner/notifications/{notification.id}/dismiss",
                json={"reason": "Already aware"},
                headers={
                    "X-Demo-Role": "partner",
                    "X-Demo-User-Id": str(seed_data["partner"].id),
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "dismissed"

    def test_unauthorized_technician_access(self, client):
        """Technicians should not access partner endpoints."""
        response = client.get(
            "/api/partner/notifications",
            headers={"X-Demo-Role": "technician", "X-Demo-User-Id": "tech-1"},
        )
        assert response.status_code == 403

    def test_ops_can_access_partner_endpoints(self, client):
        """Ops users can access partner endpoints."""
        response = client.get(
            "/api/partner/notifications",
            headers={"X-Demo-Role": "ops", "X-Demo-User-Id": "ops-1"},
        )
        assert response.status_code == 200

    def test_scan_now_ops_only(self, client):
        """Only ops can trigger manual scan."""
        # Partner should be forbidden
        response = client.post(
            "/api/partner/scan-now",
            headers={"X-Demo-Role": "partner", "X-Demo-User-Id": "partner-1"},
        )
        assert response.status_code == 403

    def test_notification_not_found(self, client, seed_data):
        """Should return 404 for non-existent notification."""
        fake_id = str(uuid.uuid4())
        response = client.post(
            f"/api/partner/notifications/{fake_id}/confirm",
            json={},
            headers={
                "X-Demo-Role": "partner",
                "X-Demo-User-Id": str(seed_data["partner"].id),
            },
        )
        assert response.status_code == 404
