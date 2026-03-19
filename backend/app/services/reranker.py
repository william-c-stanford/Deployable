"""LLM Re-Ranker stage: Claude-powered candidate scoring.

Takes the pre-filtered candidate list and produces:
- 5-dimension scorecards (Skills Match, Cert Coverage, Availability,
  Geographic Proximity, Experience Depth)
- Natural-language explanations differentiating each candidate
- Final ranked order with overall scores

Uses Claude Haiku for cost efficiency in background agent mode.
Falls back to deterministic scoring if the LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from app.services.prefilter import PrefilterCandidate
from app.schemas.staffing import (
    Scorecard,
    ScorecardDimension,
    CandidateRanking,
)

logger = logging.getLogger("deployable.staffing.reranker")


# ---------------------------------------------------------------------------
# LLM Client (lazy-loaded, optional)
# ---------------------------------------------------------------------------

def _get_llm_client():
    """Get an Anthropic client if the API key is available."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        logger.warning("anthropic package not installed, using fallback scoring")
        return None


# ---------------------------------------------------------------------------
# System prompt for the LLM re-ranker
# ---------------------------------------------------------------------------

RERANKER_SYSTEM_PROMPT = """You are the Deployable Staffing Agent's re-ranking module.
Your job is to evaluate and rank technician candidates for a specific project role.

You will receive:
1. Role requirements (skills, certifications, region, timeline)
2. A list of pre-filtered candidates with their profiles

For each candidate, produce a scorecard with exactly 5 dimensions:
- skills_match (0-10): How well do the candidate's skills align with role requirements? Consider proficiency levels.
- certification_coverage (0-10): What percentage of required certs does the candidate hold? Expired certs score lower.
- availability_fit (0-10): How well does the candidate's availability align with the project timeline?
- geographic_proximity (0-10): Is the candidate local, in-region, or would they need to travel?
- experience_depth (0-10): Years of experience, project count, and total approved hours.

Also provide:
- A 1-2 sentence explanation differentiating this candidate from others
- Key highlights (strengths for this role)
- Any disqualifiers or concerns

Use these weights: skills_match=2.0, certification_coverage=1.5, availability_fit=1.0, geographic_proximity=1.0, experience_depth=1.0

Respond ONLY with valid JSON in this exact format:
{
  "rankings": [
    {
      "technician_id": "...",
      "scores": {
        "skills_match": {"score": 8.5, "rationale": "..."},
        "certification_coverage": {"score": 9.0, "rationale": "..."},
        "availability_fit": {"score": 7.0, "rationale": "..."},
        "geographic_proximity": {"score": 6.5, "rationale": "..."},
        "experience_depth": {"score": 8.0, "rationale": "..."}
      },
      "explanation": "...",
      "highlights": ["...", "..."],
      "disqualifiers": []
    }
  ]
}
"""


@dataclass
class RerankerInput:
    """Input for the LLM re-ranker."""
    candidates: List[PrefilterCandidate]
    required_skills: List[Dict[str, str]]
    required_certs: List[str]
    preferred_region: Optional[str] = None
    role_name: Optional[str] = None
    project_name: Optional[str] = None
    include_explanation: bool = True


@dataclass
class RerankerResult:
    """Output of the LLM re-ranker."""
    rankings: List[CandidateRanking]
    fallback_used: bool = False
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# ---------------------------------------------------------------------------
# Deterministic fallback scorer
# ---------------------------------------------------------------------------

def _deterministic_score(
    candidate: PrefilterCandidate,
    required_skills: List[Dict[str, str]],
    required_certs: List[str],
    preferred_region: Optional[str],
) -> tuple[Scorecard, List[str], List[str]]:
    """Produce a scorecard using deterministic rules (no LLM).

    Returns (scorecard, highlights, disqualifiers).
    """
    # ── Skills match ──
    skills_score = candidate.skills_match_pct * 10.0
    skills_rationale_parts = []
    for req in required_skills:
        skill = req.get("skill", "")
        min_level = req.get("min_level", "Apprentice")
        if skill in candidate.skills:
            actual = candidate.skills[skill]["level"]
            skills_rationale_parts.append(f"{skill}: {actual}")
        else:
            skills_rationale_parts.append(f"{skill}: missing")
    skills_rationale = "; ".join(skills_rationale_parts) if skills_rationale_parts else "No specific skills required"

    # ── Cert coverage ──
    cert_score = candidate.certs_match_pct * 10.0
    active_cert_names = {c["cert_name"] for c in candidate.certifications if c["status"] == "Active"}
    cert_rationale_parts = []
    for cert in required_certs:
        if cert in active_cert_names:
            cert_rationale_parts.append(f"{cert}: ✓")
        else:
            cert_rationale_parts.append(f"{cert}: ✗")
    cert_rationale = "; ".join(cert_rationale_parts) if cert_rationale_parts else "No specific certs required"

    # ── Availability ──
    avail_score = 8.0 if candidate.availability_match else 4.0
    avail_rationale = (
        f"Available from {candidate.available_from}" if candidate.available_from
        else "Availability not specified"
    )

    # ── Geographic proximity ──
    geo_score = 8.0 if candidate.region_match else (5.0 if candidate.willing_to_travel else 3.0)
    geo_rationale = (
        f"Based in {candidate.home_base_city or '?'}, {candidate.home_base_state or '?'}. "
        f"{'In target region' if candidate.region_match else 'Outside target region'}"
    )

    # ── Experience depth ──
    exp_score = min(candidate.years_experience / 2.0, 10.0)  # 20+ years = max score
    if candidate.total_project_count >= 10:
        exp_score = min(exp_score + 1.5, 10.0)
    if candidate.total_approved_hours >= 5000:
        exp_score = min(exp_score + 1.0, 10.0)
    exp_rationale = (
        f"{candidate.years_experience} years, {candidate.total_project_count} projects, "
        f"{candidate.total_approved_hours:.0f} approved hours"
    )

    scorecard = Scorecard(
        skills_match=ScorecardDimension(
            name="Skills Match", score=round(skills_score, 1), weight=2.0, rationale=skills_rationale,
        ),
        certification_coverage=ScorecardDimension(
            name="Certification Coverage", score=round(cert_score, 1), weight=1.5, rationale=cert_rationale,
        ),
        availability_fit=ScorecardDimension(
            name="Availability Fit", score=round(avail_score, 1), weight=1.0, rationale=avail_rationale,
        ),
        geographic_proximity=ScorecardDimension(
            name="Geographic Proximity", score=round(geo_score, 1), weight=1.0, rationale=geo_rationale,
        ),
        experience_depth=ScorecardDimension(
            name="Experience Depth", score=round(exp_score, 1), weight=1.0, rationale=exp_rationale,
        ),
    )

    # Highlights
    highlights = []
    if candidate.skills_match_pct >= 0.9:
        highlights.append("Strong skills match for this role")
    if candidate.certs_match_pct >= 1.0:
        highlights.append("Holds all required certifications")
    if candidate.region_match:
        highlights.append("Local to project region")
    if candidate.years_experience >= 8:
        highlights.append(f"Veteran technician ({candidate.years_experience} years)")
    if candidate.archetype == "senior_specialist":
        highlights.append("Senior specialist archetype")

    # Disqualifiers
    disqualifiers = []
    if candidate.certs_match_pct < 1.0:
        missing = [c for c in required_certs if c not in active_cert_names]
        if missing:
            disqualifiers.append(f"Missing certs: {', '.join(missing)}")
    if not candidate.availability_match:
        disqualifiers.append("May not be available by required date")

    for adj in candidate.preference_adjustments:
        if adj.startswith("Demoted:"):
            disqualifiers.append(adj)

    return scorecard, highlights, disqualifiers


def _build_llm_prompt(input: RerankerInput) -> str:
    """Build the user prompt for the LLM re-ranker."""
    role_desc = f"Role: {input.role_name or 'Unknown'}"
    if input.project_name:
        role_desc += f" on project: {input.project_name}"

    skills_desc = "\n".join(
        f"  - {s.get('skill', '?')} (min: {s.get('min_level', 'Apprentice')})"
        for s in input.required_skills
    )
    certs_desc = "\n".join(f"  - {c}" for c in input.required_certs)
    region_desc = f"Preferred region: {input.preferred_region or 'Any'}"

    candidates_json = []
    for c in input.candidates:
        candidates_json.append({
            "technician_id": c.technician_id,
            "name": c.technician_name,
            "skills": c.skills,
            "certifications": c.certifications,
            "home_base": f"{c.home_base_city}, {c.home_base_state}",
            "approved_regions": c.approved_regions,
            "available_from": str(c.available_from) if c.available_from else None,
            "years_experience": c.years_experience,
            "total_projects": c.total_project_count,
            "total_hours": c.total_approved_hours,
            "archetype": c.archetype,
            "willing_to_travel": c.willing_to_travel,
            "preference_adjustments": c.preference_adjustments,
        })

    return f"""{role_desc}

Required Skills:
{skills_desc or '  None specified'}

Required Certifications:
{certs_desc or '  None specified'}

{region_desc}

Candidates ({len(input.candidates)} pre-filtered):
{json.dumps(candidates_json, indent=2, default=str)}

Rank all candidates from best to worst fit. Provide detailed scorecards and differentiating explanations."""


def _parse_llm_response(
    response_text: str,
    candidates: List[PrefilterCandidate],
) -> List[CandidateRanking]:
    """Parse the LLM JSON response into CandidateRanking objects."""
    # Extract JSON from response (handle potential markdown code blocks)
    text = response_text.strip()
    if text.startswith("```"):
        # Remove code block markers
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    data = json.loads(text)
    rankings_data = data.get("rankings", [])

    # Build technician name lookup
    name_map = {c.technician_id: c.technician_name for c in candidates}

    rankings = []
    for rank_idx, item in enumerate(rankings_data, start=1):
        tech_id = item["technician_id"]
        scores = item.get("scores", {})

        scorecard = Scorecard(
            skills_match=ScorecardDimension(
                name="Skills Match",
                score=scores.get("skills_match", {}).get("score", 5.0),
                weight=2.0,
                rationale=scores.get("skills_match", {}).get("rationale", ""),
            ),
            certification_coverage=ScorecardDimension(
                name="Certification Coverage",
                score=scores.get("certification_coverage", {}).get("score", 5.0),
                weight=1.5,
                rationale=scores.get("certification_coverage", {}).get("rationale", ""),
            ),
            availability_fit=ScorecardDimension(
                name="Availability Fit",
                score=scores.get("availability_fit", {}).get("score", 5.0),
                weight=1.0,
                rationale=scores.get("availability_fit", {}).get("rationale", ""),
            ),
            geographic_proximity=ScorecardDimension(
                name="Geographic Proximity",
                score=scores.get("geographic_proximity", {}).get("score", 5.0),
                weight=1.0,
                rationale=scores.get("geographic_proximity", {}).get("rationale", ""),
            ),
            experience_depth=ScorecardDimension(
                name="Experience Depth",
                score=scores.get("experience_depth", {}).get("score", 5.0),
                weight=1.0,
                rationale=scores.get("experience_depth", {}).get("rationale", ""),
            ),
        )

        rankings.append(CandidateRanking(
            rank=rank_idx,
            technician_id=tech_id,
            technician_name=name_map.get(tech_id, "Unknown"),
            overall_score=round(scorecard.weighted_total, 2),
            scorecard=scorecard,
            explanation=item.get("explanation", ""),
            highlights=item.get("highlights", []),
            disqualifiers=item.get("disqualifiers", []),
        ))

    return rankings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_reranker(input: RerankerInput) -> RerankerResult:
    """Execute the LLM re-ranker with automatic fallback to deterministic scoring.

    Tries the LLM first; if unavailable or erroring, falls back to
    deterministic scoring that produces the same schema output.
    """
    if not input.candidates:
        return RerankerResult(rankings=[], fallback_used=False)

    # Attempt LLM-based ranking
    client = _get_llm_client()
    if client:
        try:
            logger.info(f"Invoking Claude Haiku for re-ranking {len(input.candidates)} candidates")
            response = client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=4096,
                system=RERANKER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _build_llm_prompt(input)}],
            )
            response_text = response.content[0].text
            rankings = _parse_llm_response(response_text, input.candidates)

            # Sort by overall score descending
            rankings.sort(key=lambda r: r.overall_score, reverse=True)
            for i, r in enumerate(rankings, start=1):
                r.rank = i

            logger.info(f"LLM re-ranking complete: {len(rankings)} candidates ranked")
            return RerankerResult(rankings=rankings, fallback_used=False)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}")
            # Fall through to deterministic
        except Exception as e:
            logger.error(f"LLM re-ranker error: {e}")
            # Fall through to deterministic

    # ── Deterministic fallback ──
    logger.info(f"Using deterministic fallback for {len(input.candidates)} candidates")
    rankings = []
    for candidate in input.candidates:
        scorecard, highlights, disqualifiers = _deterministic_score(
            candidate=candidate,
            required_skills=input.required_skills,
            required_certs=input.required_certs,
            preferred_region=input.preferred_region,
        )

        # Build explanation from preference adjustments and key metrics
        explanation_parts = []
        if candidate.skills_match_pct >= 0.9:
            explanation_parts.append(f"excellent skills match ({candidate.skills_match_pct:.0%})")
        elif candidate.skills_match_pct >= 0.7:
            explanation_parts.append(f"good skills match ({candidate.skills_match_pct:.0%})")
        else:
            explanation_parts.append(f"partial skills match ({candidate.skills_match_pct:.0%})")

        if candidate.certs_match_pct >= 1.0:
            explanation_parts.append("all required certifications")
        elif candidate.certs_match_pct > 0:
            explanation_parts.append(f"{candidate.certs_match_pct:.0%} cert coverage")

        if candidate.region_match:
            explanation_parts.append("local to project region")

        explanation = f"{candidate.technician_name} offers " + ", ".join(explanation_parts) + "."

        rankings.append(CandidateRanking(
            rank=0,  # Will be set after sorting
            technician_id=candidate.technician_id,
            technician_name=candidate.technician_name,
            overall_score=round(scorecard.weighted_total, 2),
            scorecard=scorecard,
            explanation=explanation,
            highlights=highlights,
            disqualifiers=disqualifiers,
        ))

    # Sort by overall score descending
    rankings.sort(key=lambda r: r.overall_score, reverse=True)
    for i, r in enumerate(rankings, start=1):
        r.rank = i

    return RerankerResult(
        rankings=rankings,
        fallback_used=True,
        errors=["LLM unavailable, used deterministic scoring"] if not client else [],
    )
