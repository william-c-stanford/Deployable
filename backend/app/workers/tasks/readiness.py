"""Readiness re-evaluation reactive agent tasks.

Handles:
  - Technician readiness re-evaluation triggered by events
  - Batch readiness re-evaluation for all technicians
  - Status update suggestions based on readiness scores

These tasks produce SUGGESTED status changes — they do NOT autonomously
mutate state (except through the deterministic training advancement path).
All status change suggestions require human approval via the ops UI.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.technician import Technician, DeployabilityStatus
from app.models.audit import SuggestedAction
from app.models.recommendation import Recommendation, RecommendationStatus
from app.services.readiness import (
    evaluate_technician_readiness,
    evaluate_all_technicians_readiness,
    ReadinessResult,
)

logger = logging.getLogger("deployable.workers.readiness")

WS_BROADCAST_CHANNEL = "deployable:ws_broadcast"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _broadcast_ws_notification(topic: str, event: dict[str, Any]) -> None:
    """Push a WebSocket notification via Redis pub/sub."""
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        message = json.dumps({"topic": topic, "event": event}, default=str)
        r.publish(WS_BROADCAST_CHANNEL, message)
    except Exception:
        logger.warning("Failed to publish WS notification", exc_info=True)


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.readiness.reevaluate_technician_readiness",
)
def reevaluate_technician_readiness(
    self, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Re-evaluate readiness for a single technician after a domain event.

    Triggered by:
      - CERT_ADDED, CERT_RENEWED, CERT_EXPIRED, CERT_REVOKED
      - TRAINING_COMPLETED, PROFICIENCY_ADVANCED
      - ASSIGNMENT_ENDED, ASSIGNMENT_CANCELLED
      - TECHNICIAN_STATUS_CHANGED, TECHNICIAN_AVAILABILITY_CHANGED
      - DOC_VERIFIED, ALL_DOCS_VERIFIED

    This task:
    1. Evaluates the technician's readiness across all dimensions
    2. Creates a suggested action if a status change is recommended
    3. Broadcasts a WebSocket notification with the updated scores
    4. Creates a recommendation for ops review if status change needed
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.data.get("technician_id", payload.entity_id)
        if not tech_id:
            return {"status": "skipped", "reason": "No technician_id in event data"}

        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": f"Technician {tech_id} not found"}

        # Evaluate readiness
        result = evaluate_technician_readiness(session, str(tech_id))

        # Serialize the result for return
        result_dict = result.to_dict()

        cascade_events = []

        # If status change is recommended, create a suggested action for ops
        if result.status_change_recommended:
            action = SuggestedAction(
                target_role="ops",
                action_type="readiness_status_change",
                title=f"Status Change: {technician.full_name}",
                description=(
                    f"Readiness evaluation suggests changing {technician.full_name}'s "
                    f"status from '{result.current_status}' to '{result.suggested_status}'. "
                    f"Reason: {result.status_change_reason}. "
                    f"Overall readiness score: {result.overall_score:.0f}/100."
                ),
                link=f"/technicians/{tech_id}",
                priority=2 if result.overall_score >= 70 else 3,
            )
            session.add(action)

            # Create a readiness recommendation for the ops approval queue
            rec = Recommendation(
                recommendation_type="readiness_update",
                target_entity_type="technician",
                target_entity_id=str(tech_id),
                technician_id=str(tech_id),
                overall_score=result.overall_score,
                scorecard={
                    "dimensions": result_dict["dimension_scores"],
                    "current_status": result.current_status,
                    "suggested_status": result.suggested_status,
                    "change_reason": result.status_change_reason,
                    "certification_summary": result.certification.summary,
                    "training_summary": result.training.summary,
                    "assignment_summary": result.assignment_history.summary,
                },
                explanation=(
                    f"Readiness re-evaluation for {technician.full_name}: "
                    f"Overall score {result.overall_score:.0f}/100. "
                    f"Certification: {result.certification.score:.0f}, "
                    f"Training: {result.training.score:.0f}, "
                    f"Assignment History: {result.assignment_history.score:.0f}, "
                    f"Documentation: {result.documentation.score:.0f}. "
                    f"Recommended status: {result.suggested_status} "
                    f"(currently {result.current_status})."
                ),
                status=RecommendationStatus.PENDING.value,
                agent_name="readiness_agent",
                metadata_={
                    "trigger_event": payload.event_type.value,
                    "evaluation_timestamp": result.evaluated_at,
                },
            )
            session.add(rec)

            # Cascade a status change event for downstream processing
            cascade_events.append(
                EventPayload(
                    event_type=EventType.TECHNICIAN_STATUS_CHANGED,
                    entity_type="technician",
                    entity_id=str(tech_id),
                    actor_id="readiness_agent",
                    data={
                        "old_status": result.current_status,
                        "suggested_status": result.suggested_status,
                        "overall_score": result.overall_score,
                        "status_change_pending_approval": True,
                    },
                ).to_dict()
            )

        session.commit()

        # Broadcast readiness update via WebSocket
        _broadcast_ws_notification("technicians", {
            "event_type": "readiness.reevaluated",
            "topic": "technicians",
            "technician_id": str(tech_id),
            "technician_name": technician.full_name,
            "data": {
                "overall_score": result.overall_score,
                "dimension_scores": result_dict["dimension_scores"],
                "current_status": result.current_status,
                "suggested_status": result.suggested_status,
                "status_change_recommended": result.status_change_recommended,
                "trigger_event": payload.event_type.value,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        _broadcast_ws_notification("dashboard", {
            "event_type": "dashboard.readiness_update",
            "topic": "dashboard",
            "data": {
                "technician_id": str(tech_id),
                "technician_name": technician.full_name,
                "overall_score": result.overall_score,
                "status_change_recommended": result.status_change_recommended,
                "suggested_status": result.suggested_status,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return {
            "status": "evaluated",
            "technician_id": str(tech_id),
            "technician_name": technician.full_name,
            "overall_score": result.overall_score,
            "current_status": result.current_status,
            "suggested_status": result.suggested_status,
            "status_change_recommended": result.status_change_recommended,
            "dimension_scores": result_dict["dimension_scores"],
            "cascade_events": cascade_events,
        }
    except ValueError as e:
        return {"status": "error", "reason": str(e)}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.readiness.batch_reevaluate_readiness",
)
def batch_reevaluate_readiness(
    self, event_dict: dict[str, Any] = None
) -> dict[str, Any]:
    """Batch re-evaluate readiness for all active technicians.

    Typically triggered by:
      - NIGHTLY_BATCH_TRIGGERED
      - SCORE_REFRESH_TRIGGERED
      - Manual trigger from ops

    Evaluates all non-inactive technicians and creates suggested actions
    for any that need status changes.
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.SCORE_REFRESH_TRIGGERED,
            entity_type="system",
            entity_id="readiness_batch",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        results = evaluate_all_technicians_readiness(session, only_active=True)

        stats = {
            "total_evaluated": len(results),
            "status_changes_recommended": 0,
            "high_readiness": 0,  # score >= 75
            "medium_readiness": 0,  # 50 <= score < 75
            "low_readiness": 0,  # score < 50
            "suggested_changes": [],
        }

        for result in results:
            if result.overall_score >= 75:
                stats["high_readiness"] += 1
            elif result.overall_score >= 50:
                stats["medium_readiness"] += 1
            else:
                stats["low_readiness"] += 1

            if result.status_change_recommended:
                stats["status_changes_recommended"] += 1
                stats["suggested_changes"].append({
                    "technician_id": result.technician_id,
                    "technician_name": result.technician_name,
                    "current_status": result.current_status,
                    "suggested_status": result.suggested_status,
                    "reason": result.status_change_reason,
                    "overall_score": result.overall_score,
                })

                # Create suggested action for ops
                action = SuggestedAction(
                    target_role="ops",
                    action_type="readiness_batch_status_change",
                    title=f"Batch: {result.technician_name} → {result.suggested_status}",
                    description=(
                        f"Nightly readiness batch suggests changing "
                        f"{result.technician_name}'s status from "
                        f"'{result.current_status}' to '{result.suggested_status}'. "
                        f"Reason: {result.status_change_reason}. "
                        f"Score: {result.overall_score:.0f}/100."
                    ),
                    link=f"/technicians/{result.technician_id}",
                    priority=3,
                )
                session.add(action)

        session.commit()

        logger.info(
            "Batch readiness evaluation: %d evaluated, %d changes recommended "
            "(high=%d, medium=%d, low=%d)",
            stats["total_evaluated"],
            stats["status_changes_recommended"],
            stats["high_readiness"],
            stats["medium_readiness"],
            stats["low_readiness"],
        )

        # Broadcast summary via WebSocket
        _broadcast_ws_notification("dashboard", {
            "event_type": "dashboard.readiness_batch_complete",
            "topic": "dashboard",
            "data": {
                "total_evaluated": stats["total_evaluated"],
                "status_changes_recommended": stats["status_changes_recommended"],
                "high_readiness": stats["high_readiness"],
                "medium_readiness": stats["medium_readiness"],
                "low_readiness": stats["low_readiness"],
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return {"status": "completed", **stats}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
