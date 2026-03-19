"""Tests for preference rule proposal logic.

Tests the agent-side rejection analysis and rule proposal engine:
- Template matching from rejection reasons
- Parameter extraction from rejection context
- PreferenceRule creation with status='proposed'
- Rejection pattern analysis for batch suggestions
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.preference_rule_proposer import (
    _match_template,
    _extract_parameters,
    _extract_skill_name,
    _extract_cert_name,
    _build_threshold_string,
    propose_preference_rule,
    analyze_rejection_patterns,
    RULE_TEMPLATES,
)
from app.models.recommendation import (
    Recommendation,
    PreferenceRule,
    PreferenceRuleStatus,
    PreferenceRuleTemplateType,
    PreferenceRuleCreatedByType,
    RecommendationStatus,
)


# ---------------------------------------------------------------------------
# Template Matching Tests
# ---------------------------------------------------------------------------

class TestTemplateMatching:
    """Test _match_template correctly maps rejection reasons to rule types."""

    def test_experience_keywords(self):
        rule_type, confidence = _match_template("Too junior, needs more experience on fiber projects")
        assert rule_type == "experience_threshold"
        assert confidence > 0.3

    def test_skill_keywords(self):
        rule_type, confidence = _match_template("Insufficient fiber splicing proficiency level")
        assert rule_type == "skill_level_minimum"
        assert confidence > 0.3

    def test_cert_keywords(self):
        rule_type, confidence = _match_template("Missing OSHA certification, not safe for site")
        assert rule_type == "cert_requirement"
        assert confidence > 0.3

    def test_location_keywords(self):
        rule_type, confidence = _match_template("Too far from project region, travel distance too great")
        assert rule_type == "location_restriction"
        assert confidence > 0.3

    def test_availability_keywords(self):
        rule_type, confidence = _match_template("Not available on the start date, schedule conflict")
        assert rule_type == "availability_window"
        assert confidence > 0.3

    def test_project_history_keywords(self):
        rule_type, confidence = _match_template("No track record, unproven on completed projects")
        assert rule_type == "project_count_minimum"
        assert confidence > 0.3

    def test_rate_keywords(self):
        rule_type, confidence = _match_template("Hourly rate too expensive for budget, cost $75/hr")
        assert rule_type == "rate_cap"
        assert confidence > 0.3

    def test_default_fallback(self):
        """When no keywords match well, confidence should be low."""
        rule_type, confidence = _match_template("xyz xyz xyz")
        assert confidence == 0.2  # Low confidence default

    def test_longer_keywords_score_higher(self):
        """Longer matching keywords should produce higher confidence."""
        _, conf_short = _match_template("fiber")
        _, conf_long = _match_template("fiber splicing proficiency level")
        assert conf_long > conf_short

    def test_multiple_keyword_matches(self):
        """Multiple keywords from same template should increase confidence."""
        _, conf_single = _match_template("experience")
        _, conf_multi = _match_template("too junior, inexperienced, needs more experience")
        assert conf_multi > conf_single

    def test_confidence_capped_at_one(self):
        """Confidence should never exceed 1.0."""
        _, confidence = _match_template(
            "junior inexperienced novice beginner not ready too early needs more "
            "career stage seasoned veteran senior green new"
        )
        assert confidence <= 1.0


# ---------------------------------------------------------------------------
# Parameter Extraction Tests
# ---------------------------------------------------------------------------

class TestParameterExtraction:
    """Test _extract_parameters for each rule type."""

    def _make_recommendation(self, tech_id=None, role_id=None):
        rec = MagicMock(spec=Recommendation)
        rec.id = uuid.uuid4()
        rec.target_entity_id = tech_id or str(uuid.uuid4())
        rec.role_id = role_id or str(uuid.uuid4())
        return rec

    def _make_technician(self, career_stage="In Training"):
        tech = MagicMock()
        tech.id = uuid.uuid4()
        tech.career_stage = career_stage
        tech.full_name = "John Doe"
        return tech

    def _make_role(self, required_skills=None, required_certs=None):
        role = MagicMock()
        role.id = uuid.uuid4()
        role.role_name = "Fiber Splicer"
        role.required_skills = required_skills or []
        role.required_certs = required_certs or []
        role.project = MagicMock()
        role.project.location_region = "Texas"
        return role

    def test_experience_threshold_from_tech_stage(self):
        session = MagicMock()
        rec = self._make_recommendation()
        tech = self._make_technician("In Training")
        params = _extract_parameters(
            "experience_threshold", "too junior", rec, tech, None, session,
        )
        assert params["min_career_stage"] == "Training Completed"

    def test_experience_threshold_deployed_stays(self):
        session = MagicMock()
        rec = self._make_recommendation()
        tech = self._make_technician("Deployed")
        params = _extract_parameters(
            "experience_threshold", "not experienced enough", rec, tech, None, session,
        )
        # Already at max stage
        assert params["min_career_stage"] == "Deployed"

    def test_experience_threshold_no_tech(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "experience_threshold", "need senior", rec, None, None, session,
        )
        assert params["min_career_stage"] == "Training Completed"

    def test_skill_level_minimum_defaults_intermediate(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "skill_level_minimum", "skill proficiency too low", rec, None, None, session,
        )
        assert params["min_level"] == "Intermediate"

    def test_skill_level_minimum_advanced_keyword(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "skill_level_minimum", "needs advanced fiber splicing", rec, None, None, session,
        )
        assert params["min_level"] == "Advanced"

    def test_cert_requirement_extracts_osha(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "cert_requirement", "missing OSHA 10 certification", rec, None, None, session,
        )
        assert params["cert_name"] == "OSHA 10"

    def test_cert_requirement_from_role_certs(self):
        session = MagicMock()
        rec = self._make_recommendation()
        role = self._make_role(required_certs=["CDL", "OSHA 30"])
        params = _extract_parameters(
            "cert_requirement", "doesn't have the required cert", rec, None, role, session,
        )
        assert params["cert_name"] in ["CDL", "OSHA 30"]

    def test_location_restriction_from_role(self):
        session = MagicMock()
        rec = self._make_recommendation()
        role = self._make_role()
        params = _extract_parameters(
            "location_restriction", "too far away", rec, None, role, session,
        )
        assert params["allowed_regions"] == "Texas"
        assert params["restriction_type"] == "include"

    def test_availability_window_extracts_days(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "availability_window", "not available for 30 days", rec, None, None, session,
        )
        assert params["min_days_available"] == 30

    def test_availability_window_extracts_weeks(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "availability_window", "busy for 2 weeks", rec, None, None, session,
        )
        assert params["min_days_available"] == 14

    def test_availability_window_default(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "availability_window", "schedule conflict", rec, None, None, session,
        )
        assert params["min_days_available"] == 14

    def test_project_count_minimum_from_tech(self):
        session = MagicMock()
        tech = self._make_technician()
        rec = self._make_recommendation()
        # Mock query to return count of 1 completed assignment
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 1
        session.query.return_value = mock_query
        params = _extract_parameters(
            "project_count_minimum", "no track record", rec, tech, None, session,
        )
        assert params["min_projects"] == 2  # max(1+1, 2)

    def test_rate_cap_extracts_dollar_amount(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "rate_cap", "rate is $65/hr which is too expensive", rec, None, None, session,
        )
        assert params["max_hourly_rate"] == 65.0

    def test_rate_cap_default(self):
        session = MagicMock()
        rec = self._make_recommendation()
        params = _extract_parameters(
            "rate_cap", "too expensive for the budget", rec, None, None, session,
        )
        assert params["max_hourly_rate"] == 50.0


# ---------------------------------------------------------------------------
# Threshold String Building Tests
# ---------------------------------------------------------------------------

class TestBuildThresholdString:
    """Test _build_threshold_string produces human-readable thresholds."""

    def test_experience_threshold(self):
        result = _build_threshold_string("experience_threshold", {"min_career_stage": "Training Completed"})
        assert result == "Training Completed"

    def test_skill_level_minimum(self):
        result = _build_threshold_string("skill_level_minimum", {
            "skill_name": "Fiber Splicing",
            "min_level": "Advanced",
        })
        assert result == "Fiber Splicing: Advanced"

    def test_cert_requirement(self):
        result = _build_threshold_string("cert_requirement", {"cert_name": "OSHA 30"})
        assert result == "OSHA 30"

    def test_availability_window(self):
        result = _build_threshold_string("availability_window", {"min_days_available": 21})
        assert result == "21 days"

    def test_rate_cap(self):
        result = _build_threshold_string("rate_cap", {"max_hourly_rate": 45.0})
        assert result == "$45.0/hr"


# ---------------------------------------------------------------------------
# Propose Preference Rule Tests
# ---------------------------------------------------------------------------

class TestProposePreferenceRule:
    """Test the main propose_preference_rule function."""

    def _make_session_and_rec(self, rejection_reason="Too junior"):
        session = MagicMock()
        rec = MagicMock(spec=Recommendation)
        rec.id = uuid.uuid4()
        rec.target_entity_id = str(uuid.uuid4())
        rec.role_id = str(uuid.uuid4())
        rec.rejection_reason = rejection_reason

        tech = MagicMock()
        tech.id = rec.target_entity_id
        tech.career_stage = "In Training"
        tech.full_name = "Jane Smith"

        role = MagicMock()
        role.id = rec.role_id
        role.role_name = "Fiber Tech Lead"
        role.required_skills = []
        role.required_certs = []
        role.project = MagicMock()
        role.project.location_region = "California"

        session.get.side_effect = lambda model, id_val: (
            tech if model.__name__ == "Technician" else
            role if model.__name__ == "ProjectRole" else None
        )

        return session, rec, tech, role

    def test_creates_proposed_rule(self):
        session, rec, tech, role = self._make_session_and_rec("Too junior, needs more experience")
        rule = propose_preference_rule(session, rec, "Too junior, needs more experience", tech, role)

        assert isinstance(rule, PreferenceRule)
        assert rule.status == PreferenceRuleStatus.PROPOSED.value
        assert rule.active is False
        assert rule.rule_type == "experience_threshold"
        assert rule.created_by_type == PreferenceRuleCreatedByType.AGENT.value
        assert rule.created_by_id == "rejection_learning_agent"
        assert rule.source_recommendation_id == rec.id
        assert rule.rejection_id == rec.id
        assert "Proposed based on rejection" in rule.proposed_reason

    def test_proposed_rule_has_parameters(self):
        session, rec, tech, role = self._make_session_and_rec("Missing OSHA certification")
        rule = propose_preference_rule(session, rec, "Missing OSHA certification", tech, role)

        assert rule.parameters is not None
        assert "confidence" in rule.parameters
        assert rule.parameters["confidence"] > 0

    def test_proposed_rule_has_threshold(self):
        session, rec, tech, role = self._make_session_and_rec("Needs better fiber splicing skills")
        rule = propose_preference_rule(session, rec, "Needs better fiber splicing skills", tech, role)

        assert rule.threshold is not None
        assert len(rule.threshold) > 0

    def test_proposed_rule_has_template_type(self):
        session, rec, tech, role = self._make_session_and_rec("Location too far, travel distance")
        rule = propose_preference_rule(session, rec, "Location too far, travel distance", tech, role)

        assert rule.template_type is not None
        assert rule.template_type != ""

    def test_experience_rule_suggests_higher_stage(self):
        session, rec, tech, role = self._make_session_and_rec("Too green, needs more experience")
        tech.career_stage = "Screened"
        rule = propose_preference_rule(session, rec, "Too green, needs more experience", tech, role)

        assert rule.rule_type == "experience_threshold"
        assert rule.parameters.get("min_career_stage") == "In Training"

    def test_cert_rule_proposes_exclusion(self):
        session, rec, tech, role = self._make_session_and_rec("No OSHA 30 certification")
        rule = propose_preference_rule(session, rec, "No OSHA 30 certification", tech, role)

        assert rule.rule_type == "cert_requirement"
        assert rule.effect == "exclude"
        assert rule.parameters.get("cert_name") == "OSHA 30"

    def test_skill_rule_proposes_demotion(self):
        session, rec, tech, role = self._make_session_and_rec("Fiber splicing proficiency too low")
        rule = propose_preference_rule(session, rec, "Fiber splicing proficiency too low", tech, role)

        assert rule.rule_type == "skill_level_minimum"
        assert rule.effect == "demote"

    def test_adds_rule_to_session(self):
        session, rec, tech, role = self._make_session_and_rec("Schedule conflict")
        rule = propose_preference_rule(session, rec, "Schedule conflict", tech, role)

        session.add.assert_called_once_with(rule)

    def test_score_modifier_set_for_demote(self):
        session, rec, tech, role = self._make_session_and_rec("Too junior")
        rule = propose_preference_rule(session, rec, "Too junior", tech, role)

        # Demote effect should have negative modifier
        assert rule.score_modifier < 0

    def test_score_modifier_set_for_exclude(self):
        session, rec, tech, role = self._make_session_and_rec("Missing OSHA certification")
        rule = propose_preference_rule(session, rec, "Missing OSHA certification", tech, role)

        if rule.effect == "exclude":
            assert rule.score_modifier == -100.0


# ---------------------------------------------------------------------------
# Skill / Cert Name Extraction Tests
# ---------------------------------------------------------------------------

class TestSkillNameExtraction:
    """Test _extract_skill_name from rejection reasons."""

    def test_known_skill_in_reason(self):
        result = _extract_skill_name("poor fusion splicing ability", None, None, MagicMock())
        assert result == "Fusion Splicing"

    def test_otdr_testing(self):
        result = _extract_skill_name("failed otdr testing assessment", None, None, MagicMock())
        assert result == "Otdr Testing"

    def test_from_role_required_skills(self):
        role = MagicMock()
        role.required_skills = [{"skill_name": "Conduit Placement"}]
        result = _extract_skill_name("can't do conduit placement", None, role, MagicMock())
        assert result == "Conduit Placement"

    def test_fallback_to_general(self):
        result = _extract_skill_name("not good enough", None, None, MagicMock())
        assert result == "General Skills"


class TestCertNameExtraction:
    """Test _extract_cert_name from rejection reasons."""

    def test_osha_10(self):
        assert _extract_cert_name("missing osha 10", None) == "OSHA 10"

    def test_cpr(self):
        assert _extract_cert_name("no cpr certification", None) == "CPR"

    def test_cdl(self):
        assert _extract_cert_name("needs cdl license", None) == "CDL"

    def test_from_role_certs(self):
        # "confined space" matches the known cert "Confined Space" before role certs check
        role = MagicMock()
        role.required_certs = ["Confined Space Entry"]
        result = _extract_cert_name("confined space entry needed", role)
        # Known cert "Confined Space" matches first
        assert "Confined Space" in result

    def test_fallback_to_first_required(self):
        role = MagicMock()
        role.required_certs = ["BICSI", "OSHA 30"]
        result = _extract_cert_name("missing required cert", role)
        assert result == "BICSI"

    def test_fallback_no_role(self):
        result = _extract_cert_name("missing something", None)
        assert result == "OSHA 10"


# ---------------------------------------------------------------------------
# Rejection Pattern Analysis Tests
# ---------------------------------------------------------------------------

class TestRejectionPatternAnalysis:
    """Test analyze_rejection_patterns for batch suggestions."""

    def _make_rejected_rec(self, reason):
        rec = MagicMock(spec=Recommendation)
        rec.id = uuid.uuid4()
        rec.rejection_reason = reason
        rec.status = RecommendationStatus.REJECTED.value
        rec.updated_at = datetime.now(timezone.utc)
        return rec

    def test_detects_pattern_from_multiple_rejections(self):
        session = MagicMock()
        recs = [
            self._make_rejected_rec("Too junior, needs more experience"),
            self._make_rejected_rec("Not experienced enough for senior role"),
            self._make_rejected_rec("Missing OSHA certification"),
        ]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = recs
        session.query.return_value = mock_query

        patterns = analyze_rejection_patterns(session)
        # Should detect experience pattern (2 experience rejections)
        assert len(patterns) >= 1
        exp_pattern = [p for p in patterns if p["rule_type"] == "experience_threshold"]
        assert len(exp_pattern) == 1
        assert exp_pattern[0]["occurrence_count"] == 2

    def test_no_patterns_when_all_unique(self):
        session = MagicMock()
        recs = [
            self._make_rejected_rec("Too junior"),
            self._make_rejected_rec("Missing OSHA cert"),
            self._make_rejected_rec("Too far away location"),
        ]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = recs
        session.query.return_value = mock_query

        patterns = analyze_rejection_patterns(session)
        # Each rejection matches a different template, so no patterns
        assert len(patterns) == 0

    def test_empty_rejections_returns_empty(self):
        session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        session.query.return_value = mock_query

        patterns = analyze_rejection_patterns(session)
        assert patterns == []


# ---------------------------------------------------------------------------
# Template Registry Tests
# ---------------------------------------------------------------------------

class TestRuleTemplates:
    """Verify the rule template registry is well-formed."""

    def test_all_templates_have_keywords(self):
        for name, template in RULE_TEMPLATES.items():
            assert len(template.keywords) > 0, f"Template {name} has no keywords"

    def test_all_templates_have_description(self):
        for name, template in RULE_TEMPLATES.items():
            assert template.description, f"Template {name} has no description"

    def test_all_templates_have_valid_effect(self):
        valid_effects = {"exclude", "demote", "boost"}
        for name, template in RULE_TEMPLATES.items():
            assert template.default_effect in valid_effects, (
                f"Template {name} has invalid effect: {template.default_effect}"
            )

    def test_all_templates_have_valid_scope(self):
        valid_scopes = {"global", "client", "project_type"}
        for name, template in RULE_TEMPLATES.items():
            assert template.default_scope in valid_scopes, (
                f"Template {name} has invalid scope: {template.default_scope}"
            )

    def test_all_templates_have_parameter_schema(self):
        for name, template in RULE_TEMPLATES.items():
            assert isinstance(template.parameter_schema, dict), (
                f"Template {name} has invalid parameter_schema"
            )
            assert len(template.parameter_schema) > 0, (
                f"Template {name} has empty parameter_schema"
            )
