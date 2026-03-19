"""Background recommendation agent tasks — event-driven recommendation generation.

This module provides the event signal registry and Celery task skeletons for
the autonomous background recommendation agents. Each task handles a category
of domain events and produces recommendation dicts that can be surfaced in
the Agent Inbox or pushed to ops via WebSocket.

Event Signal Categories:
  - training_completion: Training milestones that may unlock new assignments
  - cert_update: Certification changes affecting deployability
  - doc_verification: Document verification status changes
  - assignment_change: Assignment lifecycle events affecting availability

These tasks are triggered by the dispatcher when relevant domain events fire.
They read state via the FastAPI REST API layer (through DB sessions) and
produce Recommendation records — never mutating technician state directly.
"""

import enum
import logging
from datetime import datetime, timezone
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal


logger = logging.getLogger("deployable.workers.recommendation_tasks")


# ---------------------------------------------------------------------------
# Event Signal Registry — maps recommendation categories to triggering events
# ---------------------------------------------------------------------------

class RecommendationSignalCategory(str, enum.Enum):
    """Categories of domain signals that trigger background recommendations."""
    TRAINING_COMPLETION = "training_completion"
    CERT_UPDATE = "cert_update"
    DOC_VERIFICATION = "doc_verification"
    ASSIGNMENT_CHANGE = "assignment_change"


# Maps each recommendation signal category to the EventTypes that trigger it
RECOMMENDATION_SIGNAL_REGISTRY: dict[RecommendationSignalCategory, list[EventType]] = {
    RecommendationSignalCategory.TRAINING_COMPLETION: [
        EventType.TRAINING_COMPLETED,
        EventType.PROFICIENCY_ADVANCED,
        EventType.TRAINING_THRESHOLD_MET,
        EventType.TRAINING_HOURS_LOGGED,
    ],
    RecommendationSignalCategory.CERT_UPDATE: [
        EventType.CERT_ADDED,
        EventType.CERT_RENEWED,
        EventType.CERT_EXPIRED,
        EventType.CERT_EXPIRING_SOON,
        EventType.CERT_REVOKED,
    ],
    RecommendationSignalCategory.DOC_VERIFICATION: [
        EventType.DOC_UPLOADED,
        EventType.DOC_VERIFIED,
        EventType.DOC_REJECTED,
        EventType.DOC_EXPIRED,
        EventType.ALL_DOCS_VERIFIED,
    ],
    RecommendationSignalCategory.ASSIGNMENT_CHANGE: [
        EventType.ASSIGNMENT_CREATED,
        EventType.ASSIGNMENT_STARTED,
        EventType.ASSIGNMENT_ENDED,
        EventType.ASSIGNMENT_CANCELLED,
        EventType.TECHNICIAN_ROLLING_OFF,
    ],
}

# Reverse lookup: EventType -> RecommendationSignalCategory
EVENT_TO_SIGNAL_CATEGORY: dict[EventType, RecommendationSignalCategory] = {
    event_type: category
    for category, event_types in RECOMMENDATION_SIGNAL_REGISTRY.items()
    for event_type in event_types
}


def _enum_val(v: Any) -> str:
    """Safely extract .value from an enum, or return str."""
    return v.value if hasattr(v, "value") else str(v) if v else ""


def _build_placeholder_recommendation(
    category: RecommendationSignalCategory,
    payload: EventPayload,
    recommendation_type: str,
    summary: str,
) -> dict[str, Any]:
    """Build a placeholder recommendation dict for stub tasks.

    Returns a dict matching the Recommendation model's expected shape,
    to be persisted once the full scoring/LLM logic is wired up.
    """
    return {
        "recommendation_type": recommendation_type,
        "signal_category": category.value,
        "target_entity_type": payload.entity_type,
        "target_entity_id": payload.entity_id,
        "event_type": payload.event_type.value,
        "scorecard": {
            "signal_category": category.value,
            "triggering_event": payload.event_type.value,
            "entity_type": payload.entity_type,
            "entity_id": payload.entity_id,
        },
        "explanation": summary,
        "status": "pending",
        "agent_name": f"{category.value}_recommendation_agent",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Task: Handle training completion signals → staffing recommendations
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation_tasks.handle_training_completion",
)
def handle_training_completion(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Generate recommendations when training milestones are reached.

    Triggered by: TRAINING_COMPLETED, PROFICIENCY_ADVANCED,
                  TRAINING_THRESHOLD_MET, TRAINING_HOURS_LOGGED

    When a technician completes training or advances proficiency, this task
    evaluates whether new staffing opportunities have opened up and produces
    recommendation candidates for ops review.

    Returns:
        Placeholder recommendation dict with signal metadata.
    """
    payload = EventPayload.from_dict(event_dict)
    category = RecommendationSignalCategory.TRAINING_COMPLETION

    logger.info(
        "Processing training completion signal: %s for %s/%s",
        payload.event_type.value,
        payload.entity_type,
        payload.entity_id,
    )

    tech_id = payload.data.get("technician_id", payload.entity_id)
    skill_name = payload.data.get("skill_name", "unknown")
    new_level = payload.data.get("new_level", "unknown")

    recommendation = _build_placeholder_recommendation(
        category=category,
        payload=payload,
        recommendation_type="training_milestone",
        summary=(
            f"Training signal received for technician {tech_id}: "
            f"{payload.event_type.value}. "
            f"Skill '{skill_name}' reached level '{new_level}'. "
            f"Evaluate for new staffing eligibility."
        ),
    )
    recommendation["data"] = {
        "technician_id": str(tech_id),
        "skill_name": skill_name,
        "new_level": new_level,
        "signal_event": payload.event_type.value,
    }

    return {
        "status": "recommendation_generated",
        "signal_category": category.value,
        "event_type": payload.event_type.value,
        "technician_id": str(tech_id),
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Task: Handle certification update signals → cert-related recommendations
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation_tasks.handle_cert_update",
)
def handle_cert_update(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Generate recommendations when certification status changes.

    Triggered by: CERT_ADDED, CERT_RENEWED, CERT_EXPIRED,
                  CERT_EXPIRING_SOON, CERT_REVOKED

    When a certification is added, renewed, or expires, this task evaluates
    the impact on the technician's deployability and generates appropriate
    recommendations (e.g., renewal reminders, newly-eligible roles).

    Returns:
        Placeholder recommendation dict with signal metadata.
    """
    payload = EventPayload.from_dict(event_dict)
    category = RecommendationSignalCategory.CERT_UPDATE

    logger.info(
        "Processing cert update signal: %s for %s/%s",
        payload.event_type.value,
        payload.entity_type,
        payload.entity_id,
    )

    tech_id = payload.data.get("technician_id", payload.entity_id)
    cert_name = payload.data.get("cert_name", "unknown")
    cert_status = payload.data.get("status", "unknown")

    # Determine recommendation type based on event
    if payload.event_type in (EventType.CERT_EXPIRED, EventType.CERT_REVOKED):
        rec_type = "cert_renewal_urgent"
        summary = (
            f"URGENT: Certification '{cert_name}' for technician {tech_id} "
            f"has {payload.event_type.value}. "
            f"Schedule renewal to restore deployment eligibility."
        )
    elif payload.event_type == EventType.CERT_EXPIRING_SOON:
        rec_type = "cert_renewal_proactive"
        days = payload.data.get("days_until_expiry", "unknown")
        summary = (
            f"Certification '{cert_name}' for technician {tech_id} "
            f"expires in {days} days. Schedule proactive renewal."
        )
    else:
        rec_type = "cert_eligibility_update"
        summary = (
            f"Certification '{cert_name}' {payload.event_type.value} "
            f"for technician {tech_id}. "
            f"Re-evaluate staffing eligibility for cert-gated roles."
        )

    recommendation = _build_placeholder_recommendation(
        category=category,
        payload=payload,
        recommendation_type=rec_type,
        summary=summary,
    )
    recommendation["data"] = {
        "technician_id": str(tech_id),
        "cert_name": cert_name,
        "cert_status": cert_status,
        "signal_event": payload.event_type.value,
    }

    return {
        "status": "recommendation_generated",
        "signal_category": category.value,
        "event_type": payload.event_type.value,
        "technician_id": str(tech_id),
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Task: Handle document verification signals → doc-related recommendations
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation_tasks.handle_doc_verification",
)
def handle_doc_verification(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Generate recommendations when document verification status changes.

    Triggered by: DOC_UPLOADED, DOC_VERIFIED, DOC_REJECTED,
                  DOC_EXPIRED, ALL_DOCS_VERIFIED

    When documents are verified or rejected, this task evaluates the impact
    on the technician's readiness and generates recommendations such as
    "all docs complete — ready for assignment" or "doc resubmission needed".

    Returns:
        Placeholder recommendation dict with signal metadata.
    """
    payload = EventPayload.from_dict(event_dict)
    category = RecommendationSignalCategory.DOC_VERIFICATION

    logger.info(
        "Processing doc verification signal: %s for %s/%s",
        payload.event_type.value,
        payload.entity_type,
        payload.entity_id,
    )

    tech_id = payload.data.get("technician_id", payload.entity_id)
    doc_type = payload.data.get("doc_type", "unknown")

    if payload.event_type == EventType.ALL_DOCS_VERIFIED:
        rec_type = "docs_complete"
        summary = (
            f"All required documents verified for technician {tech_id}. "
            f"Technician may now be eligible for deployment. "
            f"Re-evaluate staffing recommendations."
        )
    elif payload.event_type in (EventType.DOC_REJECTED, EventType.DOC_EXPIRED):
        rec_type = "doc_action_required"
        summary = (
            f"Document '{doc_type}' for technician {tech_id} "
            f"has been {payload.event_type.value}. "
            f"Follow up required to restore deployment eligibility."
        )
    else:
        rec_type = "doc_progress_update"
        summary = (
            f"Document '{doc_type}' {payload.event_type.value} "
            f"for technician {tech_id}. "
            f"Check document completeness status."
        )

    recommendation = _build_placeholder_recommendation(
        category=category,
        payload=payload,
        recommendation_type=rec_type,
        summary=summary,
    )
    recommendation["data"] = {
        "technician_id": str(tech_id),
        "doc_type": doc_type,
        "signal_event": payload.event_type.value,
    }

    return {
        "status": "recommendation_generated",
        "signal_category": category.value,
        "event_type": payload.event_type.value,
        "technician_id": str(tech_id),
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Task: Handle assignment change signals → availability recommendations
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.recommendation_tasks.handle_assignment_change",
)
def handle_assignment_change(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Generate recommendations when assignment status changes.

    Triggered by: ASSIGNMENT_CREATED, ASSIGNMENT_STARTED, ASSIGNMENT_ENDED,
                  ASSIGNMENT_CANCELLED, TECHNICIAN_ROLLING_OFF

    When assignments change, this task evaluates the impact on staffing
    pipelines and generates recommendations such as backfill needs,
    availability updates, or pre-booking suggestions.

    Returns:
        Placeholder recommendation dict with signal metadata.
    """
    payload = EventPayload.from_dict(event_dict)
    category = RecommendationSignalCategory.ASSIGNMENT_CHANGE

    logger.info(
        "Processing assignment change signal: %s for %s/%s",
        payload.event_type.value,
        payload.entity_type,
        payload.entity_id,
    )

    tech_id = payload.data.get("technician_id", payload.entity_id)
    role_id = payload.data.get("role_id")
    assignment_id = payload.entity_id

    if payload.event_type in (EventType.ASSIGNMENT_ENDED, EventType.ASSIGNMENT_CANCELLED):
        rec_type = "backfill_opportunity"
        summary = (
            f"Assignment {assignment_id} {payload.event_type.value}. "
            f"Technician {tech_id} may be available for re-assignment. "
            f"Check for backfill needs on the vacated role."
        )
    elif payload.event_type == EventType.TECHNICIAN_ROLLING_OFF:
        roll_off_date = payload.data.get("roll_off_date", "unknown")
        rec_type = "pre_booking"
        summary = (
            f"Technician {tech_id} is rolling off on {roll_off_date}. "
            f"Pre-book next assignment to minimize bench time."
        )
    elif payload.event_type == EventType.ASSIGNMENT_CREATED:
        rec_type = "assignment_impact"
        summary = (
            f"New assignment created for technician {tech_id}. "
            f"Update availability and refresh pending recommendations."
        )
    else:
        rec_type = "assignment_status_update"
        summary = (
            f"Assignment {assignment_id} {payload.event_type.value}. "
            f"Evaluate impact on staffing pipeline."
        )

    recommendation = _build_placeholder_recommendation(
        category=category,
        payload=payload,
        recommendation_type=rec_type,
        summary=summary,
    )
    recommendation["data"] = {
        "technician_id": str(tech_id),
        "assignment_id": str(assignment_id),
        "role_id": str(role_id) if role_id else None,
        "signal_event": payload.event_type.value,
    }

    return {
        "status": "recommendation_generated",
        "signal_category": category.value,
        "event_type": payload.event_type.value,
        "technician_id": str(tech_id),
        "recommendation": recommendation,
    }
