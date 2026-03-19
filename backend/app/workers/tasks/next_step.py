"""Next-step recommendation generation Celery tasks.

Handles:
  - Nightly batch generation of 'next step' and 'suggested actions' for all
    technicians, storing results in PostgreSQL and Redis cache
  - Event-triggered single-technician updates when state changes occur
    (cert expiry, training progress, assignment changes, etc.)

These tasks use the deterministic next_step_engine (no LLM calls) and
cache results in Redis for fast API reads.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.technician import Technician
from app.services.next_step_engine import (
    generate_next_steps_for_technician,
    generate_all_next_steps,
    generate_ops_suggested_actions,
    persist_next_step_recommendations,
    persist_ops_suggested_actions,
)
from app.services.recommendation_cache import (
    cache_next_steps,
    cache_suggested_actions,
    invalidate_next_steps,
    invalidate_all_next_steps,
    invalidate_suggested_actions,
    set_last_refresh_timestamp,
    get_last_refresh_timestamp,
)
from app.services.ws_broadcast import (
    publish_notification,
    publish_badge_count_update,
    publish_ws_event,
)

logger = logging.getLogger("deployable.workers.next_step")


# ---------------------------------------------------------------------------
# Nightly batch: generate all next-step recommendations
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.next_step.nightly_next_step_batch",
)
def nightly_next_step_batch(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Nightly batch: regenerate next-step recommendations for all technicians.

    Lifecycle:
      1. Invalidate all cached next-step data
      2. Evaluate each technician's state via the deterministic engine
      3. Persist new Recommendation records (type=next_step) in PostgreSQL
      4. Cache results in Redis for fast API retrieval
      5. Generate and cache ops suggested actions
      6. Broadcast WebSocket notification with summary

    This task is idempotent — running it multiple times will supersede
    previous pending next-step recommendations and refresh the cache.
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.NIGHTLY_BATCH_TRIGGERED,
            entity_type="system",
            entity_id="next_step_batch",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        batch_id = str(uuid4())

        # Step 1: Invalidate all existing cache
        invalidated = invalidate_all_next_steps()
        invalidate_suggested_actions()
        logger.info("Invalidated %d cached next-step entries", invalidated)

        # Step 2: Generate next steps for all technicians
        result = generate_all_next_steps(session)
        stats = result["stats"]
        all_results = result["results"]

        # Step 3: Persist to PostgreSQL and cache in Redis
        total_persisted = 0
        total_cached = 0

        for tech_id, steps in all_results.items():
            # Persist to DB
            recs = persist_next_step_recommendations(
                session, tech_id, steps, batch_id=batch_id,
            )
            total_persisted += len(recs)

            # Cache in Redis
            if cache_next_steps(tech_id, steps):
                total_cached += 1

        session.commit()

        # Step 4: Generate and persist ops suggested actions
        ops_actions = generate_ops_suggested_actions(session)
        persist_ops_suggested_actions(session, ops_actions)
        session.commit()

        # Cache ops actions
        cache_suggested_actions("ops", None, ops_actions)

        # Step 5: Set refresh timestamp
        set_last_refresh_timestamp()

        # Step 6: WebSocket broadcast
        summary = {
            "total_technicians": stats["total_technicians"],
            "technicians_with_steps": stats["technicians_with_steps"],
            "total_steps_generated": stats["total_steps_generated"],
            "ops_actions_generated": len(ops_actions),
            "batch_id": batch_id,
            "by_urgency": stats["by_urgency"],
        }

        publish_ws_event("dashboard", {
            "event_type": "next_step.batch_complete",
            "topic": "dashboard",
            "data": summary,
        })

        if stats["by_urgency"].get("critical", 0) > 0:
            publish_notification(
                notification_type="next_step_batch",
                title="Next Steps Updated",
                message=(
                    f"Generated {stats['total_steps_generated']} next-step recommendations "
                    f"for {stats['technicians_with_steps']} technicians. "
                    f"{stats['by_urgency']['critical']} critical items."
                ),
                role="ops",
                severity="warning",
                link="/dashboard",
            )
        elif stats["total_steps_generated"] > 0:
            publish_notification(
                notification_type="next_step_batch",
                title="Next Steps Updated",
                message=(
                    f"Generated {stats['total_steps_generated']} next-step recommendations "
                    f"for {stats['technicians_with_steps']} technicians."
                ),
                role="ops",
                severity="info",
                link="/dashboard",
            )

        logger.info(
            "Next-step batch complete: %d technicians, %d steps, %d persisted, "
            "%d cached, %d ops actions (batch: %s)",
            stats["total_technicians"],
            stats["total_steps_generated"],
            total_persisted,
            total_cached,
            len(ops_actions),
            batch_id,
        )

        return {
            "status": "completed",
            "batch_id": batch_id,
            **summary,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Event-triggered: refresh next steps for a single technician
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.next_step.refresh_technician_next_steps",
)
def refresh_technician_next_steps(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Refresh next-step recommendations for a single technician after an event.

    Triggered by state-changing events:
      - CERT_ADDED, CERT_EXPIRED, CERT_EXPIRING_SOON
      - TRAINING_COMPLETED, PROFICIENCY_ADVANCED
      - ASSIGNMENT_ENDED, ASSIGNMENT_CREATED
      - DOC_VERIFIED, ALL_DOCS_VERIFIED
      - TECHNICIAN_STATUS_CHANGED

    Updates both PostgreSQL records and Redis cache for the affected technician.
    Also refreshes ops suggested actions since counts may have changed.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        # Determine technician ID from event payload
        technician_id = payload.data.get("technician_id", payload.entity_id)

        # If entity_type is not technician, try to extract tech ID from data
        if payload.entity_type != "technician":
            technician_id = payload.data.get("technician_id", technician_id)

        technician = session.get(Technician, technician_id)
        if not technician:
            return {
                "status": "skipped",
                "reason": f"Technician {technician_id} not found",
            }

        # Generate fresh next steps
        steps = generate_next_steps_for_technician(session, technician)

        # Persist to DB (supersedes old pending next-step recs)
        recs = persist_next_step_recommendations(session, str(technician.id), steps)
        session.commit()

        # Update Redis cache
        invalidate_next_steps(str(technician.id))
        cache_next_steps(str(technician.id), steps)

        # Also refresh ops actions (counts may have changed)
        ops_actions = generate_ops_suggested_actions(session)
        persist_ops_suggested_actions(session, ops_actions)
        session.commit()
        cache_suggested_actions("ops", None, ops_actions)
        invalidate_suggested_actions("ops")

        # WebSocket push to notify technician of updated next steps
        if steps:
            critical_count = sum(1 for s in steps if s.get("urgency") == "critical")
            publish_ws_event("technician", {
                "event_type": "next_step.updated",
                "topic": "technician",
                "technician_id": str(technician.id),
                "data": {
                    "technician_id": str(technician.id),
                    "steps_count": len(steps),
                    "critical_count": critical_count,
                    "top_step": steps[0].get("title") if steps else None,
                    "trigger_event": payload.event_type.value,
                },
            })

        logger.info(
            "Refreshed next steps for technician %s: %d steps (%d persisted), "
            "triggered by %s",
            technician_id,
            len(steps),
            len(recs),
            payload.event_type.value,
        )

        return {
            "status": "refreshed",
            "technician_id": str(technician.id),
            "steps_count": len(steps),
            "persisted_count": len(recs),
            "trigger_event": payload.event_type.value,
            "categories": list({s.get("category") for s in steps}),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Manual trigger: refresh ops suggested actions only
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.next_step.refresh_ops_suggested_actions",
)
def refresh_ops_suggested_actions(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Refresh ops suggested actions (dashboard action cards).

    Can be triggered manually or by system events that change aggregate counts
    (e.g., headcount request created, recommendation acted upon).
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.SCORE_REFRESH_TRIGGERED,
            entity_type="system",
            entity_id="ops_actions_refresh",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        ops_actions = generate_ops_suggested_actions(session)
        persist_ops_suggested_actions(session, ops_actions)
        session.commit()

        # Cache
        invalidate_suggested_actions("ops")
        cache_suggested_actions("ops", None, ops_actions)

        logger.info("Refreshed %d ops suggested actions", len(ops_actions))

        return {
            "status": "refreshed",
            "actions_count": len(ops_actions),
            "action_types": [a.get("action_key") for a in ops_actions],
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
