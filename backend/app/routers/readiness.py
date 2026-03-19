"""Readiness evaluation API endpoints.

Provides endpoints for:
  - GET /readiness/{technician_id} — evaluate a single technician's readiness
  - POST /readiness/{technician_id}/apply — apply a suggested status change
  - POST /readiness/batch — trigger batch re-evaluation for all technicians
  - GET /readiness/summary — get readiness summary across all technicians
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user
from app.models.technician import Technician, DeployabilityStatus
from app.services.readiness import (
    evaluate_technician_readiness,
    evaluate_all_technicians_readiness,
    apply_readiness_status_update,
)
from app.workers.events import EventPayload, EventType
from app.workers.dispatcher import dispatch_event_safe

logger = logging.getLogger("deployable.routers.readiness")

router = APIRouter(prefix="/readiness", tags=["readiness"])


@router.get("/{technician_id}")
def get_technician_readiness(
    technician_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Evaluate and return readiness scores for a single technician.

    Computes scores across 4 dimensions:
      - Certification (35%): Active vs expired certs, expiry proximity
      - Training (35%): Skill levels, hours, enrollment progress
      - Assignment History (20%): Completion rate, recency, diversity
      - Documentation (10%): Verification completeness

    Returns the full scoring breakdown with suggested status.
    """
    try:
        result = evaluate_technician_readiness(db, technician_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return result.to_dict()


@router.post("/{technician_id}/apply")
def apply_readiness_update(
    technician_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply the suggested status change from a readiness evaluation.

    This endpoint:
    1. Re-evaluates the technician's readiness (to ensure freshness)
    2. Applies the suggested status change if one exists
    3. Dispatches events for downstream processing

    Requires ops role.
    """
    role = current_user.get("role", "ops")
    if role not in ("ops", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Only ops users can apply readiness status changes",
        )

    try:
        result = evaluate_technician_readiness(db, technician_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if not result.status_change_recommended:
        return {
            "applied": False,
            "reason": "No status change recommended",
            "current_status": result.current_status,
            "overall_score": result.overall_score,
        }

    update_result = apply_readiness_status_update(db, technician_id, result)
    db.commit()

    # Dispatch event for downstream processing
    if update_result.get("changed"):
        dispatch_event_safe(EventPayload(
            event_type=EventType.TECHNICIAN_STATUS_CHANGED,
            entity_type="technician",
            entity_id=technician_id,
            actor_id=current_user.get("user_id", "ops"),
            data={
                "old_status": update_result.get("old_status"),
                "new_status": update_result.get("new_status"),
                "reason": update_result.get("reason"),
                "source": "readiness_evaluation",
            },
        ))

    return update_result


@router.post("/batch")
def trigger_batch_readiness_evaluation(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Trigger batch readiness re-evaluation for all active technicians.

    This dispatches a Celery task for async processing. Results
    are delivered via WebSocket notifications.
    """
    role = current_user.get("role", "ops")
    if role not in ("ops", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Only ops users can trigger batch readiness evaluation",
        )

    dispatch_event_safe(EventPayload(
        event_type=EventType.SCORE_REFRESH_TRIGGERED,
        entity_type="system",
        entity_id="readiness_batch",
        actor_id=current_user.get("user_id", "ops"),
        data={"source": "manual_batch_trigger"},
    ))

    return {
        "status": "dispatched",
        "message": "Batch readiness re-evaluation has been queued. Results will be delivered via WebSocket.",
    }


@router.get("/summary/all")
def get_readiness_summary(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    limit: int = Query(default=100, le=500),
) -> dict[str, Any]:
    """Get a readiness summary across all active technicians.

    Returns aggregate statistics and per-technician scores sorted
    by overall readiness score (ascending — lowest readiness first
    to highlight techs needing attention).
    """
    results = evaluate_all_technicians_readiness(db, only_active=True)

    # Sort by score ascending (show techs needing attention first)
    results.sort(key=lambda r: r.overall_score)

    summary = {
        "total_evaluated": len(results),
        "status_changes_recommended": sum(1 for r in results if r.status_change_recommended),
        "average_score": (
            sum(r.overall_score for r in results) / len(results)
            if results else 0
        ),
        "score_distribution": {
            "high": sum(1 for r in results if r.overall_score >= 75),
            "medium": sum(1 for r in results if 50 <= r.overall_score < 75),
            "low": sum(1 for r in results if r.overall_score < 50),
        },
        "technicians": [
            {
                "technician_id": r.technician_id,
                "technician_name": r.technician_name,
                "overall_score": round(r.overall_score, 1),
                "current_status": r.current_status,
                "suggested_status": r.suggested_status,
                "status_change_recommended": r.status_change_recommended,
                "dimension_scores": {
                    k: round(v, 1) for k, v in r.dimension_scores.items()
                },
            }
            for r in results[:limit]
        ],
    }

    return summary
