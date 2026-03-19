"""LangChain chain for forward staffing gap analysis and recommendations.

Uses Claude Haiku (background agent tier) to generate:
  - Natural-language gap analysis summaries
  - Proactive staffing recommendations with explanations
  - Priority-ranked action items for ops users

This chain is stateless and idempotent:
  - All data is fetched via the REST API / service layer
  - No direct database access
  - Output is structured JSON that can be serialized and pushed via WebSocket
"""

import logging
import os
from typing import Any, Optional

from agents.config import AgentConfig, load_agent_config

logger = logging.getLogger("deployable.agents.forward_staffing")

# Try importing LangChain; gracefully degrade if unavailable
_LANGCHAIN_AVAILABLE = False

try:
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.output_parsers import StrOutputParser

    _LANGCHAIN_AVAILABLE = True
except ImportError:
    logger.info("langchain-anthropic not installed; using deterministic analysis")


FORWARD_STAFFING_SYSTEM_PROMPT = """You are a forward staffing analyst for Deployable, a workforce operating system for fiber optic and data center technicians.

You analyze upcoming staffing gaps within a 90-day window and generate actionable recommendations for ops managers.

Your analysis should be:
- Concise and actionable (2-4 sentences per gap)
- Specific about skills, certifications, and timing
- Clear about urgency and impact
- Focused on what ops needs to DO, not just what the data shows

You are generating recommendations that require human approval before any action is taken. Your role is to surface insights and suggest next steps, not to make autonomous decisions."""


def _get_haiku_llm(config: Optional[AgentConfig] = None):
    """Get Claude Haiku LLM instance for background processing."""
    if not _LANGCHAIN_AVAILABLE:
        return None

    config = config or load_agent_config()
    api_key = config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    return ChatAnthropic(
        model=config.haiku_model,
        anthropic_api_key=api_key,
        temperature=0.2,  # Low temperature for analytical tasks
        max_tokens=2048,
    )


def generate_gap_analysis_summary(
    gaps_data: list[dict[str, Any]],
    available_tech_count: int,
    scan_date: str,
    window_end: str,
) -> str:
    """Generate NL summary of the forward staffing gap analysis.

    Uses Claude Haiku for cost-efficient background processing.
    Falls back to deterministic template if API unavailable.
    """
    llm = _get_haiku_llm()

    if llm is None or not gaps_data:
        return _deterministic_gap_summary(gaps_data, available_tech_count, scan_date, window_end)

    try:
        # Build gap descriptions
        gap_descriptions = []
        for i, gap in enumerate(gaps_data[:10], 1):  # Limit to top 10 for token efficiency
            role = gap.get("role", {})
            gap_descriptions.append(
                f"{i}. {role.get('role_name', 'Unknown')} on {role.get('project_name', 'Unknown')} "
                f"({role.get('project_region', '')}): "
                f"{role.get('gap_slots', 0)} slot(s) open, "
                f"urgency: {role.get('urgency', 'unknown')}, "
                f"gap starts: {role.get('gap_start_date', 'unknown')}, "
                f"type: {gap.get('gap_type', 'unknown')}, "
                f"candidates matched: {gap.get('recommended_candidate_count', 0)}"
            )

        prompt = f"""Analyze these forward staffing gaps for a fiber/data center workforce and write a concise executive summary (3-5 sentences).

Scan period: {scan_date} to {window_end}
Available technicians in pool: {available_tech_count}
Total gaps found: {len(gaps_data)}

Gaps (ordered by urgency):
{chr(10).join(gap_descriptions)}

Write a professional summary highlighting:
1. Overall staffing posture (healthy, at-risk, critical)
2. Most urgent gaps requiring immediate attention
3. Whether the available tech pool can cover the gaps
4. Recommended priority actions

Keep it actionable and concise. No bullet points — use flowing prose."""

        messages = [
            SystemMessage(content=FORWARD_STAFFING_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        response = llm.invoke(messages)
        return response.content.strip()

    except Exception as e:
        logger.warning("LLM gap analysis failed, using deterministic: %s", str(e))
        return _deterministic_gap_summary(gaps_data, available_tech_count, scan_date, window_end)


def generate_gap_recommendation(
    gap_data: dict[str, Any],
    candidate_profiles: list[dict[str, Any]],
) -> str:
    """Generate NL recommendation for a specific staffing gap.

    Produces actionable recommendation text for ops to review.
    """
    llm = _get_haiku_llm()
    role = gap_data.get("role", {})

    if llm is None or not candidate_profiles:
        return _deterministic_gap_recommendation(gap_data, candidate_profiles)

    try:
        candidate_desc = []
        for i, cand in enumerate(candidate_profiles[:5], 1):
            candidate_desc.append(
                f"{i}. {cand.get('full_name', 'Unknown')} — "
                f"available {cand.get('available_from', 'unknown')}, "
                f"base: {cand.get('home_base_city', '')}, {cand.get('home_base_state', '')}, "
                f"skills: {', '.join(s.get('skill_name', '') for s in cand.get('skills', [])[:3])}, "
                f"stage: {cand.get('career_stage', 'unknown')}"
            )

        prompt = f"""Generate a staffing recommendation for this gap:

Role: {role.get('role_name', 'Unknown')} on {role.get('project_name', 'Unknown')}
Region: {role.get('project_region', '')}
Gap type: {gap_data.get('gap_type', 'unknown')}
Gap starts: {role.get('gap_start_date', 'unknown')}
Open slots: {role.get('gap_slots', 0)}
Required skills: {role.get('required_skills', [])}
Required certs: {role.get('required_certs', [])}

Top candidates:
{chr(10).join(candidate_desc) if candidate_desc else "No strong candidates identified."}

Write a 2-3 sentence recommendation. If candidates are available, recommend the top pick and explain why.
If no strong candidates exist, suggest what action ops should take (e.g., expand search, relax requirements, schedule training).
Be specific and actionable."""

        messages = [
            SystemMessage(content=FORWARD_STAFFING_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        response = llm.invoke(messages)
        return response.content.strip()

    except Exception as e:
        logger.warning("LLM gap recommendation failed, using deterministic: %s", str(e))
        return _deterministic_gap_recommendation(gap_data, candidate_profiles)


def _deterministic_gap_summary(
    gaps_data: list[dict[str, Any]],
    available_tech_count: int,
    scan_date: str,
    window_end: str,
) -> str:
    """Deterministic fallback for gap analysis summary."""
    if not gaps_data:
        return (
            f"Forward staffing scan ({scan_date} to {window_end}): "
            f"No staffing gaps identified within the 90-day window. "
            f"{available_tech_count} technicians available in the pool."
        )

    critical = sum(1 for g in gaps_data if g.get("role", {}).get("urgency") == "critical")
    high = sum(1 for g in gaps_data if g.get("role", {}).get("urgency") == "high")
    total_slots = sum(g.get("role", {}).get("gap_slots", 0) for g in gaps_data)

    posture = "critical" if critical > 0 else "at-risk" if high > 0 else "stable"

    parts = [
        f"Forward staffing posture: {posture.upper()}. "
        f"{len(gaps_data)} gap(s) identified requiring {total_slots} slot(s) "
        f"between {scan_date} and {window_end}. "
    ]

    if critical:
        top = gaps_data[0].get("role", {})
        parts.append(
            f"Most urgent: {top.get('role_name', 'Unknown')} on {top.get('project_name', 'Unknown')} "
            f"needs immediate staffing attention ({top.get('gap_slots', 0)} slot(s)). "
        )

    coverage = "adequate" if available_tech_count >= total_slots * 2 else "tight" if available_tech_count >= total_slots else "insufficient"
    parts.append(
        f"Available technician pool ({available_tech_count}) is {coverage} "
        f"for covering {total_slots} open slot(s)."
    )

    return "".join(parts)


def _deterministic_gap_recommendation(
    gap_data: dict[str, Any],
    candidate_profiles: list[dict[str, Any]],
) -> str:
    """Deterministic fallback for gap-specific recommendations."""
    role = gap_data.get("role", {})
    role_name = role.get("role_name", "Unknown Role")
    project_name = role.get("project_name", "Unknown Project")
    urgency = role.get("urgency", "medium")
    gap_slots = role.get("gap_slots", 0)

    if not candidate_profiles:
        return (
            f"No strong candidates identified for {role_name} on {project_name}. "
            f"Consider expanding the search radius, relaxing skill requirements, "
            f"or scheduling targeted training for available apprentice-level technicians."
        )

    top = candidate_profiles[0]
    return (
        f"Recommend {top.get('full_name', 'Unknown')} for {role_name} on {project_name} "
        f"({urgency} urgency, {gap_slots} slot(s)). "
        f"Available from {top.get('available_from', 'soon')}, "
        f"based in {top.get('home_base_city', 'unknown')}, {top.get('home_base_state', '')}. "
        f"{len(candidate_profiles)} total candidates matched."
    )
