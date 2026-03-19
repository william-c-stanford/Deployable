"""Tests for the Milestone Badge Auto-Generation Engine.

Tests cover:
  1. Hours-based milestone evaluation using coarse Technician.total_approved_hours
  2. Project-count milestone evaluation using coarse Technician.total_project_count
  3. Role-diversity milestone evaluation using distinct role counts from assignments
  4. Certification-count milestone evaluation using active cert counts
  5. Badge sync creates new MilestoneBadge records for newly earned milestones
  6. Badge sync is idempotent (no duplicates on repeated calls)
  7. Progress reporting with percentages for partial achievement
  8. Tier assignment (bronze/silver/gold) matches threshold definitions
  9. Batch sync across all technicians
  10. Edge cases: zero data, exact threshold, just below threshold
"""

import uuid
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

import app.models as _models  # noqa: F401 — ensure all models registered
from app.database import Base
from app.models.technician import (
    Technician,
    TechnicianCertification,
    TechnicianSkill,
    CareerStage,
    DeployabilityStatus,
    ProficiencyLevel,
    CertStatus,
)
from app.models.badge import MilestoneBadge, MilestoneType
from app.models.assignment import Assignment, AssignmentType
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.user import Partner

from app.services.milestone_badge_engine import (
    evaluate_milestones,
    sync_milestone_badges,
    sync_all_technicians,
    get_milestone_progress,
    HOURS_MILESTONES,
    PROJECT_MILESTONES,
    ROLE_DIVERSITY_MILESTONES,
    CERT_MILESTONES,
    MILESTONE_CATALOG,
    _get_coarse_hours,
    _get_coarse_project_count,
    _get_active_cert_count,
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


def _make_technician(
    db: Session,
    first_name: str = "Test",
    last_name: str = "Tech",
    email: str = None,
    total_approved_hours: float = 0.0,
    total_project_count: int = 0,
    hire_date: date = None,
) -> Technician:
    """Helper to create and persist a technician."""
    tech = Technician(
        first_name=first_name,
        last_name=last_name,
        email=email or f"{first_name.lower()}.{last_name.lower()}@test.com",
        career_stage=CareerStage.DEPLOYED,
        deployability_status=DeployabilityStatus.READY_NOW,
        total_approved_hours=total_approved_hours,
        total_project_count=total_project_count,
        hire_date=hire_date,
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    return tech


def _add_certs(db: Session, tech: Technician, count: int, status=CertStatus.ACTIVE):
    """Helper to add N certifications."""
    for i in range(count):
        cert = TechnicianCertification(
            technician_id=tech.id,
            cert_name=f"Cert-{i+1}",
            status=status,
            issue_date=date(2023, 1, 1),
        )
        db.add(cert)
    db.commit()
    db.refresh(tech)


def _add_role_assignments(db: Session, tech: Technician, role_names: list[str]):
    """Helper to create assignments with distinct role names."""
    partner = Partner(name="Test Partner")
    db.add(partner)
    db.flush()

    project = Project(
        name="Test Project",
        partner_id=partner.id,
        status=ProjectStatus.ACTIVE,
        location_region="Northeast",
        start_date=date(2024, 1, 1),
    )
    db.add(project)
    db.flush()

    for role_name in role_names:
        role = ProjectRole(
            project_id=project.id,
            role_name=role_name,
        )
        db.add(role)
        db.flush()

        assignment = Assignment(
            technician_id=tech.id,
            role_id=role.id,
            start_date=date(2024, 1, 1),
            status="Completed",
        )
        db.add(assignment)

    db.commit()
    db.refresh(tech)


# ---------------------------------------------------------------------------
# Tests: Coarse data extraction helpers
# ---------------------------------------------------------------------------

class TestCoarseDataHelpers:
    def test_get_coarse_hours_zero(self, db):
        tech = _make_technician(db, total_approved_hours=0.0)
        assert _get_coarse_hours(tech) == 0.0

    def test_get_coarse_hours_value(self, db):
        tech = _make_technician(db, total_approved_hours=1234.5)
        assert _get_coarse_hours(tech) == 1234.5

    def test_get_coarse_hours_none(self, db):
        tech = _make_technician(db)
        tech.total_approved_hours = None
        assert _get_coarse_hours(tech) == 0.0

    def test_get_coarse_project_count_zero(self, db):
        tech = _make_technician(db, total_project_count=0)
        assert _get_coarse_project_count(tech) == 0

    def test_get_coarse_project_count_value(self, db):
        tech = _make_technician(db, total_project_count=42)
        assert _get_coarse_project_count(tech) == 42

    def test_get_active_cert_count_empty(self, db):
        tech = _make_technician(db)
        assert _get_active_cert_count(tech) == 0

    def test_get_active_cert_count_mixed(self, db):
        tech = _make_technician(db)
        _add_certs(db, tech, 3, CertStatus.ACTIVE)
        _add_certs(db, tech, 2, CertStatus.EXPIRED)
        db.refresh(tech)
        assert _get_active_cert_count(tech) == 3


# ---------------------------------------------------------------------------
# Tests: Hours milestone evaluation
# ---------------------------------------------------------------------------

class TestHoursMilestones:
    def test_no_hours_earns_nothing(self, db):
        tech = _make_technician(db, total_approved_hours=0)
        report = evaluate_milestones(db, tech)
        hours_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.HOURS_THRESHOLD
        ]
        assert len(hours_earned) == 0

    def test_100_hours_earns_first_badge(self, db):
        tech = _make_technician(db, total_approved_hours=100)
        report = evaluate_milestones(db, tech)
        hours_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.HOURS_THRESHOLD
        ]
        assert len(hours_earned) == 1
        assert hours_earned[0].threshold.badge_name == "First 100 Hours"
        assert hours_earned[0].threshold.tier == 1

    def test_99_hours_does_not_earn(self, db):
        tech = _make_technician(db, total_approved_hours=99.9)
        report = evaluate_milestones(db, tech)
        hours_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.HOURS_THRESHOLD
        ]
        assert len(hours_earned) == 0

    def test_5000_hours_earns_multiple(self, db):
        tech = _make_technician(db, total_approved_hours=5000)
        report = evaluate_milestones(db, tech)
        hours_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.HOURS_THRESHOLD
        ]
        earned_names = {e.threshold.badge_name for e in hours_earned}
        assert "First 100 Hours" in earned_names
        assert "500 Hour Veteran" in earned_names
        assert "1000 Hour Club" in earned_names
        assert "5000 Hour Legend" in earned_names
        assert "10000 Hour Master" not in earned_names

    def test_10000_hours_earns_all(self, db):
        tech = _make_technician(db, total_approved_hours=10000)
        report = evaluate_milestones(db, tech)
        hours_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.HOURS_THRESHOLD
        ]
        assert len(hours_earned) == len(HOURS_MILESTONES)

    def test_tier_assignment_for_hours(self, db):
        tech = _make_technician(db, total_approved_hours=10000)
        report = evaluate_milestones(db, tech)
        hours_earned = {
            e.threshold.badge_name: e.threshold.tier
            for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.HOURS_THRESHOLD
        }
        assert hours_earned["First 100 Hours"] == 1  # bronze
        assert hours_earned["1000 Hour Club"] == 2  # silver
        assert hours_earned["5000 Hour Legend"] == 3  # gold


# ---------------------------------------------------------------------------
# Tests: Project milestones
# ---------------------------------------------------------------------------

class TestProjectMilestones:
    def test_no_projects_earns_nothing(self, db):
        tech = _make_technician(db, total_project_count=0)
        report = evaluate_milestones(db, tech)
        proj_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.PROJECTS_COMPLETED
        ]
        assert len(proj_earned) == 0

    def test_1_project_earns_first(self, db):
        tech = _make_technician(db, total_project_count=1)
        report = evaluate_milestones(db, tech)
        proj_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.PROJECTS_COMPLETED
        ]
        assert len(proj_earned) == 1
        assert proj_earned[0].threshold.badge_name == "First Project Complete"

    def test_25_projects_earns_multiple(self, db):
        tech = _make_technician(db, total_project_count=25)
        report = evaluate_milestones(db, tech)
        proj_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.PROJECTS_COMPLETED
        ]
        earned_names = {e.threshold.badge_name for e in proj_earned}
        assert "First Project Complete" in earned_names
        assert "5 Projects Strong" in earned_names
        assert "10 Project Pro" in earned_names
        assert "25 Project Master" in earned_names
        assert "50 Project Legend" not in earned_names

    def test_100_projects_earns_all(self, db):
        tech = _make_technician(db, total_project_count=100)
        report = evaluate_milestones(db, tech)
        proj_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.PROJECTS_COMPLETED
        ]
        assert len(proj_earned) == len(PROJECT_MILESTONES)


# ---------------------------------------------------------------------------
# Tests: Role diversity milestones
# ---------------------------------------------------------------------------

class TestRoleDiversityMilestones:
    def test_no_assignments_no_role_badges(self, db):
        tech = _make_technician(db)
        report = evaluate_milestones(db, tech)
        role_earned = [
            e for e in report.all_earned
            if e.threshold in ROLE_DIVERSITY_MILESTONES
        ]
        assert len(role_earned) == 0

    def test_2_roles_earns_explorer(self, db):
        tech = _make_technician(db)
        _add_role_assignments(db, tech, ["Lead Splicer", "Cable Puller"])
        report = evaluate_milestones(db, tech)
        role_earned = [
            e for e in report.all_earned
            if e.threshold in ROLE_DIVERSITY_MILESTONES
        ]
        assert len(role_earned) == 1
        assert role_earned[0].threshold.badge_name == "Role Explorer"

    def test_4_roles_earns_multi_specialist(self, db):
        tech = _make_technician(db)
        _add_role_assignments(
            db, tech,
            ["Lead Splicer", "Cable Puller", "Site Surveyor", "OTDR Tester"],
        )
        report = evaluate_milestones(db, tech)
        role_earned = [
            e for e in report.all_earned
            if e.threshold in ROLE_DIVERSITY_MILESTONES
        ]
        earned_names = {e.threshold.badge_name for e in role_earned}
        assert "Role Explorer" in earned_names
        assert "Multi-Role Specialist" in earned_names
        assert "Swiss Army Tech" not in earned_names

    def test_6_roles_earns_all_role_badges(self, db):
        tech = _make_technician(db)
        _add_role_assignments(
            db, tech,
            ["Splicer", "Puller", "Surveyor", "Tester", "Lead", "Installer"],
        )
        report = evaluate_milestones(db, tech)
        role_earned = [
            e for e in report.all_earned
            if e.threshold in ROLE_DIVERSITY_MILESTONES
        ]
        assert len(role_earned) == len(ROLE_DIVERSITY_MILESTONES)


# ---------------------------------------------------------------------------
# Tests: Certification milestones
# ---------------------------------------------------------------------------

class TestCertMilestones:
    def test_no_certs_no_badges(self, db):
        tech = _make_technician(db)
        report = evaluate_milestones(db, tech)
        cert_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.CERTS_EARNED
        ]
        assert len(cert_earned) == 0

    def test_1_active_cert_earns_first(self, db):
        tech = _make_technician(db)
        _add_certs(db, tech, 1, CertStatus.ACTIVE)
        report = evaluate_milestones(db, tech)
        cert_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.CERTS_EARNED
        ]
        assert len(cert_earned) == 1
        assert cert_earned[0].threshold.badge_name == "First Cert Earned"

    def test_expired_certs_dont_count(self, db):
        tech = _make_technician(db)
        _add_certs(db, tech, 5, CertStatus.EXPIRED)
        report = evaluate_milestones(db, tech)
        cert_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.CERTS_EARNED
        ]
        assert len(cert_earned) == 0

    def test_5_active_certs_earns_all(self, db):
        tech = _make_technician(db)
        _add_certs(db, tech, 5, CertStatus.ACTIVE)
        report = evaluate_milestones(db, tech)
        cert_earned = [
            e for e in report.all_earned
            if e.threshold.milestone_type == MilestoneType.CERTS_EARNED
        ]
        assert len(cert_earned) == len(CERT_MILESTONES)


# ---------------------------------------------------------------------------
# Tests: Badge sync (persistence)
# ---------------------------------------------------------------------------

class TestBadgeSync:
    def test_sync_creates_new_badges(self, db):
        tech = _make_technician(db, total_approved_hours=600, total_project_count=5)
        _add_certs(db, tech, 1, CertStatus.ACTIVE)

        new_badges = sync_milestone_badges(db, tech)
        assert len(new_badges) > 0
        badge_names = {b.badge_name for b in new_badges}
        assert "First 100 Hours" in badge_names
        assert "500 Hour Veteran" in badge_names
        assert "First Project Complete" in badge_names
        assert "5 Projects Strong" in badge_names
        assert "First Cert Earned" in badge_names

    def test_sync_uses_milestone_badge_model(self, db):
        tech = _make_technician(db, total_approved_hours=100)
        new_badges = sync_milestone_badges(db, tech)
        assert len(new_badges) >= 1
        badge = new_badges[0]
        assert isinstance(badge, MilestoneBadge)
        assert badge.milestone_type == MilestoneType.HOURS_THRESHOLD
        assert badge.threshold_value == 100.0
        assert badge.actual_value == 100.0
        assert badge.tier == 1
        assert badge.icon == "clock"
        assert badge.granted_at is not None

    def test_sync_is_idempotent(self, db):
        tech = _make_technician(db, total_approved_hours=500)

        first_sync = sync_milestone_badges(db, tech)
        assert len(first_sync) >= 2  # At least 100h and 500h

        second_sync = sync_milestone_badges(db, tech)
        assert len(second_sync) == 0  # No new badges

    def test_sync_respects_existing_badges(self, db):
        tech = _make_technician(db, total_approved_hours=500)

        # Pre-create one badge
        existing = MilestoneBadge(
            technician_id=tech.id,
            milestone_type=MilestoneType.HOURS_THRESHOLD,
            badge_name="First 100 Hours",
            description="Already exists",
            threshold_value=100,
            actual_value=100,
            tier=1,
        )
        db.add(existing)
        db.commit()

        new_badges = sync_milestone_badges(db, tech)
        new_names = {b.badge_name for b in new_badges}
        # Should NOT re-create "First 100 Hours"
        assert "First 100 Hours" not in new_names
        # Should create "500 Hour Veteran"
        assert "500 Hour Veteran" in new_names

    def test_sync_incremental_on_data_change(self, db):
        tech = _make_technician(db, total_approved_hours=100)
        first_sync = sync_milestone_badges(db, tech)
        first_names = {b.badge_name for b in first_sync}
        assert "First 100 Hours" in first_names
        assert "500 Hour Veteran" not in first_names

        # Simulate hours increase
        tech.total_approved_hours = 500
        db.commit()
        db.refresh(tech)

        second_sync = sync_milestone_badges(db, tech)
        second_names = {b.badge_name for b in second_sync}
        assert "500 Hour Veteran" in second_names
        # Should NOT re-create the 100 hour badge
        assert "First 100 Hours" not in second_names


# ---------------------------------------------------------------------------
# Tests: Batch sync
# ---------------------------------------------------------------------------

class TestBatchSync:
    def test_sync_all_technicians(self, db):
        tech1 = _make_technician(
            db, first_name="Alice", total_approved_hours=100, total_project_count=1,
        )
        tech2 = _make_technician(
            db, first_name="Bob", last_name="Builder",
            email="bob@test.com", total_approved_hours=1000, total_project_count=10,
        )
        tech3 = _make_technician(
            db, first_name="Carol", last_name="Zero",
            email="carol@test.com", total_approved_hours=0, total_project_count=0,
        )

        results = sync_all_technicians(db)
        # Alice and Bob should have new badges, Carol should not
        assert "Alice Tech" in results
        assert "Bob Builder" in results
        assert "Carol Zero" not in results

    def test_sync_all_is_idempotent(self, db):
        _make_technician(db, total_approved_hours=500)
        results1 = sync_all_technicians(db)
        assert len(results1) > 0

        results2 = sync_all_technicians(db)
        assert len(results2) == 0


# ---------------------------------------------------------------------------
# Tests: Progress reporting
# ---------------------------------------------------------------------------

class TestMilestoneProgress:
    def test_progress_returns_all_milestones(self, db):
        tech = _make_technician(db, total_approved_hours=250)
        progress = get_milestone_progress(db, tech)
        assert len(progress) == len(MILESTONE_CATALOG)

    def test_progress_pct_calculation(self, db):
        tech = _make_technician(db, total_approved_hours=250)
        progress = get_milestone_progress(db, tech)

        # Find the 500-hour milestone
        hour_500 = next(
            p for p in progress if p["badge_name"] == "500 Hour Veteran"
        )
        assert hour_500["progress_pct"] == 50.0
        assert hour_500["earned"] is False
        assert hour_500["actual_value"] == 250.0
        assert hour_500["threshold_value"] == 500.0

    def test_progress_caps_at_100_pct(self, db):
        tech = _make_technician(db, total_approved_hours=999)
        progress = get_milestone_progress(db, tech)

        hour_100 = next(
            p for p in progress if p["badge_name"] == "First 100 Hours"
        )
        assert hour_100["progress_pct"] == 100.0
        assert hour_100["earned"] is True

    def test_progress_zero_for_zero_data(self, db):
        tech = _make_technician(db)
        progress = get_milestone_progress(db, tech)

        hour_100 = next(
            p for p in progress if p["badge_name"] == "First 100 Hours"
        )
        assert hour_100["progress_pct"] == 0.0
        assert hour_100["earned"] is False

    def test_progress_includes_persisted_earned_at(self, db):
        tech = _make_technician(db, total_approved_hours=100)
        sync_milestone_badges(db, tech)

        progress = get_milestone_progress(db, tech)
        hour_100 = next(
            p for p in progress if p["badge_name"] == "First 100 Hours"
        )
        assert hour_100["earned"] is True
        assert hour_100["earned_at"] is not None
        assert hour_100["badge_id"] is not None

    def test_progress_includes_tier_and_icon(self, db):
        tech = _make_technician(db, total_approved_hours=5000)
        progress = get_milestone_progress(db, tech)

        legend = next(
            p for p in progress if p["badge_name"] == "5000 Hour Legend"
        )
        assert legend["tier"] == 3
        assert legend["icon"] == "trophy"


# ---------------------------------------------------------------------------
# Tests: Report data class properties
# ---------------------------------------------------------------------------

class TestReportProperties:
    def test_newly_earned_vs_persisted(self, db):
        tech = _make_technician(db, total_approved_hours=500)

        # First evaluation — nothing persisted yet
        report1 = evaluate_milestones(db, tech)
        assert len(report1.newly_earned) >= 2
        assert all(not e.already_persisted for e in report1.newly_earned)

        # Persist badges
        sync_milestone_badges(db, tech)

        # Second evaluation — everything persisted
        report2 = evaluate_milestones(db, tech)
        assert len(report2.newly_earned) == 0
        assert len(report2.all_earned) >= 2
        assert all(e.already_persisted for e in report2.all_earned)

    def test_not_yet_earned(self, db):
        tech = _make_technician(db, total_approved_hours=50)
        report = evaluate_milestones(db, tech)
        not_earned = report.not_yet_earned
        # All hours milestones should be not-earned
        hours_not_earned = [
            e for e in not_earned
            if e.threshold.milestone_type == MilestoneType.HOURS_THRESHOLD
        ]
        assert len(hours_not_earned) == len(HOURS_MILESTONES)

    def test_actual_value_recorded(self, db):
        tech = _make_technician(db, total_approved_hours=1234)
        report = evaluate_milestones(db, tech)
        for eval_result in report.evaluations:
            if eval_result.threshold.milestone_type == MilestoneType.HOURS_THRESHOLD:
                assert eval_result.actual_value == 1234.0


# ---------------------------------------------------------------------------
# Tests: Catalog integrity
# ---------------------------------------------------------------------------

class TestCatalogIntegrity:
    def test_no_duplicate_badge_names(self):
        names = [m.badge_name for m in MILESTONE_CATALOG]
        assert len(names) == len(set(names)), "Duplicate badge names in catalog"

    def test_all_milestones_have_valid_types(self):
        for m in MILESTONE_CATALOG:
            assert isinstance(m.milestone_type, MilestoneType)

    def test_all_milestones_have_positive_thresholds(self):
        for m in MILESTONE_CATALOG:
            assert m.threshold_value > 0, f"{m.badge_name} has non-positive threshold"

    def test_tiers_are_valid(self):
        for m in MILESTONE_CATALOG:
            assert m.tier in (1, 2, 3), f"{m.badge_name} has invalid tier {m.tier}"

    def test_hours_milestones_sorted_ascending(self):
        values = [m.threshold_value for m in HOURS_MILESTONES]
        assert values == sorted(values)

    def test_project_milestones_sorted_ascending(self):
        values = [m.threshold_value for m in PROJECT_MILESTONES]
        assert values == sorted(values)
