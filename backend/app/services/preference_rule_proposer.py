"""Preference rule proposal engine.

Analyzes rejection reasons and proposes matching predefined template-type rules
with suggested parameters. Proposed rules are stored with status='proposed' and
require human approval before activating.

Template Rule Types:
  - experience_threshold: Minimum career stage for role eligibility
  - skill_level_minimum: Minimum proficiency level for a skill
  - cert_requirement: Require a specific active certification
  - location_restriction: Restrict to specific regions
  - availability_window: Minimum days of availability before start
  - project_count_minimum: Minimum number of completed projects
  - rate_cap: Maximum hourly rate threshold
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.recommendation import (
    Recommendation,
    PreferenceRule,
    PreferenceRuleStatus,
    PreferenceRuleTemplateType,
    PreferenceRuleCreatedByType,
    RecommendationStatus,
)
from app.models.technician import Technician, TechnicianSkill, TechnicianCertification
from app.models.project import ProjectRole
from app.models.assignment import Assignment

logger = logging.getLogger("deployable.preference_rule_proposer")


# ---------------------------------------------------------------------------
# Rule Templates — predefined templates the agent can propose
# ---------------------------------------------------------------------------

@dataclass
class RuleTemplate:
    """A predefined rule template that the agent can propose."""
    rule_type: str
    description: str
    default_effect: str  # exclude, demote, boost
    default_scope: str   # global, client, project_type
    parameter_schema: dict[str, str]  # param_name -> description
    keywords: list[str] = field(default_factory=list)  # trigger keywords


RULE_TEMPLATES: dict[str, RuleTemplate] = {
    "experience_threshold": RuleTemplate(
        rule_type="experience_threshold",
        description="Require minimum career stage for staffing eligibility",
        default_effect="demote",
        default_scope="global",
        parameter_schema={
            "min_career_stage": "Minimum career stage (e.g. Training Completed, Deployed)",
        },
        keywords=[
            "experience", "junior", "senior", "green", "new", "inexperienced",
            "not ready", "too early", "needs more", "novice", "beginner",
            "career stage", "seasoned", "veteran",
        ],
    ),
    "skill_level_minimum": RuleTemplate(
        rule_type="skill_level_minimum",
        description="Require minimum proficiency level for a specific skill",
        default_effect="demote",
        default_scope="global",
        parameter_schema={
            "skill_name": "Name of the skill",
            "min_level": "Minimum proficiency level (Apprentice, Intermediate, Advanced)",
        },
        keywords=[
            "skill", "proficiency", "level", "ability", "competent", "capable",
            "qualified", "unqualified", "splicing", "fiber", "otdr", "testing",
            "termination", "fusion", "cable", "routing",
        ],
    ),
    "cert_requirement": RuleTemplate(
        rule_type="cert_requirement",
        description="Require a specific active certification",
        default_effect="exclude",
        default_scope="global",
        parameter_schema={
            "cert_name": "Name of the required certification",
        },
        keywords=[
            "cert", "certification", "certified", "license", "osha",
            "cpr", "first aid", "safety", "osp", "bicsi", "dot",
            "hazmat", "confined space", "cdl",
        ],
    ),
    "location_restriction": RuleTemplate(
        rule_type="location_restriction",
        description="Restrict candidates to specific regions or exclude regions",
        default_effect="exclude",
        default_scope="global",
        parameter_schema={
            "allowed_regions": "Comma-separated list of allowed regions/states",
            "restriction_type": "include (only these) or exclude (not these)",
        },
        keywords=[
            "location", "region", "travel", "distance", "far", "remote",
            "local", "nearby", "state", "area", "geographic", "relocate",
            "commute",
        ],
    ),
    "availability_window": RuleTemplate(
        rule_type="availability_window",
        description="Require minimum days of availability before project start",
        default_effect="demote",
        default_scope="global",
        parameter_schema={
            "min_days_available": "Minimum days available before start date",
        },
        keywords=[
            "available", "availability", "timing", "date", "schedule",
            "conflict", "committed", "busy", "booked", "overlap",
            "start date", "not free",
        ],
    ),
    "project_count_minimum": RuleTemplate(
        rule_type="project_count_minimum",
        description="Require minimum number of completed project assignments",
        default_effect="demote",
        default_scope="global",
        parameter_schema={
            "min_projects": "Minimum number of completed projects",
        },
        keywords=[
            "project", "track record", "history", "assignment", "deployed",
            "completed", "proven", "unproven", "first time",
        ],
    ),
    "rate_cap": RuleTemplate(
        rule_type="rate_cap",
        description="Set maximum hourly rate threshold for cost control",
        default_effect="exclude",
        default_scope="global",
        parameter_schema={
            "max_hourly_rate": "Maximum allowed hourly rate in dollars",
        },
        keywords=[
            "rate", "cost", "expensive", "budget", "price", "hourly",
            "pay", "compensation", "costly", "cheap", "affordable",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Rejection reason analysis
# ---------------------------------------------------------------------------

def _match_template(rejection_reason: str) -> tuple[str, float]:
    """Match a rejection reason to the best-fitting rule template.

    Returns (rule_type, confidence_score) where confidence is 0.0-1.0.
    """
    reason_lower = rejection_reason.lower()
    best_match = "experience_threshold"
    best_score = 0.0

    for rule_type, template in RULE_TEMPLATES.items():
        score = 0.0
        for keyword in template.keywords:
            if keyword in reason_lower:
                # Longer keywords are more specific, so weight more
                score += len(keyword) / 10.0

        if score > best_score:
            best_score = score
            best_match = rule_type

    # Normalize confidence: cap at 1.0
    confidence = min(best_score / 3.0, 1.0)

    # If no keywords matched, default with low confidence
    if best_score == 0.0:
        confidence = 0.2

    return best_match, confidence


def _extract_parameters(
    rule_type: str,
    rejection_reason: str,
    recommendation: Recommendation,
    technician: Optional[Technician],
    role: Optional[ProjectRole],
    session: Session,
) -> dict[str, Any]:
    """Extract suggested parameters from the rejection context.

    Examines the rejection reason text, the rejected technician's profile,
    and the role requirements to propose reasonable parameter values.
    """
    reason_lower = rejection_reason.lower()
    params: dict[str, Any] = {}

    if rule_type == "experience_threshold":
        # Infer min stage from technician's current stage (they were deemed too junior)
        if technician:
            stage = getattr(technician, "career_stage", None)
            stage_hierarchy = [
                "Sourced", "Screened", "In Training",
                "Training Completed", "Awaiting Assignment", "Deployed",
            ]
            if stage and stage in stage_hierarchy:
                idx = stage_hierarchy.index(stage)
                # Suggest one level above current as minimum
                next_idx = min(idx + 1, len(stage_hierarchy) - 1)
                params["min_career_stage"] = stage_hierarchy[next_idx]
            else:
                params["min_career_stage"] = "Training Completed"
        else:
            params["min_career_stage"] = "Training Completed"

    elif rule_type == "skill_level_minimum":
        # Try to extract skill name from rejection reason
        skill_name = _extract_skill_name(reason_lower, technician, role, session)
        params["skill_name"] = skill_name
        # Default to Intermediate minimum
        if "advanced" in reason_lower:
            params["min_level"] = "Advanced"
        else:
            params["min_level"] = "Intermediate"

    elif rule_type == "cert_requirement":
        cert_name = _extract_cert_name(reason_lower, role)
        params["cert_name"] = cert_name

    elif rule_type == "location_restriction":
        if role:
            project = role.project
            if project and hasattr(project, "location_region"):
                params["allowed_regions"] = project.location_region
                params["restriction_type"] = "include"
            else:
                params["allowed_regions"] = ""
                params["restriction_type"] = "include"
        else:
            params["allowed_regions"] = ""
            params["restriction_type"] = "include"

    elif rule_type == "availability_window":
        # Extract number of days if mentioned
        day_match = re.search(r"(\d+)\s*(?:day|week)", reason_lower)
        if day_match:
            days = int(day_match.group(1))
            if "week" in day_match.group(0):
                days *= 7
            params["min_days_available"] = days
        else:
            params["min_days_available"] = 14  # Default 2 weeks

    elif rule_type == "project_count_minimum":
        # Count tech's completed assignments to infer a reasonable minimum
        if technician:
            completed = session.query(Assignment).filter(
                Assignment.technician_id == str(technician.id),
                Assignment.status == "Completed",
            ).count()
            # Suggest one more than what the rejected tech had
            params["min_projects"] = max(completed + 1, 2)
        else:
            params["min_projects"] = 2

    elif rule_type == "rate_cap":
        rate_match = re.search(r"\$?(\d+(?:\.\d{1,2})?)", rejection_reason)
        if rate_match:
            params["max_hourly_rate"] = float(rate_match.group(1))
        else:
            params["max_hourly_rate"] = 50.0  # Reasonable default

    return params


def _extract_skill_name(
    reason_lower: str,
    technician: Optional[Technician],
    role: Optional[ProjectRole],
    session: Session,
) -> str:
    """Try to extract a skill name from the rejection reason or role requirements."""
    # Common fiber/data center skill names to look for
    known_skills = [
        "fiber splicing", "fusion splicing", "otdr testing", "cable termination",
        "fiber routing", "aerial construction", "underground construction",
        "data center operations", "rack and stack", "cable management",
        "network testing", "safety protocol", "project management",
        "site survey", "as-built documentation",
    ]
    for skill in known_skills:
        if skill in reason_lower:
            return skill.title()

    # Check role required skills if available
    if role and role.required_skills:
        for rs in role.required_skills:
            skill_name = rs.get("skill_name", rs.get("name", ""))
            if skill_name and skill_name.lower() in reason_lower:
                return skill_name

    # Fall back to the technician's weakest skill on the role
    if technician and role and role.required_skills:
        return role.required_skills[0].get("skill_name", "General Skills") if role.required_skills else "General Skills"

    return "General Skills"


def _extract_cert_name(reason_lower: str, role: Optional[ProjectRole]) -> str:
    """Try to extract a certification name from the rejection reason."""
    known_certs = [
        "OSHA 10", "OSHA 30", "CPR", "First Aid", "CDL", "BICSI",
        "Confined Space", "HAZMAT", "DOT", "Flagging", "NFPA 70E",
        "Fiber Optic Installer", "OSP Technician",
    ]
    for cert in known_certs:
        if cert.lower() in reason_lower:
            return cert

    # Check role required certs
    if role and role.required_certs:
        for cert_name in role.required_certs:
            if isinstance(cert_name, str) and cert_name.lower() in reason_lower:
                return cert_name
        # If none matched in reason, suggest the first required cert
        if role.required_certs:
            first = role.required_certs[0]
            return first if isinstance(first, str) else first.get("name", "OSHA 10")

    return "OSHA 10"


# ---------------------------------------------------------------------------
# Main proposal function
# ---------------------------------------------------------------------------

def propose_preference_rule(
    session: Session,
    recommendation: Recommendation,
    rejection_reason: str,
    technician: Optional[Technician] = None,
    role: Optional[ProjectRole] = None,
) -> PreferenceRule:
    """Analyze a rejection and propose a matching preference rule.

    Creates a PreferenceRule with status='proposed' that contains:
    - The matched template rule_type
    - Suggested parameters extracted from context
    - The rejection reason as proposed_reason
    - Reference to the source recommendation

    The proposed rule is inactive (active=False) until approved by ops.

    Args:
        session: Database session
        recommendation: The rejected recommendation
        rejection_reason: Ops-provided rejection reason text
        technician: The rejected technician (optional, loaded if needed)
        role: The project role (optional, loaded if needed)

    Returns:
        The created PreferenceRule with status='proposed'
    """
    # Load related entities if not provided
    if technician is None and recommendation.target_entity_id:
        technician = session.get(Technician, recommendation.target_entity_id)
    if role is None and recommendation.role_id:
        role = session.get(ProjectRole, recommendation.role_id)

    # Match rejection reason to best template
    rule_type, confidence = _match_template(rejection_reason)
    template = RULE_TEMPLATES[rule_type]

    # Extract suggested parameters from context
    parameters = _extract_parameters(
        rule_type=rule_type,
        rejection_reason=rejection_reason,
        recommendation=recommendation,
        technician=technician,
        role=role,
        session=session,
    )

    # Build threshold string from parameters
    threshold = _build_threshold_string(rule_type, parameters)

    # Map rule_type to PreferenceRuleTemplateType
    template_type_map = {
        "experience_threshold": PreferenceRuleTemplateType.EXPERIENCE_MINIMUM.value,
        "skill_level_minimum": PreferenceRuleTemplateType.SKILL_MINIMUM.value,
        "cert_requirement": PreferenceRuleTemplateType.CERT_REQUIRED.value,
        "location_restriction": PreferenceRuleTemplateType.REGION_EXCLUSION.value,
        "availability_window": PreferenceRuleTemplateType.AVAILABILITY_WINDOW.value,
        "project_count_minimum": PreferenceRuleTemplateType.PROJECT_HISTORY.value,
        "rate_cap": PreferenceRuleTemplateType.SCORE_THRESHOLD.value,
    }

    # Determine score modifier based on effect
    score_modifier_map = {
        "exclude": -100.0,
        "demote": -20.0,
        "boost": 10.0,
    }

    # Create the proposed rule
    proposed_rule = PreferenceRule(
        rule_type=rule_type,
        template_type=template_type_map.get(rule_type, PreferenceRuleTemplateType.CUSTOM.value),
        description=template.description,
        threshold=threshold,
        scope=template.default_scope,
        effect=template.default_effect,
        score_modifier=score_modifier_map.get(template.default_effect, -20.0),
        parameters={
            **parameters,
            "confidence": round(confidence, 2),
            "template_description": template.description,
        },
        active=False,  # Not active until approved
        status=PreferenceRuleStatus.PROPOSED.value,
        rejection_id=recommendation.id,
        source_recommendation_id=recommendation.id,
        created_by_type=PreferenceRuleCreatedByType.AGENT.value,
        created_by_id="rejection_learning_agent",
        proposed_reason=(
            f"Proposed based on rejection of technician recommendation: "
            f"{rejection_reason}"
        ),
    )

    session.add(proposed_rule)

    logger.info(
        "Proposed preference rule: type=%s, effect=%s, threshold=%s, confidence=%.2f "
        "(source_recommendation=%s)",
        rule_type, template.default_effect, threshold, confidence,
        str(recommendation.id),
    )

    return proposed_rule


def _build_threshold_string(rule_type: str, parameters: dict) -> str:
    """Build a human-readable threshold string from rule parameters."""
    if rule_type == "experience_threshold":
        return parameters.get("min_career_stage", "Training Completed")

    elif rule_type == "skill_level_minimum":
        skill = parameters.get("skill_name", "General Skills")
        level = parameters.get("min_level", "Intermediate")
        return f"{skill}: {level}"

    elif rule_type == "cert_requirement":
        return parameters.get("cert_name", "OSHA 10")

    elif rule_type == "location_restriction":
        regions = parameters.get("allowed_regions", "")
        rtype = parameters.get("restriction_type", "include")
        return f"{rtype}: {regions}" if regions else "No restriction"

    elif rule_type == "availability_window":
        days = parameters.get("min_days_available", 14)
        return f"{days} days"

    elif rule_type == "project_count_minimum":
        count = parameters.get("min_projects", 2)
        return f"{count} projects"

    elif rule_type == "rate_cap":
        rate = parameters.get("max_hourly_rate", 50.0)
        return f"${rate}/hr"

    return str(parameters)


# ---------------------------------------------------------------------------
# Bulk analysis: check for pattern in recent rejections
# ---------------------------------------------------------------------------

def analyze_rejection_patterns(
    session: Session,
    lookback_count: int = 10,
) -> list[dict[str, Any]]:
    """Analyze recent rejections for recurring patterns.

    Looks at the last N rejected recommendations to identify if multiple
    rejections point to the same rule type. Returns suggested rules for
    patterns that appear 2+ times.

    This is used by the nightly batch to proactively suggest rules
    from accumulated rejection feedback.
    """
    rejected = (
        session.query(Recommendation)
        .filter(
            Recommendation.status == RecommendationStatus.REJECTED.value,
            Recommendation.rejection_reason.isnot(None),
        )
        .order_by(Recommendation.updated_at.desc())
        .limit(lookback_count)
        .all()
    )

    if not rejected:
        return []

    # Count template matches
    pattern_counts: dict[str, list[Recommendation]] = {}
    for rec in rejected:
        rule_type, confidence = _match_template(rec.rejection_reason or "")
        if confidence >= 0.3:
            pattern_counts.setdefault(rule_type, []).append(rec)

    patterns = []
    for rule_type, recs in pattern_counts.items():
        if len(recs) >= 2:
            template = RULE_TEMPLATES[rule_type]
            patterns.append({
                "rule_type": rule_type,
                "description": template.description,
                "occurrence_count": len(recs),
                "rejection_reasons": [r.rejection_reason for r in recs],
                "recommendation_ids": [str(r.id) for r in recs],
            })

    return patterns
