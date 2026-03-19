"""Celery tasks for auto-resolving transitional deployability states.

Transitional states (Onboarding, PendingReview, Suspended) are temporary
statuses that auto-resolve based on:
- Timeout: state expires after N hours
- Event: a domain event (cert.added, doc.verified, etc.) triggers resolution
- Condition: periodic scan checks conditions (all docs verified, min certs, etc.)
- Manual: only ops can resolve

The periodic scan task runs every 10 minutes to catch timeout expirations
and condition-based resolutions. Event-based resolutions are triggered
immediately by the event dispatcher routing table.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal

logger = logging.getLogger("deployable.workers.transitional")

# ---------------------------------------------------------------------------
# Transitional status values (must match DeployabilityStatus enum values)
# ---------------------------------------------------------------------------
TRANSITIONAL_STATUSES = {"Onboarding", "Pending Review", "Suspended"}


def _is_transitional(status_value: str) -> bool:
    """Check if a deployability status value is a transitional status."""
    return status_value in TRANSITIONAL_STATUSES


def _compute_resolved_status(session, technician, fallback_status=None) -> str:
    """Determine what status the technician should resolve to.

    If a fallback_status is specified, use it. Otherwise, compute the
    appropriate status based on the technician's current state using
    a simplified readiness check.
    """
    if fallback_status:
        return fallback_status.value if hasattr(fallback_status, "value") else str(fallback_status)

    from app.models.technician import (
        DeployabilityStatus,
        CertStatus,
        VerificationStatus,
    )
    from app.models.assignment import Assignment

    # Check current assignments
    active_assignment = (
        session.query(Assignment)
        .filter(
            Assignment.technician_id == technician.id,
            Assignment.status.in_(["Active", "Pre-Booked", "Pending Confirmation"]),
        )
        .first()
    )
    if active_assignment:
        return DeployabilityStatus.CURRENTLY_ASSIGNED.value

    # Check training status
    if technician.career_stage and hasattr(technician.career_stage, "value"):
        stage = technician.career_stage.value
    else:
        stage = str(technician.career_stage)

    if stage in ("Sourced", "Screened", "In Training"):
        return DeployabilityStatus.IN_TRAINING.value

    # Check for missing certs
    has_expired_cert = any(
        (c.status.value if hasattr(c.status, "value") else str(c.status)) == "Expired"
        for c in technician.certifications
    )
    if has_expired_cert:
        return DeployabilityStatus.MISSING_CERT.value

    # Check for missing docs
    has_unverified_docs = any(
        (d.verification_status.value if hasattr(d.verification_status, "value") else str(d.verification_status))
        not in ("Verified",)
        for d in technician.documents
    )
    if has_unverified_docs and technician.documents:
        return DeployabilityStatus.MISSING_DOCS.value

    # Default: Ready Now
    return DeployabilityStatus.READY_NOW.value


def _check_conditions_met(session, technician, conditions: dict) -> bool:
    """Check if resolution conditions are met for a condition-based transitional state.

    Supported conditions:
    - all_docs_verified: bool — all documents must be verified
    - min_certs: int — minimum number of active certifications
    - all_certs_active: bool — no expired or revoked certs
    - min_training_hours: float — minimum total approved hours
    - career_stage_reached: str — career stage must be at or past this stage
    """
    if not conditions:
        return True

    from app.models.technician import CertStatus, VerificationStatus

    # all_docs_verified
    if conditions.get("all_docs_verified"):
        if not technician.documents:
            return False
        for doc in technician.documents:
            vs = doc.verification_status.value if hasattr(doc.verification_status, "value") else str(doc.verification_status)
            if vs != "Verified":
                return False

    # min_certs
    min_certs = conditions.get("min_certs")
    if min_certs is not None:
        active_count = sum(
            1
            for c in technician.certifications
            if (c.status.value if hasattr(c.status, "value") else str(c.status)) == "Active"
        )
        if active_count < min_certs:
            return False

    # all_certs_active
    if conditions.get("all_certs_active"):
        for c in technician.certifications:
            cs = c.status.value if hasattr(c.status, "value") else str(c.status)
            if cs in ("Expired", "Revoked"):
                return False

    # min_training_hours
    min_hours = conditions.get("min_training_hours")
    if min_hours is not None:
        if (technician.total_approved_hours or 0) < min_hours:
            return False

    # career_stage_reached
    stage_target = conditions.get("career_stage_reached")
    if stage_target:
        stage_order = [
            "Sourced", "Screened", "In Training",
            "Training Completed", "Awaiting Assignment", "Deployed",
        ]
        current = technician.career_stage.value if hasattr(technician.career_stage, "value") else str(technician.career_stage)
        try:
            if stage_order.index(current) < stage_order.index(stage_target):
                return False
        except ValueError:
            return False

    return True


def _resolve_transitional_state(
    session,
    ts_record,
    technician,
    resolution_reason: str,
    resolved_by: str = "system",
    resolution_event_type: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve a transitional state record and update the technician's status.

    Returns a result dict with cascade events.
    """
    from app.models.technician import DeployabilityStatus

    # Compute the target status
    resolved_to = _compute_resolved_status(session, technician, ts_record.fallback_status)

    # Update the transitional state record
    ts_record.is_active = False
    ts_record.resolved_at = datetime.utcnow()
    ts_record.resolved_by = resolved_by
    ts_record.resolution_reason = resolution_reason
    ts_record.resolution_event_type = resolution_event_type

    # Find the matching enum value for resolved_to
    for member in DeployabilityStatus:
        if member.value == resolved_to:
            ts_record.resolved_to_status = member
            break

    # Update technician status (unless deployability is locked)
    old_status = technician.deployability_status.value if hasattr(technician.deployability_status, "value") else str(technician.deployability_status)

    if not technician.deployability_locked:
        for member in DeployabilityStatus:
            if member.value == resolved_to:
                technician.deployability_status = member
                break

    session.flush()

    logger.info(
        "Resolved transitional state %s for technician %s: %s -> %s (reason: %s)",
        ts_record.id,
        technician.id,
        old_status,
        resolved_to,
        resolution_reason,
    )

    # Build cascade events
    cascade_events = []

    # Notify status change
    cascade_events.append(
        EventPayload(
            event_type=EventType.TRANSITIONAL_STATE_RESOLVED,
            entity_type="transitional_state",
            entity_id=str(ts_record.id),
            actor_id=resolved_by,
            data={
                "technician_id": str(technician.id),
                "technician_name": technician.full_name,
                "transitional_status": ts_record.transitional_status.value
                if hasattr(ts_record.transitional_status, "value")
                else str(ts_record.transitional_status),
                "resolved_to": resolved_to,
                "resolution_reason": resolution_reason,
                "resolution_type": ts_record.resolution_type.value
                if hasattr(ts_record.resolution_type, "value")
                else str(ts_record.resolution_type),
            },
        ).to_dict()
    )

    # Also fire a technician status changed event if status actually changed
    if old_status != resolved_to:
        cascade_events.append(
            EventPayload(
                event_type=EventType.TECHNICIAN_STATUS_CHANGED,
                entity_type="technician",
                entity_id=str(technician.id),
                actor_id=resolved_by,
                data={
                    "field": "deployability_status",
                    "old_value": old_status,
                    "new_value": resolved_to,
                    "source": "transitional_resolution",
                    "transitional_state_id": str(ts_record.id),
                },
            ).to_dict()
        )

    return {
        "status": "resolved",
        "transitional_state_id": str(ts_record.id),
        "technician_id": str(technician.id),
        "old_status": old_status,
        "resolved_to": resolved_to,
        "resolution_reason": resolution_reason,
        "cascade_events": cascade_events,
    }


def _broadcast_transitional_ws(technician_id: str, event_type: str, data: dict):
    """Broadcast a transitional state change via Redis pub/sub for WebSocket relay."""
    try:
        import redis
        import os

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis.from_url(redis_url)
        ws_event = {
            "topic": "technicians",
            "event": {
                "event_type": event_type,
                "topic": "technicians",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data,
            },
        }
        r.publish("deployable:ws_broadcast", json.dumps(ws_event))
        r.close()
    except Exception:
        logger.warning("Failed to broadcast transitional WS event", exc_info=True)


# ---------------------------------------------------------------------------
# Periodic scan: resolve timed-out and condition-met transitional states
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, base=ReactiveAgentTask, name="app.workers.tasks.transitional.scan_transitional_states")
def scan_transitional_states(self, event_dict: dict[str, Any] | None = None) -> dict[str, Any]:
    """Periodic scan for transitional states that need resolution.

    Checks all active transitional state records for:
    1. Timeout expiration (expires_at <= now)
    2. Condition satisfaction (condition-based records)

    Runs every 10 minutes via Celery Beat.
    """
    from app.models.transitional_state import TransitionalState
    from app.models.technician import Technician
    from sqlalchemy.orm import joinedload

    session = self._get_session()
    resolved_count = 0
    errors = []

    try:
        now = datetime.utcnow()

        # 1. Find timeout-expired transitional states
        expired_states = (
            session.query(TransitionalState)
            .filter(
                TransitionalState.is_active == True,  # noqa: E712
                TransitionalState.expires_at != None,  # noqa: E711
                TransitionalState.expires_at <= now,
            )
            .all()
        )

        for ts in expired_states:
            try:
                technician = (
                    session.query(Technician)
                    .options(
                        joinedload(Technician.certifications),
                        joinedload(Technician.documents),
                        joinedload(Technician.skills),
                    )
                    .filter(Technician.id == ts.technician_id)
                    .first()
                )
                if not technician:
                    ts.is_active = False
                    ts.resolved_at = now
                    ts.resolution_reason = "Technician not found — auto-closed"
                    ts.resolved_by = "system"
                    continue

                result = _resolve_transitional_state(
                    session,
                    ts,
                    technician,
                    resolution_reason=f"Timeout expired ({ts.timeout_hours}h)",
                    resolved_by="system",
                )
                resolved_count += 1

                _broadcast_transitional_ws(
                    str(technician.id),
                    "technician.transitional_resolved",
                    {
                        "technician_id": str(technician.id),
                        "technician_name": technician.full_name,
                        "resolved_to": result["resolved_to"],
                        "reason": "timeout_expired",
                    },
                )
            except Exception as exc:
                errors.append(f"Error resolving expired state {ts.id}: {str(exc)}")
                logger.exception("Error resolving expired transitional state %s", ts.id)

        # 2. Find condition-based transitional states and check conditions
        condition_states = (
            session.query(TransitionalState)
            .filter(
                TransitionalState.is_active == True,  # noqa: E712
                TransitionalState.resolution_type == "condition",
                TransitionalState.resolution_conditions != None,  # noqa: E711
            )
            .all()
        )

        for ts in condition_states:
            try:
                technician = (
                    session.query(Technician)
                    .options(
                        joinedload(Technician.certifications),
                        joinedload(Technician.documents),
                        joinedload(Technician.skills),
                    )
                    .filter(Technician.id == ts.technician_id)
                    .first()
                )
                if not technician:
                    ts.is_active = False
                    ts.resolved_at = now
                    ts.resolution_reason = "Technician not found — auto-closed"
                    ts.resolved_by = "system"
                    continue

                conditions = ts.resolution_conditions or {}
                if _check_conditions_met(session, technician, conditions):
                    result = _resolve_transitional_state(
                        session,
                        ts,
                        technician,
                        resolution_reason=f"Conditions met: {json.dumps(conditions)}",
                        resolved_by="system",
                    )
                    resolved_count += 1

                    _broadcast_transitional_ws(
                        str(technician.id),
                        "technician.transitional_resolved",
                        {
                            "technician_id": str(technician.id),
                            "technician_name": technician.full_name,
                            "resolved_to": result["resolved_to"],
                            "reason": "conditions_met",
                        },
                    )
            except Exception as exc:
                errors.append(f"Error checking conditions for state {ts.id}: {str(exc)}")
                logger.exception("Error checking conditions for transitional state %s", ts.id)

        session.commit()

        logger.info(
            "Transitional state scan complete: %d resolved, %d errors",
            resolved_count,
            len(errors),
        )

        return {
            "status": "ok",
            "resolved_count": resolved_count,
            "errors": errors,
        }

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Event-based resolution: triggered by domain events matching resolution_events
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, base=ReactiveAgentTask, name="app.workers.tasks.transitional.check_event_resolution")
def check_event_resolution(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Check if a domain event resolves any active transitional states.

    When certain events fire (cert.added, doc.verified, training.completed, etc.),
    this task checks all active event-based transitional states for the affected
    technician to see if the event matches their resolution_events list.
    """
    from app.models.transitional_state import TransitionalState
    from app.models.technician import Technician
    from sqlalchemy.orm import joinedload

    payload = EventPayload.from_dict(event_dict)
    session = self._get_session()
    resolved_count = 0

    try:
        # Extract technician_id from the event data
        technician_id = payload.data.get("technician_id") or payload.entity_id

        # Find active event-based transitional states for this technician
        active_states = (
            session.query(TransitionalState)
            .filter(
                TransitionalState.is_active == True,  # noqa: E712
                TransitionalState.technician_id == technician_id,
                TransitionalState.resolution_type == "event",
                TransitionalState.resolution_events != None,  # noqa: E711
            )
            .all()
        )

        if not active_states:
            return {"status": "no_matching_states", "event_type": payload.event_type.value}

        technician = (
            session.query(Technician)
            .options(
                joinedload(Technician.certifications),
                joinedload(Technician.documents),
                joinedload(Technician.skills),
            )
            .filter(Technician.id == technician_id)
            .first()
        )

        if not technician:
            return {"status": "technician_not_found"}

        event_type_value = payload.event_type.value

        for ts in active_states:
            resolution_events = ts.resolution_events or []
            if event_type_value in resolution_events:
                result = _resolve_transitional_state(
                    session,
                    ts,
                    technician,
                    resolution_reason=f"Resolved by event: {event_type_value}",
                    resolved_by="system",
                    resolution_event_type=event_type_value,
                )
                resolved_count += 1

                _broadcast_transitional_ws(
                    str(technician.id),
                    "technician.transitional_resolved",
                    {
                        "technician_id": str(technician.id),
                        "technician_name": technician.full_name,
                        "resolved_to": result["resolved_to"],
                        "reason": f"event:{event_type_value}",
                    },
                )

        session.commit()

        return {
            "status": "ok",
            "resolved_count": resolved_count,
            "triggering_event": event_type_value,
        }

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Enter transitional state task (called from API or other tasks)
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, base=ReactiveAgentTask, name="app.workers.tasks.transitional.enter_transitional_state")
def enter_transitional_state(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Process a TRANSITIONAL_STATE_ENTERED event to set up the transitional state.

    This task is triggered when a new transitional state is created via the API.
    It ensures the technician's deployability status is updated and broadcasts
    the WebSocket notification.
    """
    from app.models.transitional_state import TransitionalState
    from app.models.technician import Technician, DeployabilityStatus

    payload = EventPayload.from_dict(event_dict)
    session = self._get_session()

    try:
        ts_id = payload.entity_id
        ts = session.query(TransitionalState).filter(TransitionalState.id == ts_id).first()
        if not ts:
            return {"status": "transitional_state_not_found"}

        technician = session.query(Technician).filter(Technician.id == ts.technician_id).first()
        if not technician:
            return {"status": "technician_not_found"}

        # Ensure the technician's status matches the transitional status
        if not technician.deployability_locked:
            technician.deployability_status = ts.transitional_status
            session.flush()

        session.commit()

        _broadcast_transitional_ws(
            str(technician.id),
            "technician.transitional_entered",
            {
                "technician_id": str(technician.id),
                "technician_name": technician.full_name,
                "transitional_status": ts.transitional_status.value
                if hasattr(ts.transitional_status, "value")
                else str(ts.transitional_status),
                "trigger": ts.trigger.value if hasattr(ts.trigger, "value") else str(ts.trigger),
                "resolution_type": ts.resolution_type.value
                if hasattr(ts.resolution_type, "value")
                else str(ts.resolution_type),
                "timeout_hours": ts.timeout_hours,
                "expires_at": ts.expires_at.isoformat() if ts.expires_at else None,
            },
        )

        return {
            "status": "ok",
            "transitional_state_id": str(ts.id),
            "technician_id": str(technician.id),
        }

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
