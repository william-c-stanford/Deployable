"""Badge API endpoints — legacy TechnicianBadge + new ManualBadge/MilestoneBadge CRUD.

Legacy endpoints (TechnicianBadge):
  GET    /api/technicians/{tech_id}/badges          — list all badges (filterable)
  POST   /api/technicians/{tech_id}/badges/grant     — grant a manual badge (ops only)
  DELETE /api/technicians/{tech_id}/badges/{badge_id} — revoke a badge (ops only)
  GET    /api/technicians/{tech_id}/badges/milestones — milestone badges (earned + available)
  POST   /api/technicians/{tech_id}/badges/milestones/sync — persist newly-earned milestones

New ManualBadge CRUD:
  GET    /api/badges/manual/technician/{tech_id}     — list manual badges
  POST   /api/badges/manual/technician/{tech_id}     — grant a manual badge
  GET    /api/badges/manual/{badge_id}               — get a manual badge
  PUT    /api/badges/manual/{badge_id}               — update a manual badge
  DELETE /api/badges/manual/{badge_id}               — revoke/delete a manual badge

New MilestoneBadge CRUD:
  GET    /api/badges/milestone/technician/{tech_id}  — list milestone badges
  POST   /api/badges/milestone/technician/{tech_id}  — create a milestone badge
  GET    /api/badges/milestone/{badge_id}            — get a milestone badge
  PUT    /api/badges/milestone/{badge_id}            — update a milestone badge
  DELETE /api/badges/milestone/{badge_id}            — delete a milestone badge

Combined view:
  GET    /api/badges/technician/{tech_id}/all        — all badges for a technician
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.technician import (
    Technician,
    TechnicianBadge,
    BadgeType,
)
from app.models.badge import (
    ManualBadge,
    MilestoneBadge,
    ManualBadgeCategory,
    MilestoneType,
)
from app.schemas.badge import (
    BadgeGrantRequest,
    BadgeRevokeRequest,
    BadgeResponse,
    BadgeListResponse,
    MilestoneBadgeDefinition,
    MilestoneBadgesResponse,
    ManualBadgeCreate,
    ManualBadgeUpdate,
    ManualBadgeResponse,
    ManualBadgeListResponse,
    MilestoneBadgeCreate,
    MilestoneBadgeUpdate,
    MilestoneBadgeResponse,
    MilestoneBadgeListResponse,
)
from app.services.badge_service import (
    list_badges,
    grant_manual_badge,
    revoke_badge,
    compute_milestone_badges,
    sync_milestone_badges,
)
from app.services.milestone_badge_engine import (
    evaluate_milestones as engine_evaluate_milestones,
    sync_milestone_badges as engine_sync_milestones,
    get_milestone_progress,
    sync_all_technicians as engine_sync_all,
)

# Two routers: one nested under technicians, one standalone
legacy_router = APIRouter(prefix="/api/technicians/{tech_id}/badges", tags=["badges"])
router = APIRouter(prefix="/api/badges", tags=["badges"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_technician_or_404(db: Session, tech_id: uuid.UUID) -> Technician:
    tech = db.query(Technician).filter(Technician.id == tech_id).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    return tech


# ===========================================================================
# LEGACY ENDPOINTS (TechnicianBadge — backwards compat)
# ===========================================================================

@legacy_router.get("", response_model=BadgeListResponse)
def list_technician_badges(
    tech_id: uuid.UUID,
    badge_type: Optional[BadgeType] = Query(
        None, description="Filter by badge type: site, client, milestone"
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all badges for a technician with optional type filtering."""
    _get_technician_or_404(db, tech_id)
    badges = list_badges(db, tech_id, badge_type=badge_type)
    return BadgeListResponse(
        items=[BadgeResponse.model_validate(b) for b in badges],
        total=len(badges),
        badge_type_filter=badge_type.value if badge_type else None,
    )


@legacy_router.post(
    "/grant",
    response_model=BadgeResponse,
    status_code=status.HTTP_201_CREATED,
)
def grant_badge(
    tech_id: uuid.UUID,
    data: BadgeGrantRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Grant a manual (site or client) badge to a technician."""
    _get_technician_or_404(db, tech_id)
    try:
        badge = grant_manual_badge(
            db,
            technician_id=tech_id,
            badge_type=data.badge_type,
            badge_name=data.badge_name,
            description=data.description,
        )
        db.commit()
        db.refresh(badge)
        return BadgeResponse.model_validate(badge)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@legacy_router.delete("/{badge_id}", response_model=BadgeResponse)
def revoke_technician_badge(
    tech_id: uuid.UUID,
    badge_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Revoke (delete) a badge from a technician."""
    _get_technician_or_404(db, tech_id)
    try:
        badge_data = revoke_badge(db, technician_id=tech_id, badge_id=badge_id)
        response = BadgeResponse(
            id=badge_data.id,
            technician_id=badge_data.technician_id,
            badge_type=badge_data.badge_type,
            badge_name=badge_data.badge_name,
            description=badge_data.description,
            granted_at=badge_data.granted_at,
        )
        db.commit()
        return response
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@legacy_router.get("/milestones", response_model=MilestoneBadgesResponse)
def get_milestone_badges(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get all milestone badges for a technician: earned and available."""
    technician = _get_technician_or_404(db, tech_id)
    earned, available = compute_milestone_badges(db, technician)
    return MilestoneBadgesResponse(
        technician_id=tech_id,
        earned=[MilestoneBadgeDefinition(**e) for e in earned],
        available=[MilestoneBadgeDefinition(**a) for a in available],
        total_earned=len(earned),
        total_available=len(available),
    )


@legacy_router.post("/milestones/sync", response_model=List[BadgeResponse])
def sync_milestones(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Evaluate and persist any newly-earned milestone badges."""
    technician = _get_technician_or_404(db, tech_id)
    new_badges = sync_milestone_badges(db, technician)
    db.commit()
    result = []
    for badge in new_badges:
        db.refresh(badge)
        result.append(BadgeResponse.model_validate(badge))
    return result


# ===========================================================================
# NEW MANUAL BADGE CRUD
# ===========================================================================

@router.get(
    "/manual/technician/{tech_id}",
    response_model=ManualBadgeListResponse,
    summary="List manual badges for a technician",
)
def list_manual_badges(
    tech_id: uuid.UUID,
    category: Optional[ManualBadgeCategory] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    query = db.query(ManualBadge).filter(ManualBadge.technician_id == tech_id)
    if category:
        query = query.filter(ManualBadge.category == category)
    if is_active is not None:
        query = query.filter(ManualBadge.is_active == is_active)
    badges = query.order_by(ManualBadge.granted_at.desc()).all()
    return ManualBadgeListResponse(items=badges, total=len(badges))


@router.get(
    "/manual/{badge_id}",
    response_model=ManualBadgeResponse,
    summary="Get a manual badge by ID",
)
def get_manual_badge(
    badge_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    badge = db.query(ManualBadge).filter(ManualBadge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail="Manual badge not found")
    return badge


@router.post(
    "/manual/technician/{tech_id}",
    response_model=ManualBadgeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a manual badge to a technician",
)
def create_manual_badge(
    tech_id: uuid.UUID,
    data: ManualBadgeCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    badge = ManualBadge(
        technician_id=tech_id,
        granted_by=current_user.id,
        **data.model_dump(),
    )
    db.add(badge)
    db.commit()
    db.refresh(badge)
    return badge


@router.put(
    "/manual/{badge_id}",
    response_model=ManualBadgeResponse,
    summary="Update a manual badge",
)
def update_manual_badge(
    badge_id: uuid.UUID,
    data: ManualBadgeUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    badge = db.query(ManualBadge).filter(ManualBadge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail="Manual badge not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(badge, field, value)
    db.commit()
    db.refresh(badge)
    return badge


@router.patch(
    "/manual/{badge_id}",
    response_model=ManualBadgeResponse,
    summary="Partially update a manual badge",
)
def patch_manual_badge(
    badge_id: uuid.UUID,
    data: ManualBadgeUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return update_manual_badge(badge_id, data, db, current_user)


@router.delete(
    "/manual/{badge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke/delete a manual badge",
)
def delete_manual_badge(
    badge_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    badge = db.query(ManualBadge).filter(ManualBadge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail="Manual badge not found")
    db.delete(badge)
    db.commit()
    return None


# ===========================================================================
# NEW MILESTONE BADGE CRUD
# ===========================================================================

@router.get(
    "/milestone/technician/{tech_id}",
    response_model=MilestoneBadgeListResponse,
    summary="List milestone badges for a technician",
)
def list_milestone_badges(
    tech_id: uuid.UUID,
    milestone_type: Optional[MilestoneType] = Query(None),
    min_tier: Optional[int] = Query(None, ge=1, le=3),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    query = db.query(MilestoneBadge).filter(MilestoneBadge.technician_id == tech_id)
    if milestone_type:
        query = query.filter(MilestoneBadge.milestone_type == milestone_type)
    if min_tier:
        query = query.filter(MilestoneBadge.tier >= min_tier)
    badges = query.order_by(MilestoneBadge.granted_at.desc()).all()
    return MilestoneBadgeListResponse(items=badges, total=len(badges))


@router.get(
    "/milestone/{badge_id}",
    response_model=MilestoneBadgeResponse,
    summary="Get a milestone badge by ID",
)
def get_milestone_badge(
    badge_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    badge = db.query(MilestoneBadge).filter(MilestoneBadge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail="Milestone badge not found")
    return badge


@router.post(
    "/milestone/technician/{tech_id}",
    response_model=MilestoneBadgeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a milestone badge for a technician",
)
def create_milestone_badge(
    tech_id: uuid.UUID,
    data: MilestoneBadgeCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    # Check for duplicate milestone badge
    existing = db.query(MilestoneBadge).filter(
        MilestoneBadge.technician_id == tech_id,
        MilestoneBadge.milestone_type == data.milestone_type,
        MilestoneBadge.threshold_value == data.threshold_value,
        MilestoneBadge.reference_entity_type == data.reference_entity_type,
        MilestoneBadge.reference_entity_id == data.reference_entity_id,
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Milestone badge already exists for this technician with the same type and threshold",
        )
    badge = MilestoneBadge(technician_id=tech_id, **data.model_dump())
    db.add(badge)
    db.commit()
    db.refresh(badge)
    return badge


@router.put(
    "/milestone/{badge_id}",
    response_model=MilestoneBadgeResponse,
    summary="Update a milestone badge",
)
def update_milestone_badge(
    badge_id: uuid.UUID,
    data: MilestoneBadgeUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    badge = db.query(MilestoneBadge).filter(MilestoneBadge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail="Milestone badge not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(badge, field, value)
    db.commit()
    db.refresh(badge)
    return badge


@router.delete(
    "/milestone/{badge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a milestone badge",
)
def delete_milestone_badge(
    badge_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    badge = db.query(MilestoneBadge).filter(MilestoneBadge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail="Milestone badge not found")
    db.delete(badge)
    db.commit()
    return None


# ===========================================================================
# AUTO-GENERATED MILESTONE ENDPOINTS (using milestone_badge_engine)
# ===========================================================================

@router.get(
    "/milestone/technician/{tech_id}/progress",
    summary="Get milestone progress with percentages for all thresholds",
)
def get_milestone_badge_progress(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return milestone progress for career passport / profile display.

    Each item includes badge_name, threshold, actual value, progress percentage,
    tier, icon, and whether the badge is earned/persisted.
    """
    technician = _get_technician_or_404(db, tech_id)
    progress = get_milestone_progress(db, technician)
    return {
        "technician_id": str(tech_id),
        "technician_name": technician.full_name,
        "progress": progress,
        "total": len(progress),
        "earned_count": sum(1 for p in progress if p["earned"]),
    }


@router.post(
    "/milestone/technician/{tech_id}/auto-sync",
    summary="Auto-sync milestone badges from coarse technician data",
)
def auto_sync_milestones(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Evaluate coarse data thresholds and persist any newly earned milestone badges.

    Uses the milestone_badge_engine which reads from denormalized
    Technician.total_approved_hours and total_project_count fields.
    """
    technician = _get_technician_or_404(db, tech_id)
    new_badges = engine_sync_milestones(db, technician)
    db.commit()
    result = []
    for badge in new_badges:
        db.refresh(badge)
        result.append(MilestoneBadgeResponse.model_validate(badge))
    return {
        "technician_id": str(tech_id),
        "new_badges": result,
        "new_count": len(result),
    }


@router.post(
    "/milestone/batch-sync",
    summary="Batch sync milestone badges for all technicians (nightly job)",
)
def batch_sync_milestones(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Run milestone badge auto-generation for all technicians.

    Intended for nightly batch jobs. Returns summary of newly earned badges.
    """
    results = engine_sync_all(db)
    db.commit()
    summary = {
        name: [MilestoneBadgeResponse.model_validate(b) for b in badges]
        for name, badges in results.items()
    }
    return {
        "technicians_with_new_badges": len(summary),
        "results": summary,
    }


# ===========================================================================
# COMBINED VIEW
# ===========================================================================

@router.get(
    "/technician/{tech_id}/all",
    summary="Get all badges (manual + milestone) for a technician",
)
def list_all_badges(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _get_technician_or_404(db, tech_id)
    manual = db.query(ManualBadge).filter(
        ManualBadge.technician_id == tech_id
    ).order_by(ManualBadge.granted_at.desc()).all()
    milestones = db.query(MilestoneBadge).filter(
        MilestoneBadge.technician_id == tech_id
    ).order_by(MilestoneBadge.granted_at.desc()).all()
    return {
        "technician_id": str(tech_id),
        "manual_badges": [ManualBadgeResponse.model_validate(b) for b in manual],
        "milestone_badges": [MilestoneBadgeResponse.model_validate(b) for b in milestones],
        "total_manual": len(manual),
        "total_milestone": len(milestones),
    }
