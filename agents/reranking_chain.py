"""
LLM Re-Ranking Chain — LangChain chain that takes a top-20 shortlist and
uses Claude to re-rank candidates with structured 5-dimension scorecards.

Architecture:
  1. Serialize candidate profiles + role requirements into a structured prompt
  2. Call Claude (Haiku tier for background agents) with JSON output schema
  3. Parse structured response into RankedCandidate objects with scorecards
  4. Sort by weighted score and return ranked results

The chain is idempotent and stateless — all data comes from the pre-filter
stage via FastAPI REST API, never direct DB access.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from typing import Any, Optional

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda

from agents.config import AgentConfig, load_agent_config
from agents.schemas import (
    CandidateProfile,
    DimensionScore,
    RankedCandidate,
    RerankingInput,
    RerankingOutput,
    RoleRequirements,
    Scorecard,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt Template
# ---------------------------------------------------------------------------

RERANKING_SYSTEM_PROMPT = """\
You are the Deployable Staffing Intelligence Agent — an expert workforce analyst \
for fiber optic and data center field operations. Your job is to evaluate and rank \
technician candidates for a specific project role.

You will receive:
1. A project role with its requirements (skills, certifications, location, budget)
2. A shortlist of pre-filtered candidate technicians with their profiles
3. Any active preference rules that should modify your scoring

SCORING FRAMEWORK — 5 Dimensions (each scored 0-10):

1. **Skill Match** (weight: {skill_match_weight:.0%})
   - Does the candidate have all required skills at or above the minimum proficiency?
   - Advanced proficiency in required skills scores higher than just meeting minimum
   - Additional relevant skills beyond requirements add bonus points
   - Missing required skills is a major penalty

2. **Proximity** (weight: {proximity_weight:.0%})
   - Is the candidate's home base in the same city/state as the project?
   - Are they in an approved region for this project location?
   - Willing to travel with adequate radius?
   - Same city = 9-10, same state = 7-8, approved region = 5-6, travel required = 3-4

3. **Availability** (weight: {availability_weight:.0%})
   - Is the candidate's deployability_status "Ready Now" or "Rolling Off Soon"?
   - Is their available_from date on or before the project start date?
   - "Currently Assigned" or "In Training" reduces this score significantly
   - "Missing Cert" or "Missing Docs" are blockers unless close to resolution

4. **Cost Efficiency** (weight: {cost_efficiency_weight:.0%})
   - How does the candidate's hourly rate range compare to the role budget?
   - Below budget = high score, at budget = medium, above budget = low
   - Factor in per diem costs for traveling candidates
   - Consider value — a slightly higher rate for significantly better skills may still score well

5. **Past Performance** (weight: {past_performance_weight:.0%})
   - Years of experience relative to role complexity
   - Total completed projects and approved hours
   - Number of badges (site, client, milestone)
   - Career stage progression (Deployed > Awaiting Assignment > Training Completed)
   - Documents verified is a plus

PREFERENCE RULES:
{preference_rules_text}

OUTPUT FORMAT:
Return a JSON object with this exact structure:
{{
  "ranked_candidates": [
    {{
      "rank": 1,
      "technician_id": "uuid-string",
      "full_name": "First Last",
      "scorecard": {{
        "skill_match": {{"score": 8.5, "reasoning": "Has Advanced Fiber Splicing and Intermediate OTDR..."}},
        "proximity": {{"score": 7.0, "reasoning": "Based in Atlanta, same state as project..."}},
        "availability": {{"score": 9.0, "reasoning": "Ready Now status, available immediately..."}},
        "cost_efficiency": {{"score": 6.5, "reasoning": "Rate range $45-55/hr vs budget $52/hr..."}},
        "past_performance": {{"score": 8.0, "reasoning": "12 years experience, 28 completed projects..."}}
      }},
      "weighted_score": 7.85,
      "explanation": "Top candidate due to excellent skill match with Advanced fiber splicing...",
      "flags": ["cert expiring in 60 days"]
    }}
  ],
  "summary": "Strong candidate pool with 3 excellent matches. Top pick Jane Smith..."
}}

RULES:
- Rank ALL candidates provided (do not skip any)
- Be precise with scores — differentiate between candidates meaningfully
- weighted_score must equal the weighted sum of dimension scores using the weights above
- Explanations should be 2-3 sentences highlighting key differentiators
- Flags should note any concerns (expiring certs, travel required, missing docs, etc.)
- The summary should be 2-3 sentences about the overall pool quality
"""

RERANKING_USER_PROMPT = """\
## Project Role to Staff

**Role:** {role_name}
**Project:** {project_name}
**Location:** {project_city}, {project_region}
**Start Date:** {start_date}
**Hourly Rate Budget:** ${hourly_rate_budget}/hr
**Per Diem Budget:** ${per_diem_budget}/day

### Required Skills:
{required_skills_text}

### Required Certifications:
{required_certs_text}

---

## Candidate Shortlist ({candidate_count} candidates)

{candidates_text}

---

Evaluate and rank all {candidate_count} candidates. Return the JSON response.
"""


# ---------------------------------------------------------------------------
# Prompt Formatting Helpers
# ---------------------------------------------------------------------------

def _proficiency_rank(level: str) -> int:
    """Convert proficiency level to numeric rank for comparison."""
    return {"Beginner": 1, "Intermediate": 2, "Advanced": 3}.get(level, 0)


def _format_candidate(idx: int, c: CandidateProfile) -> str:
    """Format a single candidate profile for the prompt."""
    skills_str = ", ".join(
        f"{s.skill_name} ({s.proficiency_level}, {s.training_hours:.0f}h)"
        for s in c.skills
    ) or "None listed"

    certs_str = ", ".join(
        f"{cert.cert_name} ({cert.status})"
        for cert in c.certifications
    ) or "None listed"

    rate_str = ""
    if c.hourly_rate_min and c.hourly_rate_max:
        rate_str = f"${c.hourly_rate_min:.0f}-${c.hourly_rate_max:.0f}/hr"
    elif c.hourly_rate_min:
        rate_str = f"${c.hourly_rate_min:.0f}/hr min"
    else:
        rate_str = "Not specified"

    avail_str = c.available_from.isoformat() if c.available_from else "Not specified"

    return f"""### Candidate {idx + 1}: {c.full_name}
- **ID:** {c.technician_id}
- **Location:** {c.home_base_city}, {c.home_base_state}
- **Approved Regions:** {', '.join(c.approved_regions) if c.approved_regions else 'None'}
- **Travel:** {'Willing' if c.willing_to_travel else 'Not willing'}{f', max {c.max_travel_radius_miles} mi' if c.max_travel_radius_miles else ''}
- **Career Stage:** {c.career_stage}
- **Deployability:** {c.deployability_status}
- **Available From:** {avail_str}
- **Archetype:** {c.archetype or 'Not specified'}
- **Experience:** {c.years_experience:.1f} years, {c.total_project_count} projects, {c.total_approved_hours:.0f} approved hours
- **Rate:** {rate_str}
- **Docs Verified:** {'Yes' if c.docs_verified else 'No'}
- **Badges:** {c.badge_count}
- **Skills:** {skills_str}
- **Certifications:** {certs_str}
- **Pre-filter Score:** {c.sql_score:.2f}
"""


def _format_preference_rules(rules: list[dict]) -> str:
    """Format preference rules into prompt text."""
    if not rules:
        return "No active preference rules. Use standard scoring."

    lines = []
    for i, rule in enumerate(rules, 1):
        rule_type = rule.get("rule_type", "unknown")
        effect = rule.get("effect", "demote")
        threshold = rule.get("threshold", "")
        params = rule.get("parameters", {})
        scope = rule.get("scope", "global")

        lines.append(
            f"{i}. **{rule_type}** (scope: {scope}, effect: {effect}): "
            f"threshold={threshold}, params={json.dumps(params)}"
        )

    return "\n".join(lines)


def _build_prompt_variables(input_data: RerankingInput) -> dict[str, Any]:
    """Transform RerankingInput into prompt template variables."""
    role = input_data.role
    weights = input_data.dimension_weights

    # Required skills text
    req_skills = "\n".join(
        f"- {s.skill_name}: minimum {s.min_proficiency}"
        for s in role.required_skills
    ) or "- None specified"

    # Required certs text
    req_certs = "\n".join(
        f"- {c}" for c in role.required_certs
    ) or "- None specified"

    # Candidates text
    candidates_text = "\n".join(
        _format_candidate(i, c) for i, c in enumerate(input_data.candidates)
    )

    return {
        # Weights for system prompt
        "skill_match_weight": weights.get("skill_match", 0.30),
        "proximity_weight": weights.get("proximity", 0.15),
        "availability_weight": weights.get("availability", 0.20),
        "cost_efficiency_weight": weights.get("cost_efficiency", 0.15),
        "past_performance_weight": weights.get("past_performance", 0.20),
        "preference_rules_text": _format_preference_rules(input_data.preference_rules),
        # Role details for user prompt
        "role_name": role.role_name,
        "project_name": role.project_name,
        "project_city": role.project_location_city,
        "project_region": role.project_location_region,
        "start_date": role.start_date.isoformat() if role.start_date else "ASAP",
        "hourly_rate_budget": f"{role.hourly_rate_budget:.0f}" if role.hourly_rate_budget else "Not specified",
        "per_diem_budget": f"{role.per_diem_budget:.0f}" if role.per_diem_budget else "Not specified",
        "required_skills_text": req_skills,
        "required_certs_text": req_certs,
        "candidate_count": len(input_data.candidates),
        "candidates_text": candidates_text,
    }


# ---------------------------------------------------------------------------
# Response Parsing
# ---------------------------------------------------------------------------

def _parse_scorecard(sc_data: dict) -> Scorecard:
    """Parse a raw scorecard dict from LLM output into a Scorecard object."""
    dims = {}
    for dim_name in ["skill_match", "proximity", "availability", "cost_efficiency", "past_performance"]:
        dim_data = sc_data.get(dim_name, {})
        dims[dim_name] = DimensionScore(
            dimension=dim_name,
            score=float(dim_data.get("score", 0.0)),
            reasoning=str(dim_data.get("reasoning", "No reasoning provided")),
        )
    return Scorecard(**dims)


def _parse_reranking_response(
    raw_output: dict,
    input_data: RerankingInput,
    model_name: str,
    elapsed_ms: float,
) -> RerankingOutput:
    """Parse the LLM JSON response into a validated RerankingOutput."""
    ranked = []

    # Build a lookup of candidate profiles by ID for enrichment
    candidate_lookup = {c.technician_id: c for c in input_data.candidates}

    raw_candidates = raw_output.get("ranked_candidates", [])

    for item in raw_candidates:
        tech_id = str(item.get("technician_id", ""))
        full_name = item.get("full_name", "Unknown")

        # If ID not found in lookup, try matching by name
        if tech_id not in candidate_lookup:
            for cid, cprof in candidate_lookup.items():
                if cprof.full_name == full_name:
                    tech_id = cid
                    break

        scorecard = _parse_scorecard(item.get("scorecard", {}))
        weighted = item.get("weighted_score", scorecard.weighted_total)

        ranked.append(RankedCandidate(
            rank=item.get("rank", len(ranked) + 1),
            technician_id=tech_id,
            full_name=full_name,
            scorecard=scorecard,
            weighted_score=round(float(weighted), 2),
            explanation=item.get("explanation", ""),
            flags=item.get("flags", []),
        ))

    # Sort by weighted score descending and reassign ranks
    ranked.sort(key=lambda r: r.weighted_score, reverse=True)
    for i, r in enumerate(ranked):
        r.rank = i + 1

    return RerankingOutput(
        role_id=input_data.role.role_id,
        role_name=input_data.role.role_name,
        project_name=input_data.role.project_name,
        ranked_candidates=ranked,
        total_evaluated=len(input_data.candidates),
        agent_model=model_name,
        processing_time_ms=elapsed_ms,
        summary=raw_output.get("summary", ""),
    )


# ---------------------------------------------------------------------------
# Fallback: Deterministic Scoring (when LLM unavailable)
# ---------------------------------------------------------------------------

def _deterministic_score_candidate(
    candidate: CandidateProfile,
    role: RoleRequirements,
    weights: dict[str, float],
) -> RankedCandidate:
    """
    Score a candidate deterministically without LLM when API key is missing.
    Uses heuristic rules that approximate the 5-dimension framework.
    """
    # --- Skill Match ---
    required_skills = {s.skill_name.lower(): s.min_proficiency for s in role.required_skills}
    candidate_skills = {s.skill_name.lower(): s for s in candidate.skills}

    matched = 0
    exceeded = 0
    total_required = len(required_skills) or 1

    for skill_name, min_prof in required_skills.items():
        if skill_name in candidate_skills:
            matched += 1
            c_level = _proficiency_rank(candidate_skills[skill_name].proficiency_level)
            r_level = _proficiency_rank(min_prof)
            if c_level > r_level:
                exceeded += 1

    skill_base = (matched / total_required) * 8.0
    skill_bonus = min(exceeded * 0.5, 2.0)
    extra_skills = len(candidate_skills) - len(required_skills)
    skill_extra = min(max(extra_skills, 0) * 0.2, 1.0)
    skill_score = min(skill_base + skill_bonus + skill_extra, 10.0)

    # --- Proximity ---
    prox_score = 3.0  # Default: requires travel
    if role.project_location_city and candidate.home_base_city:
        if candidate.home_base_city.lower() == role.project_location_city.lower():
            prox_score = 9.5
        elif candidate.home_base_state == role.project_location_region:
            prox_score = 7.5
        elif role.project_location_region in candidate.approved_regions:
            prox_score = 5.5

    if not candidate.willing_to_travel and prox_score < 7.0:
        prox_score = max(prox_score - 3.0, 0.0)

    # --- Availability ---
    avail_score = 5.0
    status = candidate.deployability_status
    if status == "Ready Now":
        avail_score = 9.0
    elif status == "Rolling Off Soon":
        avail_score = 7.5
    elif status == "Awaiting Assignment":
        avail_score = 7.0
    elif status == "In Training":
        avail_score = 4.0
    elif status == "Currently Assigned":
        avail_score = 3.0
    elif status in ("Missing Cert", "Missing Docs"):
        avail_score = 2.5
    elif status == "Inactive":
        avail_score = 1.0

    if candidate.available_from and role.start_date:
        if candidate.available_from <= role.start_date:
            avail_score = min(avail_score + 1.0, 10.0)
        else:
            days_late = (candidate.available_from - role.start_date).days
            avail_score = max(avail_score - min(days_late / 30.0, 3.0), 0.0)

    # --- Cost Efficiency ---
    cost_score = 5.0
    if role.hourly_rate_budget and candidate.hourly_rate_min:
        rate_mid = (candidate.hourly_rate_min + (candidate.hourly_rate_max or candidate.hourly_rate_min)) / 2.0
        ratio = rate_mid / role.hourly_rate_budget
        if ratio <= 0.85:
            cost_score = 9.0
        elif ratio <= 0.95:
            cost_score = 7.5
        elif ratio <= 1.05:
            cost_score = 6.0
        elif ratio <= 1.15:
            cost_score = 4.0
        else:
            cost_score = 2.5

    # --- Past Performance ---
    perf_score = 3.0
    perf_score += min(candidate.years_experience / 3.0, 3.0)  # Up to +3 for 9+ years
    perf_score += min(candidate.total_project_count / 10.0, 2.0)  # Up to +2 for 20+ projects
    perf_score += 0.5 if candidate.docs_verified else 0.0
    perf_score += min(candidate.badge_count * 0.2, 1.0)  # Up to +1 for badges

    stage_bonus = {
        "Deployed": 1.0,
        "Awaiting Assignment": 0.7,
        "Training Completed": 0.5,
        "In Training": 0.2,
    }
    perf_score += stage_bonus.get(candidate.career_stage, 0.0)
    perf_score = min(perf_score, 10.0)

    # Build scorecard
    scorecard = Scorecard(
        skill_match=DimensionScore(
            dimension="skill_match",
            score=round(skill_score, 1),
            reasoning=f"Matched {matched}/{total_required} required skills, {exceeded} exceeded minimum level.",
        ),
        proximity=DimensionScore(
            dimension="proximity",
            score=round(prox_score, 1),
            reasoning=f"Home base: {candidate.home_base_city}, {candidate.home_base_state}. "
            f"Project: {role.project_location_city}, {role.project_location_region}.",
        ),
        availability=DimensionScore(
            dimension="availability",
            score=round(avail_score, 1),
            reasoning=f"Status: {candidate.deployability_status}. "
            f"Available from: {candidate.available_from or 'unspecified'}.",
        ),
        cost_efficiency=DimensionScore(
            dimension="cost_efficiency",
            score=round(cost_score, 1),
            reasoning=f"Rate: ${candidate.hourly_rate_min or '?'}-${candidate.hourly_rate_max or '?'}/hr "
            f"vs budget ${role.hourly_rate_budget or '?'}/hr.",
        ),
        past_performance=DimensionScore(
            dimension="past_performance",
            score=round(perf_score, 1),
            reasoning=f"{candidate.years_experience:.0f} years exp, "
            f"{candidate.total_project_count} projects, "
            f"{candidate.total_approved_hours:.0f} approved hours, "
            f"{candidate.badge_count} badges.",
        ),
    )

    weighted = scorecard.weighted_total

    # Generate flags
    flags = []
    if not candidate.docs_verified:
        flags.append("documents not verified")
    if candidate.deployability_status in ("Missing Cert", "Missing Docs"):
        flags.append(f"status: {candidate.deployability_status}")
    if not candidate.willing_to_travel and prox_score < 7.0:
        flags.append("unwilling to travel, not local")
    for cert in candidate.certifications:
        if cert.status == "Expired":
            flags.append(f"expired cert: {cert.cert_name}")
        elif cert.expiry_date and role.start_date and cert.expiry_date < role.start_date:
            flags.append(f"cert expires before start: {cert.cert_name}")

    # Generate explanation
    strengths = []
    if skill_score >= 7.0:
        strengths.append("strong skill match")
    if prox_score >= 7.0:
        strengths.append("good proximity")
    if avail_score >= 7.0:
        strengths.append("readily available")
    if cost_score >= 7.0:
        strengths.append("cost-efficient")
    if perf_score >= 7.0:
        strengths.append("proven track record")

    explanation = f"{candidate.full_name} scores {weighted:.1f}/10 overall. "
    if strengths:
        explanation += f"Key strengths: {', '.join(strengths)}. "
    if flags:
        explanation += f"Concerns: {', '.join(flags[:2])}."

    return RankedCandidate(
        rank=999,  # Placeholder — will be reassigned after sorting
        technician_id=candidate.technician_id,
        full_name=candidate.full_name,
        scorecard=scorecard,
        weighted_score=round(weighted, 2),
        explanation=explanation,
        flags=flags,
    )


def deterministic_rerank(input_data: RerankingInput) -> RerankingOutput:
    """
    Deterministic fallback re-ranking when LLM is unavailable.
    Uses heuristic scoring rules for the 5-dimension framework.
    """
    start = time.time()

    ranked = [
        _deterministic_score_candidate(c, input_data.role, input_data.dimension_weights)
        for c in input_data.candidates
    ]

    # Apply preference rule modifiers
    for rule in input_data.preference_rules:
        rule_type = rule.get("rule_type", "")
        effect = rule.get("effect", "demote")
        params = rule.get("parameters", {})

        for rc in ranked:
            candidate = next(
                (c for c in input_data.candidates if c.technician_id == rc.technician_id),
                None,
            )
            if not candidate:
                continue

            if rule_type == "experience_threshold":
                min_years = float(params.get("min_years", 0))
                if candidate.years_experience < min_years:
                    if effect == "exclude":
                        rc.weighted_score = 0.0
                        rc.flags.append(f"excluded by rule: min {min_years} years experience")
                    else:
                        rc.weighted_score = max(rc.weighted_score - 1.5, 0.0)
                        rc.flags.append(f"demoted by rule: min {min_years} years experience")

            elif rule_type == "skill_level_minimum":
                req_skill = params.get("skill_name", "").lower()
                req_level = params.get("min_level", "Intermediate")
                has_skill = any(
                    s.skill_name.lower() == req_skill
                    and _proficiency_rank(s.proficiency_level) >= _proficiency_rank(req_level)
                    for s in candidate.skills
                )
                if not has_skill:
                    if effect == "exclude":
                        rc.weighted_score = 0.0
                        rc.flags.append(f"excluded by rule: {req_skill} min {req_level}")
                    else:
                        rc.weighted_score = max(rc.weighted_score - 1.0, 0.0)
                        rc.flags.append(f"demoted by rule: {req_skill} min {req_level}")

            elif rule_type == "proximity_preference":
                preferred_state = params.get("preferred_state", "")
                if preferred_state and candidate.home_base_state != preferred_state:
                    if effect == "demote":
                        rc.weighted_score = max(rc.weighted_score - 0.5, 0.0)

    # Sort and assign ranks
    ranked.sort(key=lambda r: r.weighted_score, reverse=True)
    for i, r in enumerate(ranked):
        r.rank = i + 1

    elapsed = (time.time() - start) * 1000

    # Generate summary
    top3 = ranked[:3]
    if top3:
        summary = (
            f"Evaluated {len(ranked)} candidates for {input_data.role.role_name} on "
            f"{input_data.role.project_name}. "
            f"Top pick: {top3[0].full_name} ({top3[0].weighted_score:.1f}/10)"
        )
        if len(top3) > 1:
            summary += f", followed by {top3[1].full_name} ({top3[1].weighted_score:.1f}/10)"
        summary += "."
    else:
        summary = "No candidates evaluated."

    return RerankingOutput(
        role_id=input_data.role.role_id,
        role_name=input_data.role.role_name,
        project_name=input_data.role.project_name,
        ranked_candidates=ranked,
        total_evaluated=len(input_data.candidates),
        agent_model="deterministic-fallback",
        processing_time_ms=elapsed,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# LangChain Chain Builder
# ---------------------------------------------------------------------------

def build_reranking_chain(config: Optional[AgentConfig] = None):
    """
    Build and return the LangChain re-ranking chain (LCEL).

    Returns a Runnable that accepts RerankingInput and returns RerankingOutput.
    Falls back to deterministic scoring if no API key is configured.
    """
    if config is None:
        config = load_agent_config()

    # If no API key, return a wrapped deterministic fallback
    if not config.anthropic_api_key:
        logger.warning(
            "No ANTHROPIC_API_KEY configured. Re-ranking chain will use deterministic fallback."
        )
        return RunnableLambda(deterministic_rerank)

    # Import here to avoid import errors when anthropic isn't fully configured
    from langchain_anthropic import ChatAnthropic

    # Build the LLM
    llm = ChatAnthropic(
        model=config.reranking_model,
        anthropic_api_key=config.anthropic_api_key,
        temperature=config.reranking_temperature,
        max_tokens=config.reranking_max_tokens,
    )

    # Build the prompt template
    prompt = ChatPromptTemplate.from_messages([
        ("system", RERANKING_SYSTEM_PROMPT),
        ("human", RERANKING_USER_PROMPT),
    ])

    # JSON output parser
    parser = JsonOutputParser()

    # The chain: format prompt → LLM → parse JSON → construct output
    def _prepare_inputs(input_data: RerankingInput) -> dict:
        """Prepare prompt variables from the input data."""
        return _build_prompt_variables(input_data)

    def _invoke_chain(input_data: RerankingInput) -> RerankingOutput:
        """Full chain invocation with timing and error handling."""
        start = time.time()

        try:
            # Prepare prompt variables
            variables = _build_prompt_variables(input_data)

            # Format and invoke
            chain = prompt | llm | parser
            raw_output = chain.invoke(variables)

            elapsed = (time.time() - start) * 1000

            return _parse_reranking_response(
                raw_output,
                input_data,
                config.reranking_model,
                elapsed,
            )

        except Exception as e:
            logger.error(f"LLM re-ranking failed: {e}. Falling back to deterministic scoring.")
            elapsed = (time.time() - start) * 1000

            # Fallback to deterministic
            result = deterministic_rerank(input_data)
            result.agent_model = f"deterministic-fallback (LLM error: {type(e).__name__})"
            result.processing_time_ms = elapsed
            return result

    return RunnableLambda(_invoke_chain)


# ---------------------------------------------------------------------------
# Convenience Function
# ---------------------------------------------------------------------------

def rerank_candidates(
    role: RoleRequirements,
    candidates: list[CandidateProfile],
    preference_rules: Optional[list[dict]] = None,
    dimension_weights: Optional[dict[str, float]] = None,
    config: Optional[AgentConfig] = None,
) -> RerankingOutput:
    """
    High-level convenience function to re-rank candidates for a role.

    This is the primary entry point for other services (Celery tasks, API routes).

    Args:
        role: The project role requirements
        candidates: Pre-filtered candidate shortlist (max 20-25)
        preference_rules: Active preference rules to apply
        dimension_weights: Optional custom dimension weights
        config: Optional agent configuration override

    Returns:
        RerankingOutput with ranked candidates, scorecards, and explanations
    """
    input_data = RerankingInput(
        role=role,
        candidates=candidates,
        preference_rules=preference_rules or [],
        dimension_weights=dimension_weights or {
            "skill_match": 0.30,
            "proximity": 0.15,
            "availability": 0.20,
            "cost_efficiency": 0.15,
            "past_performance": 0.20,
        },
    )

    chain = build_reranking_chain(config)
    return chain.invoke(input_data)
