"""
Comprehensive tests for the SQL scoring layer.

Tests cover:
1. Individual SQL fragment builders (experience, archetype, rate_cap, etc.)
2. Python-level evaluators (skill_level_minimum, cert_bonus)
3. Composite modifier building
4. Score application with multiple stacked rules
5. Integration with prefilter engine
6. Edge cases and error handling

Uses in-memory SQLite for fast unit-level tests.
"""

import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from app.database import Base
from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    ProficiencyLevel,
    DeployabilityStatus,
    CareerStage,
    CertStatus,
)
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.user import Partner
from app.models.assignment import Assignment
from app.models.recommendation import PreferenceRule

from app.services.sql_scoring import (
    _build_experience_threshold_sql,
    _build_archetype_preference_sql,
    _build_rate_cap_sql,
    _build_project_count_minimum_sql,
    _build_location_preference_sql,
    _build_skill_level_minimum_python,
    _build_cert_bonus_python,
    build_scoring_modifiers,
    build_scoring_modifiers_with_params,
    build_composite_sql_modifier,
    apply_sql_modifiers_to_score,
    apply_python_modifiers,
    compute_preference_adjusted_scores,
    get_sql_scoring_summary,
    get_supported_rule_types,
    load_active_rules,
    RULE_SQL_BUILDERS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db_engine():
    """Create a fresh in-memory SQLite engine for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)

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


def _make_rule(
    db: Session,
    rule_type: str,
    effect: str = "demote",
    parameters: dict = None,
    active: bool = True,
    scope: str = "global",
) -> PreferenceRule:
    """Helper to create and persist a preference rule."""
    rule = PreferenceRule(
        id=uuid.uuid4(),
        rule_type=rule_type,
        effect=effect,
        parameters=parameters or {},
        active=active,
        scope=scope,
    )
    db.add(rule)
    db.commit()
    return rule


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
    hourly_rate_min: float = 35.0,
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
        willing_to_travel=True,
        hourly_rate_min=hourly_rate_min,
        hourly_rate_max=hourly_rate_min + 20,
    )
    db.add(tech)
    db.flush()

    for skill_data in (skills or []):
        ts = TechnicianSkill(
            id=uuid.uuid4(),
            technician_id=tech.id,
            skill_name=skill_data["name"],
            proficiency_level=skill_data.get("level", ProficiencyLevel.APPRENTICE),
            training_hours_accumulated=skill_data.get("hours", 0.0),
        )
        db.add(ts)

    for cert_data in (certs or []):
        tc = TechnicianCertification(
            id=uuid.uuid4(),
            technician_id=tech.id,
            cert_name=cert_data["name"],
            status=cert_data.get("status", CertStatus.ACTIVE),
        )
        db.add(tc)

    db.commit()
    db.refresh(tech)
    return tech


# ---------------------------------------------------------------------------
# Test: Individual SQL fragment builders
# ---------------------------------------------------------------------------

class TestExperienceThresholdSQL:
    """Tests for experience_threshold SQL modifier."""

    def test_below_threshold_returns_penalty(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 5.0, "penalty": -10})
        tech = _make_technician(db, "Junior", "Dev", years_exp=2.0)

        mod = _build_experience_threshold_sql(rule)
        assert mod.rule_type == "experience_threshold"
        assert mod.effect == "demote"
        assert mod.sql_expression is not None
        assert mod.modifier_value == -10

    def test_above_threshold_no_penalty(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 3.0, "penalty": -10})
        tech = _make_technician(db, "Senior", "Dev", years_exp=8.0)

        mod = _build_experience_threshold_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == 0.0

    def test_below_threshold_triggers_penalty(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 5.0, "penalty": -15})
        tech = _make_technician(db, "Jr", "Tech", years_exp=2.0)

        mod = _build_experience_threshold_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == -15


class TestArchetypePreferenceSQL:
    """Tests for archetype_preference SQL modifier."""

    def test_matching_archetype_gets_bonus(self, db):
        rule = _make_rule(db, "archetype_preference", "boost",
                         {"preferred_archetype": "senior_specialist", "bonus": 12})
        tech = _make_technician(db, "Match", "Arch", archetype="senior_specialist")

        mod = _build_archetype_preference_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == 12

    def test_non_matching_archetype_no_bonus(self, db):
        rule = _make_rule(db, "archetype_preference", "boost",
                         {"preferred_archetype": "senior_specialist", "bonus": 12})
        tech = _make_technician(db, "NoMatch", "Arch", archetype="field_technician")

        mod = _build_archetype_preference_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == 0.0


class TestRateCapSQL:
    """Tests for rate_cap SQL modifier."""

    def test_rate_above_cap_penalised(self, db):
        rule = _make_rule(db, "rate_cap", "exclude",
                         {"max_hourly_rate": 50, "penalty": -100})
        tech = _make_technician(db, "Expensive", "Tech", hourly_rate_min=75.0)

        mod = _build_rate_cap_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == -100

    def test_rate_below_cap_no_penalty(self, db):
        rule = _make_rule(db, "rate_cap", "exclude",
                         {"max_hourly_rate": 50, "penalty": -100})
        tech = _make_technician(db, "Cheap", "Tech", hourly_rate_min=35.0)

        mod = _build_rate_cap_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == 0.0


class TestProjectCountMinimumSQL:
    """Tests for project_count_minimum SQL modifier."""

    def test_below_minimum_penalised(self, db):
        rule = _make_rule(db, "project_count_minimum", "demote",
                         {"min_projects": 5, "penalty": -8})
        tech = _make_technician(db, "Few", "Projects", project_count=2)

        mod = _build_project_count_minimum_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == -8

    def test_above_minimum_no_penalty(self, db):
        rule = _make_rule(db, "project_count_minimum", "demote",
                         {"min_projects": 5, "penalty": -8})
        tech = _make_technician(db, "Many", "Projects", project_count=10)

        mod = _build_project_count_minimum_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == 0.0


class TestLocationPreferenceSQL:
    """Tests for location_preference SQL modifier."""

    def test_matching_state_gets_bonus(self, db):
        rule = _make_rule(db, "location_preference", "boost",
                         {"preferred_state": "TX", "bonus": 10})
        tech = _make_technician(db, "Texas", "Tech", home_state="TX")

        mod = _build_location_preference_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == 10

    def test_different_state_no_bonus(self, db):
        rule = _make_rule(db, "location_preference", "boost",
                         {"preferred_state": "TX", "bonus": 10})
        tech = _make_technician(db, "Georgia", "Tech", home_state="GA")

        mod = _build_location_preference_sql(rule)
        mod._cached_params = rule.parameters

        from app.services.sql_scoring import _evaluate_sql_modifier_locally
        result = _evaluate_sql_modifier_locally(mod, tech)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Test: Python-level evaluators
# ---------------------------------------------------------------------------

class TestSkillLevelMinimumPython:
    """Tests for skill_level_minimum Python evaluator."""

    def test_skill_below_minimum_penalised(self, db):
        rule = _make_rule(db, "skill_level_minimum", "demote",
                         {"skill_name": "Fiber Splicing", "min_level": "Advanced", "penalty": -15})
        tech = _make_technician(db, "Low", "Skill",
            skills=[{"name": "Fiber Splicing", "level": ProficiencyLevel.INTERMEDIATE}])

        mod = _build_skill_level_minimum_python(rule)
        assert mod.python_evaluator is not None
        result = mod.python_evaluator(tech)
        assert result == -15

    def test_skill_meets_minimum_no_penalty(self, db):
        rule = _make_rule(db, "skill_level_minimum", "demote",
                         {"skill_name": "Fiber Splicing", "min_level": "Advanced", "penalty": -15})
        tech = _make_technician(db, "High", "Skill",
            skills=[{"name": "Fiber Splicing", "level": ProficiencyLevel.ADVANCED}])

        mod = _build_skill_level_minimum_python(rule)
        result = mod.python_evaluator(tech)
        assert result == 0.0

    def test_missing_skill_penalised(self, db):
        rule = _make_rule(db, "skill_level_minimum", "demote",
                         {"skill_name": "OTDR Testing", "min_level": "Intermediate", "penalty": -10})
        tech = _make_technician(db, "NoSkill", "Tech", skills=[])

        mod = _build_skill_level_minimum_python(rule)
        result = mod.python_evaluator(tech)
        assert result == -10

    def test_case_insensitive_skill_match(self, db):
        rule = _make_rule(db, "skill_level_minimum", "demote",
                         {"skill_name": "fiber splicing", "min_level": "Advanced", "penalty": -15})
        tech = _make_technician(db, "Case", "Test",
            skills=[{"name": "Fiber Splicing", "level": ProficiencyLevel.ADVANCED}])

        mod = _build_skill_level_minimum_python(rule)
        result = mod.python_evaluator(tech)
        assert result == 0.0


class TestCertBonusPython:
    """Tests for cert_bonus Python evaluator."""

    def test_has_bonus_cert_gets_boost(self, db):
        rule = _make_rule(db, "cert_bonus", "boost",
                         {"cert_name": "BICSI RCDD", "bonus": 8})
        tech = _make_technician(db, "Cert", "Holder",
            certs=[{"name": "BICSI RCDD", "status": CertStatus.ACTIVE}])

        mod = _build_cert_bonus_python(rule)
        result = mod.python_evaluator(tech)
        assert result == 8

    def test_missing_cert_no_bonus(self, db):
        rule = _make_rule(db, "cert_bonus", "boost",
                         {"cert_name": "BICSI RCDD", "bonus": 8})
        tech = _make_technician(db, "NoCert", "Tech", certs=[])

        mod = _build_cert_bonus_python(rule)
        result = mod.python_evaluator(tech)
        assert result == 0.0

    def test_expired_cert_no_bonus(self, db):
        rule = _make_rule(db, "cert_bonus", "boost",
                         {"cert_name": "BICSI RCDD", "bonus": 8})
        tech = _make_technician(db, "Expired", "Cert",
            certs=[{"name": "BICSI RCDD", "status": CertStatus.EXPIRED}])

        mod = _build_cert_bonus_python(rule)
        result = mod.python_evaluator(tech)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Test: Composite modifier building
# ---------------------------------------------------------------------------

class TestBuildScoringModifiers:
    """Tests for build_scoring_modifiers and build_composite_sql_modifier."""

    def test_builds_modifiers_for_known_types(self, db):
        rules = [
            _make_rule(db, "experience_threshold", "demote",
                      {"min_years": 3.0, "penalty": -10}),
            _make_rule(db, "archetype_preference", "boost",
                      {"preferred_archetype": "senior_specialist", "bonus": 15}),
        ]

        modifiers = build_scoring_modifiers(rules)
        assert len(modifiers) == 2
        types = {m.rule_type for m in modifiers}
        assert "experience_threshold" in types
        assert "archetype_preference" in types

    def test_unknown_rule_type_skipped(self, db):
        rules = [
            _make_rule(db, "unknown_type_xyz", "demote", {"foo": "bar"}),
        ]
        modifiers = build_scoring_modifiers(rules)
        assert len(modifiers) == 0

    def test_build_composite_sql_modifier(self, db):
        rules = [
            _make_rule(db, "experience_threshold", "demote",
                      {"min_years": 3.0, "penalty": -10}),
            _make_rule(db, "archetype_preference", "boost",
                      {"preferred_archetype": "senior_specialist", "bonus": 15}),
        ]
        modifiers = build_scoring_modifiers(rules)

        # SQL-expressible modifiers
        sql_mods = [m for m in modifiers if m.sql_expression is not None]
        assert len(sql_mods) == 2

        composite = build_composite_sql_modifier(sql_mods)
        assert composite is not None

    def test_composite_none_when_no_sql_modifiers(self, db):
        rules = [
            _make_rule(db, "skill_level_minimum", "demote",
                      {"skill_name": "Fiber", "min_level": "Advanced"}),
        ]
        modifiers = build_scoring_modifiers(rules)

        # This rule uses python evaluator, no SQL expression
        sql_mods = [m for m in modifiers if m.sql_expression is not None]
        composite = build_composite_sql_modifier(sql_mods)
        assert composite is None

    def test_build_with_params_caches(self, db):
        rules = [
            _make_rule(db, "rate_cap", "exclude",
                      {"max_hourly_rate": 60, "penalty": -50}),
        ]
        modifiers = build_scoring_modifiers_with_params(rules)
        assert len(modifiers) == 1
        assert hasattr(modifiers[0], "_cached_params")
        assert modifiers[0]._cached_params["max_hourly_rate"] == 60


# ---------------------------------------------------------------------------
# Test: Score application
# ---------------------------------------------------------------------------

class TestApplySQLModifiersToScore:
    """Tests for apply_sql_modifiers_to_score."""

    def test_single_demote_reduces_score(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 5.0, "penalty": -10})
        tech = _make_technician(db, "Jr", "Dev", years_exp=2.0)

        modifiers = build_scoring_modifiers_with_params([rule])
        adjusted, adjustments, excluded = apply_sql_modifiers_to_score(
            tech, 80.0, modifiers
        )

        assert adjusted == 70.0
        assert len(adjustments) == 1
        assert adjustments[0]["modifier"] == -10
        assert not excluded

    def test_single_boost_increases_score(self, db):
        rule = _make_rule(db, "archetype_preference", "boost",
                         {"preferred_archetype": "senior_specialist", "bonus": 15})
        tech = _make_technician(db, "Match", "Arch", archetype="senior_specialist")

        modifiers = build_scoring_modifiers_with_params([rule])
        adjusted, adjustments, excluded = apply_sql_modifiers_to_score(
            tech, 60.0, modifiers
        )

        assert adjusted == 75.0
        assert len(adjustments) == 1
        assert adjustments[0]["modifier"] == 15
        assert not excluded

    def test_exclude_effect_marks_excluded(self, db):
        rule = _make_rule(db, "rate_cap", "exclude",
                         {"max_hourly_rate": 50, "penalty": -100})
        tech = _make_technician(db, "Pricey", "Tech", hourly_rate_min=80.0)

        modifiers = build_scoring_modifiers_with_params([rule])
        adjusted, adjustments, excluded = apply_sql_modifiers_to_score(
            tech, 90.0, modifiers
        )

        assert excluded is True
        assert adjustments[0]["effect"] == "exclude"

    def test_multiple_rules_stack_additively(self, db):
        rules = [
            _make_rule(db, "experience_threshold", "demote",
                      {"min_years": 10.0, "penalty": -10}),
            _make_rule(db, "archetype_preference", "boost",
                      {"preferred_archetype": "senior_specialist", "bonus": 5}),
            _make_rule(db, "project_count_minimum", "demote",
                      {"min_projects": 8, "penalty": -5}),
        ]
        tech = _make_technician(
            db, "Multi", "Rule",
            years_exp=3.0,  # below 10 → -10
            archetype="senior_specialist",  # matches → +5
            project_count=3,  # below 8 → -5
        )

        modifiers = build_scoring_modifiers_with_params(rules)
        adjusted, adjustments, excluded = apply_sql_modifiers_to_score(
            tech, 70.0, modifiers
        )

        # 70 - 10 + 5 - 5 = 60
        assert adjusted == 60.0
        assert len(adjustments) == 3
        assert not excluded

    def test_score_doesnt_go_below_zero(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 20.0, "penalty": -50})
        tech = _make_technician(db, "Young", "One", years_exp=1.0)

        modifiers = build_scoring_modifiers_with_params([rule])
        adjusted, _, _ = apply_sql_modifiers_to_score(tech, 30.0, modifiers)

        assert adjusted == 0.0  # max(0, 30 - 50) = 0

    def test_no_rules_returns_base_score(self, db):
        tech = _make_technician(db, "NoRule", "Tech")
        adjusted, adjustments, excluded = apply_sql_modifiers_to_score(
            tech, 75.0, []
        )
        assert adjusted == 75.0
        assert adjustments == []
        assert not excluded

    def test_mixed_sql_and_python_modifiers(self, db):
        """SQL and Python modifiers work together."""
        rules = [
            _make_rule(db, "experience_threshold", "demote",
                      {"min_years": 8.0, "penalty": -10}),  # SQL
            _make_rule(db, "skill_level_minimum", "demote",
                      {"skill_name": "Fiber Splicing", "min_level": "Advanced", "penalty": -15}),  # Python
            _make_rule(db, "cert_bonus", "boost",
                      {"cert_name": "FOA CFOT", "bonus": 5}),  # Python
        ]
        tech = _make_technician(
            db, "Mixed", "Mod",
            years_exp=3.0,  # below 8 → -10
            skills=[{"name": "Fiber Splicing", "level": ProficiencyLevel.INTERMEDIATE}],  # below Advanced → -15
            certs=[{"name": "FOA CFOT", "status": CertStatus.ACTIVE}],  # has cert → +5
        )

        modifiers = build_scoring_modifiers_with_params(rules)
        adjusted, adjustments, excluded = apply_sql_modifiers_to_score(
            tech, 80.0, modifiers
        )

        # 80 - 10 - 15 + 5 = 60
        assert adjusted == 60.0
        assert len(adjustments) == 3


# ---------------------------------------------------------------------------
# Test: Python modifier application
# ---------------------------------------------------------------------------

class TestApplyPythonModifiers:
    """Tests for apply_python_modifiers."""

    def test_applies_python_evaluators(self, db):
        rule = _make_rule(db, "cert_bonus", "boost",
                         {"cert_name": "OSHA 30", "bonus": 7})
        tech = _make_technician(db, "CertGuy", "Test",
            certs=[{"name": "OSHA 30", "status": CertStatus.ACTIVE}])

        modifiers = build_scoring_modifiers([rule])
        total, adjustments = apply_python_modifiers(tech, modifiers)

        assert total == 7.0
        assert len(adjustments) == 1

    def test_skips_sql_only_modifiers(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 5.0, "penalty": -10})
        tech = _make_technician(db, "SqlOnly", "Test", years_exp=2.0)

        modifiers = build_scoring_modifiers([rule])
        total, adjustments = apply_python_modifiers(tech, modifiers)

        # experience_threshold has sql_expression, not python_evaluator
        assert total == 0.0
        assert len(adjustments) == 0


# ---------------------------------------------------------------------------
# Test: Integration with DB
# ---------------------------------------------------------------------------

class TestComputePreferenceAdjustedScores:
    """Tests for compute_preference_adjusted_scores (main integration point)."""

    def test_adjusts_scores_for_multiple_techs(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 5.0, "penalty": -10})

        tech1 = _make_technician(db, "Senior", "One", years_exp=10.0)
        tech2 = _make_technician(db, "Junior", "Two", years_exp=2.0)

        base_scores = {
            str(tech1.id): 80.0,
            str(tech2.id): 75.0,
        }

        results = compute_preference_adjusted_scores(
            db, [tech1, tech2], base_scores
        )

        # Senior (10yr >= 5yr) → no penalty → 80
        adj_senior, adjs_senior, exc_senior = results[str(tech1.id)]
        assert adj_senior == 80.0
        assert len(adjs_senior) == 0
        assert not exc_senior

        # Junior (2yr < 5yr) → -10 penalty → 65
        adj_junior, adjs_junior, exc_junior = results[str(tech2.id)]
        assert adj_junior == 65.0
        assert len(adjs_junior) == 1
        assert not exc_junior

    def test_no_rules_returns_base_scores(self, db):
        tech = _make_technician(db, "NoRules", "Tech")
        base_scores = {str(tech.id): 70.0}

        results = compute_preference_adjusted_scores(db, [tech], base_scores)

        adj, adjs, exc = results[str(tech.id)]
        assert adj == 70.0
        assert adjs == []
        assert not exc

    def test_inactive_rules_ignored(self, db):
        _make_rule(db, "experience_threshold", "demote",
                  {"min_years": 5.0, "penalty": -10}, active=False)

        tech = _make_technician(db, "Inactive", "Rule", years_exp=2.0)
        base_scores = {str(tech.id): 70.0}

        results = compute_preference_adjusted_scores(db, [tech], base_scores)

        adj, _, _ = results[str(tech.id)]
        assert adj == 70.0  # No penalty because rule is inactive


# ---------------------------------------------------------------------------
# Test: Scoring summary and utilities
# ---------------------------------------------------------------------------

class TestScoringSummary:
    """Tests for get_sql_scoring_summary and utility functions."""

    def test_summary_returns_active_modifiers(self, db):
        _make_rule(db, "experience_threshold", "demote",
                  {"min_years": 5.0, "penalty": -10})
        _make_rule(db, "archetype_preference", "boost",
                  {"preferred_archetype": "senior_specialist", "bonus": 15})
        _make_rule(db, "rate_cap", "exclude",
                  {"max_hourly_rate": 60, "penalty": -100}, active=False)

        summary = get_sql_scoring_summary(db)

        # Only 2 active rules
        assert len(summary) == 2
        types = {s["rule_type"] for s in summary}
        assert "experience_threshold" in types
        assert "archetype_preference" in types
        assert "rate_cap" not in types  # inactive

    def test_summary_includes_evaluation_mode(self, db):
        _make_rule(db, "experience_threshold", "demote",
                  {"min_years": 5.0, "penalty": -10})
        _make_rule(db, "cert_bonus", "boost",
                  {"cert_name": "FOA CFOT", "bonus": 5})

        summary = get_sql_scoring_summary(db)
        modes = {s["rule_type"]: s["evaluation_mode"] for s in summary}

        assert modes["experience_threshold"] == "sql"
        assert modes["cert_bonus"] == "python"

    def test_get_supported_rule_types(self):
        types = get_supported_rule_types()
        assert "experience_threshold" in types
        assert "archetype_preference" in types
        assert "rate_cap" in types
        assert "project_count_minimum" in types
        assert "skill_level_minimum" in types
        assert "cert_bonus" in types
        assert "location_preference" in types

    def test_all_supported_types_have_builders(self):
        """Every supported type maps to a builder function."""
        for rule_type in get_supported_rule_types():
            assert rule_type in RULE_SQL_BUILDERS
            assert callable(RULE_SQL_BUILDERS[rule_type])


# ---------------------------------------------------------------------------
# Test: Load active rules
# ---------------------------------------------------------------------------

class TestLoadActiveRules:
    """Tests for load_active_rules."""

    def test_loads_active_global_rules(self, db):
        _make_rule(db, "experience_threshold", "demote",
                  {"min_years": 5.0}, active=True, scope="global")
        _make_rule(db, "rate_cap", "exclude",
                  {"max_hourly_rate": 60}, active=False, scope="global")
        _make_rule(db, "archetype_preference", "boost",
                  {"preferred_archetype": "x"}, active=True, scope="client")

        rules = load_active_rules(db, "global")
        # Should get the active global rule only
        assert len(rules) == 1
        assert rules[0].rule_type == "experience_threshold"

    def test_loads_scope_and_global_rules(self, db):
        _make_rule(db, "experience_threshold", "demote",
                  {"min_years": 5.0}, active=True, scope="global")
        _make_rule(db, "archetype_preference", "boost",
                  {"preferred_archetype": "x"}, active=True, scope="client")

        rules = load_active_rules(db, "client")
        # Should get both: global + client scope
        assert len(rules) == 2


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and boundary conditions for SQL scoring."""

    def test_none_years_experience_treated_as_zero(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 1.0, "penalty": -5})
        tech = _make_technician(db, "None", "Exp", years_exp=0.0)
        tech.years_experience = None
        db.commit()
        db.refresh(tech)

        modifiers = build_scoring_modifiers_with_params([rule])
        adjusted, adjustments, _ = apply_sql_modifiers_to_score(
            tech, 50.0, modifiers
        )
        assert adjusted == 45.0

    def test_none_project_count_treated_as_zero(self, db):
        rule = _make_rule(db, "project_count_minimum", "demote",
                         {"min_projects": 1, "penalty": -5})
        tech = _make_technician(db, "None", "Count", project_count=0)
        tech.total_project_count = None
        db.commit()
        db.refresh(tech)

        modifiers = build_scoring_modifiers_with_params([rule])
        adjusted, adjustments, _ = apply_sql_modifiers_to_score(
            tech, 50.0, modifiers
        )
        assert adjusted == 45.0

    def test_empty_archetype_doesnt_match(self, db):
        rule = _make_rule(db, "archetype_preference", "boost",
                         {"preferred_archetype": "senior_specialist", "bonus": 10})
        tech = _make_technician(db, "No", "Arch", archetype="")

        modifiers = build_scoring_modifiers_with_params([rule])
        adjusted, adjustments, _ = apply_sql_modifiers_to_score(
            tech, 50.0, modifiers
        )
        assert adjusted == 50.0
        assert len(adjustments) == 0

    def test_description_format(self, db):
        rule = _make_rule(db, "experience_threshold", "demote",
                         {"min_years": 5.0, "penalty": -10})
        mod = _build_experience_threshold_sql(rule)
        assert "5.0" in mod.description or "5" in mod.description
        assert "-10" in mod.description
