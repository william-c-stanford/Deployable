"""Full CRUD + search/filter endpoints for technicians and nested resources.

Each mutating endpoint dispatches domain events via the Celery-based
reactive agent system. Events are dispatched AFTER the DB commit succeeds,
using dispatch_event_safe to ensure event failures never break API responses.
"""

import uuid
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_current_user, CurrentUser
from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    TechnicianDocument,
    TechnicianBadge,
    CareerStage,
    DeployabilityStatus,
    VerificationStatus,
)
from app.schemas.technician import (
    TechnicianCreate,
    TechnicianUpdate,
    TechnicianResponse,
    TechnicianListResponse,
    SkillCreate,
    SkillUpdate,
    SkillResponse,
    CertCreate,
    CertUpdate,
    CertResponse,
    DocumentCreate,
    DocumentUpdate,
    DocumentResponse,
    BadgeCreate,
    BadgeUpdate,
    BadgeResponse,
    InactiveOverrideRequest,
    InactiveOverrideResponse,
    ReactivateRequest,
)
from app.models.audit import AuditLog
from app.auth import require_role
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

router = APIRouter(prefix="/api/technicians", tags=["technicians"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_technician_or_404(db: Session, tech_id: uuid.UUID) -> Technician:
    tech = (
        db.query(Technician)
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
            joinedload(Technician.documents),
            joinedload(Technician.badges),
        )
        .filter(Technician.id == tech_id)
        .first()
    )
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    return tech


def _apply_nested_creates(db: Session, technician: Technician, data: TechnicianCreate):
    for s in data.skills:
        db.add(TechnicianSkill(technician_id=technician.id, **s.model_dump()))
    for c in data.certifications:
        db.add(TechnicianCertification(technician_id=technician.id, **c.model_dump()))
    for d in data.documents:
        db.add(TechnicianDocument(technician_id=technician.id, **d.model_dump()))
    for b in data.badges:
        db.add(TechnicianBadge(technician_id=technician.id, **b.model_dump()))


def _replace_nested(db: Session, technician: Technician, data: TechnicianUpdate):
    """Replace nested collections when provided (full replacement semantics)."""
    if data.skills is not None:
        db.query(TechnicianSkill).filter(TechnicianSkill.technician_id == technician.id).delete()
        for s in data.skills:
            db.add(TechnicianSkill(technician_id=technician.id, **s.model_dump()))
    if data.certifications is not None:
        db.query(TechnicianCertification).filter(TechnicianCertification.technician_id == technician.id).delete()
        for c in data.certifications:
            db.add(TechnicianCertification(technician_id=technician.id, **c.model_dump()))
    if data.documents is not None:
        db.query(TechnicianDocument).filter(TechnicianDocument.technician_id == technician.id).delete()
        for d in data.documents:
            db.add(TechnicianDocument(technician_id=technician.id, **d.model_dump()))
    if data.badges is not None:
        db.query(TechnicianBadge).filter(TechnicianBadge.technician_id == technician.id).delete()
        for b in data.badges:
            db.add(TechnicianBadge(technician_id=technician.id, **b.model_dump()))


def _dispatch_technician_change_events(
    technician: Technician,
    old_career_stage,
    old_deployability,
    actor_id: str,
) -> None:
    """Detect and dispatch events for technician field changes."""
    # Career stage change
    if technician.career_stage != old_career_stage:
        old_val = old_career_stage.value if hasattr(old_career_stage, 'value') else str(old_career_stage)
        new_val = technician.career_stage.value if hasattr(technician.career_stage, 'value') else str(technician.career_stage)

        # Training completion is a special event
        if new_val == CareerStage.TRAINING_COMPLETED.value:
            dispatch_event_safe(EventPayload(
                event_type=EventType.TRAINING_COMPLETED,
                entity_type="technician",
                entity_id=str(technician.id),
                actor_id=str(actor_id),
                data={
                    "technician_id": str(technician.id),
                    "old_stage": old_val,
                    "new_stage": new_val,
                },
            ))
        else:
            dispatch_event_safe(EventPayload(
                event_type=EventType.TECHNICIAN_STATUS_CHANGED,
                entity_type="technician",
                entity_id=str(technician.id),
                actor_id=str(actor_id),
                data={
                    "field": "career_stage",
                    "old_value": old_val,
                    "new_value": new_val,
                },
            ))

    # Deployability status change
    if technician.deployability_status != old_deployability:
        old_val = old_deployability.value if hasattr(old_deployability, 'value') else str(old_deployability)
        new_val = technician.deployability_status.value if hasattr(technician.deployability_status, 'value') else str(technician.deployability_status)

        dispatch_event_safe(EventPayload(
            event_type=EventType.TECHNICIAN_STATUS_CHANGED,
            entity_type="technician",
            entity_id=str(technician.id),
            actor_id=str(actor_id),
            data={
                "field": "deployability_status",
                "old_value": old_val,
                "new_value": new_val,
            },
        ))


def _cert_status_to_event_type(new_status: str) -> EventType | None:
    """Map certification status string to the appropriate event type."""
    status_map = {
        "Expired": EventType.CERT_EXPIRED,
        "Revoked": EventType.CERT_REVOKED,
        "Active": EventType.CERT_RENEWED,  # Re-activation = renewal
    }
    return status_map.get(new_status)


def _doc_status_to_event_type(new_status: str) -> EventType | None:
    """Map document verification status string to the appropriate event type."""
    status_map = {
        "Verified": EventType.DOC_VERIFIED,
        "Expired": EventType.DOC_EXPIRED,
        "Pending Review": EventType.DOC_UPLOADED,
    }
    return status_map.get(new_status)


# ---------------------------------------------------------------------------
# Technician CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=TechnicianListResponse)
def list_technicians(
    search: Optional[str] = Query(None, description="Search by name or email"),
    career_stage: Optional[CareerStage] = Query(None),
    deployability_status: Optional[DeployabilityStatus] = Query(None),
    skills: Optional[List[str]] = Query(None, description="Filter by skill names"),
    available_from: Optional[date] = Query(None, description="Available on or before this date"),
    region: Optional[str] = Query(None, description="Filter by approved region"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    query = db.query(Technician)

    # Full-text-ish search across name and email
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                Technician.first_name.ilike(pattern),
                Technician.last_name.ilike(pattern),
                Technician.email.ilike(pattern),
            )
        )

    if career_stage:
        query = query.filter(Technician.career_stage == career_stage)

    if deployability_status:
        query = query.filter(Technician.deployability_status == deployability_status)

    if available_from:
        query = query.filter(Technician.available_from <= available_from)

    if region:
        # JSON array contains check (PostgreSQL)
        query = query.filter(Technician.approved_regions.op("@>")(f'["{region}"]'))

    if skills:
        # Technician must have ALL listed skills
        for skill_name in skills:
            query = query.filter(
                Technician.skills.any(TechnicianSkill.skill_name.ilike(skill_name))
            )

    total = query.count()

    technicians = (
        query
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
            joinedload(Technician.documents),
            joinedload(Technician.badges),
        )
        .order_by(Technician.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    # Deduplicate rows produced by joinedload cartesian product
    seen = set()
    unique = []
    for t in technicians:
        if t.id not in seen:
            seen.add(t.id)
            unique.append(t)

    return TechnicianListResponse(items=unique, total=total, skip=skip, limit=limit)


@router.get("/{tech_id}", response_model=TechnicianResponse)
def get_technician(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return _get_technician_or_404(db, tech_id)


@router.post("", response_model=TechnicianResponse, status_code=status.HTTP_201_CREATED)
def create_technician(
    data: TechnicianCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    tech_data = data.model_dump(exclude={"skills", "certifications", "documents", "badges"})
    technician = Technician(**tech_data)
    db.add(technician)
    db.flush()
    _apply_nested_creates(db, technician, data)
    db.commit()
    db.refresh(technician)

    # Dispatch: technician created
    dispatch_event_safe(EventPayload(
        event_type=EventType.TECHNICIAN_CREATED,
        entity_type="technician",
        entity_id=str(technician.id),
        actor_id=str(current_user.id),
        data={
            "name": technician.full_name,
            "career_stage": str(technician.career_stage.value) if technician.career_stage else None,
        },
    ))

    return _get_technician_or_404(db, technician.id)


@router.put("/{tech_id}", response_model=TechnicianResponse)
def update_technician(
    tech_id: uuid.UUID,
    data: TechnicianUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    technician = _get_technician_or_404(db, tech_id)

    # Snapshot pre-update values for change detection
    old_career_stage = technician.career_stage
    old_deployability = technician.deployability_status

    update_data = data.model_dump(
        exclude={"skills", "certifications", "documents", "badges"},
        exclude_unset=True,
    )
    for field, value in update_data.items():
        setattr(technician, field, value)
    _replace_nested(db, technician, data)
    db.commit()

    # Dispatch events for detected changes
    _dispatch_technician_change_events(
        technician, old_career_stage, old_deployability, current_user.id,
    )

    return _get_technician_or_404(db, tech_id)


@router.patch("/{tech_id}", response_model=TechnicianResponse)
def partial_update_technician(
    tech_id: uuid.UUID,
    data: TechnicianUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    technician = _get_technician_or_404(db, tech_id)

    # Snapshot pre-update values for change detection
    old_career_stage = technician.career_stage
    old_deployability = technician.deployability_status

    update_data = data.model_dump(
        exclude={"skills", "certifications", "documents", "badges"},
        exclude_unset=True,
    )
    for field, value in update_data.items():
        setattr(technician, field, value)
    _replace_nested(db, technician, data)
    db.commit()

    # Dispatch events for detected changes
    _dispatch_technician_change_events(
        technician, old_career_stage, old_deployability, current_user.id,
    )

    return _get_technician_or_404(db, tech_id)


@router.delete("/{tech_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_technician(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    technician = _get_technician_or_404(db, tech_id)
    db.delete(technician)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Skills sub-resource
# ---------------------------------------------------------------------------

@router.get("/{tech_id}/skills", response_model=List[SkillResponse])
def list_skills(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    return db.query(TechnicianSkill).filter(TechnicianSkill.technician_id == tech_id).all()


@router.post("/{tech_id}/skills", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
def add_skill(
    tech_id: uuid.UUID,
    data: SkillCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    skill = TechnicianSkill(technician_id=tech_id, **data.model_dump())
    db.add(skill)
    db.commit()
    db.refresh(skill)

    # Dispatch: training hours logged (for threshold checking)
    if skill.training_hours_accumulated and skill.training_hours_accumulated > 0:
        dispatch_event_safe(EventPayload(
            event_type=EventType.TRAINING_HOURS_LOGGED,
            entity_type="technician_skill",
            entity_id=str(skill.id),
            actor_id=str(current_user.id),
            data={
                "technician_id": str(tech_id),
                "skill_name": skill.skill_name,
                "new_hours_total": skill.training_hours_accumulated,
            },
        ))

    return skill


@router.put("/{tech_id}/skills/{skill_id}", response_model=SkillResponse)
def update_skill(
    tech_id: uuid.UUID,
    skill_id: uuid.UUID,
    data: SkillUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    skill = db.query(TechnicianSkill).filter(
        TechnicianSkill.id == skill_id,
        TechnicianSkill.technician_id == tech_id,
    ).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    old_hours = skill.training_hours_accumulated or 0
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(skill, field, value)
    db.commit()
    db.refresh(skill)

    # Dispatch: training hours logged if hours changed
    new_hours = skill.training_hours_accumulated or 0
    if new_hours != old_hours:
        dispatch_event_safe(EventPayload(
            event_type=EventType.TRAINING_HOURS_LOGGED,
            entity_type="technician_skill",
            entity_id=str(skill.id),
            actor_id=str(current_user.id),
            data={
                "technician_id": str(tech_id),
                "skill_name": skill.skill_name,
                "old_hours": old_hours,
                "new_hours_total": new_hours,
            },
        ))

    return skill


@router.delete("/{tech_id}/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    tech_id: uuid.UUID,
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    skill = db.query(TechnicianSkill).filter(
        TechnicianSkill.id == skill_id,
        TechnicianSkill.technician_id == tech_id,
    ).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    db.delete(skill)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Certifications sub-resource
# ---------------------------------------------------------------------------

@router.get("/{tech_id}/certifications", response_model=List[CertResponse])
def list_certifications(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    return db.query(TechnicianCertification).filter(TechnicianCertification.technician_id == tech_id).all()


@router.post("/{tech_id}/certifications", response_model=CertResponse, status_code=status.HTTP_201_CREATED)
def add_certification(
    tech_id: uuid.UUID,
    data: CertCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    cert = TechnicianCertification(technician_id=tech_id, **data.model_dump())
    db.add(cert)
    db.commit()
    db.refresh(cert)

    # Dispatch: cert added
    dispatch_event_safe(EventPayload(
        event_type=EventType.CERT_ADDED,
        entity_type="technician_certification",
        entity_id=str(cert.id),
        actor_id=str(current_user.id),
        data={
            "technician_id": str(tech_id),
            "cert_name": cert.cert_name,
            "status": str(cert.status.value) if hasattr(cert.status, 'value') else str(cert.status),
        },
    ))

    return cert


@router.put("/{tech_id}/certifications/{cert_id}", response_model=CertResponse)
def update_certification(
    tech_id: uuid.UUID,
    cert_id: uuid.UUID,
    data: CertUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    cert = db.query(TechnicianCertification).filter(
        TechnicianCertification.id == cert_id,
        TechnicianCertification.technician_id == tech_id,
    ).first()
    if not cert:
        raise HTTPException(status_code=404, detail="Certification not found")

    old_status = str(cert.status.value) if hasattr(cert.status, 'value') else str(cert.status)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(cert, field, value)
    db.commit()
    db.refresh(cert)

    # Dispatch: cert status change event based on new status
    new_status = str(cert.status.value) if hasattr(cert.status, 'value') else str(cert.status)
    if old_status != new_status:
        event_type = _cert_status_to_event_type(new_status)
        if event_type:
            dispatch_event_safe(EventPayload(
                event_type=event_type,
                entity_type="technician_certification",
                entity_id=str(cert.id),
                actor_id=str(current_user.id),
                data={
                    "technician_id": str(tech_id),
                    "cert_name": cert.cert_name,
                    "old_status": old_status,
                    "new_status": new_status,
                },
            ))

    return cert


@router.delete("/{tech_id}/certifications/{cert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_certification(
    tech_id: uuid.UUID,
    cert_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    cert = db.query(TechnicianCertification).filter(
        TechnicianCertification.id == cert_id,
        TechnicianCertification.technician_id == tech_id,
    ).first()
    if not cert:
        raise HTTPException(status_code=404, detail="Certification not found")
    db.delete(cert)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Documents sub-resource
# ---------------------------------------------------------------------------

@router.get("/{tech_id}/documents", response_model=List[DocumentResponse])
def list_documents(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    return db.query(TechnicianDocument).filter(TechnicianDocument.technician_id == tech_id).all()


@router.post("/{tech_id}/documents", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def add_document(
    tech_id: uuid.UUID,
    data: DocumentCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    doc = TechnicianDocument(technician_id=tech_id, **data.model_dump())
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Dispatch: doc uploaded
    dispatch_event_safe(EventPayload(
        event_type=EventType.DOC_UPLOADED,
        entity_type="technician_document",
        entity_id=str(doc.id),
        actor_id=str(current_user.id),
        data={
            "technician_id": str(tech_id),
            "doc_type": doc.doc_type,
            "verification_status": str(doc.verification_status.value) if hasattr(doc.verification_status, 'value') else str(doc.verification_status),
        },
    ))

    return doc


@router.put("/{tech_id}/documents/{doc_id}", response_model=DocumentResponse)
def update_document(
    tech_id: uuid.UUID,
    doc_id: uuid.UUID,
    data: DocumentUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    doc = db.query(TechnicianDocument).filter(
        TechnicianDocument.id == doc_id,
        TechnicianDocument.technician_id == tech_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    old_status = str(doc.verification_status.value) if hasattr(doc.verification_status, 'value') else str(doc.verification_status)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(doc, field, value)
    db.commit()
    db.refresh(doc)

    # Dispatch: doc verification status change
    new_status = str(doc.verification_status.value) if hasattr(doc.verification_status, 'value') else str(doc.verification_status)
    if old_status != new_status:
        event_type = _doc_status_to_event_type(new_status)
        if event_type:
            dispatch_event_safe(EventPayload(
                event_type=event_type,
                entity_type="technician_document",
                entity_id=str(doc.id),
                actor_id=str(current_user.id),
                data={
                    "technician_id": str(tech_id),
                    "doc_type": doc.doc_type,
                    "old_status": old_status,
                    "new_status": new_status,
                },
            ))

    return doc


@router.delete("/{tech_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    tech_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    doc = db.query(TechnicianDocument).filter(
        TechnicianDocument.id == doc_id,
        TechnicianDocument.technician_id == tech_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Badges sub-resource
# ---------------------------------------------------------------------------

@router.get("/{tech_id}/badges", response_model=List[BadgeResponse])
def list_badges(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    return db.query(TechnicianBadge).filter(TechnicianBadge.technician_id == tech_id).all()


@router.post("/{tech_id}/badges", response_model=BadgeResponse, status_code=status.HTTP_201_CREATED)
def add_badge(
    tech_id: uuid.UUID,
    data: BadgeCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    badge = TechnicianBadge(technician_id=tech_id, **data.model_dump())
    db.add(badge)
    db.commit()
    db.refresh(badge)
    return badge


@router.put("/{tech_id}/badges/{badge_id}", response_model=BadgeResponse)
def update_badge(
    tech_id: uuid.UUID,
    badge_id: uuid.UUID,
    data: BadgeUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    badge = db.query(TechnicianBadge).filter(
        TechnicianBadge.id == badge_id,
        TechnicianBadge.technician_id == tech_id,
    ).first()
    if not badge:
        raise HTTPException(status_code=404, detail="Badge not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(badge, field, value)
    db.commit()
    db.refresh(badge)
    return badge


@router.delete("/{tech_id}/badges/{badge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_badge(
    tech_id: uuid.UUID,
    badge_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    badge = db.query(TechnicianBadge).filter(
        TechnicianBadge.id == badge_id,
        TechnicianBadge.technician_id == tech_id,
    ).first()
    if not badge:
        raise HTTPException(status_code=404, detail="Badge not found")
    db.delete(badge)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Manual Inactive Override (lock / unlock)
# ---------------------------------------------------------------------------

@router.post("/{tech_id}/override/inactive", response_model=InactiveOverrideResponse)
def set_inactive_override(
    tech_id: uuid.UUID,
    data: InactiveOverrideRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Manually set a technician to Inactive with a locked override.

    This locks the deployability status so that auto-computation (readiness
    evaluation, nightly batch jobs) cannot change it. Only an explicit
    unlock/reactivate call can release the lock.

    Requires ops role.
    """
    technician = _get_technician_or_404(db, tech_id)

    # Already locked inactive — idempotent but inform the caller
    if (
        technician.deployability_locked
        and technician.deployability_status == DeployabilityStatus.INACTIVE
    ):
        return InactiveOverrideResponse(
            technician_id=str(technician.id),
            technician_name=technician.full_name,
            previous_status=DeployabilityStatus.INACTIVE.value,
            new_status=DeployabilityStatus.INACTIVE.value,
            deployability_locked=True,
            inactive_locked_at=technician.inactive_locked_at,
            inactive_locked_by=technician.inactive_locked_by,
            inactive_lock_reason=technician.inactive_lock_reason,
            action="already_locked_inactive",
        )

    old_status = (
        technician.deployability_status.value
        if hasattr(technician.deployability_status, "value")
        else str(technician.deployability_status)
    )

    # Apply the locked inactive override
    technician.deployability_status = DeployabilityStatus.INACTIVE
    technician.deployability_locked = True
    technician.inactive_locked_at = datetime.utcnow()
    technician.inactive_locked_by = current_user.user_id
    technician.inactive_lock_reason = data.reason

    # Audit log
    audit = AuditLog(
        user_id=current_user.user_id,
        action="manual_inactive_override",
        entity_type="technician",
        entity_id=str(technician.id),
        details={
            "previous_status": old_status,
            "new_status": DeployabilityStatus.INACTIVE.value,
            "reason": data.reason,
            "locked": True,
        },
    )
    db.add(audit)
    db.commit()

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.TECHNICIAN_STATUS_CHANGED,
        entity_type="technician",
        entity_id=str(technician.id),
        actor_id=current_user.user_id,
        data={
            "field": "deployability_status",
            "old_value": old_status,
            "new_value": DeployabilityStatus.INACTIVE.value,
            "locked": True,
            "reason": data.reason,
            "source": "manual_inactive_override",
        },
    ))

    return InactiveOverrideResponse(
        technician_id=str(technician.id),
        technician_name=technician.full_name,
        previous_status=old_status,
        new_status=DeployabilityStatus.INACTIVE.value,
        deployability_locked=True,
        inactive_locked_at=technician.inactive_locked_at,
        inactive_locked_by=technician.inactive_locked_by,
        inactive_lock_reason=technician.inactive_lock_reason,
        action="locked_inactive",
    )


@router.post("/{tech_id}/override/reactivate", response_model=InactiveOverrideResponse)
def reactivate_technician(
    tech_id: uuid.UUID,
    data: ReactivateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Unlock a manually-locked Inactive technician and reactivate them.

    Removes the deployability lock so auto-computation can take over again.
    Optionally sets a specific target status; if omitted, defaults to
    'Awaiting Assignment' so the readiness engine can re-evaluate.

    Requires ops role.
    """
    technician = _get_technician_or_404(db, tech_id)

    if not technician.deployability_locked:
        raise HTTPException(
            status_code=400,
            detail="Technician is not manually locked. Use the standard update endpoint to change status.",
        )

    old_status = (
        technician.deployability_status.value
        if hasattr(technician.deployability_status, "value")
        else str(technician.deployability_status)
    )

    # Determine target status
    if data.target_status is not None:
        new_status = data.target_status
    else:
        # Default: set to Awaiting Assignment so readiness engine can re-evaluate
        new_status = DeployabilityStatus.READY_NOW

    # Unlock and update
    technician.deployability_status = new_status
    technician.deployability_locked = False
    old_locked_at = technician.inactive_locked_at
    old_locked_by = technician.inactive_locked_by
    old_lock_reason = technician.inactive_lock_reason
    technician.inactive_locked_at = None
    technician.inactive_locked_by = None
    technician.inactive_lock_reason = None

    # Audit log
    audit = AuditLog(
        user_id=current_user.user_id,
        action="manual_inactive_unlock",
        entity_type="technician",
        entity_id=str(technician.id),
        details={
            "previous_status": old_status,
            "new_status": new_status.value if hasattr(new_status, "value") else str(new_status),
            "was_locked_by": old_locked_by,
            "was_locked_at": str(old_locked_at) if old_locked_at else None,
            "was_locked_reason": old_lock_reason,
            "reactivation_reason": data.reason,
        },
    )
    db.add(audit)
    db.commit()

    new_status_val = new_status.value if hasattr(new_status, "value") else str(new_status)

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.TECHNICIAN_STATUS_CHANGED,
        entity_type="technician",
        entity_id=str(technician.id),
        actor_id=current_user.user_id,
        data={
            "field": "deployability_status",
            "old_value": old_status,
            "new_value": new_status_val,
            "unlocked": True,
            "reason": data.reason,
            "source": "manual_reactivation",
        },
    ))

    return InactiveOverrideResponse(
        technician_id=str(technician.id),
        technician_name=technician.full_name,
        previous_status=old_status,
        new_status=new_status_val,
        deployability_locked=False,
        inactive_locked_at=None,
        inactive_locked_by=None,
        inactive_lock_reason=None,
        action="unlocked_reactivated",
    )


@router.get("/{tech_id}/override/status")
def get_override_status(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Check the manual override status of a technician.

    Returns lock state, metadata, and audit trail of override actions.
    """
    technician = _get_technician_or_404(db, tech_id)

    # Fetch recent override audit entries
    audit_entries = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_type == "technician",
            AuditLog.entity_id == str(tech_id),
            AuditLog.action.in_(["manual_inactive_override", "manual_inactive_unlock"]),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(10)
        .all()
    )

    return {
        "technician_id": str(technician.id),
        "technician_name": technician.full_name,
        "deployability_status": (
            technician.deployability_status.value
            if hasattr(technician.deployability_status, "value")
            else str(technician.deployability_status)
        ),
        "deployability_locked": technician.deployability_locked,
        "inactive_locked_at": str(technician.inactive_locked_at) if technician.inactive_locked_at else None,
        "inactive_locked_by": technician.inactive_locked_by,
        "inactive_lock_reason": technician.inactive_lock_reason,
        "override_history": [
            {
                "id": str(a.id),
                "action": a.action,
                "user_id": a.user_id,
                "details": a.details,
                "created_at": str(a.created_at),
            }
            for a in audit_entries
        ],
    }
