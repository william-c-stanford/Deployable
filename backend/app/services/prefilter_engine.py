"""
Deterministic pre-filtering engine for staffing recommendations.

This module implements the SQL-based candidate pre-filtering and scoring pipeline:
1. Hard constraint filtering (certifications, availability, region, clearance)
2. Soft scoring across 5 dimensions (skills match, cert match, experience, availability, travel)
3. Preference rule weight modifiers (boost/demote/exclude)
4. Final ranked top-N shortlist production

The engine is deterministic — given the same inputs, it always produces the same output.
No LLM calls here; this is the SQL pre-filter stage before optional LLM re-ranking.
"""

from __future__ import annotations

import math
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Union

from sqlalchemy import and_, or_, func, case, literal
from sqlalchemy.orm import Session, joinedload

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    ProficiencyLevel,
    DeployabilityStatus,
    CertStatus,
)
from app.models.project import ProjectRole, Project
from app.models.assignment import Assignment
from app.models.recommendation import PreferenceRule
from app.services.sql_scoring import (
    build_scoring_modifiers_with_params,
    apply_sql_modifiers_to_score,
    SQLScoringModifier,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFICIENCY_RANK = {
    ProficiencyLevel.APPRENTICE.value: 1,
    ProficiencyLevel.INTERMEDIATE.value: 2,
    ProficiencyLevel.ADVANCED.value: 3,
    "Apprentice": 1,
    "Intermediate": 2,
    "Advanced": 3,
}

# Default scoring weights (out of 100)
DEFAULT_WEIGHTS = {
    "skills_match": 30.0,
    "cert_match": 25.0,
    "experience": 20.0,
    "availability": 15.0,
    "travel_fit": 10.0,
}

# Deployability statuses that are eligible for staffing
ELIGIBLE_STATUSES = {
    DeployabilityStatus.READY_NOW.value,
    DeployabilityStatus.ROLLING_OFF_SOON.value,
}

DEFAULT_TOP_N = 20


# ---------------------------------------------------------------------------
# Data classes for structured output
# ---------------------------------------------------------------------------

@dataclass
class SkillScore:
    """Score for a single required skill."""
    skill_name: str
    required_level: str
    technician_level: Optional[str]
    training_hours: float
    score: float  # 0.0 to 1.0
    met: bool


@dataclass
class CertScore:
    """Score for a single required certification."""
    cert_name: str
    has_cert: bool
    is_active: bool
    expiry_date: Optional[date]
    score: float  # 0.0 or 1.0


@dataclass
class Scorecard:
    """5-dimension scorecard for a technician-role match."""
    skills_match: float = 0.0
    cert_match: float = 0.0
    experience: float = 0.0
    availability: float = 0.0
    travel_fit: float = 0.0
    total_weighted: float = 0.0
    skill_details: list[SkillScore] = field(default_factory=list)
    cert_details: list[CertScore] = field(default_factory=list)
    preference_adjustments: list[dict] = field(default_factory=list)
    disqualified: bool = False
    disqualification_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skills_match": round(self.skills_match, 2),
            "cert_match": round(self.cert_match, 2),
            "experience": round(self.experience, 2),
            "availability": round(self.availability, 2),
            "travel_fit": round(self.travel_fit, 2),
            "total_weighted": round(self.total_weighted, 2),
            "skill_details": [
                {
                    "skill_name": s.skill_name,
                    "required_level": s.required_level,
                    "technician_level": s.technician_level,
                    "training_hours": s.training_hours,
                    "score": round(s.score, 2),
                    "met": s.met,
                }
                for s in self.skill_details
            ],
            "cert_details": [
                {
                    "cert_name": c.cert_name,
                    "has_cert": c.has_cert,
                    "is_active": c.is_active,
                    "expiry_date": c.expiry_date.isoformat() if c.expiry_date else None,
                    "score": round(c.score, 2),
                }
                for c in self.cert_details
            ],
            "preference_adjustments": self.preference_adjustments,
            "disqualified": self.disqualified,
            "disqualification_reasons": self.disqualification_reasons,
        }


@dataclass
class CandidateResult:
    """A scored candidate in the shortlist."""
    technician_id: str
    technician_name: str
    scorecard: Scorecard
    rank: int = 0
    explanation: str = ""

    def to_dict(self) -> dict:
        return {
            "technician_id": self.technician_id,
            "technician_name": self.technician_name,
            "rank": self.rank,
            "scorecard": self.scorecard.to_dict(),
            "explanation": self.explanation,
        }


@dataclass
class PrefilterResult:
    """Complete result of pre-filtering for a role."""
    role_id: str
    role_name: str
    project_id: str
    project_name: str
    candidates: list[CandidateResult]
    total_evaluated: int
    total_passed_hard_filter: int
    total_shortlisted: int
    weights_used: dict
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "candidates": [c.to_dict() for c in self.candidates],
            "total_evaluated": self.total_evaluated,
            "total_passed_hard_filter": self.total_passed_hard_filter,
            "total_shortlisted": self.total_shortlisted,
            "weights_used": self.weights_used,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Hard constraint filters (SQL-level)
# ---------------------------------------------------------------------------

def _get_eligible_technicians(
    db: Session,
    role: ProjectRole,
    project: Project,
    as_of_date: Optional[date] = None,
) -> list[Technician]:
    """
    Stage 1: SQL-level hard constraint filtering.

    Filters applied:
    1. Deployability status must be in ELIGIBLE_STATUSES
    2. Region overlap: technician's approved_regions must overlap project location_region
    3. Availability: technician's available_from must be <= project start_date (or as_of_date)
    4. Not already assigned to this role (active assignment)
    """
    if as_of_date is None:
        as_of_date = project.start_date or date.today()

    query = (
        db.query(Technician)
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
        )
        .filter(
            # 1. Must be in an eligible deployability status
            Technician.deployability_status.in_(list(ELIGIBLE_STATUSES)),
        )
    )

    # 2. Availability check: available_from must be null (available now) or <= start date
    query = query.filter(
        or_(
            Technician.available_from.is_(None),
            Technician.available_from <= as_of_date,
        )
    )

    # Execute and do region filtering in Python (JSON array contains check varies by DB)
    candidates = query.all()

    # Deduplicate (joinedload can produce cartesian products)
    seen_ids = set()
    unique_candidates = []
    for tech in candidates:
        tech_id = str(tech.id)
        if tech_id not in seen_ids:
            seen_ids.add(tech_id)
            unique_candidates.append(tech)

    # 3. Region overlap filter (Python-side due to JSON array)
    project_region = project.location_region
    if project_region:
        region_filtered = []
        for tech in unique_candidates:
            regions = tech.approved_regions or []
            if project_region in regions:
                region_filtered.append(tech)
        unique_candidates = region_filtered

    # 4. Exclude technicians already actively assigned to this role
    existing_assignment_tech_ids = set()
    if role.id:
        existing = (
            db.query(Assignment.technician_id)
            .filter(
                Assignment.role_id == role.id,
                Assignment.status == "Active",
            )
            .all()
        )
        existing_assignment_tech_ids = {str(row[0]) for row in existing}

    final_candidates = [
        t for t in unique_candidates
        if str(t.id) not in existing_assignment_tech_ids
    ]

    return final_candidates


# ---------------------------------------------------------------------------
# Hard cert check (disqualification gate)
# ---------------------------------------------------------------------------

def _check_required_certs(
    tech: Technician,
    required_certs: list[str],
) -> tuple[bool, list[CertScore], list[str]]:
    """
    Check if a technician has all required certifications with Active status.

    Returns:
        (all_met, cert_scores, disqualification_reasons)
    """
    if not required_certs:
        return True, [], []

    tech_cert_map: dict[str, TechnicianCertification] = {}
    for tc in (tech.certifications or []):
        # Normalize cert name for matching
        key = tc.cert_name.strip().lower()
        tech_cert_map[key] = tc

    cert_scores = []
    disqualifications = []
    all_met = True

    for required in required_certs:
        required_key = required.strip().lower()
        tc = tech_cert_map.get(required_key)

        if tc is None:
            cert_scores.append(CertScore(
                cert_name=required,
                has_cert=False,
                is_active=False,
                expiry_date=None,
                score=0.0,
            ))
            disqualifications.append(f"Missing required certification: {required}")
            all_met = False
        elif tc.status and tc.status.value if hasattr(tc.status, 'value') else tc.status != CertStatus.ACTIVE.value:
            # Has cert but not active
            status_val = tc.status.value if hasattr(tc.status, 'value') else tc.status
            is_active = status_val == CertStatus.ACTIVE.value
            cert_scores.append(CertScore(
                cert_name=required,
                has_cert=True,
                is_active=is_active,
                expiry_date=tc.expiry_date,
                score=0.5 if not is_active else 1.0,
            ))
            if not is_active:
                disqualifications.append(
                    f"Certification {required} exists but status is {status_val}"
                )
                all_met = False
        else:
            cert_scores.append(CertScore(
                cert_name=required,
                has_cert=True,
                is_active=True,
                expiry_date=tc.expiry_date,
                score=1.0,
            ))

    return all_met, cert_scores, disqualifications


# ---------------------------------------------------------------------------
# Soft scoring functions (Python-level)
# ---------------------------------------------------------------------------

def _score_skills(
    tech: Technician,
    skill_bundle: list[dict],
    skill_weights: Optional[dict] = None,
) -> tuple[float, list[SkillScore]]:
    """
    Score a technician's skill match against a role's required skill bundle.

    Each skill in the bundle is scored 0-1 based on proficiency level match:
    - Has skill at or above required level: 1.0
    - Has skill one level below required: 0.5
    - Has skill two levels below required: 0.25
    - Missing skill entirely: 0.0

    Returns (aggregate_score_0_to_100, skill_detail_list)
    """
    if not skill_bundle:
        return 100.0, []

    tech_skill_map: dict[str, TechnicianSkill] = {}
    for ts in (tech.skills or []):
        key = ts.skill_name.strip().lower()
        tech_skill_map[key] = ts

    scores = []
    weights = []

    for req in skill_bundle:
        skill_name = req.get("skill", "")
        min_level = req.get("min_level", "Apprentice")
        skill_key = skill_name.strip().lower()

        # Get weight for this skill (default 1.0)
        weight = 1.0
        if skill_weights and skill_name in skill_weights:
            weight = float(skill_weights[skill_name])

        ts = tech_skill_map.get(skill_key)

        if ts is None:
            scores.append(SkillScore(
                skill_name=skill_name,
                required_level=min_level,
                technician_level=None,
                training_hours=0.0,
                score=0.0,
                met=False,
            ))
            weights.append(weight)
            continue

        tech_level_val = ts.proficiency_level.value if hasattr(ts.proficiency_level, 'value') else ts.proficiency_level
        required_rank = PROFICIENCY_RANK.get(min_level, 1)
        tech_rank = PROFICIENCY_RANK.get(tech_level_val, 0)

        if tech_rank >= required_rank:
            score = 1.0
            met = True
        elif tech_rank == required_rank - 1:
            score = 0.5
            met = False
        else:
            score = 0.25
            met = False

        scores.append(SkillScore(
            skill_name=skill_name,
            required_level=min_level,
            technician_level=tech_level_val,
            training_hours=ts.training_hours_accumulated or 0.0,
            score=score,
            met=met,
        ))
        weights.append(weight)

    # Weighted average
    if not weights or sum(weights) == 0:
        return 0.0, scores

    total = sum(s.score * w for s, w in zip(scores, weights))
    aggregate = (total / sum(weights)) * 100.0
    return aggregate, scores


def _score_certs(cert_scores: list[CertScore]) -> float:
    """
    Compute aggregate cert score (0-100).
    Simple average of individual cert scores.
    """
    if not cert_scores:
        return 100.0  # No certs required = perfect score
    return (sum(c.score for c in cert_scores) / len(cert_scores)) * 100.0


def _score_experience(tech: Technician, role: ProjectRole) -> float:
    """
    Score experience on a 0-100 scale based on years and project count.

    - years_experience: 0-15+ years mapped to 0-70 points
    - total_project_count: 0-20+ projects mapped to 0-30 points
    """
    years = tech.years_experience or 0
    years_score = min(years / 15.0, 1.0) * 70.0

    projects = tech.total_project_count or 0
    project_score = min(projects / 20.0, 1.0) * 30.0

    return years_score + project_score


def _score_availability(
    tech: Technician,
    project: Project,
    as_of_date: Optional[date] = None,
) -> float:
    """
    Score availability on a 0-100 scale.

    - Available now (no available_from or available_from <= start_date): 100
    - Available within 2 weeks of start: 80
    - Available within 4 weeks: 50
    - Further out: 20
    """
    target = as_of_date or project.start_date or date.today()
    avail = tech.available_from

    if avail is None:
        return 100.0  # Immediately available

    delta_days = (avail - target).days

    if delta_days <= 0:
        return 100.0
    elif delta_days <= 14:
        return 80.0
    elif delta_days <= 28:
        return 50.0
    else:
        # Gradual decay for further out
        return max(10.0, 50.0 - (delta_days - 28) * 0.5)


def _score_travel_fit(
    tech: Technician,
    project: Project,
) -> float:
    """
    Score travel/location fit on a 0-100 scale.

    - Same state as project: 100
    - Willing to travel and project region in approved regions: 80
    - Willing to travel but region not explicitly approved: 40
    - Not willing to travel and different state: 10
    """
    project_region = project.location_region
    tech_state = tech.home_base_state or ""
    approved = tech.approved_regions or []
    willing = tech.willing_to_travel if tech.willing_to_travel is not None else True

    # Same state
    if tech_state.upper() == project_region.upper():
        return 100.0

    # Different state but region is in approved list
    if project_region in approved:
        return 90.0 if willing else 60.0

    # Different state, not in approved regions
    if willing:
        return 40.0
    else:
        return 10.0


# ---------------------------------------------------------------------------
# Preference rule application
# ---------------------------------------------------------------------------

def _load_active_preference_rules(db: Session, scope: str = "global") -> list[PreferenceRule]:
    """Load all active preference rules for the given scope."""
    return (
        db.query(PreferenceRule)
        .filter(
            PreferenceRule.active == True,
            or_(
                PreferenceRule.scope == scope,
                PreferenceRule.scope == "global",
            ),
        )
        .all()
    )


def _apply_preference_rules(
    scorecard: Scorecard,
    tech: Technician,
    rules: list[PreferenceRule],
) -> Scorecard:
    """
    Apply preference rules to modify the scorecard.

    Rule types:
    - experience_threshold: min years of experience
    - skill_level_minimum: min proficiency for a skill
    - archetype_preference: boost/demote by archetype
    - cert_bonus: extra score for having certain certs
    - rate_cap: exclude if hourly rate exceeds threshold
    - project_count_minimum: min project history
    """
    for rule in rules:
        adjustment = _evaluate_rule(rule, tech, scorecard)
        if adjustment is not None:
            scorecard.preference_adjustments.append(adjustment)

            effect = rule.effect or "demote"
            modifier = adjustment.get("modifier", 0)

            if effect == "exclude" and modifier < 0:
                scorecard.disqualified = True
                scorecard.disqualification_reasons.append(
                    f"Excluded by rule: {rule.rule_type} — {adjustment.get('reason', '')}"
                )
            elif effect == "boost":
                scorecard.total_weighted += modifier
            elif effect == "demote":
                scorecard.total_weighted += modifier  # modifier is negative for demote

    return scorecard


def _evaluate_rule(
    rule: PreferenceRule,
    tech: Technician,
    scorecard: Scorecard,
) -> Optional[dict]:
    """Evaluate a single preference rule against a technician. Returns adjustment dict or None."""
    params = rule.parameters or {}
    rule_type = rule.rule_type

    if rule_type == "experience_threshold":
        min_years = float(params.get("min_years", 0))
        tech_years = tech.years_experience or 0
        if tech_years < min_years:
            penalty = float(params.get("penalty", -10))
            return {
                "rule_type": rule_type,
                "rule_id": str(rule.id),
                "reason": f"Experience {tech_years:.1f}yr below threshold {min_years}yr",
                "modifier": penalty,
            }

    elif rule_type == "skill_level_minimum":
        skill_name = params.get("skill_name", "")
        min_level = params.get("min_level", "Intermediate")
        required_rank = PROFICIENCY_RANK.get(min_level, 2)

        for sd in scorecard.skill_details:
            if sd.skill_name.lower() == skill_name.lower():
                tech_rank = PROFICIENCY_RANK.get(sd.technician_level or "", 0)
                if tech_rank < required_rank:
                    penalty = float(params.get("penalty", -15))
                    return {
                        "rule_type": rule_type,
                        "rule_id": str(rule.id),
                        "reason": f"{skill_name} at {sd.technician_level or 'None'}, rule requires {min_level}",
                        "modifier": penalty,
                    }

    elif rule_type == "archetype_preference":
        preferred = params.get("preferred_archetype", "")
        bonus = float(params.get("bonus", 10))
        tech_archetype = tech.archetype or ""
        if tech_archetype == preferred:
            return {
                "rule_type": rule_type,
                "rule_id": str(rule.id),
                "reason": f"Archetype {preferred} matches preference",
                "modifier": bonus,
            }

    elif rule_type == "cert_bonus":
        cert_name = params.get("cert_name", "")
        bonus = float(params.get("bonus", 5))
        for tc in (tech.certifications or []):
            if tc.cert_name.strip().lower() == cert_name.strip().lower():
                status_val = tc.status.value if hasattr(tc.status, 'value') else tc.status
                if status_val == CertStatus.ACTIVE.value:
                    return {
                        "rule_type": rule_type,
                        "rule_id": str(rule.id),
                        "reason": f"Holds bonus certification: {cert_name}",
                        "modifier": bonus,
                    }

    elif rule_type == "rate_cap":
        max_rate = float(params.get("max_hourly_rate", 999))
        tech_rate = tech.hourly_rate_min or 0
        if tech_rate > max_rate:
            penalty = float(params.get("penalty", -20))
            return {
                "rule_type": rule_type,
                "rule_id": str(rule.id),
                "reason": f"Min rate ${tech_rate}/hr exceeds cap ${max_rate}/hr",
                "modifier": penalty,
            }

    elif rule_type == "project_count_minimum":
        min_count = int(params.get("min_projects", 3))
        tech_count = tech.total_project_count or 0
        if tech_count < min_count:
            penalty = float(params.get("penalty", -5))
            return {
                "rule_type": rule_type,
                "rule_id": str(rule.id),
                "reason": f"Only {tech_count} prior projects, rule requires {min_count}",
                "modifier": penalty,
            }

    return None


# ---------------------------------------------------------------------------
# Explanation generation
# ---------------------------------------------------------------------------

def _generate_explanation(tech: Technician, scorecard: Scorecard, role: ProjectRole) -> str:
    """Generate a natural-language explanation for the candidate's ranking."""
    parts = []

    # Lead with overall assessment
    total = scorecard.total_weighted
    if total >= 80:
        parts.append(f"{tech.full_name} is a strong match for {role.role_name}.")
    elif total >= 60:
        parts.append(f"{tech.full_name} is a solid candidate for {role.role_name}.")
    elif total >= 40:
        parts.append(f"{tech.full_name} is a partial match for {role.role_name}.")
    else:
        parts.append(f"{tech.full_name} is a marginal match for {role.role_name}.")

    # Skills summary
    met_skills = [s for s in scorecard.skill_details if s.met]
    unmet_skills = [s for s in scorecard.skill_details if not s.met]
    if met_skills:
        names = ", ".join(s.skill_name for s in met_skills)
        parts.append(f"Meets skill requirements for: {names}.")
    if unmet_skills:
        gaps = ", ".join(f"{s.skill_name} (has {s.technician_level or 'None'}, needs {s.required_level})" for s in unmet_skills)
        parts.append(f"Skill gaps: {gaps}.")

    # Cert summary
    missing_certs = [c for c in scorecard.cert_details if not c.has_cert]
    inactive_certs = [c for c in scorecard.cert_details if c.has_cert and not c.is_active]
    if missing_certs:
        names = ", ".join(c.cert_name for c in missing_certs)
        parts.append(f"Missing certs: {names}.")
    if inactive_certs:
        names = ", ".join(c.cert_name for c in inactive_certs)
        parts.append(f"Inactive certs: {names}.")

    # Experience
    years = tech.years_experience or 0
    if years >= 10:
        parts.append(f"Highly experienced ({years:.0f} years, {tech.total_project_count or 0} projects).")
    elif years >= 5:
        parts.append(f"Experienced ({years:.0f} years, {tech.total_project_count or 0} projects).")
    else:
        parts.append(f"Earlier career ({years:.1f} years, {tech.total_project_count or 0} projects).")

    # Preference adjustments
    for adj in scorecard.preference_adjustments:
        sign = "+" if adj["modifier"] > 0 else ""
        parts.append(f"Pref rule ({adj['rule_type']}): {adj['reason']} [{sign}{adj['modifier']:.0f}pts].")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Main engine entry point
# ---------------------------------------------------------------------------

def _to_uuid(val: Union[str, uuid_mod.UUID]) -> uuid_mod.UUID:
    """Convert a string or UUID to a UUID object for ORM filtering."""
    if isinstance(val, uuid_mod.UUID):
        return val
    return uuid_mod.UUID(str(val))


def run_prefilter(
    db: Session,
    role_id: str,
    top_n: int = DEFAULT_TOP_N,
    as_of_date: Optional[date] = None,
    custom_weights: Optional[dict] = None,
    exclude_technician_ids: Optional[list[str]] = None,
) -> PrefilterResult:
    """
    Run the full deterministic pre-filtering pipeline for a project role.

    Args:
        db: Database session
        role_id: UUID of the ProjectRole to staff
        top_n: Number of candidates to return (default 20)
        as_of_date: Override date for availability checks
        custom_weights: Override default scoring weights
        exclude_technician_ids: Technician IDs to exclude (e.g. already dismissed)

    Returns:
        PrefilterResult with ranked candidates and metadata
    """
    # Load the role and its project
    role_uuid = _to_uuid(role_id)
    role = (
        db.query(ProjectRole)
        .options(joinedload(ProjectRole.project).joinedload(Project.partner))
        .filter(ProjectRole.id == role_uuid)
        .first()
    )

    if role is None:
        raise ValueError(f"ProjectRole {role_id} not found")

    project = role.project
    if project is None:
        raise ValueError(f"Project not found for role {role_id}")

    # Merge weights
    weights = dict(DEFAULT_WEIGHTS)
    if custom_weights:
        for k, v in custom_weights.items():
            if k in weights:
                weights[k] = float(v)

    # Normalize weights to sum to 100
    total_w = sum(weights.values())
    if total_w > 0 and total_w != 100:
        factor = 100.0 / total_w
        weights = {k: v * factor for k, v in weights.items()}

    # Stage 1: SQL hard constraint filtering
    eligible = _get_eligible_technicians(db, role, project, as_of_date)
    total_evaluated = len(eligible)

    # Apply exclusion list
    exclude_set = set(exclude_technician_ids or [])
    eligible = [t for t in eligible if str(t.id) not in exclude_set]

    # Load preference rules and build SQL scoring modifiers
    preference_rules = _load_active_preference_rules(db)
    sql_modifiers = build_scoring_modifiers_with_params(preference_rules)

    # Extract role requirements
    required_certs = role.required_certs or []
    skill_bundle = role.required_skills or []
    skill_weights_map = role.skill_weights or {}

    # Stage 2: Score each candidate
    candidates: list[CandidateResult] = []
    passed_hard_filter = 0

    for tech in eligible:
        # Hard cert gate
        certs_met, cert_details, cert_disqualifications = _check_required_certs(
            tech, required_certs
        )

        # Build scorecard even if certs not fully met (for transparency)
        skills_score, skill_details = _score_skills(tech, skill_bundle, skill_weights_map)
        cert_score = _score_certs(cert_details)
        experience_score = _score_experience(tech, role)
        availability_score = _score_availability(tech, project, as_of_date)
        travel_score = _score_travel_fit(tech, project)

        scorecard = Scorecard(
            skills_match=skills_score,
            cert_match=cert_score,
            experience=experience_score,
            availability=availability_score,
            travel_fit=travel_score,
            skill_details=skill_details,
            cert_details=cert_details,
        )

        # Calculate weighted total
        base_weighted = (
            (skills_score * weights["skills_match"] / 100.0)
            + (cert_score * weights["cert_match"] / 100.0)
            + (experience_score * weights["experience"] / 100.0)
            + (availability_score * weights["availability"] / 100.0)
            + (travel_score * weights["travel_fit"] / 100.0)
        )
        scorecard.total_weighted = base_weighted

        # Hard cert gate: disqualify if missing required certs
        if not certs_met:
            scorecard.disqualified = True
            scorecard.disqualification_reasons.extend(cert_disqualifications)

        # Apply preference rules via SQL scoring layer
        if sql_modifiers:
            adjusted, adjustments, excluded = apply_sql_modifiers_to_score(
                tech, scorecard.total_weighted, sql_modifiers
            )
            scorecard.total_weighted = adjusted
            scorecard.preference_adjustments = adjustments

            if excluded:
                scorecard.disqualified = True
                exclude_reasons = [
                    adj["reason"]
                    for adj in adjustments
                    if adj.get("effect") == "exclude" and adj.get("modifier", 0) < 0
                ]
                scorecard.disqualification_reasons.extend(
                    [f"Excluded by rule: {r}" for r in exclude_reasons]
                )
        else:
            # Fallback to legacy preference rules (no SQL modifiers built)
            scorecard = _apply_preference_rules(scorecard, tech, preference_rules)

        if not scorecard.disqualified:
            passed_hard_filter += 1

        explanation = _generate_explanation(tech, scorecard, role)

        candidates.append(CandidateResult(
            technician_id=str(tech.id),
            technician_name=tech.full_name,
            scorecard=scorecard,
            explanation=explanation,
        ))

    # Stage 3: Sort and rank
    # Non-disqualified candidates first, then by total weighted score descending
    candidates.sort(
        key=lambda c: (
            0 if not c.scorecard.disqualified else 1,
            -c.scorecard.total_weighted,
        )
    )

    # Assign ranks and trim to top_n (only non-disqualified)
    qualified = [c for c in candidates if not c.scorecard.disqualified]
    shortlist = qualified[:top_n]

    for i, candidate in enumerate(shortlist, start=1):
        candidate.rank = i

    return PrefilterResult(
        role_id=str(role.id),
        role_name=role.role_name,
        project_id=str(project.id),
        project_name=project.name,
        candidates=shortlist,
        total_evaluated=total_evaluated,
        total_passed_hard_filter=passed_hard_filter,
        total_shortlisted=len(shortlist),
        weights_used=weights,
        timestamp=datetime.utcnow().isoformat(),
    )


def run_prefilter_batch(
    db: Session,
    project_id: str,
    top_n: int = DEFAULT_TOP_N,
    as_of_date: Optional[date] = None,
    custom_weights: Optional[dict] = None,
) -> list[PrefilterResult]:
    """
    Run pre-filtering for ALL open roles in a project.

    Returns a list of PrefilterResult, one per role with open slots.
    """
    project_uuid = _to_uuid(project_id)
    project = (
        db.query(Project)
        .options(joinedload(Project.roles), joinedload(Project.partner))
        .filter(Project.id == project_uuid)
        .first()
    )

    if project is None:
        raise ValueError(f"Project {project_id} not found")

    results = []
    for role in project.roles:
        if role.open_slots > 0:
            result = run_prefilter(
                db=db,
                role_id=str(role.id),
                top_n=top_n,
                as_of_date=as_of_date,
                custom_weights=custom_weights,
            )
            results.append(result)

    return results
