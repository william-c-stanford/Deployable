"""
Tests for the LLM re-ranking chain.

Tests cover:
- Schema validation (Scorecard, CandidateProfile, RerankingInput/Output)
- Deterministic scoring logic (all 5 dimensions)
- Preference rule application
- Chain builder fallback behavior
- Prompt formatting
- Response parsing
- End-to-end deterministic re-ranking with realistic data
"""

import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from agents.schemas import (
    CandidateProfile,
    CandidateSkill,
    CandidateCert,
    DimensionScore,
    RankedCandidate,
    RerankingInput,
    RerankingOutput,
    RoleRequirements,
    Scorecard,
    SkillRequirement,
)
from agents.config import AgentConfig
from agents.reranking_chain import (
    build_reranking_chain,
    deterministic_rerank,
    rerank_candidates,
    _deterministic_score_candidate,
    _format_candidate,
    _format_preference_rules,
    _build_prompt_variables,
    _parse_scorecard,
    _parse_reranking_response,
    _proficiency_rank,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_role() -> RoleRequirements:
    """A realistic Lead Splicer role for testing."""
    return RoleRequirements(
        role_id="role_atl_lead_splicer",
        role_name="Lead Splicer",
        project_id="proj_atl_fiber_ring",
        project_name="Atlanta Metro Fiber Ring Expansion",
        project_location_city="Atlanta",
        project_location_region="GA",
        required_skills=[
            SkillRequirement(skill_name="Fiber Splicing", min_proficiency="Advanced"),
            SkillRequirement(skill_name="OTDR Testing", min_proficiency="Intermediate"),
        ],
        required_certs=["FOA CFOT", "OSHA 10"],
        quantity_needed=2,
        hourly_rate_budget=52.00,
        per_diem_budget=75.00,
        start_date=date(2026, 4, 1),
    )


@pytest.fixture
def strong_candidate() -> CandidateProfile:
    """A strong candidate — local, experienced, right skills."""
    return CandidateProfile(
        technician_id="tech-001",
        full_name="Marcus Johnson",
        home_base_city="Atlanta",
        home_base_state="GA",
        approved_regions=["GA", "FL", "SC", "NC"],
        willing_to_travel=True,
        max_travel_radius_miles=200,
        career_stage="Deployed",
        deployability_status="Ready Now",
        available_from=date(2026, 3, 15),
        archetype="senior_specialist",
        years_experience=12.0,
        total_project_count=28,
        total_approved_hours=14500.0,
        hourly_rate_min=48.0,
        hourly_rate_max=55.0,
        docs_verified=True,
        skills=[
            CandidateSkill(skill_name="Fiber Splicing", proficiency_level="Advanced", training_hours=450.0),
            CandidateSkill(skill_name="OTDR Testing", proficiency_level="Advanced", training_hours=320.0),
            CandidateSkill(skill_name="Cable Pulling", proficiency_level="Intermediate", training_hours=200.0),
        ],
        certifications=[
            CandidateCert(cert_name="FOA CFOT", status="Active", expiry_date=date(2027, 6, 1)),
            CandidateCert(cert_name="OSHA 10", status="Active"),
        ],
        badge_count=8,
        sql_score=92.5,
    )


@pytest.fixture
def weak_candidate() -> CandidateProfile:
    """A weak candidate — remote, beginner, missing certs."""
    return CandidateProfile(
        technician_id="tech-002",
        full_name="Tyler Reed",
        home_base_city="Seattle",
        home_base_state="WA",
        approved_regions=["WA", "OR"],
        willing_to_travel=False,
        max_travel_radius_miles=50,
        career_stage="In Training",
        deployability_status="In Training",
        available_from=date(2026, 7, 1),
        archetype="apprentice",
        years_experience=0.5,
        total_project_count=0,
        total_approved_hours=80.0,
        hourly_rate_min=28.0,
        hourly_rate_max=32.0,
        docs_verified=False,
        skills=[
            CandidateSkill(skill_name="Fiber Splicing", proficiency_level="Beginner", training_hours=60.0),
            CandidateSkill(skill_name="Cable Pulling", proficiency_level="Beginner", training_hours=20.0),
        ],
        certifications=[
            CandidateCert(cert_name="OSHA 10", status="Active"),
        ],
        badge_count=0,
        sql_score=25.0,
    )


@pytest.fixture
def medium_candidate() -> CandidateProfile:
    """A medium candidate — nearby, intermediate skills, decent experience."""
    return CandidateProfile(
        technician_id="tech-003",
        full_name="Sarah Chen",
        home_base_city="Savannah",
        home_base_state="GA",
        approved_regions=["GA", "SC"],
        willing_to_travel=True,
        max_travel_radius_miles=300,
        career_stage="Awaiting Assignment",
        deployability_status="Ready Now",
        available_from=date(2026, 3, 20),
        archetype="generalist",
        years_experience=5.0,
        total_project_count=12,
        total_approved_hours=5800.0,
        hourly_rate_min=40.0,
        hourly_rate_max=48.0,
        docs_verified=True,
        skills=[
            CandidateSkill(skill_name="Fiber Splicing", proficiency_level="Intermediate", training_hours=250.0),
            CandidateSkill(skill_name="OTDR Testing", proficiency_level="Intermediate", training_hours=180.0),
            CandidateSkill(skill_name="Structured Cabling", proficiency_level="Intermediate", training_hours=200.0),
        ],
        certifications=[
            CandidateCert(cert_name="FOA CFOT", status="Active", expiry_date=date(2027, 3, 15)),
            CandidateCert(cert_name="OSHA 10", status="Active"),
            CandidateCert(cert_name="BICSI Installer 1", status="Active"),
        ],
        badge_count=4,
        sql_score=68.0,
    )


@pytest.fixture
def sample_input(sample_role, strong_candidate, weak_candidate, medium_candidate) -> RerankingInput:
    """Complete re-ranking input with 3 candidates."""
    return RerankingInput(
        role=sample_role,
        candidates=[strong_candidate, weak_candidate, medium_candidate],
    )


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------

class TestSchemas:
    """Test Pydantic schema validation."""

    def test_dimension_score_valid(self):
        ds = DimensionScore(dimension="skill_match", score=8.5, reasoning="Good match")
        assert ds.score == 8.5
        assert ds.dimension == "skill_match"

    def test_dimension_score_bounds(self):
        with pytest.raises(Exception):
            DimensionScore(dimension="skill_match", score=11.0, reasoning="Too high")
        with pytest.raises(Exception):
            DimensionScore(dimension="skill_match", score=-1.0, reasoning="Too low")

    def test_scorecard_weighted_total(self):
        sc = Scorecard(
            skill_match=DimensionScore(dimension="skill_match", score=8.0, reasoning="Good"),
            proximity=DimensionScore(dimension="proximity", score=9.0, reasoning="Local"),
            availability=DimensionScore(dimension="availability", score=7.0, reasoning="Ready"),
            cost_efficiency=DimensionScore(dimension="cost_efficiency", score=6.0, reasoning="Fair"),
            past_performance=DimensionScore(dimension="past_performance", score=8.0, reasoning="Strong"),
        )
        # 8*0.30 + 9*0.15 + 7*0.20 + 6*0.15 + 8*0.20 = 2.4+1.35+1.4+0.9+1.6 = 7.65
        assert sc.weighted_total == 7.65

    def test_scorecard_to_dict(self):
        sc = Scorecard(
            skill_match=DimensionScore(dimension="skill_match", score=8.0, reasoning="Good"),
            proximity=DimensionScore(dimension="proximity", score=9.0, reasoning="Local"),
            availability=DimensionScore(dimension="availability", score=7.0, reasoning="Ready"),
            cost_efficiency=DimensionScore(dimension="cost_efficiency", score=6.0, reasoning="Fair"),
            past_performance=DimensionScore(dimension="past_performance", score=8.0, reasoning="Strong"),
        )
        d = sc.to_dict()
        assert "weighted_total" in d
        assert d["skill_match"]["score"] == 8.0
        assert d["proximity"]["reasoning"] == "Local"

    def test_candidate_profile_defaults(self):
        cp = CandidateProfile(
            technician_id="test-id",
            full_name="Test User",
            home_base_city="Test City",
            home_base_state="TX",
        )
        assert cp.willing_to_travel is True
        assert cp.skills == []
        assert cp.sql_score == 0.0

    def test_role_requirements_minimal(self):
        rr = RoleRequirements(
            role_id="test-role",
            role_name="Test Role",
            project_id="test-project",
            project_name="Test Project",
            project_location_city="Test City",
            project_location_region="TX",
        )
        assert rr.required_skills == []
        assert rr.quantity_needed == 1

    def test_reranking_input_max_candidates(self):
        """Should accept up to 25 candidates."""
        candidates = [
            CandidateProfile(
                technician_id=f"tech-{i}",
                full_name=f"Tech {i}",
                home_base_city="City",
                home_base_state="TX",
            )
            for i in range(25)
        ]
        ri = RerankingInput(
            role=RoleRequirements(
                role_id="r", role_name="R", project_id="p",
                project_name="P", project_location_city="C",
                project_location_region="TX",
            ),
            candidates=candidates,
        )
        assert len(ri.candidates) == 25


# ---------------------------------------------------------------------------
# Proficiency Rank Tests
# ---------------------------------------------------------------------------

class TestProficiencyRank:
    def test_rank_values(self):
        assert _proficiency_rank("Beginner") == 1
        assert _proficiency_rank("Intermediate") == 2
        assert _proficiency_rank("Advanced") == 3
        assert _proficiency_rank("Unknown") == 0


# ---------------------------------------------------------------------------
# Deterministic Scoring Tests
# ---------------------------------------------------------------------------

class TestDeterministicScoring:
    """Test the deterministic (non-LLM) scoring logic."""

    def test_strong_candidate_scores_high(self, sample_role, strong_candidate):
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(strong_candidate, sample_role, weights)
        assert result.weighted_score >= 7.0
        assert result.full_name == "Marcus Johnson"

    def test_weak_candidate_scores_low(self, sample_role, weak_candidate):
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(weak_candidate, sample_role, weights)
        assert result.weighted_score < 5.0

    def test_strong_beats_weak(self, sample_role, strong_candidate, weak_candidate):
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        strong_result = _deterministic_score_candidate(strong_candidate, sample_role, weights)
        weak_result = _deterministic_score_candidate(weak_candidate, sample_role, weights)
        assert strong_result.weighted_score > weak_result.weighted_score

    def test_medium_between_strong_and_weak(self, sample_role, strong_candidate, weak_candidate, medium_candidate):
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        strong_result = _deterministic_score_candidate(strong_candidate, sample_role, weights)
        medium_result = _deterministic_score_candidate(medium_candidate, sample_role, weights)
        weak_result = _deterministic_score_candidate(weak_candidate, sample_role, weights)
        assert strong_result.weighted_score > medium_result.weighted_score > weak_result.weighted_score

    def test_skill_match_scoring(self, sample_role, strong_candidate):
        """Strong candidate has Advanced Fiber Splicing (exceeds req) and Advanced OTDR (exceeds req)."""
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(strong_candidate, sample_role, weights)
        assert result.scorecard.skill_match.score >= 8.0  # Both skills match + exceed

    def test_proximity_same_city(self, sample_role, strong_candidate):
        """Strong candidate is in Atlanta, project is in Atlanta."""
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(strong_candidate, sample_role, weights)
        assert result.scorecard.proximity.score >= 9.0

    def test_proximity_different_state(self, sample_role, weak_candidate):
        """Weak candidate is in Seattle (WA), not willing to travel."""
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(weak_candidate, sample_role, weights)
        assert result.scorecard.proximity.score <= 2.0  # Remote + unwilling

    def test_availability_ready_now(self, sample_role, strong_candidate):
        """Ready Now status + available before start date."""
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(strong_candidate, sample_role, weights)
        assert result.scorecard.availability.score >= 9.0

    def test_availability_in_training(self, sample_role, weak_candidate):
        """In Training status + available after start date."""
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(weak_candidate, sample_role, weights)
        assert result.scorecard.availability.score <= 4.0

    def test_cost_efficiency_below_budget(self, sample_role, medium_candidate):
        """Medium candidate rate $40-48 vs budget $52 — below budget."""
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(medium_candidate, sample_role, weights)
        assert result.scorecard.cost_efficiency.score >= 7.0

    def test_past_performance_experienced(self, sample_role, strong_candidate):
        """12 years, 28 projects, deployed, docs verified, 8 badges."""
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(strong_candidate, sample_role, weights)
        assert result.scorecard.past_performance.score >= 8.0

    def test_flags_for_unverified_docs(self, sample_role, weak_candidate):
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(weak_candidate, sample_role, weights)
        assert "documents not verified" in result.flags

    def test_flags_for_unwilling_to_travel(self, sample_role, weak_candidate):
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(weak_candidate, sample_role, weights)
        assert "unwilling to travel, not local" in result.flags

    def test_explanation_contains_name_and_score(self, sample_role, strong_candidate):
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(strong_candidate, sample_role, weights)
        assert "Marcus Johnson" in result.explanation
        assert "/10" in result.explanation


# ---------------------------------------------------------------------------
# Deterministic Re-ranking (full pipeline) Tests
# ---------------------------------------------------------------------------

class TestDeterministicRerank:
    """Test the full deterministic re-ranking pipeline."""

    def test_ranks_candidates_correctly(self, sample_input):
        result = deterministic_rerank(sample_input)
        assert isinstance(result, RerankingOutput)
        assert len(result.ranked_candidates) == 3
        assert result.ranked_candidates[0].rank == 1
        assert result.ranked_candidates[1].rank == 2
        assert result.ranked_candidates[2].rank == 3

    def test_strong_candidate_ranked_first(self, sample_input):
        result = deterministic_rerank(sample_input)
        assert result.ranked_candidates[0].full_name == "Marcus Johnson"

    def test_weak_candidate_ranked_last(self, sample_input):
        result = deterministic_rerank(sample_input)
        assert result.ranked_candidates[-1].full_name == "Tyler Reed"

    def test_scores_decrease_monotonically(self, sample_input):
        result = deterministic_rerank(sample_input)
        scores = [r.weighted_score for r in result.ranked_candidates]
        assert scores == sorted(scores, reverse=True)

    def test_output_metadata(self, sample_input):
        result = deterministic_rerank(sample_input)
        assert result.role_id == "role_atl_lead_splicer"
        assert result.role_name == "Lead Splicer"
        assert result.total_evaluated == 3
        assert result.agent_model == "deterministic-fallback"
        assert result.processing_time_ms is not None
        assert result.processing_time_ms >= 0

    def test_summary_contains_top_pick(self, sample_input):
        result = deterministic_rerank(sample_input)
        assert "Marcus Johnson" in result.summary

    def test_each_candidate_has_scorecard(self, sample_input):
        result = deterministic_rerank(sample_input)
        for rc in result.ranked_candidates:
            assert rc.scorecard is not None
            assert rc.scorecard.skill_match.score >= 0
            assert rc.scorecard.proximity.score >= 0
            assert rc.scorecard.availability.score >= 0
            assert rc.scorecard.cost_efficiency.score >= 0
            assert rc.scorecard.past_performance.score >= 0

    def test_each_candidate_has_explanation(self, sample_input):
        result = deterministic_rerank(sample_input)
        for rc in result.ranked_candidates:
            assert len(rc.explanation) > 10


# ---------------------------------------------------------------------------
# Preference Rules Tests
# ---------------------------------------------------------------------------

class TestPreferenceRules:
    """Test that preference rules correctly modify scoring."""

    def test_experience_threshold_demote(self, sample_input):
        """Demote candidates with < 3 years experience."""
        sample_input.preference_rules = [
            {
                "rule_type": "experience_threshold",
                "effect": "demote",
                "parameters": {"min_years": 3.0},
            }
        ]
        result = deterministic_rerank(sample_input)
        # Tyler Reed has 0.5 years — should be demoted
        tyler = next(r for r in result.ranked_candidates if r.full_name == "Tyler Reed")
        assert any("experience" in f for f in tyler.flags)

    def test_experience_threshold_exclude(self, sample_input):
        """Exclude candidates with < 1 year experience."""
        sample_input.preference_rules = [
            {
                "rule_type": "experience_threshold",
                "effect": "exclude",
                "parameters": {"min_years": 1.0},
            }
        ]
        result = deterministic_rerank(sample_input)
        tyler = next(r for r in result.ranked_candidates if r.full_name == "Tyler Reed")
        assert tyler.weighted_score == 0.0
        assert tyler.rank == 3  # Should be last

    def test_skill_level_minimum_demote(self, sample_input):
        """Demote candidates without Advanced Fiber Splicing."""
        sample_input.preference_rules = [
            {
                "rule_type": "skill_level_minimum",
                "effect": "demote",
                "parameters": {"skill_name": "Fiber Splicing", "min_level": "Advanced"},
            }
        ]
        result = deterministic_rerank(sample_input)
        # Sarah Chen has Intermediate Fiber Splicing — should be demoted
        sarah = next(r for r in result.ranked_candidates if r.full_name == "Sarah Chen")
        assert any("fiber splicing" in f.lower() for f in sarah.flags)

    def test_multiple_rules_stack(self, sample_input):
        """Multiple rules should stack their effects."""
        sample_input.preference_rules = [
            {
                "rule_type": "experience_threshold",
                "effect": "demote",
                "parameters": {"min_years": 3.0},
            },
            {
                "rule_type": "skill_level_minimum",
                "effect": "demote",
                "parameters": {"skill_name": "Fiber Splicing", "min_level": "Advanced"},
            },
        ]
        result = deterministic_rerank(sample_input)
        # Tyler should be hit by both rules
        tyler = next(r for r in result.ranked_candidates if r.full_name == "Tyler Reed")
        assert len([f for f in tyler.flags if "demoted" in f or "excluded" in f]) >= 2


# ---------------------------------------------------------------------------
# Prompt Formatting Tests
# ---------------------------------------------------------------------------

class TestPromptFormatting:
    def test_format_candidate(self, strong_candidate):
        text = _format_candidate(0, strong_candidate)
        assert "Marcus Johnson" in text
        assert "Atlanta" in text
        assert "Advanced" in text
        assert "Fiber Splicing" in text
        assert "FOA CFOT" in text

    def test_format_preference_rules_empty(self):
        text = _format_preference_rules([])
        assert "No active preference rules" in text

    def test_format_preference_rules_with_rules(self):
        rules = [
            {"rule_type": "experience_threshold", "effect": "demote", "threshold": "3", "parameters": {"min_years": 3}},
        ]
        text = _format_preference_rules(rules)
        assert "experience_threshold" in text
        assert "demote" in text

    def test_build_prompt_variables(self, sample_input):
        vars = _build_prompt_variables(sample_input)
        assert vars["role_name"] == "Lead Splicer"
        assert vars["project_name"] == "Atlanta Metro Fiber Ring Expansion"
        assert vars["candidate_count"] == 3
        assert "Marcus Johnson" in vars["candidates_text"]
        assert "Fiber Splicing" in vars["required_skills_text"]
        assert "FOA CFOT" in vars["required_certs_text"]


# ---------------------------------------------------------------------------
# Response Parsing Tests
# ---------------------------------------------------------------------------

class TestResponseParsing:
    def test_parse_scorecard(self):
        raw = {
            "skill_match": {"score": 8.5, "reasoning": "Good match"},
            "proximity": {"score": 7.0, "reasoning": "Nearby"},
            "availability": {"score": 9.0, "reasoning": "Ready"},
            "cost_efficiency": {"score": 6.0, "reasoning": "Fair"},
            "past_performance": {"score": 8.0, "reasoning": "Solid"},
        }
        sc = _parse_scorecard(raw)
        assert isinstance(sc, Scorecard)
        assert sc.skill_match.score == 8.5
        assert sc.past_performance.reasoning == "Solid"

    def test_parse_scorecard_missing_dimension(self):
        """Missing dimensions should get score 0."""
        raw = {"skill_match": {"score": 8.0, "reasoning": "Ok"}}
        sc = _parse_scorecard(raw)
        assert sc.proximity.score == 0.0

    def test_parse_reranking_response(self, sample_input):
        raw_output = {
            "ranked_candidates": [
                {
                    "rank": 1,
                    "technician_id": "tech-001",
                    "full_name": "Marcus Johnson",
                    "scorecard": {
                        "skill_match": {"score": 9.0, "reasoning": "Excellent"},
                        "proximity": {"score": 9.5, "reasoning": "Local"},
                        "availability": {"score": 9.0, "reasoning": "Ready now"},
                        "cost_efficiency": {"score": 7.0, "reasoning": "Near budget"},
                        "past_performance": {"score": 8.5, "reasoning": "Very experienced"},
                    },
                    "weighted_score": 8.65,
                    "explanation": "Top pick due to local, experienced, advanced skills.",
                    "flags": [],
                },
                {
                    "rank": 2,
                    "technician_id": "tech-003",
                    "full_name": "Sarah Chen",
                    "scorecard": {
                        "skill_match": {"score": 6.0, "reasoning": "Meets minimums"},
                        "proximity": {"score": 7.0, "reasoning": "Same state"},
                        "availability": {"score": 8.0, "reasoning": "Available soon"},
                        "cost_efficiency": {"score": 8.0, "reasoning": "Below budget"},
                        "past_performance": {"score": 6.5, "reasoning": "Moderate experience"},
                    },
                    "weighted_score": 7.05,
                    "explanation": "Solid generalist with cost advantage.",
                    "flags": [],
                },
            ],
            "summary": "Good pool with strong #1 pick.",
        }
        result = _parse_reranking_response(raw_output, sample_input, "test-model", 500.0)
        assert isinstance(result, RerankingOutput)
        assert len(result.ranked_candidates) == 2
        assert result.ranked_candidates[0].full_name == "Marcus Johnson"
        assert result.agent_model == "test-model"
        assert result.processing_time_ms == 500.0


# ---------------------------------------------------------------------------
# Chain Builder Tests
# ---------------------------------------------------------------------------

class TestChainBuilder:
    def test_build_chain_no_api_key(self):
        """Without API key, should return deterministic fallback chain."""
        config = AgentConfig(anthropic_api_key="")
        chain = build_reranking_chain(config)
        assert chain is not None

    def test_chain_no_api_key_produces_output(self, sample_input):
        """Deterministic fallback should produce valid output."""
        config = AgentConfig(anthropic_api_key="")
        chain = build_reranking_chain(config)
        result = chain.invoke(sample_input)
        assert isinstance(result, RerankingOutput)
        assert len(result.ranked_candidates) == 3

    def test_rerank_candidates_convenience(self, sample_role, strong_candidate, medium_candidate):
        """Test the convenience function."""
        config = AgentConfig(anthropic_api_key="")
        result = rerank_candidates(
            role=sample_role,
            candidates=[strong_candidate, medium_candidate],
            config=config,
        )
        assert isinstance(result, RerankingOutput)
        assert result.ranked_candidates[0].full_name == "Marcus Johnson"
        assert len(result.ranked_candidates) == 2


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_candidates_list(self, sample_role):
        """Should handle empty candidate list gracefully."""
        input_data = RerankingInput(
            role=sample_role,
            candidates=[],
        )
        result = deterministic_rerank(input_data)
        assert len(result.ranked_candidates) == 0
        assert "No candidates" in result.summary

    def test_single_candidate(self, sample_role, strong_candidate):
        """Should work with just one candidate."""
        input_data = RerankingInput(
            role=sample_role,
            candidates=[strong_candidate],
        )
        result = deterministic_rerank(input_data)
        assert len(result.ranked_candidates) == 1
        assert result.ranked_candidates[0].rank == 1

    def test_candidate_missing_skills(self, sample_role):
        """Candidate with no skills should get low skill score."""
        no_skills = CandidateProfile(
            technician_id="tech-empty",
            full_name="No Skills Person",
            home_base_city="Atlanta",
            home_base_state="GA",
            deployability_status="Ready Now",
        )
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(no_skills, sample_role, weights)
        assert result.scorecard.skill_match.score == 0.0

    def test_candidate_missing_rates(self, sample_role):
        """Candidate with no rate info should get default cost score."""
        no_rate = CandidateProfile(
            technician_id="tech-norate",
            full_name="No Rate Person",
            home_base_city="Atlanta",
            home_base_state="GA",
        )
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(no_rate, sample_role, weights)
        assert result.scorecard.cost_efficiency.score == 5.0  # Default

    def test_role_with_no_budget(self, strong_candidate):
        """Role with no budget should give default cost score."""
        no_budget_role = RoleRequirements(
            role_id="r", role_name="R", project_id="p",
            project_name="P", project_location_city="Atlanta",
            project_location_region="GA",
        )
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(strong_candidate, no_budget_role, weights)
        assert result.scorecard.cost_efficiency.score == 5.0

    def test_expired_cert_flagged(self, sample_role):
        """Candidate with expired cert should have it flagged."""
        expired_cert = CandidateProfile(
            technician_id="tech-expired",
            full_name="Expired Cert Person",
            home_base_city="Atlanta",
            home_base_state="GA",
            certifications=[
                CandidateCert(cert_name="FOA CFOT", status="Expired", expiry_date=date(2025, 1, 1)),
            ],
        )
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(expired_cert, sample_role, weights)
        assert any("expired cert" in f for f in result.flags)

    def test_cert_expiring_before_start_flagged(self, sample_role):
        """Cert expiring before project start should be flagged."""
        expiring_cert = CandidateProfile(
            technician_id="tech-expiring",
            full_name="Expiring Cert Person",
            home_base_city="Atlanta",
            home_base_state="GA",
            certifications=[
                CandidateCert(cert_name="FOA CFOT", status="Active", expiry_date=date(2026, 3, 15)),
            ],
        )
        weights = {"skill_match": 0.30, "proximity": 0.15, "availability": 0.20, "cost_efficiency": 0.15, "past_performance": 0.20}
        result = _deterministic_score_candidate(expiring_cert, sample_role, weights)
        assert any("cert expires before start" in f for f in result.flags)


# ---------------------------------------------------------------------------
# Large Shortlist Test (20 candidates)
# ---------------------------------------------------------------------------

class TestLargeShortlist:
    """Test with a realistic 20-candidate shortlist."""

    def test_twenty_candidates(self, sample_role):
        """Should handle 20 candidates and rank them consistently."""
        candidates = []
        for i in range(20):
            exp = float(i)
            skill_level = "Advanced" if i >= 15 else ("Intermediate" if i >= 8 else "Beginner")
            status = "Ready Now" if i % 3 == 0 else ("In Training" if i % 3 == 1 else "Rolling Off Soon")

            candidates.append(CandidateProfile(
                technician_id=f"tech-{i:03d}",
                full_name=f"Technician {i:02d}",
                home_base_city="Atlanta" if i % 4 == 0 else "Dallas",
                home_base_state="GA" if i % 4 == 0 else "TX",
                approved_regions=["GA", "TX"],
                willing_to_travel=True,
                career_stage="Deployed" if exp >= 5 else "In Training",
                deployability_status=status,
                available_from=date(2026, 3, 1),
                years_experience=exp,
                total_project_count=i * 2,
                total_approved_hours=i * 500.0,
                hourly_rate_min=30.0 + i,
                hourly_rate_max=40.0 + i,
                docs_verified=i % 2 == 0,
                skills=[
                    CandidateSkill(skill_name="Fiber Splicing", proficiency_level=skill_level, training_hours=i * 30.0),
                    CandidateSkill(skill_name="OTDR Testing", proficiency_level="Intermediate" if i >= 5 else "Beginner", training_hours=i * 15.0),
                ],
                certifications=[
                    CandidateCert(cert_name="FOA CFOT", status="Active"),
                    CandidateCert(cert_name="OSHA 10", status="Active"),
                ],
                badge_count=i,
                sql_score=float(i * 5),
            ))

        input_data = RerankingInput(role=sample_role, candidates=candidates)
        result = deterministic_rerank(input_data)

        assert len(result.ranked_candidates) == 20
        assert result.total_evaluated == 20
        assert result.ranked_candidates[0].rank == 1
        assert result.ranked_candidates[-1].rank == 20

        # Scores should be monotonically non-increasing
        scores = [r.weighted_score for r in result.ranked_candidates]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

        # Every candidate should have a complete scorecard
        for rc in result.ranked_candidates:
            assert rc.scorecard.weighted_total >= 0
            assert len(rc.explanation) > 0
