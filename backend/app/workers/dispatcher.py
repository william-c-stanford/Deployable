"""Central event dispatcher for the Deployable reactive agent system.

This module provides the `dispatch_event` function that routes domain events
to the appropriate Celery task(s) based on event type. It is the single entry
point for all event emission from API endpoints and SQLAlchemy hooks.

Usage in API endpoints:
    from app.workers.dispatcher import dispatch_event
    from app.workers.events import EventPayload, EventType

    dispatch_event(EventPayload(
        event_type=EventType.CERT_ADDED,
        entity_type="technician_certification",
        entity_id=str(cert.id),
        actor_id=str(current_user.id),
        data={"technician_id": str(tech_id), "cert_name": cert.cert_name},
    ))
"""

import logging
from typing import Optional

from app.workers.events import EventPayload, EventType, EventCategory, EVENT_CATEGORY_MAP

logger = logging.getLogger("deployable.dispatcher")


# ---------------------------------------------------------------------------
# Task name registry: maps EventType -> Celery task name(s) to invoke
# ---------------------------------------------------------------------------

EVENT_TASK_ROUTING: dict[EventType, list[str]] = {
    # --- Training events ---
    EventType.TRAINING_HOURS_LOGGED: [
        "app.workers.tasks.training.check_proficiency_advancement",
        "app.workers.tasks.training.update_deployability_for_training",
    ],
    EventType.TRAINING_THRESHOLD_MET: [
        "app.workers.tasks.training.advance_proficiency",
    ],
    EventType.PROFICIENCY_ADVANCED: [
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],
    EventType.TRAINING_COMPLETED: [
        "app.workers.tasks.training.update_career_stage_training_complete",
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
        "app.workers.tasks.transitional.check_event_resolution",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],

    # --- Certification events ---
    EventType.CERT_ADDED: [
        "app.workers.tasks.certification.recalc_deployability_for_cert",
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
        "app.workers.tasks.transitional.check_event_resolution",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],
    EventType.CERT_RENEWED: [
        "app.workers.tasks.certification.recalc_deployability_for_cert",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
        "app.workers.tasks.transitional.check_event_resolution",
    ],
    EventType.CERT_EXPIRED: [
        "app.workers.tasks.certification.handle_cert_expiry",
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],
    EventType.CERT_EXPIRING_SOON: [
        "app.workers.tasks.certification.create_cert_renewal_alert",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],
    EventType.CERT_REVOKED: [
        "app.workers.tasks.certification.handle_cert_expiry",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
    ],

    # --- Document events ---
    EventType.DOC_UPLOADED: [
        "app.workers.tasks.document.check_doc_completeness",
    ],
    EventType.DOC_VERIFIED: [
        "app.workers.tasks.document.check_doc_completeness",
        "app.workers.tasks.transitional.check_event_resolution",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],
    EventType.DOC_REJECTED: [
        "app.workers.tasks.document.handle_doc_rejection",
    ],
    EventType.DOC_EXPIRED: [
        "app.workers.tasks.document.handle_doc_expiry",
    ],
    EventType.ALL_DOCS_VERIFIED: [
        "app.workers.tasks.document.update_deployability_docs_complete",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
        "app.workers.tasks.transitional.check_event_resolution",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],

    # --- Assignment events ---
    EventType.ASSIGNMENT_CREATED: [
        "app.workers.tasks.assignment.update_tech_status_for_assignment",
        "app.workers.tasks.assignment.notify_assignment_created",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],
    EventType.ASSIGNMENT_STARTED: [
        "app.workers.tasks.assignment.update_tech_status_for_assignment",
    ],
    EventType.ASSIGNMENT_ENDED: [
        "app.workers.tasks.assignment.handle_assignment_end",
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
        "app.workers.tasks.forward_staffing.refresh_forward_recommendations",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],
    EventType.ASSIGNMENT_CANCELLED: [
        "app.workers.tasks.assignment.handle_assignment_cancellation",
        "app.workers.tasks.forward_staffing.refresh_forward_recommendations",
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
    ],
    EventType.TECHNICIAN_ROLLING_OFF: [
        "app.workers.tasks.assignment.create_rolling_off_alert",
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
    ],

    # --- Escalation events ---
    EventType.CONFIRMATION_ESCALATED: [
        "app.workers.tasks.escalation.handle_escalation_triggered",
    ],
    EventType.ESCALATION_RESOLVED: [
        "app.workers.tasks.escalation.handle_escalation_resolved",
    ],
    EventType.ESCALATION_SCAN_TRIGGERED: [
        "app.workers.tasks.escalation.scan_overdue_confirmations",
    ],

    # --- Technician lifecycle ---
    EventType.TECHNICIAN_CREATED: [
        "app.workers.tasks.training.initialize_training_plan",
    ],
    EventType.TECHNICIAN_STATUS_CHANGED: [
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
        "app.workers.tasks.next_step.refresh_technician_next_steps",
    ],
    EventType.TECHNICIAN_AVAILABILITY_CHANGED: [
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
    ],

    # --- Project events ---
    EventType.PROJECT_CREATED: [],
    EventType.PROJECT_STATUS_CHANGED: [
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
    ],
    EventType.HEADCOUNT_REQUESTED: [
        "app.workers.tasks.recommendation.generate_staffing_recommendations",
    ],
    EventType.HEADCOUNT_APPROVED: [
        "app.workers.tasks.headcount.process_approved_headcount",
    ],
    EventType.ROLE_UNFILLED: [
        "app.workers.tasks.recommendation.generate_staffing_recommendations",
    ],

    # --- Recommendation lifecycle ---
    EventType.RECOMMENDATION_CREATED: [],
    EventType.RECOMMENDATION_APPROVED: [
        "app.workers.tasks.recommendation.execute_approved_recommendation",
    ],
    EventType.RECOMMENDATION_REJECTED: [
        "app.workers.tasks.recommendation.handle_rejection_feedback",
    ],
    EventType.RECOMMENDATION_DISMISSED: [],

    # --- Preference rules ---
    EventType.PREFERENCE_RULE_CREATED: [
        "app.workers.tasks.recommendation.reeval_recommendations_for_rule",
    ],
    EventType.PREFERENCE_RULE_UPDATED: [
        "app.workers.tasks.recommendation.reeval_recommendations_for_rule",
    ],
    EventType.PREFERENCE_RULE_DELETED: [
        "app.workers.tasks.recommendation.reeval_recommendations_for_rule",
    ],

    # --- Timesheet ---
    EventType.TIMESHEET_SUBMITTED: [
        "app.workers.tasks.training.check_proficiency_advancement",
    ],
    EventType.TIMESHEET_APPROVED: [
        "app.workers.tasks.training.process_approved_timesheet",
        "app.workers.tasks.training.check_proficiency_advancement",
    ],
    EventType.TIMESHEET_FLAGGED: [],
    EventType.TIMESHEET_PARTNER_APPROVED: [
        "app.workers.tasks.training.process_approved_timesheet",
        "app.workers.tasks.training.check_proficiency_advancement",
    ],
    EventType.TIMESHEET_PARTNER_FLAGGED: [],
    EventType.TIMESHEET_RESOLVED: [
        "app.workers.tasks.training.process_approved_timesheet",
        "app.workers.tasks.training.check_proficiency_advancement",
    ],

    # --- Forward Staffing ---
    EventType.FORWARD_STAFFING_SCAN_TRIGGERED: [
        "app.workers.tasks.forward_staffing.forward_staffing_scan",
    ],
    EventType.FORWARD_STAFFING_GAP_DETECTED: [
        "app.workers.tasks.forward_staffing.forward_staffing_scan",
    ],

    # --- Skill Breakdown ---
    EventType.SKILL_BREAKDOWN_SUBMITTED: [],
    EventType.SKILL_BREAKDOWN_APPROVED: [],
    EventType.SKILL_BREAKDOWN_REJECTED: [],
    EventType.SKILL_BREAKDOWN_REVISION_REQUESTED: [],

    # --- Transitional state lifecycle ---
    EventType.TRANSITIONAL_STATE_ENTERED: [
        "app.workers.tasks.transitional.enter_transitional_state",
    ],
    EventType.TRANSITIONAL_STATE_RESOLVED: [
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
        "app.workers.tasks.recommendation.refresh_affected_recommendations",
    ],
    EventType.TRANSITIONAL_STATE_EXPIRED: [
        "app.workers.tasks.readiness.reevaluate_technician_readiness",
    ],
    EventType.TRANSITIONAL_SCAN_TRIGGERED: [
        "app.workers.tasks.transitional.scan_transitional_states",
    ],

    # --- Batch ---
    EventType.NIGHTLY_BATCH_TRIGGERED: [
        "app.workers.tasks.batch.nightly_batch",
    ],
    EventType.NIGHTLY_READINESS_TRIGGERED: [
        "app.workers.tasks.readiness.batch_reevaluate_readiness",
    ],
    EventType.CERT_EXPIRY_SCAN_TRIGGERED: [
        "app.workers.tasks.batch.cert_expiry_scan",
    ],
    EventType.SCORE_REFRESH_TRIGGERED: [
        "app.workers.tasks.batch.score_refresh",
        "app.workers.tasks.readiness.batch_reevaluate_readiness",
    ],
}


def dispatch_event(
    payload: EventPayload,
    *,
    countdown: Optional[int] = None,
    queue: Optional[str] = None,
) -> list[str]:
    """Dispatch a domain event to the appropriate Celery task(s).

    Args:
        payload: The event payload to dispatch.
        countdown: Optional delay in seconds before task execution.
        queue: Override the default queue for this dispatch.

    Returns:
        List of Celery AsyncResult IDs for dispatched tasks.
    """
    from app.workers.celery_app import celery_app

    event_dict = payload.to_dict()
    task_names = EVENT_TASK_ROUTING.get(payload.event_type, [])

    if not task_names:
        logger.warning(
            "No tasks registered for event type %s — event will be logged but not processed",
            payload.event_type.value,
        )
        return []

    # Determine queue from category if not overridden
    category = EVENT_CATEGORY_MAP.get(payload.event_type)
    default_queue = category.value if category else "default"

    result_ids = []
    for task_name in task_names:
        try:
            result = celery_app.send_task(
                task_name,
                args=[event_dict],
                countdown=countdown,
                queue=queue or default_queue,
            )
            result_ids.append(result.id)
            logger.info(
                "Dispatched %s -> %s [task_id=%s, queue=%s, correlation=%s]",
                payload.event_type.value,
                task_name,
                result.id,
                queue or default_queue,
                payload.correlation_id,
            )
        except Exception:
            logger.exception(
                "Failed to dispatch %s -> %s",
                payload.event_type.value,
                task_name,
            )

    return result_ids


def dispatch_event_safe(
    payload: EventPayload,
    *,
    countdown: Optional[int] = None,
    queue: Optional[str] = None,
) -> list[str]:
    """Like dispatch_event but swallows all exceptions.

    Use this in API endpoints where event dispatch failure should NOT
    cause the request to fail. The primary DB mutation has already
    succeeded; the event is best-effort.
    """
    try:
        return dispatch_event(payload, countdown=countdown, queue=queue)
    except Exception:
        logger.exception(
            "dispatch_event_safe swallowed error for %s",
            payload.event_type.value,
        )
        return []
