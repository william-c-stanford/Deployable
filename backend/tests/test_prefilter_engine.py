"""
Comprehensive tests for the deterministic pre-filtering engine.

Tests cover:
1. Scoring logic for each dimension (skills, certs, experience, availability, travel)
2. Hard constraint filtering (cert gates, region, availability)
3. Preference rule application (boost, demote, exclude)
4. End-to-end pipeline with ranked shortlist
5. Edge cases (empty inputs, missing data, etc.)

Uses in-memory SQLite for fast unit-level tests.
"""

import uuid
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from app.database import Base
from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    TechnicianDocument,
    ProficiencyLevel,
    DeployabilityStatus,
    CareerStage,
    CertStatus,
)
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.user import Partner
from app.models.assignment import Assignment
from app.models.recommendation import PreferenceRule

from app.services.prefilter_engine import (
    _check_required_certs,
    _score_skills,
    _score_experience,
    _score_availability,
    _score_travel_fit,
    _apply_preference_rules,
    _generate_explanation,
    run_prefilter,
    run_prefilter_batch,
    Scorecard,
    CandidateResult,
    PROFICIENCY_RANK,
    DEFAULT_WEIGHTS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db_engine():
    """Create a fresh in-memory SQLite engine for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    # Enable foreign keys in SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="function")
def db(db_engine):
    """Provide a clean session for each test."""
    TestSession = sessionmaker(bind=db_engine)
    session = TestSession()
    yield session
    session.close()


@pytest.fixture
def partner(db: Session):
    """Create a test partner."""
    p = Partner(
        id=uuid.uuid4(),
        name="Test Partner Inc",
        contact_email="partner@test.com",
    )
    db.add(p)
    db.commit()
    return p


@pytest.fixture
def project(db: Session, partner):
    """Create a test project in Staffing status."""
    proj = Project(
        id=uuid.uuid4(),
        name="Test Fiber Project",
        partner_id=partner.id,
        status=ProjectStatus.STAFFING,
        location_region="GA",
        location_city="Atlanta",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 10, 31),
        budget_hours=10000,
    )
    db.add(proj)
    db.commit()
    return proj


@pytest.fixture
def role_lead_splicer(db: Session, project):
    """A role requiring Advanced Fiber Splicing + FOA CFOT + OSHA 10."""
    role = ProjectRole(
        id=uuid.uuid4(),
        project_id=project.id,
        role_name="Lead Splicer",
        quantity=2,
        filled=0,
        required_skills=[
            {"skill": "Fiber Splicing", "min_level": "Advanced"},
            {"skill": "OTDR Testing", "min_level": "Intermediate"},
        ],
        required_certs=["FOA CFOT", "OSHA 10"],
        hourly_rate=52.00,
        per_diem=75.00,
    )
    db.add(role)
    db.commit()
    return role


@pytest.fixture
def role_fiber_tech(db: Session, project):
    """A role with lower requirements."""
    role = ProjectRole(
        id=uuid.uuid4(),
        project_id=project.id,
        role_name="Fiber Technician",
        quantity=4,
        filled=0,
        required_skills=[
            {"skill": "Fiber Splicing", "min_level": "Intermediate"},
        ],
        required_certs=["FOA CFOT"],
        hourly_rate=38.00,
        per_diem=65.00,
    )
    db.add(role)
    db.commit()
    return role


def _make_technician(
    db: Session,
    first_name: str,
    last_name: str,
    home_state: str = "GA",
    regions: list = None,
    status: str = DeployabilityStatus.READY_NOW.value,
    available_from: date = None,
    years_exp: float = 5.0,
    project_count: int = 5,
    archetype: str = "senior_specialist",
    skills: list = None,
    certs: list = None,
    willing_to_travel: bool = True,
) -> Technician:
    """Helper to create a technician with skills and certs."""
    tech = Technician(
        id=uuid.uuid4(),
        first_name=first_name,
        last_name=last_name,
        email=f"{first_name.lower()}.{last_name.lower()}@test.com",
        home_base_city="Atlanta",
        home_base_state=home_state,
        approved_regions=regions or ["GA"],
        deployability_status=status,
        career_stage=CareerStage.DEPLOYED.value,
        available_from=available_from,
        years_experience=years_exp,
        total_project_count=project_count,
        archetype=archetype,
        willing_to_travel=willing_to_travel,
        hourly_rate_min=35.0,
        hourly_rate_max=55.0,
    )
    db.add(tech)
    db.flush()

    # Add skills
    for skill_data in (skills or []):
        ts = TechnicianSkill(
            id=uuid.uuid4(),
            technician_id=tech.id,
            skill_name=skill_data["name"],
            proficiency_level=skill_data.get("level", ProficiencyLevel.APPRENTICE),
            training_hours_accumulated=skill_data.get("hours", 0.0),
        )
        db.add(ts)

    # Add certs
    for cert_data in (certs or []):
        tc = TechnicianCertification(
            id=uuid.uuid4(),
            technician_id=tech.id,
            cert_name=cert_data["name"],
            status=cert_data.get("status", CertStatus.ACTIVE),
            issue_date=cert_data.get("issue_date"),
            expiry_date=cert_data.get("expiry_date"),
        )
        db.add(tc)

    db.commit()
    # Refresh to load relationships
    db.refresh(tech)
    return tech


# ---------------------------------------------------------------------------
# Unit tests: Individual scoring functions
# ---------------------------------------------------------------------------

class TestSkillScoring:
    """Tests for the _score_skills function."""

    def test_perfect_match(self, db):
        """Technician has all skills at or above required level."""
        tech = _make_technician(
            db, "Alice", "Perfect",
            skills=[
                {"name": "Fiber Splicing", "level": ProficiencyLevel.ADVANCED, "hours": 500},
                {"name": "OTDR Testing", "level": ProficiencyLevel.ADVANCED, "hours": 400},
            ],
        )
        skill_bundle = [
            {"skill": "Fiber Splicing", "min_level": "Advanced"},
            {"skill": "OTDR Testing", "min_level": "Intermediate"},
        ]
        score, details = _score_skills(tech, skill_bundle)
        assert score == 100.0
        assert all(d.met for d in details)

    def test_partial_match(self, db):
        """Technician has some skills below required level."""
        tech = _make_technician(
            db, "Bob", "Partial",
            skills=[
                {"name": "Fiber Splicing", "level": ProficiencyLevel.INTERMEDIATE, "hours": 200},
                {"name": "OTDR Testing", "level": ProficiencyLevel.APPRENTICE, "hours": 50},
            ],
        )
        skill_bundle = [
            {"skill": "Fiber Splicing", "min_level": "Advanced"},
            {"skill": "OTDR Testing", "min_level": "Intermediate"},
        ]
        score, details = _score_skills(tech, skill_bundle)
        # Fiber: Intermediate vs Advanced = 0.5; OTDR: Beginner vs Intermediate = 0.5
        assert score == 50.0
        assert not details[0].met
        assert not details[1].met

    def test_missing_skills(self, db):
        """Technician is missing required skills entirely."""
        tech = _make_technician(db, "Charlie", "NoSkills", skills=[])
        skill_bundle = [
            {"skill": "Fiber Splicing", "min_level": "Advanced"},
        ]
        score, details = _score_skills(tech, skill_bundle)
        assert score == 0.0
        assert not details[0].met
        assert details[0].technician_level is None

    def test_empty_bundle(self, db):
        """No skills required returns 100."""
        tech = _make_technician(db, "Dan", "Easy", skills=[])
        score, details = _score_skills(tech, [])
        assert score == 100.0
        assert details == []

    def test_case_insensitive_match(self, db):
        """Skill matching should be case-insensitive."""
        tech = _make_technician(
            db, "Eve", "CaseTest",
            skills=[{"name": "fiber splicing", "level": ProficiencyLevel.ADVANCED}],
        )
        skill_bundle = [{"skill": "Fiber Splicing", "min_level": "Advanced"}]
        score, details = _score_skills(tech, skill_bundle)
        assert score == 100.0

    def test_skill_weights(self, db):
        """Custom skill weights affect the aggregate score."""
        tech = _make_technician(
            db, "Frank", "Weighted",
            skills=[
                {"name": "Fiber Splicing", "level": ProficiencyLevel.ADVANCED},
                {"name": "OTDR Testing", "level": ProficiencyLevel.APPRENTICE},  # 2 below Adv
            ],
        )
        skill_bundle = [
            {"skill": "Fiber Splicing", "min_level": "Advanced"},
            {"skill": "OTDR Testing", "min_level": "Advanced"},
        ]
        # Without weights: (1.0 + 0.25) / 2 = 0.625 * 100 = 62.5
        score_no_weight, _ = _score_skills(tech, skill_bundle)
        assert abs(score_no_weight - 62.5) < 0.01

        # With weights: splicing weight 3, otdr weight 1
        # (1.0 * 3 + 0.25 * 1) / 4 = 3.25 / 4 = 0.8125 * 100 = 81.25
        skill_weights = {"Fiber Splicing": 3.0, "OTDR Testing": 1.0}
        score_weighted, _ = _score_skills(tech, skill_bundle, skill_weights)
        assert abs(score_weighted - 81.25) < 0.01


class TestCertChecking:
    """Tests for _check_required_certs."""

    def test_all_certs_active(self, db):
        """Technician has all required certs with Active status."""
        tech = _make_technician(
            db, "Grace", "Certified",
            certs=[
                {"name": "FOA CFOT", "status": CertStatus.ACTIVE},
                {"name": "OSHA 10", "status": CertStatus.ACTIVE},
            ],
        )
        all_met, scores, disqualifications = _check_required_certs(
            tech, ["FOA CFOT", "OSHA 10"]
        )
        assert all_met is True
        assert len(disqualifications) == 0
        assert all(s.score == 1.0 for s in scores)

    def test_missing_cert(self, db):
        """Missing a required cert disqualifies."""
        tech = _make_technician(
            db, "Henry", "NoCert",
            certs=[{"name": "FOA CFOT", "status": CertStatus.ACTIVE}],
        )
        all_met, scores, disqualifications = _check_required_certs(
            tech, ["FOA CFOT", "OSHA 10"]
        )
        assert all_met is False
        assert len(disqualifications) == 1
        assert "OSHA 10" in disqualifications[0]

    def test_expired_cert(self, db):
        """Expired cert doesn't satisfy requirement."""
        tech = _make_technician(
            db, "Irene", "ExpiredCert",
            certs=[
                {"name": "FOA CFOT", "status": CertStatus.EXPIRED},
            ],
        )
        all_met, scores, disqualifications = _check_required_certs(
            tech, ["FOA CFOT"]
        )
        assert all_met is False
        assert len(disqualifications) == 1

    def test_no_certs_required(self, db):
        """No certs required means everyone passes."""
        tech = _make_technician(db, "Jack", "NoCertsNeeded", certs=[])
        all_met, scores, disqualifications = _check_required_certs(tech, [])
        assert all_met is True
        assert scores == []

    def test_case_insensitive_cert_match(self, db):
        """Cert matching should be case-insensitive."""
        tech = _make_technician(
            db, "Kate", "CaseTest",
            certs=[{"name": "foa cfot", "status": CertStatus.ACTIVE}],
        )
        all_met, scores, disqualifications = _check_required_certs(
            tech, ["FOA CFOT"]
        )
        assert all_met is True


class TestExperienceScoring:
    """Tests for _score_experience."""

    def test_senior_high_score(self, db):
        """Senior tech with many years and projects scores high."""
        tech = _make_technician(
            db, "Leo", "Senior", years_exp=15.0, project_count=25,
        )
        role = MagicMock()
        score = _score_experience(tech, role)
        assert score == 100.0  # 70 (15yr cap) + 30 (20+ projects cap)

    def test_junior_low_score(self, db):
        """Junior tech scores lower."""
        tech = _make_technician(
            db, "Mary", "Junior", years_exp=1.0, project_count=1,
        )
        role = MagicMock()
        score = _score_experience(tech, role)
        expected = (1.0 / 15.0) * 70.0 + (1.0 / 20.0) * 30.0
        assert abs(score - expected) < 0.01

    def test_zero_experience(self, db):
        """Zero experience scores zero."""
        tech = _make_technician(
            db, "Nancy", "Fresh", years_exp=0.0, project_count=0,
        )
        role = MagicMock()
        score = _score_experience(tech, role)
        assert score == 0.0


class TestAvailabilityScoring:
    """Tests for _score_availability."""

    def test_immediately_available(self, db, project):
        """Null available_from means immediately available = 100."""
        tech = _make_technician(
            db, "Oscar", "Available", available_from=None,
        )
        score = _score_availability(tech, project)
        assert score == 100.0

    def test_available_before_start(self, db, project):
        """Available before project start date = 100."""
        tech = _make_technician(
            db, "Pat", "Early",
            available_from=date(2026, 3, 15),
        )
        score = _score_availability(tech, project)
        assert score == 100.0

    def test_available_within_two_weeks(self, db, project):
        """Available within 2 weeks of start = 80."""
        tech = _make_technician(
            db, "Quinn", "Soon",
            available_from=date(2026, 4, 10),
        )
        score = _score_availability(tech, project)
        assert score == 80.0

    def test_available_within_four_weeks(self, db, project):
        """Available within 4 weeks = 50."""
        tech = _make_technician(
            db, "Rex", "Later",
            available_from=date(2026, 4, 20),
        )
        score = _score_availability(tech, project)
        assert score == 50.0


class TestTravelFitScoring:
    """Tests for _score_travel_fit."""

    def test_same_state(self, db, project):
        """Technician in same state as project = 100."""
        tech = _make_technician(
            db, "Sam", "Local", home_state="GA", regions=["GA"],
        )
        score = _score_travel_fit(tech, project)
        assert score == 100.0

    def test_different_state_approved_region(self, db, project):
        """Different state but in approved regions = 90."""
        tech = _make_technician(
            db, "Tina", "Regional",
            home_state="FL", regions=["FL", "GA"], willing_to_travel=True,
        )
        score = _score_travel_fit(tech, project)
        assert score == 90.0

    def test_different_state_willing(self, db, project):
        """Different state, willing to travel, not explicitly approved = 40."""
        tech = _make_technician(
            db, "Ulf", "Traveler",
            home_state="CA", regions=["CA", "NV"], willing_to_travel=True,
        )
        score = _score_travel_fit(tech, project)
        assert score == 40.0

    def test_different_state_not_willing(self, db, project):
        """Different state, not willing to travel = 10."""
        tech = _make_technician(
            db, "Victor", "Homebody",
            home_state="CA", regions=["CA"], willing_to_travel=False,
        )
        score = _score_travel_fit(tech, project)
        assert score == 10.0


# ---------------------------------------------------------------------------
# Preference rule tests
# ---------------------------------------------------------------------------

class TestPreferenceRules:
    """Tests for preference rule application."""

    def test_experience_threshold_demote(self, db):
        """Experience below threshold applies penalty."""
        tech = _make_technician(db, "Wendy", "Low", years_exp=1.5)
        rule = PreferenceRule(
            id=uuid.uuid4(),
            rule_type="experience_threshold",
            effect="demote",
            parameters={"min_years": 3.0, "penalty": -10},
            active=True,
        )
        db.add(rule)
        db.commit()

        scorecard = Scorecard(total_weighted=70.0)
        result = _apply_preference_rules(scorecard, tech, [rule])
        assert len(result.preference_adjustments) == 1
        assert result.total_weighted == 60.0  # 70 - 10

    def test_experience_threshold_not_triggered(self, db):
        """Experience above threshold has no effect."""
        tech = _make_technician(db, "Xavier", "Exp", years_exp=5.0)
        rule = PreferenceRule(
            id=uuid.uuid4(),
            rule_type="experience_threshold",
            effect="demote",
            parameters={"min_years": 3.0, "penalty": -10},
            active=True,
        )
        db.add(rule)
        db.commit()

        scorecard = Scorecard(total_weighted=70.0)
        result = _apply_preference_rules(scorecard, tech, [rule])
        assert len(result.preference_adjustments) == 0
        assert result.total_weighted == 70.0

    def test_archetype_boost(self, db):
        """Matching archetype gets a boost."""
        tech = _make_technician(db, "Yolanda", "Senior", archetype="senior_specialist")
        rule = PreferenceRule(
            id=uuid.uuid4(),
            rule_type="archetype_preference",
            effect="boost",
            parameters={"preferred_archetype": "senior_specialist", "bonus": 15},
            active=True,
        )
        db.add(rule)
        db.commit()

        scorecard = Scorecard(total_weighted=60.0)
        result = _apply_preference_rules(scorecard, tech, [rule])
        assert result.total_weighted == 75.0  # 60 + 15

    def test_rate_cap_exclude(self, db):
        """Exceeding rate cap with exclude effect disqualifies."""
        tech = _make_technician(db, "Zach", "Pricey", years_exp=10.0)
        tech.hourly_rate_min = 80.0
        db.commit()
        db.refresh(tech)

        rule = PreferenceRule(
            id=uuid.uuid4(),
            rule_type="rate_cap",
            effect="exclude",
            parameters={"max_hourly_rate": 60, "penalty": -100},
            active=True,
        )
        db.add(rule)
        db.commit()

        scorecard = Scorecard(total_weighted=85.0)
        result = _apply_preference_rules(scorecard, tech, [rule])
        assert result.disqualified is True
        assert len(result.disqualification_reasons) == 1

    def test_cert_bonus(self, db):
        """Having a bonus cert adds points."""
        tech = _make_technician(
            db, "Amy", "CertBonus",
            certs=[{"name": "BICSI RCDD", "status": CertStatus.ACTIVE}],
        )
        rule = PreferenceRule(
            id=uuid.uuid4(),
            rule_type="cert_bonus",
            effect="boost",
            parameters={"cert_name": "BICSI RCDD", "bonus": 8},
            active=True,
        )
        db.add(rule)
        db.commit()

        scorecard = Scorecard(total_weighted=70.0)
        result = _apply_preference_rules(scorecard, tech, [rule])
        assert result.total_weighted == 78.0

    def test_project_count_minimum(self, db):
        """Below project count minimum applies penalty."""
        tech = _make_technician(db, "Ben", "Newbie", project_count=1)
        rule = PreferenceRule(
            id=uuid.uuid4(),
            rule_type="project_count_minimum",
            effect="demote",
            parameters={"min_projects": 3, "penalty": -5},
            active=True,
        )
        db.add(rule)
        db.commit()

        scorecard = Scorecard(total_weighted=65.0)
        result = _apply_preference_rules(scorecard, tech, [rule])
        assert result.total_weighted == 60.0

    def test_multiple_rules_stack(self, db):
        """Multiple rules apply additively."""
        tech = _make_technician(
            db, "Carla", "Multi", years_exp=1.0, archetype="senior_specialist",
        )
        rules = [
            PreferenceRule(
                id=uuid.uuid4(),
                rule_type="experience_threshold",
                effect="demote",
                parameters={"min_years": 3.0, "penalty": -10},
                active=True,
            ),
            PreferenceRule(
                id=uuid.uuid4(),
                rule_type="archetype_preference",
                effect="boost",
                parameters={"preferred_archetype": "senior_specialist", "bonus": 5},
                active=True,
            ),
        ]
        for r in rules:
            db.add(r)
        db.commit()

        scorecard = Scorecard(total_weighted=70.0)
        result = _apply_preference_rules(scorecard, tech, rules)
        assert len(result.preference_adjustments) == 2
        assert result.total_weighted == 65.0  # 70 - 10 + 5


# ---------------------------------------------------------------------------
# Explanation generation tests
# ---------------------------------------------------------------------------

class TestExplanationGeneration:
    """Tests for _generate_explanation."""

    def test_strong_match(self, db):
        tech = _make_technician(db, "Dave", "Strong", years_exp=12.0, project_count=15)
        scorecard = Scorecard(
            total_weighted=85.0,
            skill_details=[],
            cert_details=[],
        )
        role = MagicMock()
        role.role_name = "Lead Splicer"
        explanation = _generate_explanation(tech, scorecard, role)
        assert "strong match" in explanation.lower()
        assert "Lead Splicer" in explanation

    def test_marginal_match(self, db):
        tech = _make_technician(db, "Eve", "Weak", years_exp=0.5, project_count=0)
        scorecard = Scorecard(
            total_weighted=25.0,
            skill_details=[],
            cert_details=[],
        )
        role = MagicMock()
        role.role_name = "Fiber Tech"
        explanation = _generate_explanation(tech, scorecard, role)
        assert "marginal" in explanation.lower()


# ---------------------------------------------------------------------------
# Integration tests: Full pipeline
# ---------------------------------------------------------------------------

class TestFullPipeline:
    """End-to-end tests for run_prefilter."""

    def _seed_candidates(self, db, project, role):
        """Create a pool of technicians with varying qualifications."""
        # Strong candidate: perfect skills, certs, experience, same region
        _make_technician(
            db, "Alpha", "Top",
            home_state="GA", regions=["GA", "FL", "AL"],
            years_exp=12.0, project_count=20,
            skills=[
                {"name": "Fiber Splicing", "level": ProficiencyLevel.ADVANCED, "hours": 600},
                {"name": "OTDR Testing", "level": ProficiencyLevel.ADVANCED, "hours": 400},
            ],
            certs=[
                {"name": "FOA CFOT", "status": CertStatus.ACTIVE},
                {"name": "OSHA 10", "status": CertStatus.ACTIVE},
            ],
        )

        # Good candidate: meets most requirements
        _make_technician(
            db, "Bravo", "Good",
            home_state="GA", regions=["GA"],
            years_exp=7.0, project_count=10,
            skills=[
                {"name": "Fiber Splicing", "level": ProficiencyLevel.ADVANCED, "hours": 350},
                {"name": "OTDR Testing", "level": ProficiencyLevel.INTERMEDIATE, "hours": 200},
            ],
            certs=[
                {"name": "FOA CFOT", "status": CertStatus.ACTIVE},
                {"name": "OSHA 10", "status": CertStatus.ACTIVE},
            ],
        )

        # Missing cert: should be disqualified
        _make_technician(
            db, "Charlie", "NoCert",
            home_state="GA", regions=["GA"],
            years_exp=10.0, project_count=15,
            skills=[
                {"name": "Fiber Splicing", "level": ProficiencyLevel.ADVANCED, "hours": 500},
                {"name": "OTDR Testing", "level": ProficiencyLevel.ADVANCED, "hours": 300},
            ],
            certs=[
                {"name": "FOA CFOT", "status": CertStatus.ACTIVE},
                # Missing OSHA 10!
            ],
        )

        # Wrong region: should be filtered out in hard constraint phase
        _make_technician(
            db, "Delta", "WrongRegion",
            home_state="CA", regions=["CA", "NV"],
            years_exp=15.0, project_count=25,
            skills=[
                {"name": "Fiber Splicing", "level": ProficiencyLevel.ADVANCED, "hours": 800},
                {"name": "OTDR Testing", "level": ProficiencyLevel.ADVANCED, "hours": 500},
            ],
            certs=[
                {"name": "FOA CFOT", "status": CertStatus.ACTIVE},
                {"name": "OSHA 10", "status": CertStatus.ACTIVE},
            ],
        )

        # Not deployable: should be filtered
        _make_technician(
            db, "Echo", "InTraining",
            home_state="GA", regions=["GA"],
            status=DeployabilityStatus.IN_TRAINING.value,
            years_exp=2.0, project_count=2,
            skills=[
                {"name": "Fiber Splicing", "level": ProficiencyLevel.APPRENTICE},
            ],
            certs=[],
        )

    def test_basic_prefilter(self, db, project, role_lead_splicer):
        """Full pipeline produces ranked candidates correctly."""
        self._seed_candidates(db, project, role_lead_splicer)

        result = run_prefilter(db, str(role_lead_splicer.id), top_n=20)

        assert result.role_id == str(role_lead_splicer.id)
        assert result.role_name == "Lead Splicer"
        assert result.project_id == str(project.id)

        # Should have 2 qualified candidates (Alpha and Bravo)
        # Charlie is disqualified (missing OSHA 10)
        # Delta filtered by region
        # Echo filtered by deployability status
        assert result.total_shortlisted == 2
        assert result.candidates[0].rank == 1
        assert result.candidates[1].rank == 2

        # Alpha should rank higher than Bravo (more experience, higher skills)
        assert result.candidates[0].technician_name == "Alpha Top"
        assert result.candidates[1].technician_name == "Bravo Good"

        # Check scorecard structure
        top_card = result.candidates[0].scorecard
        assert top_card.skills_match > 0
        assert top_card.cert_match > 0
        assert top_card.experience > 0
        assert top_card.availability > 0
        assert top_card.travel_fit > 0
        assert not top_card.disqualified

    def test_prefilter_with_exclusion_list(self, db, project, role_lead_splicer):
        """Exclude list prevents candidates from appearing."""
        self._seed_candidates(db, project, role_lead_splicer)

        # Find Alpha's ID
        alpha = db.query(Technician).filter(Technician.first_name == "Alpha").first()

        result = run_prefilter(
            db, str(role_lead_splicer.id),
            exclude_technician_ids=[str(alpha.id)],
        )

        names = [c.technician_name for c in result.candidates]
        assert "Alpha Top" not in names

    def test_prefilter_custom_weights(self, db, project, role_lead_splicer):
        """Custom weights change relative scoring."""
        self._seed_candidates(db, project, role_lead_splicer)

        result = run_prefilter(
            db, str(role_lead_splicer.id),
            custom_weights={"experience": 80.0, "skills_match": 5.0, "cert_match": 5.0, "availability": 5.0, "travel_fit": 5.0},
        )

        # Weights should be normalized to sum to 100
        assert abs(sum(result.weights_used.values()) - 100.0) < 0.01

    def test_prefilter_with_preference_rules(self, db, project, role_lead_splicer):
        """Preference rules modify final scores."""
        self._seed_candidates(db, project, role_lead_splicer)

        # Boost senior_specialist archetype
        rule = PreferenceRule(
            id=uuid.uuid4(),
            rule_type="archetype_preference",
            effect="boost",
            parameters={"preferred_archetype": "senior_specialist", "bonus": 20},
            active=True,
            scope="global",
        )
        db.add(rule)
        db.commit()

        result = run_prefilter(db, str(role_lead_splicer.id))

        # Both Alpha and Bravo are senior_specialist, so both get the boost
        for candidate in result.candidates:
            assert len(candidate.scorecard.preference_adjustments) >= 1

    def test_prefilter_empty_result(self, db, project, role_lead_splicer):
        """Pre-filter with no eligible candidates returns empty list."""
        # Don't seed any candidates
        result = run_prefilter(db, str(role_lead_splicer.id))
        assert result.total_shortlisted == 0
        assert result.candidates == []

    def test_prefilter_invalid_role(self, db):
        """Invalid role ID raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            run_prefilter(db, "00000000-0000-0000-0000-000000000000")

    def test_batch_prefilter(self, db, project, role_lead_splicer, role_fiber_tech):
        """Batch pre-filter runs for all open roles in a project."""
        self._seed_candidates(db, project, role_lead_splicer)

        results = run_prefilter_batch(db, str(project.id))

        assert len(results) == 2  # Both roles have open slots
        role_names = {r.role_name for r in results}
        assert "Lead Splicer" in role_names
        assert "Fiber Technician" in role_names

    def test_filled_role_excluded_from_batch(self, db, project, role_lead_splicer, role_fiber_tech):
        """Fully filled roles are excluded from batch processing."""
        self._seed_candidates(db, project, role_lead_splicer)

        # Fill the lead splicer role
        role_lead_splicer.filled = role_lead_splicer.quantity
        db.commit()

        results = run_prefilter_batch(db, str(project.id))

        assert len(results) == 1
        assert results[0].role_name == "Fiber Technician"

    def test_to_dict_serialization(self, db, project, role_lead_splicer):
        """Results can be serialized to dict without errors."""
        self._seed_candidates(db, project, role_lead_splicer)
        result = run_prefilter(db, str(role_lead_splicer.id))

        d = result.to_dict()
        assert isinstance(d, dict)
        assert "candidates" in d
        assert isinstance(d["candidates"], list)
        if d["candidates"]:
            c = d["candidates"][0]
            assert "scorecard" in c
            assert "explanation" in c
            assert isinstance(c["scorecard"]["skill_details"], list)


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_top_n_limits_output(self, db, project, role_fiber_tech):
        """top_n parameter correctly limits output size."""
        # Create 5 qualified technicians
        for i in range(5):
            _make_technician(
                db, f"Tech{i}", f"Test{i}",
                home_state="GA", regions=["GA"],
                skills=[{"name": "Fiber Splicing", "level": ProficiencyLevel.INTERMEDIATE}],
                certs=[{"name": "FOA CFOT", "status": CertStatus.ACTIVE}],
            )

        result = run_prefilter(db, str(role_fiber_tech.id), top_n=3)
        assert result.total_shortlisted == 3
        assert len(result.candidates) == 3

    def test_rolling_off_soon_eligible(self, db, project, role_fiber_tech):
        """Rolling Off Soon status is eligible for staffing."""
        _make_technician(
            db, "Rolling", "Off",
            home_state="GA", regions=["GA"],
            status=DeployabilityStatus.ROLLING_OFF_SOON.value,
            skills=[{"name": "Fiber Splicing", "level": ProficiencyLevel.INTERMEDIATE}],
            certs=[{"name": "FOA CFOT", "status": CertStatus.ACTIVE}],
        )

        result = run_prefilter(db, str(role_fiber_tech.id))
        assert result.total_shortlisted == 1

    def test_currently_assigned_not_eligible(self, db, project, role_fiber_tech):
        """Currently Assigned status is not eligible."""
        _make_technician(
            db, "Busy", "Tech",
            home_state="GA", regions=["GA"],
            status=DeployabilityStatus.CURRENTLY_ASSIGNED.value,
            skills=[{"name": "Fiber Splicing", "level": ProficiencyLevel.INTERMEDIATE}],
            certs=[{"name": "FOA CFOT", "status": CertStatus.ACTIVE}],
        )

        result = run_prefilter(db, str(role_fiber_tech.id))
        assert result.total_shortlisted == 0

    def test_no_required_skills_or_certs(self, db, project):
        """Role with no requirements ranks by experience and location."""
        role = ProjectRole(
            id=uuid.uuid4(),
            project_id=project.id,
            role_name="General Helper",
            quantity=5,
            filled=0,
            required_skills=[],
            required_certs=[],
        )
        db.add(role)
        db.commit()

        _make_technician(
            db, "Anyone", "Will Do",
            home_state="GA", regions=["GA"],
        )

        result = run_prefilter(db, str(role.id))
        assert result.total_shortlisted == 1
        # With no skill/cert requirements, those dimensions should be 100
        card = result.candidates[0].scorecard
        assert card.skills_match == 100.0
        assert card.cert_match == 100.0
