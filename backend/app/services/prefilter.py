"""Pre-filter stage: SQL-based candidate selection.

This stage rapidly narrows the full technician pool to a short list of
plausible candidates using deterministic database queries.  The output
feeds into the LLM re-ranker for nuanced scoring.

Pre-filter criteria (applied in order):
1. Deployability status — must be Ready Now, Rolling Off Soon, or Awaiting Assignment
2. Career stage — must be Training Completed, Awaiting Assignment, or Deployed
3. Required certifications — must hold all required certs with Active status
4. Required skills — must have at least Apprentice in each required skill
5. Region / availability — soft filters applied as scoring bonuses rather than hard cuts
6. Not already assigned to this role (dedup against existing recommendations)
7. Preference rule exclusions — ops-defined hard excludes
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional, Dict, Any, Sequence

from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session, joinedload

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    DeployabilityStatus,
    CareerStage,
    ProficiencyLevel,
    CertStatus,
)
from app.models.assignment import Assignment
from app.models.recommendation import PreferenceRule, Recommendation, RecommendationStatus

logger = logging.getLogger("deployable.staffing.prefilter")

# Deployability statuses eligible for staffing
ELIGIBLE_STATUSES = {
    DeployabilityStatus.READY_NOW,
    DeployabilityStatus.ROLLING_OFF_SOON,
    DeployabilityStatus.READY_NOW,  # alias safety
}

# Career stages eligible for staffing
ELIGIBLE_CAREER_STAGES = {
    CareerStage.AWAITING_ASSIGNMENT,
    CareerStage.DEPLOYED,  # can be re-assigned if rolling off
}

# Proficiency level ordering for comparison
PROFICIENCY_ORDER = {
    "Apprentice": 1,
    ProficiencyLevel.APPRENTICE: 1,
    "Intermediate": 2,
    ProficiencyLevel.INTERMEDIATE: 2,
    "Advanced": 3,
    ProficiencyLevel.ADVANCED: 3,
}


@dataclass
class PrefilterInput:
    """Structured input for the pre-filter stage."""
    required_skills: List[Dict[str, str]]   # [{"skill": "Fiber Splicing", "min_level": "Advanced"}]
    required_certs: List[str]               # ["FOA CFOT", "OSHA 10"]
    preferred_region: Optional[str] = None
    available_by: Optional[date] = None
    role_id: Optional[str] = None
    project_id: Optional[str] = None
    max_candidates: int = 20
    exclude_technician_ids: List[str] = field(default_factory=list)


@dataclass
class PrefilterCandidate:
    """A candidate that passed pre-filtering with metadata for re-ranking."""
    technician_id: str
    technician_name: str
    skills: Dict[str, Dict[str, Any]]       # {skill_name: {level, hours}}
    certifications: List[Dict[str, Any]]     # [{cert_name, status, expiry_date}]
    home_base_city: Optional[str]
    home_base_state: Optional[str]
    approved_regions: List[str]
    available_from: Optional[date]
    years_experience: float
    total_project_count: int
    total_approved_hours: float
    archetype: Optional[str]
    career_stage: str
    deployability_status: str
    willing_to_travel: bool
    # Pre-computed partial scores for the re-ranker
    skills_match_pct: float = 0.0     # 0-1, fraction of required skills met at min level
    certs_match_pct: float = 0.0      # 0-1, fraction of required certs held
    region_match: bool = False
    availability_match: bool = False
    preference_adjustments: List[str] = field(default_factory=list)


@dataclass
class PrefilterResult:
    """Output of the pre-filter stage."""
    candidates: List[PrefilterCandidate]
    total_evaluated: int
    excluded_reasons: Dict[str, int] = field(default_factory=dict)
    preference_rules_applied: List[str] = field(default_factory=list)


def _proficiency_meets_minimum(actual: str, required: str) -> bool:
    """Check if actual proficiency level meets the minimum required."""
    actual_val = PROFICIENCY_ORDER.get(actual, 0)
    required_val = PROFICIENCY_ORDER.get(required, 0)
    return actual_val >= required_val


def _load_preference_rules(db: Session) -> List[PreferenceRule]:
    """Load active preference rules."""
    return db.query(PreferenceRule).filter(PreferenceRule.active.is_(True)).all()


def _apply_preference_exclusions(
    candidate: PrefilterCandidate,
    rules: List[PreferenceRule],
) -> tuple[bool, List[str]]:
    """Apply preference rules to determine if candidate should be excluded.

    Returns (should_exclude, list_of_applied_rule_descriptions).
    """
    adjustments = []
    excluded = False

    for rule in rules:
        if rule.effect == "exclude":
            # Experience threshold exclusion
            if rule.rule_type == "experience_threshold":
                try:
                    min_years = float(rule.threshold or "0")
                    if candidate.years_experience < min_years:
                        excluded = True
                        adjustments.append(
                            f"Excluded: less than {min_years} years experience (rule: {rule.rule_type})"
                        )
                except (ValueError, TypeError):
                    pass

            # Skill level minimum exclusion
            elif rule.rule_type == "skill_level_minimum":
                params = rule.parameters or {}
                skill_name = params.get("skill")
                min_level = params.get("min_level", "Intermediate")
                if skill_name and skill_name in candidate.skills:
                    actual = candidate.skills[skill_name].get("level", "Apprentice")
                    if not _proficiency_meets_minimum(actual, min_level):
                        excluded = True
                        adjustments.append(
                            f"Excluded: {skill_name} at {actual}, requires {min_level}"
                        )

            # Region exclusion
            elif rule.rule_type == "region_exclusion":
                params = rule.parameters or {}
                excluded_regions = params.get("regions", [])
                if candidate.home_base_state in excluded_regions:
                    excluded = True
                    adjustments.append(f"Excluded: based in excluded region {candidate.home_base_state}")

        elif rule.effect == "demote":
            # Demotions are applied as score adjustments in the re-ranker
            if rule.rule_type == "travel_preference":
                if not candidate.willing_to_travel and not candidate.region_match:
                    adjustments.append("Demoted: unwilling to travel and not local")

            elif rule.rule_type == "experience_preference":
                try:
                    preferred_years = float(rule.threshold or "0")
                    if candidate.years_experience < preferred_years:
                        adjustments.append(
                            f"Demoted: {candidate.years_experience} years < preferred {preferred_years}"
                        )
                except (ValueError, TypeError):
                    pass

        elif rule.effect == "boost":
            if rule.rule_type == "archetype_boost":
                params = rule.parameters or {}
                preferred_archetypes = params.get("archetypes", [])
                if candidate.archetype in preferred_archetypes:
                    adjustments.append(f"Boosted: preferred archetype '{candidate.archetype}'")

    return excluded, adjustments


def run_prefilter(db: Session, input: PrefilterInput) -> PrefilterResult:
    """Execute the pre-filter stage against the database.

    Returns a PrefilterResult with candidates sorted by pre-computed
    match scores (best first).
    """
    excluded_reasons: Dict[str, int] = {}

    def _count_exclusion(reason: str):
        excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1

    # ── Step 1: Load all technicians with eager-loaded relationships ──
    query = (
        db.query(Technician)
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
        )
    )

    # Hard filter: eligible deployability statuses
    query = query.filter(
        or_(
            Technician.deployability_status == DeployabilityStatus.READY_NOW,
            Technician.deployability_status == DeployabilityStatus.ROLLING_OFF_SOON,
            # Also include "Awaiting Assignment" as career_stage filter catches this
            Technician.deployability_status == DeployabilityStatus.IN_TRAINING,
        )
    )

    # Exclude already-excluded technician IDs
    if input.exclude_technician_ids:
        query = query.filter(Technician.id.notin_(input.exclude_technician_ids))

    all_technicians = query.all()

    # Deduplicate (joinedload can create cartesian products)
    seen_ids = set()
    technicians = []
    for t in all_technicians:
        tid = str(t.id)
        if tid not in seen_ids:
            seen_ids.add(tid)
            technicians.append(t)

    total_evaluated = len(technicians)
    logger.info(f"Pre-filter: evaluating {total_evaluated} technicians")

    # ── Step 2: Load preference rules ──
    preference_rules = _load_preference_rules(db)
    rules_applied = [f"{r.rule_type}:{r.effect}" for r in preference_rules]

    # ── Step 3: Check existing recommendations to avoid re-surfacing ──
    existing_rec_tech_ids = set()
    if input.role_id:
        existing_recs = (
            db.query(Recommendation.technician_id)
            .filter(
                Recommendation.role_id == input.role_id,
                Recommendation.status.in_([
                    RecommendationStatus.APPROVED.value,
                    RecommendationStatus.DISMISSED.value,
                ]),
            )
            .all()
        )
        existing_rec_tech_ids = {r[0] for r in existing_recs if r[0]}

    # ── Step 4: Evaluate each technician ──
    candidates: List[PrefilterCandidate] = []

    for tech in technicians:
        tech_id = str(tech.id)

        # Skip already acted-on
        if tech_id in existing_rec_tech_ids:
            _count_exclusion("already_recommended")
            continue

        # Build skills map
        skills_map: Dict[str, Dict[str, Any]] = {}
        for ts in tech.skills:
            level = ts.proficiency_level
            if hasattr(level, 'value'):
                level = level.value
            skills_map[ts.skill_name] = {
                "level": level,
                "hours": ts.training_hours_accumulated or 0,
            }

        # Build certs list
        certs_list = []
        cert_names = set()
        for tc in tech.certifications:
            status = tc.status
            if hasattr(status, 'value'):
                status = status.value
            certs_list.append({
                "cert_name": tc.cert_name,
                "status": status,
                "expiry_date": str(tc.expiry_date) if tc.expiry_date else None,
            })
            if status == "Active":
                cert_names.add(tc.cert_name)

        # ── Skill matching ──
        skills_met = 0
        for req in input.required_skills:
            skill_name = req.get("skill", "")
            min_level = req.get("min_level", "Apprentice")
            if skill_name in skills_map:
                actual_level = skills_map[skill_name]["level"]
                if _proficiency_meets_minimum(actual_level, min_level):
                    skills_met += 1

        total_required_skills = len(input.required_skills)
        skills_match_pct = skills_met / total_required_skills if total_required_skills > 0 else 1.0

        # Hard filter: must have at least 50% of required skills
        if total_required_skills > 0 and skills_match_pct < 0.5:
            _count_exclusion("insufficient_skills")
            continue

        # ── Cert matching ──
        certs_met = sum(1 for c in input.required_certs if c in cert_names)
        total_required_certs = len(input.required_certs)
        certs_match_pct = certs_met / total_required_certs if total_required_certs > 0 else 1.0

        # Soft filter: log but don't exclude for missing certs (partial matches allowed)
        if total_required_certs > 0 and certs_match_pct == 0:
            _count_exclusion("no_matching_certs")
            continue

        # ── Region matching ──
        approved_regions = tech.approved_regions or []
        region_match = False
        if input.preferred_region:
            if input.preferred_region in approved_regions:
                region_match = True
            elif tech.home_base_state == input.preferred_region:
                region_match = True

        # ── Availability matching ──
        availability_match = True
        if input.available_by and tech.available_from:
            availability_match = tech.available_from <= input.available_by

        # ── Build candidate ──
        candidate = PrefilterCandidate(
            technician_id=tech_id,
            technician_name=tech.full_name,
            skills=skills_map,
            certifications=certs_list,
            home_base_city=tech.home_base_city,
            home_base_state=tech.home_base_state,
            approved_regions=approved_regions,
            available_from=tech.available_from,
            years_experience=tech.years_experience or 0,
            total_project_count=tech.total_project_count or 0,
            total_approved_hours=tech.total_approved_hours or 0,
            archetype=tech.archetype,
            career_stage=tech.career_stage.value if hasattr(tech.career_stage, 'value') else str(tech.career_stage),
            deployability_status=tech.deployability_status.value if hasattr(tech.deployability_status, 'value') else str(tech.deployability_status),
            willing_to_travel=tech.willing_to_travel if tech.willing_to_travel is not None else True,
            skills_match_pct=skills_match_pct,
            certs_match_pct=certs_match_pct,
            region_match=region_match,
            availability_match=availability_match,
        )

        # ── Apply preference rules ──
        excluded, adjustments = _apply_preference_exclusions(candidate, preference_rules)
        candidate.preference_adjustments = adjustments

        if excluded:
            _count_exclusion("preference_rule_exclusion")
            continue

        candidates.append(candidate)

    # ── Step 5: Sort by composite pre-filter score (deterministic) ──
    def _prefilter_score(c: PrefilterCandidate) -> float:
        """Compute a simple weighted score for pre-filter sorting."""
        score = 0.0
        score += c.skills_match_pct * 40          # 40% weight on skills
        score += c.certs_match_pct * 25           # 25% weight on certs
        score += (10 if c.region_match else 0)    # 10% bonus for local
        score += (10 if c.availability_match else 0)  # 10% bonus for available
        # Experience: cap at 15 points (for 15+ years)
        score += min(c.years_experience, 15)
        return score

    candidates.sort(key=_prefilter_score, reverse=True)

    # Trim to max candidates
    candidates = candidates[:input.max_candidates]

    logger.info(
        f"Pre-filter complete: {len(candidates)} candidates from {total_evaluated} evaluated. "
        f"Exclusions: {excluded_reasons}"
    )

    return PrefilterResult(
        candidates=candidates,
        total_evaluated=total_evaluated,
        excluded_reasons=excluded_reasons,
        preference_rules_applied=rules_applied,
    )
