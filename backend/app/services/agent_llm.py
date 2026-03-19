"""LangChain + Claude integration for reactive agent NL explanations.

Uses tiered model selection:
  - Claude Haiku: background agents, scoring explanations, routing
  - Claude Sonnet: conversational chat, complex NL parsing

Provides fallback deterministic explanations when API key is not available.
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("deployable.agent_llm")

# Try importing LangChain; gracefully degrade if unavailable
_LANGCHAIN_AVAILABLE = False
_chain = None

try:
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.output_parsers import StrOutputParser

    _LANGCHAIN_AVAILABLE = True
except ImportError:
    logger.info("langchain-anthropic not installed; using deterministic explanations")

# Model tier constants
MODEL_HAIKU = "claude-3-5-haiku-20241022"
MODEL_SONNET = "claude-sonnet-4-20250514"


def _get_llm(model: str = MODEL_HAIKU, temperature: float = 0.3):
    """Create a ChatAnthropic instance with the specified model."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not _LANGCHAIN_AVAILABLE:
        return None
    return ChatAnthropic(
        model=model,
        anthropic_api_key=api_key,
        temperature=temperature,
        max_tokens=512,
    )


def generate_staffing_explanation(
    scorecard: dict[str, Any],
    technician_name: str,
    role_name: str,
    project_name: str = "",
) -> str:
    """Generate NL explanation for a staffing recommendation.

    Uses Claude Haiku for cost-efficient background processing.
    Falls back to deterministic template if API unavailable.
    """
    llm = _get_llm(MODEL_HAIKU)

    if llm is None:
        return _deterministic_staffing_explanation(scorecard, technician_name, role_name, project_name)

    try:
        dimensions = scorecard.get("dimensions", {})
        prompt = f"""You are an expert staffing analyst for a fiber/data center workforce platform called Deployable.

Generate a concise 2-3 sentence recommendation explanation for why this technician is a good (or poor) fit for this role.

Technician: {technician_name}
Role: {role_name}
{"Project: " + project_name if project_name else ""}
Overall Score: {scorecard.get('overall_score', 0)}/100

Dimension Scores:
- Skills Match: {dimensions.get('skills_match', {}).get('score', 0)}/100 — {dimensions.get('skills_match', {}).get('detail', '')}
- Certification Fit: {dimensions.get('certification_fit', {}).get('score', 0)}/100 — {dimensions.get('certification_fit', {}).get('detail', '')}
- Availability: {dimensions.get('availability', {}).get('score', 0)}/100 — {dimensions.get('availability', {}).get('detail', '')}
- Location Fit: {dimensions.get('location_fit', {}).get('score', 0)}/100 — {dimensions.get('location_fit', {}).get('detail', '')}
- Experience: {dimensions.get('experience', {}).get('score', 0)}/100 — {dimensions.get('experience', {}).get('detail', '')}

{"Preference adjustments: " + str(scorecard.get('preference_adjustments', [])) if scorecard.get('preference_adjustments') else ""}

Write a professional, actionable explanation highlighting the strongest dimensions and any concerns. Be specific about skills and certifications. Do not use bullet points — use flowing prose."""

        messages = [
            SystemMessage(content="You are a staffing recommendation engine. Be concise, specific, and actionable."),
            HumanMessage(content=prompt),
        ]
        response = llm.invoke(messages)
        return response.content.strip()

    except Exception as e:
        logger.warning("LLM explanation failed, using deterministic: %s", str(e))
        return _deterministic_staffing_explanation(scorecard, technician_name, role_name, project_name)


def generate_training_explanation(
    technician_name: str,
    skill_name: str,
    current_level: str,
    new_level: str,
    hours: float,
) -> str:
    """Generate NL explanation for a training advancement recommendation."""
    llm = _get_llm(MODEL_HAIKU)

    if llm is None:
        return (
            f"{technician_name} has accumulated {hours:.0f} training hours in {skill_name}, "
            f"advancing from {current_level} to {new_level}. "
            f"This progression opens new deployment opportunities for roles requiring {new_level}-level {skill_name}."
        )

    try:
        messages = [
            SystemMessage(content="You are a workforce training advisor. Be concise and encouraging."),
            HumanMessage(content=f"""Generate a 1-2 sentence explanation for this training advancement:
Technician: {technician_name}
Skill: {skill_name}
Previous Level: {current_level}
New Level: {new_level}
Total Hours: {hours:.0f}

Explain what this means for their deployability and career."""),
        ]
        response = llm.invoke(messages)
        return response.content.strip()
    except Exception as e:
        logger.warning("LLM training explanation failed: %s", str(e))
        return (
            f"{technician_name} has accumulated {hours:.0f} training hours in {skill_name}, "
            f"advancing from {current_level} to {new_level}."
        )


def generate_cert_alert_explanation(
    technician_name: str,
    cert_name: str,
    expiry_date: str,
    days_until_expiry: int,
) -> str:
    """Generate NL explanation for a certification expiry alert."""
    urgency = "critical" if days_until_expiry <= 7 else "upcoming" if days_until_expiry <= 30 else "advance notice"
    return (
        f"{technician_name}'s {cert_name} certification expires on {expiry_date} "
        f"({days_until_expiry} days — {urgency}). "
        f"Renewal should be scheduled to maintain deployment eligibility for roles requiring this certification."
    )


def generate_backfill_explanation(
    technician_name: str,
    role_name: str,
    reason: str,
) -> str:
    """Generate explanation for backfill recommendation."""
    return (
        f"Backfill recommended for {role_name}: {reason}. "
        f"{technician_name} is suggested as a replacement based on skills, availability, and location fit."
    )


def generate_rejection_rule_suggestion(
    rejection_reason: str,
    technician_name: str,
    role_name: str,
) -> Optional[str]:
    """Use LLM to suggest a preference rule from rejection feedback."""
    llm = _get_llm(MODEL_HAIKU)

    if llm is None:
        return _deterministic_rule_suggestion(rejection_reason)

    try:
        messages = [
            SystemMessage(content="You are a staffing rules engine. Suggest a concise preference rule."),
            HumanMessage(content=f"""An ops user rejected a staffing recommendation:
Technician: {technician_name}
Role: {role_name}
Rejection reason: {rejection_reason}

Suggest ONE preference rule that would prevent similar recommendations.
Format: "rule_type: <type> | threshold: <value> | effect: <exclude|demote> | reason: <why>"
Valid rule types: experience_threshold, skill_level_minimum, location_restriction, availability_window"""),
        ]
        response = llm.invoke(messages)
        return response.content.strip()
    except Exception:
        return _deterministic_rule_suggestion(rejection_reason)


def _deterministic_staffing_explanation(
    scorecard: dict[str, Any],
    technician_name: str,
    role_name: str,
    project_name: str = "",
) -> str:
    """Deterministic fallback for staffing explanations."""
    dims = scorecard.get("dimensions", {})
    overall = scorecard.get("overall_score", 0)
    project_ctx = f" on {project_name}" if project_name else ""

    # Find strongest and weakest dimensions
    sorted_dims = sorted(dims.items(), key=lambda x: x[1].get("score", 0), reverse=True)
    strongest = sorted_dims[0] if sorted_dims else ("skills_match", {"score": 0, "detail": ""})
    weakest = sorted_dims[-1] if sorted_dims else ("skills_match", {"score": 0, "detail": ""})

    strength_label = strongest[0].replace("_", " ").title()
    weakness_label = weakest[0].replace("_", " ").title()

    if overall >= 80:
        verdict = f"{technician_name} is a strong match for {role_name}{project_ctx} with an overall score of {overall}/100."
    elif overall >= 60:
        verdict = f"{technician_name} is a moderate match for {role_name}{project_ctx} scoring {overall}/100."
    else:
        verdict = f"{technician_name} is a marginal match for {role_name}{project_ctx} at {overall}/100."

    detail = f" Strongest dimension: {strength_label} ({strongest[1].get('score', 0)}/100)."
    if weakest[1].get("score", 0) < 60:
        detail += f" Area of concern: {weakness_label} ({weakest[1].get('score', 0)}/100 — {weakest[1].get('detail', '')})."

    return verdict + detail


def _deterministic_rule_suggestion(rejection_reason: str) -> str:
    """Deterministic fallback for rule suggestions."""
    reason_lower = rejection_reason.lower()
    if "experience" in reason_lower or "junior" in reason_lower or "senior" in reason_lower:
        return "rule_type: experience_threshold | threshold: Training Completed | effect: demote | reason: Insufficient experience level"
    if "skill" in reason_lower or "proficiency" in reason_lower:
        return "rule_type: skill_level_minimum | threshold: Intermediate | effect: demote | reason: Skill level below expectation"
    if "location" in reason_lower or "region" in reason_lower or "travel" in reason_lower:
        return "rule_type: location_restriction | threshold: N/A | effect: exclude | reason: Geographic mismatch"
    if "available" in reason_lower or "timing" in reason_lower or "date" in reason_lower:
        return "rule_type: availability_window | threshold: 14 days | effect: demote | reason: Availability timing mismatch"
    return "rule_type: experience_threshold | threshold: Screened | effect: demote | reason: General quality concern"
