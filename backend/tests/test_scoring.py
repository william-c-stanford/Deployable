"""Tests for the 5-dimension scoring engine."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import date, timedelta

from app.services.scoring import (
    score_technician_for_role,
    rank_technicians_for_role,
    _score_skills,
    _score_certifications,
    _score_availability,
    _score_location,
    _score_experience,
    _apply_preference_rule,
    PROFICIENCY_SCORES,
    DEFAULT_WEIGHTS,
)


# ---------------------------------------------------------------------------
# Helpers — create mock objects matching the split model structure
# ---------------------------------------------------------------------------


def make_tech_skill(skill_name: str, proficiency: str, hours: float = 0.0):
    """Create a mock TechnicianSkill."""
    skill = MagicMock()
    skill.skill_name = skill_name
    skill.proficiency_level = proficiency  # string, not enum
    skill.training_hours_accumulated = hours
    skill.training_hours = hours
    return skill


def make_cert(cert_name: str, status: str = "Active", expiry_date=None):
    """Create a mock TechnicianCertification."""
    cert = MagicMock()
    cert.cert_name = cert_name
    cert.status = status
    cert.expiry_date = expiry_date
    return cert


def make_technician(
    tech_id="tech-1",
    name="John Doe",
    career_stage="Deployed",
    deployability_status="Ready Now",
    home_base_city="Atlanta",
    approved_regions=None,
    available_from=None,
    skills=None,
    certifications=None,
    documents=None,
    deployability_locked=False,
):
    tech = MagicMock()
    tech.id = tech_id
    tech.full_name = name
    tech.first_name = name.split()[0]
    tech.last_name = name.split()[-1]
    tech.career_stage = career_stage
    tech.deployability_status = deployability_status
    tech.home_base_city = home_base_city
    tech.approved_regions = approved_regions or ["GA", "FL"]
    tech.available_from = available_from
    tech.skills = skills or []
    tech.certifications = certifications or []
    tech.documents = documents or []
    tech.deployability_locked = deployability_locked
    return tech


def make_role(
    role_id="role-1",
    role_name="Lead Splicer",
    required_skills=None,
    required_certs=None,
    skill_weights=None,
    project_id="proj-1",
    quantity=2,
    filled=0,
):
    role = MagicMock()
    role.id = role_id
    role.role_name = role_name
    role.required_skills = required_skills or []
    role.required_certs = required_certs or []
    role.skill_weights = skill_weights or {}
    role.project_id = project_id
    role.quantity = quantity
    role.filled = filled
    return role


def make_project(
    proj_id="proj-1",
    name="Test Project",
    start_date=None,
    location_region="GA",
    location_city="Atlanta",
    status="Active",
):
    proj = MagicMock()
    proj.id = proj_id
    proj.name = name
    proj.start_date = start_date or date.today() + timedelta(days=14)
    proj.location_region = location_region
    proj.location_city = location_city
    proj.status = status
    return proj


def make_preference_rule(rule_type, effect="demote", parameters=None, active=True):
    rule = MagicMock()
    rule.id = "rule-1"
    rule.rule_type = rule_type
    rule.effect = effect
    rule.parameters = parameters or {}
    rule.active = active
    return rule


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScoreSkills:
    def test_no_required_skills_gives_default_score(self):
        session = MagicMock()
        tech = make_technician()
        role = make_role(required_skills=[])
        result = _score_skills(session, tech, role)
        assert result["score"] == 75.0

    def test_all_skills_matched(self):
        session = MagicMock()
        tech = make_technician(skills=[
            make_tech_skill("Fiber Splicing", "Advanced", 350),
            make_tech_skill("OTDR Testing", "Intermediate", 150),
        ])
        role = make_role(required_skills=["Fiber Splicing", "OTDR Testing"])
        result = _score_skills(session, tech, role)
        assert result["score"] > 70.0
        assert "meets requirement" in result["detail"]

    def test_missing_skill_lowers_score(self):
        session = MagicMock()
        tech = make_technician(skills=[
            make_tech_skill("Fiber Splicing", "Advanced", 350),
        ])
        role = make_role(required_skills=["Fiber Splicing", "OTDR Testing"])
        result = _score_skills(session, tech, role)
        assert result["score"] < 80.0
        assert "missing" in result["detail"]

    def test_dict_skill_requirements(self):
        session = MagicMock()
        tech = make_technician(skills=[
            make_tech_skill("Fiber Splicing", "Apprentice", 50),
        ])
        role = make_role(required_skills=[
            {"skill_name": "Fiber Splicing", "min_proficiency": "Advanced"},
        ])
        result = _score_skills(session, tech, role)
        assert "below" in result["detail"]


class TestScoreCertifications:
    def test_no_required_certs(self):
        session = MagicMock()
        tech = make_technician()
        role = make_role(required_certs=[])
        result = _score_certifications(session, tech, role)
        assert result["score"] == 100.0

    def test_all_certs_matched(self):
        session = MagicMock()
        tech = make_technician(certifications=[
            make_cert("FOA CFOT"),
            make_cert("OSHA 10"),
        ])
        role = make_role(required_certs=["FOA CFOT", "OSHA 10"])
        result = _score_certifications(session, tech, role)
        assert result["score"] == 100.0

    def test_missing_cert_reduces_score(self):
        session = MagicMock()
        tech = make_technician(certifications=[
            make_cert("FOA CFOT"),
        ])
        role = make_role(required_certs=["FOA CFOT", "OSHA 10"])
        result = _score_certifications(session, tech, role)
        assert result["score"] == 50.0

    def test_expired_cert_not_counted(self):
        session = MagicMock()
        tech = make_technician(certifications=[
            make_cert("FOA CFOT", status="Expired"),
        ])
        role = make_role(required_certs=["FOA CFOT"])
        result = _score_certifications(session, tech, role)
        assert result["score"] == 0.0


class TestScoreAvailability:
    def test_ready_now_no_project(self):
        tech = make_technician(deployability_status="Ready Now")
        result = _score_availability(tech, None)
        assert result["score"] == 100.0

    def test_currently_assigned_low_score(self):
        tech = make_technician(deployability_status="Currently Assigned")
        result = _score_availability(tech, None)
        assert result["score"] == 20.0

    def test_available_before_project_start(self):
        tech = make_technician(available_from=date.today())
        project = make_project(start_date=date.today() + timedelta(days=7))
        result = _score_availability(tech, project)
        assert result["score"] == 100.0

    def test_available_late_after_start(self):
        tech = make_technician(available_from=date.today() + timedelta(days=30))
        project = make_project(start_date=date.today())
        result = _score_availability(tech, project)
        assert result["score"] <= 10.0


class TestScoreLocation:
    def test_city_match(self):
        tech = make_technician(home_base_city="Atlanta")
        project = make_project(location_city="Atlanta", location_region="GA")
        result = _score_location(tech, project)
        assert result["score"] == 100.0

    def test_region_match(self):
        tech = make_technician(home_base_city="Savannah", approved_regions=["GA"])
        project = make_project(location_city="Atlanta", location_region="GA")
        result = _score_location(tech, project)
        assert result["score"] == 80.0

    def test_no_region_match(self):
        tech = make_technician(home_base_city="Denver", approved_regions=["CO"])
        project = make_project(location_city="Atlanta", location_region="GA")
        result = _score_location(tech, project)
        assert result["score"] == 30.0


class TestScoreExperience:
    def test_deployed_stage_high_score(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.count.return_value = 3
        tech = make_technician(career_stage="Deployed")
        result = _score_experience(session, tech)
        assert result["score"] >= 90.0

    def test_sourced_stage_low_score(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.count.return_value = 0
        tech = make_technician(career_stage="Sourced")
        result = _score_experience(session, tech)
        assert result["score"] <= 15.0


class TestPreferenceRules:
    def test_experience_threshold_demote(self):
        tech = make_technician(career_stage="Sourced")
        rule = make_preference_rule(
            "experience_threshold",
            effect="demote",
            parameters={"min_career_stage": "Training Completed"},
        )
        result = _apply_preference_rule(rule, tech, {})
        assert result is not None
        assert result["effect"] == "demote"

    def test_skill_minimum_exclude(self):
        tech = make_technician(skills=[
            make_tech_skill("Fiber Splicing", "Apprentice"),
        ])
        rule = make_preference_rule(
            "skill_level_minimum",
            effect="exclude",
            parameters={"skill_name": "Fiber Splicing", "min_proficiency": "Advanced"},
        )
        result = _apply_preference_rule(rule, tech, {})
        assert result is not None
        assert result["effect"] == "exclude"

    def test_location_restriction(self):
        tech = make_technician(approved_regions=["GA", "FL"])
        rule = make_preference_rule(
            "location_restriction",
            effect="exclude",
            parameters={"excluded_regions": ["GA"]},
        )
        result = _apply_preference_rule(rule, tech, {})
        assert result is not None

    def test_no_trigger_when_ok(self):
        tech = make_technician(career_stage="Deployed")
        rule = make_preference_rule(
            "experience_threshold",
            parameters={"min_career_stage": "Screened"},
        )
        result = _apply_preference_rule(rule, tech, {})
        assert result is None


class TestScoreTechnicianForRole:
    def test_full_scorecard_structure(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.count.return_value = 2
        tech = make_technician(skills=[
            make_tech_skill("Fiber Splicing", "Advanced"),
        ], certifications=[
            make_cert("FOA CFOT"),
        ])
        role = make_role(
            required_skills=["Fiber Splicing"],
            required_certs=["FOA CFOT"],
        )
        project = make_project()

        result = score_technician_for_role(session, tech, role, project)

        assert "technician_id" in result
        assert "role_id" in result
        assert "overall_score" in result
        assert "dimensions" in result
        assert set(result["dimensions"].keys()) == {
            "skills_match", "certification_fit", "availability",
            "location_fit", "experience",
        }
        assert "disqualified" in result
        assert isinstance(result["overall_score"], float)
        assert 0 <= result["overall_score"] <= 100

    def test_inactive_technician_disqualified(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.count.return_value = 0
        tech = make_technician(deployability_status="Inactive")
        role = make_role()
        project = make_project()

        result = score_technician_for_role(session, tech, role, project)
        assert result["disqualified"] is True

    def test_missing_all_certs_disqualified(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.count.return_value = 0
        tech = make_technician(certifications=[])
        role = make_role(required_certs=["FOA CFOT", "OSHA 10"])

        result = score_technician_for_role(session, tech, role)
        assert result["disqualified"] is True
        assert "certifications" in result["disqualification_reason"].lower()


class TestRankTechniciansForRole:
    def test_ranking_order(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.count.return_value = 0

        tech1 = make_technician(
            tech_id="tech-1", name="Expert Joe",
            career_stage="Deployed", deployability_status="Ready Now",
            skills=[make_tech_skill("Fiber Splicing", "Advanced", 400)],
            certifications=[make_cert("FOA CFOT")],
        )
        tech2 = make_technician(
            tech_id="tech-2", name="Novice Jane",
            career_stage="Sourced", deployability_status="In Training",
            skills=[make_tech_skill("Fiber Splicing", "Apprentice", 10)],
        )

        session.query.return_value.filter.return_value.all.side_effect = [
            [],  # preference_rules
            [tech1, tech2],  # technicians
        ]

        role = make_role(required_skills=["Fiber Splicing"], required_certs=["FOA CFOT"])
        project = make_project()

        result = rank_technicians_for_role(session, role, project, limit=5)

        assert len(result) >= 1
        # First result should be the higher scorer
        if len(result) >= 2:
            assert result[0]["overall_score"] >= result[1]["overall_score"]
