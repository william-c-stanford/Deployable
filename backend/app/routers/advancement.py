"""Advancement endpoints — query status, configure cert gates, trigger re-evaluation.

Provides:
  GET  /api/advancement/{tech_id}/status  — query advancement status for a technician
  GET  /api/advancement/status             — query advancement status for all (or filtered) technicians
  GET  /api/advancement/cert-gates         — list cert gate configs for all skills
  GET  /api/advancement/cert-gates/{skill_id} — get cert gate config for a specific skill
  PUT  /api/advancement/cert-gates/{skill_id} — update cert gate config for a skill
  POST /api/advancement/re-evaluate        — manually trigger advancement re-evaluation
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    CareerStage,
    ProficiencyLevel,
    CertStatus,
)
from app.models.skill import Skill
from app.schemas.advancement import (
    SkillAdvancementStatus,
    TechnicianAdvancementStatus,
    CertGateConfig,
    CertGateUpdate,
    CertGateListResponse,
    ReEvaluationRequest,
    SkillAdvancementResult,
    TechnicianReEvaluationResult,
    ReEvaluationResponse,
)
from app.services.advancement import (
    evaluate_technician_advancement,
    evaluate_skill_advancement,
    _enum_val,
    _check_cert_gate,
)

logger = logging.getLogger("deployable.routers.advancement")

router = APIRouter(prefix="/api/advancement", tags=["advancement"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_technician(db: Session, tech_id: uuid.UUID) -> Technician:
    """Load a technician with skills and certifications, or raise 404."""
    tech = (
        db.query(Technician)
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
        )
        .filter(Technician.id == tech_id)
        .first()
    )
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    return tech


def _build_advancement_status(
    db: Session, technician: Technician
) -> TechnicianAdvancementStatus:
    """Build the full advancement status response for a technician."""
    evaluation = evaluate_technician_advancement(db, technician)

    # Map evaluation results to schema
    skill_statuses: List[SkillAdvancementStatus] = []
    counts = {"apprentice": 0, "intermediate": 0, "advanced": 0}

    for r in evaluation.skill_results:
        current = r.current_level
        if current == ProficiencyLevel.APPRENTICE.value:
            counts["apprentice"] += 1
        elif current == ProficiencyLevel.INTERMEDIATE.value:
            counts["intermediate"] += 1
        elif current == ProficiencyLevel.ADVANCED.value:
            counts["advanced"] += 1

        # Determine next level
        next_level: Optional[ProficiencyLevel] = None
        hours_to_next: Optional[float] = None
        if r.target_level:
            next_level = ProficiencyLevel(r.target_level)
            hours_to_next = max(0, r.hours_threshold - r.hours_accumulated)

        cert_gate_required = r.cert_gate.required_cert if r.cert_gate else None
        cert_gate_met = r.cert_gate.is_satisfied if r.cert_gate else True

        skill_statuses.append(
            SkillAdvancementStatus(
                skill_name=r.skill_name,
                current_level=ProficiencyLevel(r.current_level),
                training_hours_accumulated=r.hours_accumulated,
                hours_to_next_level=hours_to_next,
                next_level=next_level,
                cert_gate_required=cert_gate_required,
                cert_gate_met=cert_gate_met,
                eligible_for_advancement=r.should_advance,
                blocked_reason=r.blocked_reason,
            )
        )

    # Check overall training completeness
    all_trained = (
        all(
            _enum_val(ts.proficiency_level)
            in (ProficiencyLevel.INTERMEDIATE.value, ProficiencyLevel.ADVANCED.value)
            for ts in technician.skills
        )
        if technician.skills
        else False
    )

    return TechnicianAdvancementStatus(
        technician_id=technician.id,
        technician_name=technician.full_name,
        career_stage=_enum_val(technician.career_stage),
        deployability_status=_enum_val(technician.deployability_status),
        total_skills=len(technician.skills),
        skills_at_apprentice=counts["apprentice"],
        skills_at_intermediate=counts["intermediate"],
        skills_at_advanced=counts["advanced"],
        skills=skill_statuses,
        overall_training_complete=all_trained,
        last_evaluated=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Advancement Status Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/{tech_id}/status",
    response_model=TechnicianAdvancementStatus,
    summary="Get advancement status for a technician",
)
def get_technician_advancement_status(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Query the full advancement status for a technician.

    Returns per-skill breakdown showing current level, hours accumulated,
    hours to next level, cert gate requirements, and eligibility for advancement.
    """
    technician = _load_technician(db, tech_id)
    return _build_advancement_status(db, technician)


@router.get(
    "/status",
    response_model=List[TechnicianAdvancementStatus],
    summary="List advancement status for multiple technicians",
)
def list_advancement_status(
    career_stage: Optional[CareerStage] = Query(
        None, description="Filter by career stage"
    ),
    eligible_only: bool = Query(
        False, description="Only return technicians with at least one eligible advancement"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """List advancement status for technicians, with optional filters.

    Ops-only endpoint for reviewing the training pipeline.
    """
    query = db.query(Technician).options(
        joinedload(Technician.skills),
        joinedload(Technician.certifications),
    )

    if career_stage:
        query = query.filter(Technician.career_stage == career_stage)

    # Deduplicate from joinedload
    technicians = query.order_by(Technician.created_at.desc()).all()
    seen = set()
    unique_techs = []
    for t in technicians:
        if t.id not in seen:
            seen.add(t.id)
            unique_techs.append(t)

    results = []
    for tech in unique_techs:
        adv_status = _build_advancement_status(db, tech)
        if eligible_only and not any(s.eligible_for_advancement for s in adv_status.skills):
            continue
        results.append(adv_status)

    # Apply pagination after filtering
    total = len(results)
    return results[skip : skip + limit]


# ---------------------------------------------------------------------------
# Cert Gate Configuration Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/cert-gates",
    response_model=CertGateListResponse,
    summary="List cert gate configurations for all skills",
)
def list_cert_gates(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """List the certification gate configuration for all active skills.

    Shows hours thresholds and optional cert gates per skill level.
    """
    skills = (
        db.query(Skill)
        .filter(Skill.is_active == True)  # noqa: E712
        .order_by(Skill.display_order, Skill.name)
        .all()
    )

    items = [
        CertGateConfig(
            skill_id=s.id,
            skill_name=s.name,
            intermediate_hours_threshold=s.intermediate_hours_threshold or 100,
            advanced_hours_threshold=s.advanced_hours_threshold or 300,
            cert_gate_intermediate=s.cert_gate_intermediate,
            cert_gate_advanced=s.cert_gate_advanced,
        )
        for s in skills
    ]

    return CertGateListResponse(items=items, total=len(items))


@router.get(
    "/cert-gates/{skill_id}",
    response_model=CertGateConfig,
    summary="Get cert gate configuration for a specific skill",
)
def get_cert_gate(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get the certification gate configuration for a specific skill."""
    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    return CertGateConfig(
        skill_id=skill.id,
        skill_name=skill.name,
        intermediate_hours_threshold=skill.intermediate_hours_threshold or 100,
        advanced_hours_threshold=skill.advanced_hours_threshold or 300,
        cert_gate_intermediate=skill.cert_gate_intermediate,
        cert_gate_advanced=skill.cert_gate_advanced,
    )


@router.put(
    "/cert-gates/{skill_id}",
    response_model=CertGateConfig,
    summary="Update cert gate configuration for a skill",
)
def update_cert_gate(
    skill_id: uuid.UUID,
    data: CertGateUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Update the hours thresholds and/or certification gates for a skill.

    Only ops users can configure advancement requirements.
    """
    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    if data.intermediate_hours_threshold is not None:
        skill.intermediate_hours_threshold = data.intermediate_hours_threshold
    if data.advanced_hours_threshold is not None:
        skill.advanced_hours_threshold = data.advanced_hours_threshold
    if data.cert_gate_intermediate is not None:
        # Allow empty string to clear the gate
        skill.cert_gate_intermediate = data.cert_gate_intermediate or None
    if data.cert_gate_advanced is not None:
        skill.cert_gate_advanced = data.cert_gate_advanced or None

    db.commit()
    db.refresh(skill)

    logger.info(
        "Cert gate updated for skill '%s' by %s: intermediate=%d/%s, advanced=%d/%s",
        skill.name,
        current_user.user_id,
        skill.intermediate_hours_threshold,
        skill.cert_gate_intermediate,
        skill.advanced_hours_threshold,
        skill.cert_gate_advanced,
    )

    return CertGateConfig(
        skill_id=skill.id,
        skill_name=skill.name,
        intermediate_hours_threshold=skill.intermediate_hours_threshold or 100,
        advanced_hours_threshold=skill.advanced_hours_threshold or 300,
        cert_gate_intermediate=skill.cert_gate_intermediate,
        cert_gate_advanced=skill.cert_gate_advanced,
    )


# ---------------------------------------------------------------------------
# Manual Re-Evaluation Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/re-evaluate",
    response_model=ReEvaluationResponse,
    summary="Manually trigger advancement re-evaluation",
)
def trigger_re_evaluation(
    request: ReEvaluationRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Manually trigger advancement re-evaluation for technicians.

    Evaluates all technician skills against current thresholds and cert gates.
    If dry_run=false, applies deterministic proficiency advancements and
    career stage transitions.

    Only ops users can trigger re-evaluation.
    """
    # Build query for technicians to evaluate
    query = db.query(Technician).options(
        joinedload(Technician.skills),
        joinedload(Technician.certifications),
    )

    if request.technician_ids:
        query = query.filter(Technician.id.in_(request.technician_ids))

    technicians_raw = query.all()
    # Deduplicate from joinedload
    seen = set()
    technicians = []
    for t in technicians_raw:
        if t.id not in seen:
            seen.add(t.id)
            technicians.append(t)

    results: List[TechnicianReEvaluationResult] = []
    total_advancements = 0
    technicians_with_changes = 0

    level_map = {v.value: v for v in ProficiencyLevel}

    for tech in technicians:
        evaluation = evaluate_technician_advancement(db, tech)
        advancements: List[SkillAdvancementResult] = []

        for r in evaluation.advancements_ready:
            cert_gate = r.cert_gate
            advancements.append(
                SkillAdvancementResult(
                    skill_name=r.skill_name,
                    old_level=r.current_level,
                    new_level=r.target_level,
                    hours=r.hours_accumulated,
                    cert_gate_met=cert_gate.is_satisfied if cert_gate else True,
                )
            )

            if not request.dry_run:
                # Apply the advancement
                skill_pk = uuid.UUID(r.technician_skill_id) if isinstance(r.technician_skill_id, str) else r.technician_skill_id
                tech_skill = db.get(TechnicianSkill, skill_pk)
                if tech_skill and r.target_level:
                    tech_skill.proficiency_level = level_map.get(
                        r.target_level, ProficiencyLevel.INTERMEDIATE
                    )

        # Check career stage transition
        career_stage_changed = False
        old_career_stage = _enum_val(tech.career_stage)
        new_career_stage = old_career_stage

        if advancements and not request.dry_run:
            # Re-check after applying advancements
            all_trained = all(
                _enum_val(ts.proficiency_level)
                in (ProficiencyLevel.INTERMEDIATE.value, ProficiencyLevel.ADVANCED.value)
                for ts in tech.skills
            ) if tech.skills else False

            if (
                all_trained
                and tech.skills
                and old_career_stage == CareerStage.IN_TRAINING.value
            ):
                tech.career_stage = CareerStage.TRAINING_COMPLETED
                career_stage_changed = True
                new_career_stage = CareerStage.TRAINING_COMPLETED.value

        if advancements:
            technicians_with_changes += 1
            total_advancements += len(advancements)

        results.append(
            TechnicianReEvaluationResult(
                technician_id=tech.id,
                technician_name=tech.full_name,
                advancements=advancements,
                career_stage_changed=career_stage_changed,
                old_career_stage=old_career_stage if career_stage_changed else None,
                new_career_stage=new_career_stage if career_stage_changed else None,
            )
        )

    if not request.dry_run:
        db.commit()

    logger.info(
        "Re-evaluation %s: %d technicians evaluated, %d with changes, %d advancements (by %s)",
        "dry-run" if request.dry_run else "applied",
        len(technicians),
        technicians_with_changes,
        total_advancements,
        current_user.user_id,
    )

    return ReEvaluationResponse(
        dry_run=request.dry_run,
        technicians_evaluated=len(technicians),
        technicians_with_changes=technicians_with_changes,
        total_advancements=total_advancements,
        results=results,
    )
