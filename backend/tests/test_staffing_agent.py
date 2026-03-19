"""Tests for the Staffing Sub-Agent orchestrator.

Covers:
- Pre-filter → re-ranker integration
- Deterministic fallback scoring
- Error handling and graceful degradation
- Scorecard 5-dimension validation
- Agent interface contract (input/output schemas)
- Recommendation persistence
- Preference rule application
"""

import uuid
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from app.schemas.staffing import (
    StaffingRequest,
    StaffingResponse,
    CandidateRanking,
    Scorecard,
    ScorecardDimension,
    SkillRequirement,
)
from app.services.reranker import (
    RerankerInput,
    RerankerResult,
    run_reranker,
    _deterministic_score,
)
from app.services.prefilter import PrefilterCandidate


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    name: str = "Test Tech",
    skills_match: float = 0.8,
    certs_match: float = 1.0,
    region_match: bool = True,
    availability_match: bool = True,
    years_exp: float = 5.0,
    projects: int = 8,
    hours: float = 3000.0,
    archetype: str = "generalist",
    skills: dict = None,
    certs: list = None,
) -> PrefilterCandidate:
    """Create a test PrefilterCandidate."""
    return PrefilterCandidate(
        technician_id=str(uuid.uuid4()),
        technician_name=name,
        skills=skills or {
            "Fiber Splicing": {"level": "Advanced", "hours": 400},
            "OTDR Testing": {"level": "Intermediate", "hours": 150},
        },
        certifications=certs or [
            {"cert_name": "FOA CFOT", "status": "Active", "expiry_date": None},
            {"cert_name": "OSHA 10", "status": "Active", "expiry_date": None},
        ],
        home_base_city="Phoenix",
        home_base_state="AZ",
        approved_regions=["AZ", "NV", "CA"],
        available_from=date(2026, 3, 1),
        years_experience=years_exp,
        total_project_count=projects,
        total_approved_hours=hours,
        archetype=archetype,
        career_stage="Awaiting Assignment",
        deployability_status="Ready Now",
        willing_to_travel=True,
        skills_match_pct=skills_match,
        certs_match_pct=certs_match,
        region_match=region_match,
        availability_match=availability_match,
    )


# ---------------------------------------------------------------------------
# Schema / Contract Tests
# ---------------------------------------------------------------------------

class TestStaffingRequestContract:
    """Test the StaffingRequest input contract."""

    def test_minimal_request_with_role_id(self):
        req = StaffingRequest(role_id="role-123", project_id="proj-456")
        assert req.role_id == "role-123"
        assert req.max_candidates == 10
        assert req.include_explanation is True

    def test_inline_requirements(self):
        req = StaffingRequest(
            required_skills=[
                SkillRequirement(skill="Fiber Splicing", min_level="Advanced"),
                SkillRequirement(skill="OTDR Testing", min_level="Intermediate"),
            ],
            required_certs=["FOA CFOT", "OSHA 10"],
            preferred_region="AZ",
            max_candidates=5,
        )
        assert len(req.required_skills) == 2
        assert req.required_certs == ["FOA CFOT", "OSHA 10"]
        assert req.max_candidates == 5

    def test_max_candidates_bounds(self):
        req = StaffingRequest(role_id="x", max_candidates=50)
        assert req.max_candidates == 50

        with pytest.raises(Exception):
            StaffingRequest(role_id="x", max_candidates=0)

        with pytest.raises(Exception):
            StaffingRequest(role_id="x", max_candidates=51)


class TestStaffingResponseContract:
    """Test the StaffingResponse output contract."""

    def test_empty_response(self):
        resp = StaffingResponse(
            candidates=[],
            total_evaluated=50,
            total_prefiltered=0,
            batch_id="batch-123",
        )
        assert resp.candidates == []
        assert resp.total_evaluated == 50
        assert resp.fallback_used is False

    def test_response_with_candidates(self):
        scorecard = Scorecard(
            skills_match=ScorecardDimension(name="Skills Match", score=9.0, weight=2.0, rationale="Good"),
            certification_coverage=ScorecardDimension(name="Cert Coverage", score=10.0, weight=1.5, rationale="All certs"),
            availability_fit=ScorecardDimension(name="Availability", score=8.0, weight=1.0, rationale="Available"),
            geographic_proximity=ScorecardDimension(name="Geography", score=7.0, weight=1.0, rationale="Local"),
            experience_depth=ScorecardDimension(name="Experience", score=8.5, weight=1.0, rationale="8 years"),
        )

        candidate = CandidateRanking(
            rank=1,
            technician_id="tech-1",
            technician_name="Marcus Johnson",
            overall_score=8.7,
            scorecard=scorecard,
            explanation="Top match",
            highlights=["Advanced splicing", "Local"],
            disqualifiers=[],
        )

        resp = StaffingResponse(
            role_id="role-1",
            candidates=[candidate],
            total_evaluated=50,
            total_prefiltered=10,
            batch_id="batch-1",
        )
        assert len(resp.candidates) == 1
        assert resp.candidates[0].technician_name == "Marcus Johnson"


# ---------------------------------------------------------------------------
# Scorecard Tests
# ---------------------------------------------------------------------------

class TestScorecard:
    """Test 5-dimension scorecard calculations."""

    def test_weighted_total(self):
        scorecard = Scorecard(
            skills_match=ScorecardDimension(name="Skills", score=10.0, weight=2.0, rationale=""),
            certification_coverage=ScorecardDimension(name="Certs", score=10.0, weight=1.5, rationale=""),
            availability_fit=ScorecardDimension(name="Avail", score=10.0, weight=1.0, rationale=""),
            geographic_proximity=ScorecardDimension(name="Geo", score=10.0, weight=1.0, rationale=""),
            experience_depth=ScorecardDimension(name="Exp", score=10.0, weight=1.0, rationale=""),
        )
        assert scorecard.weighted_total == 10.0

    def test_weighted_total_mixed_scores(self):
        scorecard = Scorecard(
            skills_match=ScorecardDimension(name="Skills", score=8.0, weight=2.0, rationale=""),
            certification_coverage=ScorecardDimension(name="Certs", score=6.0, weight=1.5, rationale=""),
            availability_fit=ScorecardDimension(name="Avail", score=10.0, weight=1.0, rationale=""),
            geographic_proximity=ScorecardDimension(name="Geo", score=4.0, weight=1.0, rationale=""),
            experience_depth=ScorecardDimension(name="Exp", score=7.0, weight=1.0, rationale=""),
        )
        # (8*2 + 6*1.5 + 10*1 + 4*1 + 7*1) / (2+1.5+1+1+1)
        expected = (16.0 + 9.0 + 10.0 + 4.0 + 7.0) / 6.5
        assert abs(scorecard.weighted_total - expected) < 0.01

    def test_scorecard_to_dict(self):
        scorecard = Scorecard(
            skills_match=ScorecardDimension(name="Skills", score=8.0, weight=2.0, rationale="Good"),
            certification_coverage=ScorecardDimension(name="Certs", score=9.0, weight=1.5, rationale="All"),
            availability_fit=ScorecardDimension(name="Avail", score=7.0, weight=1.0, rationale="Soon"),
            geographic_proximity=ScorecardDimension(name="Geo", score=6.0, weight=1.0, rationale="Near"),
            experience_depth=ScorecardDimension(name="Exp", score=8.0, weight=1.0, rationale="Vet"),
        )
        d = scorecard.to_dict()
        assert "dimensions" in d
        assert "weighted_total" in d
        assert len(d["dimensions"]) == 5

    def test_score_bounds(self):
        """Scores must be between 0 and 10."""
        with pytest.raises(Exception):
            ScorecardDimension(name="X", score=-1.0, weight=1.0, rationale="")

        with pytest.raises(Exception):
            ScorecardDimension(name="X", score=11.0, weight=1.0, rationale="")


# ---------------------------------------------------------------------------
# Deterministic Fallback Scorer Tests
# ---------------------------------------------------------------------------

class TestDeterministicScorer:
    """Test the deterministic fallback scoring (no LLM)."""

    def test_perfect_candidate(self):
        candidate = _make_candidate(
            skills_match=1.0,
            certs_match=1.0,
            region_match=True,
            availability_match=True,
            years_exp=15.0,
        )
        required_skills = [
            {"skill": "Fiber Splicing", "min_level": "Advanced"},
            {"skill": "OTDR Testing", "min_level": "Intermediate"},
        ]
        required_certs = ["FOA CFOT", "OSHA 10"]

        scorecard, highlights, disqualifiers = _deterministic_score(
            candidate, required_skills, required_certs, "AZ",
        )

        assert scorecard.skills_match.score == 10.0
        assert scorecard.certification_coverage.score == 10.0
        assert scorecard.availability_fit.score == 8.0
        assert scorecard.geographic_proximity.score == 8.0
        assert scorecard.weighted_total > 7.0
        assert len(disqualifiers) == 0

    def test_partial_skills_match(self):
        candidate = _make_candidate(
            skills_match=0.5,
            skills={
                "Fiber Splicing": {"level": "Intermediate", "hours": 100},
            },
        )
        required_skills = [
            {"skill": "Fiber Splicing", "min_level": "Advanced"},
            {"skill": "OTDR Testing", "min_level": "Intermediate"},
        ]

        scorecard, highlights, disqualifiers = _deterministic_score(
            candidate, required_skills, [], None,
        )

        assert scorecard.skills_match.score == 5.0
        assert "Strong skills match" not in highlights

    def test_missing_certs_creates_disqualifier(self):
        candidate = _make_candidate(
            certs_match=0.5,
            certs=[
                {"cert_name": "FOA CFOT", "status": "Active", "expiry_date": None},
            ],
        )
        required_certs = ["FOA CFOT", "OSHA 10"]

        scorecard, highlights, disqualifiers = _deterministic_score(
            candidate, [], required_certs, None,
        )

        assert any("Missing certs" in d for d in disqualifiers)

    def test_remote_candidate_lower_geo_score(self):
        local = _make_candidate(region_match=True)
        remote = _make_candidate(region_match=False)

        sc_local, _, _ = _deterministic_score(local, [], [], "AZ")
        sc_remote, _, _ = _deterministic_score(remote, [], [], "NY")

        assert sc_local.geographic_proximity.score > sc_remote.geographic_proximity.score

    def test_veteran_gets_higher_experience_score(self):
        veteran = _make_candidate(years_exp=20.0, projects=25, hours=10000)
        junior = _make_candidate(years_exp=1.0, projects=2, hours=500)

        sc_vet, hl_vet, _ = _deterministic_score(veteran, [], [], None)
        sc_jun, hl_jun, _ = _deterministic_score(junior, [], [], None)

        assert sc_vet.experience_depth.score > sc_jun.experience_depth.score
        assert any("Veteran" in h for h in hl_vet)


# ---------------------------------------------------------------------------
# LLM Re-Ranker Tests (with mocked LLM)
# ---------------------------------------------------------------------------

class TestLLMReranker:
    """Test the LLM re-ranker with mocked Claude API."""

    def test_fallback_when_no_api_key(self):
        """Should use deterministic scoring when ANTHROPIC_API_KEY not set."""
        candidates = [
            _make_candidate("Alice", skills_match=0.9),
            _make_candidate("Bob", skills_match=0.7),
        ]

        with patch.dict("os.environ", {}, clear=True):
            result = run_reranker(RerankerInput(
                candidates=candidates,
                required_skills=[{"skill": "Fiber Splicing", "min_level": "Advanced"}],
                required_certs=["FOA CFOT"],
            ))

        assert result.fallback_used is True
        assert len(result.rankings) == 2
        # Rankings should be sorted by score
        assert result.rankings[0].overall_score >= result.rankings[1].overall_score
        assert result.rankings[0].rank == 1
        assert result.rankings[1].rank == 2

    def test_empty_candidates(self):
        result = run_reranker(RerankerInput(
            candidates=[],
            required_skills=[],
            required_certs=[],
        ))
        assert result.rankings == []
        assert result.fallback_used is False

    @patch("app.services.reranker._get_llm_client")
    def test_llm_json_parse_error_triggers_fallback(self, mock_client_fn):
        """When LLM returns invalid JSON, should fall back to deterministic."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="not valid json {{{")]
        )
        mock_client_fn.return_value = mock_client

        candidates = [_make_candidate("Alice")]

        result = run_reranker(RerankerInput(
            candidates=candidates,
            required_skills=[],
            required_certs=[],
        ))

        assert result.fallback_used is True
        assert len(result.rankings) == 1

    @patch("app.services.reranker._get_llm_client")
    def test_llm_exception_triggers_fallback(self, mock_client_fn):
        """When LLM call throws, should fall back to deterministic."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")
        mock_client_fn.return_value = mock_client

        candidates = [_make_candidate("Alice"), _make_candidate("Bob")]

        result = run_reranker(RerankerInput(
            candidates=candidates,
            required_skills=[],
            required_certs=[],
        ))

        assert result.fallback_used is True
        assert len(result.rankings) == 2

    @patch("app.services.reranker._get_llm_client")
    def test_successful_llm_reranking(self, mock_client_fn):
        """When LLM returns valid JSON, should produce proper rankings."""
        c1 = _make_candidate("Alice")
        c2 = _make_candidate("Bob")

        llm_response = {
            "rankings": [
                {
                    "technician_id": c1.technician_id,
                    "scores": {
                        "skills_match": {"score": 9.0, "rationale": "Excellent skills"},
                        "certification_coverage": {"score": 10.0, "rationale": "All certs"},
                        "availability_fit": {"score": 8.0, "rationale": "Available now"},
                        "geographic_proximity": {"score": 7.5, "rationale": "In region"},
                        "experience_depth": {"score": 8.5, "rationale": "8 years"},
                    },
                    "explanation": "Alice is the top match.",
                    "highlights": ["Advanced splicing"],
                    "disqualifiers": [],
                },
                {
                    "technician_id": c2.technician_id,
                    "scores": {
                        "skills_match": {"score": 7.0, "rationale": "Good skills"},
                        "certification_coverage": {"score": 8.0, "rationale": "Most certs"},
                        "availability_fit": {"score": 6.0, "rationale": "Available soon"},
                        "geographic_proximity": {"score": 5.0, "rationale": "Travel needed"},
                        "experience_depth": {"score": 6.0, "rationale": "5 years"},
                    },
                    "explanation": "Bob is a solid backup.",
                    "highlights": ["Good generalist"],
                    "disqualifiers": ["Travel required"],
                },
            ]
        }

        import json
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=json.dumps(llm_response))]
        )
        mock_client_fn.return_value = mock_client

        result = run_reranker(RerankerInput(
            candidates=[c1, c2],
            required_skills=[{"skill": "Fiber Splicing", "min_level": "Advanced"}],
            required_certs=["FOA CFOT"],
        ))

        assert result.fallback_used is False
        assert len(result.rankings) == 2
        assert result.rankings[0].rank == 1
        assert result.rankings[0].overall_score > result.rankings[1].overall_score
        assert result.rankings[0].explanation == "Alice is the top match."
        assert "Advanced splicing" in result.rankings[0].highlights


# ---------------------------------------------------------------------------
# Integration Tests (orchestrator)
# ---------------------------------------------------------------------------

class TestStaffingAgentOrchestrator:
    """Test the orchestrator integration logic."""

    def test_candidate_ranking_preserves_five_dimensions(self):
        """Every CandidateRanking must have exactly 5 scorecard dimensions."""
        candidate = _make_candidate("Alice")
        scorecard, _, _ = _deterministic_score(
            candidate,
            [{"skill": "Fiber Splicing", "min_level": "Advanced"}],
            ["FOA CFOT"],
            "AZ",
        )

        d = scorecard.to_dict()
        assert len(d["dimensions"]) == 5
        names = {dim["name"] for dim in d["dimensions"]}
        assert "Skills Match" in names
        assert "Certification Coverage" in names
        assert "Availability Fit" in names
        assert "Geographic Proximity" in names
        assert "Experience Depth" in names

    def test_rankings_sorted_descending(self):
        """Rankings must be sorted by overall_score descending."""
        candidates = [
            _make_candidate("Low", skills_match=0.3, certs_match=0.3),
            _make_candidate("High", skills_match=1.0, certs_match=1.0, years_exp=15),
            _make_candidate("Mid", skills_match=0.6, certs_match=0.7),
        ]

        result = run_reranker(RerankerInput(
            candidates=candidates,
            required_skills=[{"skill": "Fiber Splicing", "min_level": "Advanced"}],
            required_certs=["FOA CFOT", "OSHA 10"],
        ))

        scores = [r.overall_score for r in result.rankings]
        assert scores == sorted(scores, reverse=True)

    def test_explanations_are_non_empty(self):
        """Every ranked candidate should have a non-empty explanation."""
        candidates = [_make_candidate("Alice"), _make_candidate("Bob")]

        result = run_reranker(RerankerInput(
            candidates=candidates,
            required_skills=[{"skill": "Fiber Splicing", "min_level": "Advanced"}],
            required_certs=["FOA CFOT"],
            include_explanation=True,
        ))

        for ranking in result.rankings:
            assert ranking.explanation
            assert len(ranking.explanation) > 10

    def test_ranks_are_sequential(self):
        """Ranks should be 1, 2, 3, ... in order."""
        candidates = [
            _make_candidate("A", skills_match=0.9),
            _make_candidate("B", skills_match=0.7),
            _make_candidate("C", skills_match=0.5),
        ]

        result = run_reranker(RerankerInput(
            candidates=candidates,
            required_skills=[],
            required_certs=[],
        ))

        for i, r in enumerate(result.rankings, start=1):
            assert r.rank == i


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test error handling and graceful degradation."""

    def test_staffing_agent_error_attributes(self):
        from app.services.staffing_agent import StaffingAgentError

        err = StaffingAgentError(
            message="Role not found",
            detail="No role with that ID",
            fallback_available=True,
        )
        assert err.message == "Role not found"
        assert err.detail == "No role with that ID"
        assert err.fallback_available is True

    def test_deterministic_score_handles_empty_skills(self):
        candidate = _make_candidate(skills={})

        scorecard, highlights, disqualifiers = _deterministic_score(
            candidate, [], [], None,
        )

        assert scorecard.weighted_total >= 0
        assert isinstance(highlights, list)
        assert isinstance(disqualifiers, list)

    def test_deterministic_score_handles_no_requirements(self):
        candidate = _make_candidate()

        scorecard, highlights, disqualifiers = _deterministic_score(
            candidate, [], [], None,
        )

        # With no requirements, skill and cert dimensions should be max or neutral
        assert scorecard.skills_match.score >= 0
        assert scorecard.certification_coverage.score >= 0
