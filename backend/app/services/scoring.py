"""Scoring engine for technician-to-role matching.

Produces 5-dimension scorecards used by the staffing recommendation agent:
  1. Skills Match     — weighted overlap of required vs. held skills + proficiency
  2. Certification Fit — coverage of required certs (active, not expired)
  3. Availability      — available_from alignment with project start
  4. Location Fit      — home_base proximity + approved regions overlap
  5. Experience        — career stage, project count, total hours

Each dimension is scored 0-100. An overall weighted composite produces the
final ranking score.
"""

import logging
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    DeployabilityStatus,
    ProficiencyLevel,
    CareerStage,
)
from app.models.project import ProjectRole, Project, ProjectStatus
from app.models.assignment import Assignment
from app.models.recommendation import PreferenceRule

logger = logging.getLogger("deployable.scoring")

# Dimension weights (sum to 1.0)
DEFAULT_WEIGHTS = {
    "skills_match": 0.30,
    "certification_fit": 0.20,
    "availability": 0.20,
    "location_fit": 0.15,
    "experience": 0.15,
}

PROFICIENCY_SCORES = {
    ProficiencyLevel.APPRENTICE.value: 30,
    ProficiencyLevel.INTERMEDIATE.value: 65,
    ProficiencyLevel.ADVANCED.value: 100,
    "Apprentice": 30,
    "Intermediate": 65,
    "Advanced": 100,
}


def score_technician_for_role(
    session: Session,
    technician: Technician,
    role: ProjectRole,
    project: Optional[Project] = None,
    preference_rules: Optional[list[PreferenceRule]] = None,
) -> dict[str, Any]:
    """Score a technician against a project role on 5 dimensions.

    Returns:
        {
            "technician_id": str,
            "role_id": str,
            "overall_score": float,  # 0-100
            "dimensions": {
                "skills_match": {"score": float, "detail": str},
                "certification_fit": {"score": float, "detail": str},
                "availability": {"score": float, "detail": str},
                "location_fit": {"score": float, "detail": str},
                "experience": {"score": float, "detail": str},
            },
            "disqualified": bool,
            "disqualification_reason": str | None,
            "preference_adjustments": list[dict],
        }
    """
    dimensions = {}
    disqualified = False
    disqualification_reason = None
    preference_adjustments = []

    # --- 1. Skills Match ---
    dimensions["skills_match"] = _score_skills(session, technician, role)

    # --- 2. Certification Fit ---
    dimensions["certification_fit"] = _score_certifications(session, technician, role)

    # --- 3. Availability ---
    dimensions["availability"] = _score_availability(technician, project)

    # --- 4. Location Fit ---
    dimensions["location_fit"] = _score_location(technician, project)

    # --- 5. Experience ---
    dimensions["experience"] = _score_experience(session, technician)

    # --- Check hard disqualification ---
    if dimensions["certification_fit"]["score"] == 0 and role.required_certs:
        disqualified = True
        disqualification_reason = "Missing all required certifications"
    ds = technician.deployability_status
    ds_val = ds.value if hasattr(ds, "value") else str(ds) if ds else ""
    if ds_val == DeployabilityStatus.INACTIVE.value:
        disqualified = True
        disqualification_reason = "Technician is inactive"

    # --- Apply preference rules ---
    if preference_rules:
        for rule in preference_rules:
            if not rule.active:
                continue
            adjustment = _apply_preference_rule(rule, technician, dimensions)
            if adjustment:
                preference_adjustments.append(adjustment)
                if adjustment.get("effect") == "exclude":
                    disqualified = True
                    disqualification_reason = f"Excluded by rule: {rule.rule_type}"

    # --- Compute weighted overall ---
    weights = DEFAULT_WEIGHTS.copy()
    # Use skill_weights from role if provided
    if role.skill_weights and isinstance(role.skill_weights, dict):
        for key, weight in role.skill_weights.items():
            if key in weights:
                weights[key] = weight

    overall = sum(
        dimensions[dim]["score"] * weights.get(dim, 0.0) for dim in dimensions
    )

    # Apply preference demotions
    for adj in preference_adjustments:
        if adj.get("effect") == "demote":
            overall *= adj.get("multiplier", 0.8)

    return {
        "technician_id": str(technician.id),
        "role_id": str(role.id),
        "overall_score": round(overall, 1),
        "dimensions": dimensions,
        "disqualified": disqualified,
        "disqualification_reason": disqualification_reason,
        "preference_adjustments": preference_adjustments,
    }


def _score_skills(
    session: Session, technician: Technician, role: ProjectRole
) -> dict[str, Any]:
    """Score skill overlap between technician and role requirements."""
    required_skills = role.required_skills or []
    if not required_skills:
        return {"score": 75.0, "detail": "No specific skills required; default score"}

    # Get tech's skills — split model uses skill_name directly
    tech_skills = {}
    for ts in technician.skills:
        skill_name = getattr(ts, "skill_name", None)
        if not skill_name and hasattr(ts, "skill") and ts.skill:
            skill_name = ts.skill.name
        if skill_name:
            prof = ts.proficiency_level
            # Handle both enum and string proficiency values
            prof_val = prof.value if hasattr(prof, "value") else str(prof) if prof else "Apprentice"
            tech_skills[skill_name.lower()] = {
                "proficiency": prof_val,
                "hours": getattr(ts, "training_hours_accumulated", 0) or getattr(ts, "training_hours", 0) or 0,
            }

    matched = 0
    total_proficiency_score = 0
    details = []

    for req in required_skills:
        req_name = req if isinstance(req, str) else req.get("skill_name", req.get("name", ""))
        min_prof = "Apprentice"
        if isinstance(req, dict):
            min_prof = req.get("min_proficiency", "Apprentice")

        req_lower = req_name.lower()
        if req_lower in tech_skills:
            matched += 1
            prof = tech_skills[req_lower]["proficiency"]
            prof_score = PROFICIENCY_SCORES.get(prof, 30)
            min_score = PROFICIENCY_SCORES.get(min_prof, 30)
            # Bonus if above minimum, penalty if below
            if prof_score >= min_score:
                total_proficiency_score += min(100, prof_score + 10)
                details.append(f"{req_name}: {prof} (meets requirement)")
            else:
                total_proficiency_score += prof_score * 0.7
                details.append(f"{req_name}: {prof} (below {min_prof} requirement)")
        else:
            details.append(f"{req_name}: missing")

    if len(required_skills) == 0:
        score = 75.0
    else:
        coverage = matched / len(required_skills)
        avg_prof = total_proficiency_score / max(matched, 1)
        score = (coverage * 60) + (avg_prof / 100 * 40)

    return {
        "score": round(min(100, score), 1),
        "detail": "; ".join(details) if details else "All skills matched",
    }


def _score_certifications(
    session: Session, technician: Technician, role: ProjectRole
) -> dict[str, Any]:
    """Score certification coverage."""
    required_certs = role.required_certs or []
    if not required_certs:
        return {"score": 100.0, "detail": "No certifications required"}

    tech_certs = set()
    for cert in technician.certifications:
        cert_name = cert.cert_name if hasattr(cert, "cert_name") else ""
        status = cert.status
        # Handle both enum and string status values
        status_val = status.value if hasattr(status, "value") else str(status) if status else "Active"
        if status_val in ("Active", "Expiring Soon"):
            tech_certs.add(cert_name.lower())

    matched = 0
    details = []
    for req_cert in required_certs:
        req_lower = req_cert.lower() if isinstance(req_cert, str) else str(req_cert).lower()
        if req_lower in tech_certs:
            matched += 1
            details.append(f"{req_cert}: active")
        else:
            details.append(f"{req_cert}: missing")

    score = (matched / len(required_certs)) * 100

    return {
        "score": round(score, 1),
        "detail": "; ".join(details),
    }


def _score_availability(
    technician: Technician, project: Optional[Project]
) -> dict[str, Any]:
    """Score availability alignment with project timeline."""
    if not project or not project.start_date:
        # No project context — check general availability
        ds = technician.deployability_status
        ds_val = ds.value if hasattr(ds, "value") else str(ds) if ds else ""
        if ds_val == DeployabilityStatus.READY_NOW.value:
            return {"score": 100.0, "detail": "Ready now, no project date constraint"}
        if ds_val == DeployabilityStatus.ROLLING_OFF_SOON.value:
            return {"score": 80.0, "detail": "Rolling off soon, likely available"}
        if ds_val == DeployabilityStatus.CURRENTLY_ASSIGNED.value:
            return {"score": 20.0, "detail": "Currently assigned"}
        return {"score": 50.0, "detail": "Availability uncertain"}

    today = date.today()
    avail_date = technician.available_from or today

    if avail_date <= project.start_date:
        # Available before project starts
        days_early = (project.start_date - avail_date).days
        if days_early <= 14:
            score = 100.0
            detail = f"Available {days_early}d before start"
        elif days_early <= 30:
            score = 90.0
            detail = f"Available {days_early}d before start (minor gap)"
        else:
            score = 75.0
            detail = f"Available {days_early}d before start (idle period)"
    else:
        # Available after project starts
        days_late = (avail_date - project.start_date).days
        if days_late <= 7:
            score = 70.0
            detail = f"Available {days_late}d after project start"
        elif days_late <= 21:
            score = 40.0
            detail = f"Available {days_late}d after start (late)"
        else:
            score = 10.0
            detail = f"Available {days_late}d after start (too late)"

    return {"score": round(score, 1), "detail": detail}


def _score_location(
    technician: Technician, project: Optional[Project]
) -> dict[str, Any]:
    """Score geographic fit."""
    if not project:
        return {"score": 70.0, "detail": "No project location to match"}

    project_region = project.location_region or ""
    project_city = project.location_city or ""
    tech_regions = technician.approved_regions or []
    tech_city = technician.home_base_city or ""

    score = 0.0
    details = []

    # City match
    if tech_city.lower() == project_city.lower() and project_city:
        score = 100.0
        details.append(f"Home base matches project city ({tech_city})")
    elif project_region and project_region in tech_regions:
        score = 80.0
        details.append(f"Approved for region {project_region}")
    elif tech_regions:
        score = 30.0
        details.append(f"Not approved for {project_region}; approved: {', '.join(tech_regions[:3])}")
    else:
        score = 50.0
        details.append("No region restrictions set")

    return {"score": round(score, 1), "detail": "; ".join(details)}


def _score_experience(
    session: Session, technician: Technician
) -> dict[str, Any]:
    """Score based on career stage and assignment history."""
    cs = technician.career_stage
    career_stage = cs.value if hasattr(cs, "value") else str(cs) if cs else ""
    details = []

    # Base score from career stage
    stage_scores = {
        "Deployed": 90,
        "Awaiting Assignment": 75,
        "Training Completed": 65,
        "In Training": 40,
        "Screened": 25,
        "Sourced": 10,
    }
    base = stage_scores.get(career_stage, 50)
    details.append(f"Career stage: {career_stage} (base {base})")

    # Boost for prior assignments
    assignment_count = (
        session.query(Assignment)
        .filter(Assignment.technician_id == technician.id)
        .count()
    )
    if assignment_count > 5:
        base = min(100, base + 10)
        details.append(f"{assignment_count} prior assignments (+10)")
    elif assignment_count > 2:
        base = min(100, base + 5)
        details.append(f"{assignment_count} prior assignments (+5)")
    elif assignment_count > 0:
        details.append(f"{assignment_count} prior assignment(s)")

    return {"score": round(min(100, float(base)), 1), "detail": "; ".join(details)}


def _apply_preference_rule(
    rule: PreferenceRule, technician: Technician, dimensions: dict
) -> Optional[dict[str, Any]]:
    """Apply a single preference rule and return adjustment if triggered."""
    params = rule.parameters or {}

    if rule.rule_type == "experience_threshold":
        min_stage_order = {
            "Sourced": 0, "Screened": 1, "In Training": 2,
            "Training Completed": 3, "Awaiting Assignment": 4, "Deployed": 5,
        }
        required_min = params.get("min_career_stage", "Screened")
        cs = technician.career_stage
        cs_val = cs.value if hasattr(cs, "value") else str(cs) if cs else ""
        tech_order = min_stage_order.get(cs_val, 0)
        req_order = min_stage_order.get(required_min, 0)
        if tech_order < req_order:
            return {
                "rule_id": rule.id,
                "rule_type": rule.rule_type,
                "effect": rule.effect,
                "multiplier": 0.5 if rule.effect == "demote" else None,
                "reason": f"Career stage {cs_val} below minimum {required_min}",
            }

    elif rule.rule_type == "skill_level_minimum":
        target_skill = params.get("skill_name", "").lower()
        min_prof = params.get("min_proficiency", "Intermediate")
        min_score = PROFICIENCY_SCORES.get(min_prof, 65)
        for ts in technician.skills:
            skill_name = getattr(ts, "skill_name", "")
            if not skill_name and hasattr(ts, "skill") and ts.skill:
                skill_name = ts.skill.name
            if skill_name.lower() == target_skill:
                prof = ts.proficiency_level
                prof_val = prof.value if hasattr(prof, "value") else str(prof) if prof else "Apprentice"
                prof_score = PROFICIENCY_SCORES.get(prof_val, 30)
                if prof_score < min_score:
                    return {
                        "rule_id": rule.id,
                        "rule_type": rule.rule_type,
                        "effect": rule.effect,
                        "multiplier": 0.7 if rule.effect == "demote" else None,
                        "reason": f"{skill_name} proficiency below {min_prof}",
                    }

    elif rule.rule_type == "location_restriction":
        excluded_regions = params.get("excluded_regions", [])
        for region in (technician.approved_regions or []):
            if region in excluded_regions:
                return {
                    "rule_id": rule.id,
                    "rule_type": rule.rule_type,
                    "effect": rule.effect,
                    "multiplier": 0.6 if rule.effect == "demote" else None,
                    "reason": f"Region {region} in exclusion list",
                }

    return None


def rank_technicians_for_role(
    session: Session,
    role: ProjectRole,
    project: Optional[Project] = None,
    limit: int = 10,
    exclude_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Score and rank all eligible technicians for a role.

    Returns top `limit` candidates sorted by overall_score descending,
    with disqualified candidates filtered out.
    """
    exclude_ids = exclude_ids or set()

    # Load preference rules
    preference_rules = (
        session.query(PreferenceRule).filter(PreferenceRule.active == True).all()
    )

    # Get all non-inactive technicians
    technicians = (
        session.query(Technician)
        .filter(Technician.deployability_status != DeployabilityStatus.INACTIVE)
        .all()
    )

    scored = []
    # Convert exclude_ids to strings for consistent comparison
    exclude_strs = {str(eid) for eid in exclude_ids}
    for tech in technicians:
        if str(tech.id) in exclude_strs:
            continue
        scorecard = score_technician_for_role(
            session, tech, role, project, preference_rules
        )
        if not scorecard["disqualified"]:
            scored.append(scorecard)

    # Sort by overall score descending
    scored.sort(key=lambda s: s["overall_score"], reverse=True)

    return scored[:limit]
