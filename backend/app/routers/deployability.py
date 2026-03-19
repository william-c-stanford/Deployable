"""Deployability status API endpoints.

Provides endpoints for:
  - GET /api/deployability/{technician_id}/status — read computed status with readiness
  - POST /api/deployability/{technician_id}/override — apply manual status override
  - GET /api/deployability/{technician_id}/history — view status change history
  - GET /api/deployability/summary — aggregate deployability summary
"""

import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from app.database import get_db
from app.auth import get_current_user, CurrentUser
from app.models.technician import Technician, DeployabilityStatus
from app.models.deployability_history import (
    DeployabilityStatusHistory,
    StatusChangeSource,
)
from app.services.readiness import evaluate_technician_readiness
from app.workers.events import EventPayload, EventType
from app.workers.dispatcher import dispatch_event_safe

logger = logging.getLogger("deployable.routers.deployability")

router = APIRouter(prefix="/api/deployability", tags=["deployability"])


# ---------------------------------------------------------------------------
# Request/Response schemas
# ---------------------------------------------------------------------------

class ManualOverrideRequest(BaseModel):
    """Request body for applying a manual deployability status override."""
    new_status: str
    reason: str
    lock_status: bool = False  # Whether to lock status after override


# ---------------------------------------------------------------------------
# Helper: fetch technician by ID (compatible with SQLite test backends)
# ---------------------------------------------------------------------------

def _get_technician_or_404(db: Session, technician_id: _uuid.UUID) -> Technician:
    """Fetch a technician by primary key, raising 404 if not found."""
    tech = db.query(Technician).filter(Technician.id == technician_id).first()
    if not tech:
        raise HTTPException(status_code=404, detail=f"Technician {technician_id} not found")
    return tech


# ---------------------------------------------------------------------------
# Helper: record status change in history
# ---------------------------------------------------------------------------

def _record_status_change(
    db: Session,
    technician: Technician,
    old_status: str,
    new_status: str,
    source: StatusChangeSource,
    reason: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_name: Optional[str] = None,
    readiness_score: Optional[float] = None,
    dimension_scores: Optional[dict] = None,
    extra_metadata: Optional[dict] = None,
) -> DeployabilityStatusHistory:
    """Create an immutable history record for a status change."""
    history = DeployabilityStatusHistory(
        technician_id=technician.id,
        old_status=old_status,
        new_status=new_status,
        source=source,
        reason=reason,
        actor_id=actor_id,
        actor_name=actor_name,
        readiness_score_at_change=readiness_score,
        dimension_scores=dimension_scores,
        extra_metadata=extra_metadata,
    )
    db.add(history)
    return history


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/summary")
def get_deployability_summary(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Get aggregate deployability status summary across all technicians.

    Returns counts per status, override counts, and recent changes.
    """
    if current_user.role not in ("ops", "admin"):
        raise HTTPException(status_code=403, detail="Only ops users can view deployability summary")

    # Count by status
    status_counts = (
        db.query(
            Technician.deployability_status,
            func.count(Technician.id),
        )
        .group_by(Technician.deployability_status)
        .all()
    )

    counts_dict = {}
    total = 0
    for status_val, count in status_counts:
        key = status_val.value if hasattr(status_val, 'value') else str(status_val)
        counts_dict[key] = count
        total += count

    # Count locked (manually overridden) technicians
    locked_count = db.query(func.count(Technician.id)).filter(
        Technician.deployability_locked == True
    ).scalar() or 0

    # Recent status changes (last 20)
    recent_changes = (
        db.query(DeployabilityStatusHistory)
        .order_by(desc(DeployabilityStatusHistory.created_at))
        .limit(20)
        .all()
    )

    return {
        "total_technicians": total,
        "status_counts": counts_dict,
        "locked_count": locked_count,
        "recent_changes": [h.to_dict() for h in recent_changes],
    }


@router.get("/{technician_id}/status")
def get_deployability_status(
    technician_id: _uuid.UUID,
    include_readiness: bool = Query(default=True, description="Include full readiness evaluation"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Get the current deployability status for a technician.

    Returns the current status, whether it's locked (manually overridden),
    the computed readiness score, and whether the auto-computed status
    differs from the current one.
    """
    technician = _get_technician_or_404(db, technician_id)

    current_status = technician.deployability_status
    current_status_str = current_status.value if hasattr(current_status, 'value') else str(current_status)

    result: dict[str, Any] = {
        "technician_id": str(technician.id),
        "technician_name": technician.full_name,
        "current_status": current_status_str,
        "is_locked": technician.deployability_locked,
        "is_manual_override": technician.deployability_locked,
        "locked_at": technician.inactive_locked_at.isoformat() if technician.inactive_locked_at else None,
        "locked_by": technician.inactive_locked_by,
        "lock_reason": technician.inactive_lock_reason,
        "career_stage": (
            technician.career_stage.value
            if hasattr(technician.career_stage, 'value')
            else str(technician.career_stage)
        ),
        "available_from": technician.available_from.isoformat() if technician.available_from else None,
    }

    # Include readiness evaluation if requested
    if include_readiness:
        try:
            readiness = evaluate_technician_readiness(db, str(technician_id))
            result["readiness"] = {
                "overall_score": round(readiness.overall_score, 1),
                "suggested_status": readiness.suggested_status,
                "status_change_recommended": readiness.status_change_recommended,
                "status_change_reason": readiness.status_change_reason,
                "dimension_scores": {
                    k: round(v, 1) for k, v in readiness.dimension_scores.items()
                },
                "certification_summary": readiness.certification.summary,
                "training_summary": readiness.training.summary,
                "assignment_summary": readiness.assignment_history.summary,
                "documentation_summary": readiness.documentation.summary,
            }
            # Flag if auto-computed differs from current
            result["auto_computed_status"] = readiness.suggested_status
            result["status_divergent"] = (
                readiness.suggested_status != current_status_str
                and readiness.status_change_recommended
            )
        except Exception as e:
            logger.warning("Could not compute readiness for %s: %s", technician_id, e)
            result["readiness"] = None
            result["auto_computed_status"] = None
            result["status_divergent"] = False

    # Get the most recent history entry
    last_change = (
        db.query(DeployabilityStatusHistory)
        .filter(DeployabilityStatusHistory.technician_id == technician.id)
        .order_by(desc(DeployabilityStatusHistory.created_at))
        .first()
    )
    if last_change:
        result["last_change"] = last_change.to_dict()
    else:
        result["last_change"] = None

    return result


@router.post("/{technician_id}/override")
def apply_manual_override(
    technician_id: _uuid.UUID,
    override: ManualOverrideRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply a manual deployability status override.

    Only ops users can override status. Creates an audit trail entry
    and optionally locks the status to prevent auto-computation changes.
    """
    if current_user.role not in ("ops", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Only ops users can override deployability status",
        )

    technician = _get_technician_or_404(db, technician_id)

    # Validate new status
    status_map = {v.value: v for v in DeployabilityStatus}
    if override.new_status not in status_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{override.new_status}'. Valid: {list(status_map.keys())}",
        )

    old_status = technician.deployability_status
    old_status_str = old_status.value if hasattr(old_status, 'value') else str(old_status)

    if old_status_str == override.new_status:
        # No change needed but may want to update lock state
        if override.lock_status and not technician.deployability_locked:
            technician.deployability_locked = True
            technician.inactive_locked_at = datetime.now(timezone.utc)
            technician.inactive_locked_by = current_user.user_id
            technician.inactive_lock_reason = override.reason
            db.commit()
            return {
                "changed": False,
                "locked": True,
                "status": old_status_str,
                "message": "Status unchanged but lock applied",
            }
        return {
            "changed": False,
            "locked": technician.deployability_locked,
            "status": old_status_str,
            "message": "No change needed — already at requested status",
        }

    # Get readiness score for the history record
    readiness_score = None
    dim_scores = None
    try:
        readiness = evaluate_technician_readiness(db, technician_id)
        readiness_score = readiness.overall_score
        dim_scores = {k: round(v, 1) for k, v in readiness.dimension_scores.items()}
    except Exception:
        pass

    # Apply the override
    technician.deployability_status = status_map[override.new_status]

    if override.lock_status:
        technician.deployability_locked = True
        technician.inactive_locked_at = datetime.now(timezone.utc)
        technician.inactive_locked_by = current_user.user_id
        technician.inactive_lock_reason = override.reason

    # Record in history
    _record_status_change(
        db=db,
        technician=technician,
        old_status=old_status_str,
        new_status=override.new_status,
        source=StatusChangeSource.MANUAL_OVERRIDE,
        reason=override.reason,
        actor_id=current_user.user_id,
        actor_name=current_user.name,
        readiness_score=readiness_score,
        dimension_scores=dim_scores,
        extra_metadata={
            "locked": override.lock_status,
        },
    )

    db.commit()

    # Dispatch event for downstream processing (WebSocket push, etc.)
    dispatch_event_safe(EventPayload(
        event_type=EventType.TECHNICIAN_STATUS_CHANGED,
        entity_type="technician",
        entity_id=str(technician_id),
        actor_id=current_user.user_id,
        data={
            "old_status": old_status_str,
            "new_status": override.new_status,
            "reason": override.reason,
            "source": "manual_override",
            "locked": override.lock_status,
        },
    ))

    logger.info(
        "Manual override: %s status %s -> %s by %s (reason: %s, locked: %s)",
        technician.full_name,
        old_status_str,
        override.new_status,
        current_user.user_id,
        override.reason,
        override.lock_status,
    )

    return {
        "changed": True,
        "old_status": old_status_str,
        "new_status": override.new_status,
        "locked": override.lock_status,
        "reason": override.reason,
        "technician_name": technician.full_name,
    }


@router.post("/{technician_id}/unlock")
def unlock_status(
    technician_id: _uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Unlock a technician's deployability status so auto-computation can resume."""
    if current_user.role not in ("ops", "admin"):
        raise HTTPException(status_code=403, detail="Only ops users can unlock status")

    technician = _get_technician_or_404(db, technician_id)

    if not technician.deployability_locked:
        return {"unlocked": False, "message": "Status was not locked"}

    technician.deployability_locked = False
    technician.inactive_locked_at = None
    technician.inactive_locked_by = None
    technician.inactive_lock_reason = None
    db.commit()

    logger.info("Unlocked deployability status for %s by %s", technician.full_name, current_user.user_id)

    return {
        "unlocked": True,
        "technician_name": technician.full_name,
        "current_status": (
            technician.deployability_status.value
            if hasattr(technician.deployability_status, 'value')
            else str(technician.deployability_status)
        ),
    }


@router.get("/{technician_id}/history")
def get_status_history(
    technician_id: _uuid.UUID,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Get the deployability status change history for a technician.

    Returns a paginated list of all status changes, newest first.
    """
    technician = _get_technician_or_404(db, technician_id)

    total = (
        db.query(func.count(DeployabilityStatusHistory.id))
        .filter(DeployabilityStatusHistory.technician_id == technician.id)
        .scalar() or 0
    )

    entries = (
        db.query(DeployabilityStatusHistory)
        .filter(DeployabilityStatusHistory.technician_id == technician.id)
        .order_by(desc(DeployabilityStatusHistory.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "technician_id": str(technician.id),
        "technician_name": technician.full_name,
        "current_status": (
            technician.deployability_status.value
            if hasattr(technician.deployability_status, 'value')
            else str(technician.deployability_status)
        ),
        "total": total,
        "offset": offset,
        "limit": limit,
        "history": [entry.to_dict() for entry in entries],
    }
