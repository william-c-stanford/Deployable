"""Event type definitions for the Deployable reactive agent system.

Each event type represents a domain signal that can trigger one or more
reactive agent tasks. Events flow through Redis/Celery and are consumed
by registered task handlers.
"""

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid


# ---------------------------------------------------------------------------
# Event Type Enum
# ---------------------------------------------------------------------------

class EventType(str, enum.Enum):
    """All domain event types that trigger reactive agent processing.

    Naming: <ENTITY>_<ACTION> — always past-tense to indicate something
    that has already happened.
    """

    # --- Training & Skill progression ---
    TRAINING_HOURS_LOGGED = "training.hours_logged"
    TRAINING_THRESHOLD_MET = "training.threshold_met"
    PROFICIENCY_ADVANCED = "training.proficiency_advanced"
    TRAINING_COMPLETED = "training.completed"

    # --- Certification lifecycle ---
    CERT_ADDED = "cert.added"
    CERT_RENEWED = "cert.renewed"
    CERT_EXPIRED = "cert.expired"
    CERT_EXPIRING_SOON = "cert.expiring_soon"
    CERT_REVOKED = "cert.revoked"

    # --- Document verification ---
    DOC_UPLOADED = "doc.uploaded"
    DOC_VERIFIED = "doc.verified"
    DOC_REJECTED = "doc.rejected"
    DOC_EXPIRED = "doc.expired"
    ALL_DOCS_VERIFIED = "doc.all_verified"

    # --- Assignment & staffing ---
    ASSIGNMENT_CREATED = "assignment.created"
    ASSIGNMENT_STARTED = "assignment.started"
    ASSIGNMENT_ENDED = "assignment.ended"
    ASSIGNMENT_CANCELLED = "assignment.cancelled"
    TECHNICIAN_ROLLING_OFF = "assignment.rolling_off"

    # --- Partner confirmation ---
    CONFIRMATION_REQUESTED = "confirmation.requested"
    CONFIRMATION_CONFIRMED = "confirmation.confirmed"
    CONFIRMATION_DECLINED = "confirmation.declined"

    # --- Escalation (24-hour window) ---
    CONFIRMATION_ESCALATED = "confirmation.escalated"
    ESCALATION_RESOLVED = "confirmation.escalation_resolved"
    ESCALATION_SCAN_TRIGGERED = "batch.escalation_scan"

    # --- Technician lifecycle ---
    TECHNICIAN_CREATED = "technician.created"
    TECHNICIAN_STATUS_CHANGED = "technician.status_changed"
    TECHNICIAN_AVAILABILITY_CHANGED = "technician.availability_changed"

    # --- Project & staffing requests ---
    PROJECT_CREATED = "project.created"
    PROJECT_STATUS_CHANGED = "project.status_changed"
    HEADCOUNT_REQUESTED = "project.headcount_requested"
    HEADCOUNT_APPROVED = "project.headcount_approved"
    ROLE_UNFILLED = "project.role_unfilled"

    # --- Recommendation lifecycle ---
    RECOMMENDATION_CREATED = "recommendation.created"
    RECOMMENDATION_APPROVED = "recommendation.approved"
    RECOMMENDATION_REJECTED = "recommendation.rejected"
    RECOMMENDATION_DISMISSED = "recommendation.dismissed"

    # --- Preference rules ---
    PREFERENCE_RULE_CREATED = "preference.rule_created"
    PREFERENCE_RULE_UPDATED = "preference.rule_updated"
    PREFERENCE_RULE_DELETED = "preference.rule_deleted"

    # --- Timesheet ---
    TIMESHEET_SUBMITTED = "timesheet.submitted"
    TIMESHEET_APPROVED = "timesheet.approved"
    TIMESHEET_FLAGGED = "timesheet.flagged"
    TIMESHEET_PARTNER_APPROVED = "timesheet.partner_approved"
    TIMESHEET_PARTNER_FLAGGED = "timesheet.partner_flagged"
    TIMESHEET_RESOLVED = "timesheet.resolved"

    # --- Forward staffing ---
    FORWARD_STAFFING_SCAN_TRIGGERED = "forward_staffing.scan_triggered"
    FORWARD_STAFFING_GAP_DETECTED = "forward_staffing.gap_detected"

    # --- Skill breakdown ---
    SKILL_BREAKDOWN_SUBMITTED = "skill_breakdown.submitted"
    SKILL_BREAKDOWN_APPROVED = "skill_breakdown.approved"
    SKILL_BREAKDOWN_REJECTED = "skill_breakdown.rejected"
    SKILL_BREAKDOWN_REVISION_REQUESTED = "skill_breakdown.revision_requested"

    # --- Transitional state lifecycle ---
    TRANSITIONAL_STATE_ENTERED = "transitional.entered"
    TRANSITIONAL_STATE_RESOLVED = "transitional.resolved"
    TRANSITIONAL_STATE_EXPIRED = "transitional.expired"
    TRANSITIONAL_SCAN_TRIGGERED = "batch.transitional_scan"

    # --- Scheduled / batch ---
    NIGHTLY_BATCH_TRIGGERED = "batch.nightly"
    NIGHTLY_READINESS_TRIGGERED = "batch.nightly_readiness"
    CERT_EXPIRY_SCAN_TRIGGERED = "batch.cert_expiry_scan"
    SCORE_REFRESH_TRIGGERED = "batch.score_refresh"


# ---------------------------------------------------------------------------
# Event Category groupings (for routing)
# ---------------------------------------------------------------------------

class EventCategory(str, enum.Enum):
    TRAINING = "training"
    CERTIFICATION = "certification"
    DOCUMENT = "document"
    ASSIGNMENT = "assignment"
    TECHNICIAN = "technician"
    PROJECT = "project"
    RECOMMENDATION = "recommendation"
    PREFERENCE = "preference"
    TIMESHEET = "timesheet"
    FORWARD_STAFFING = "forward_staffing"
    SKILL_BREAKDOWN = "skill_breakdown"
    TRANSITIONAL = "transitional"
    BATCH = "batch"


EVENT_CATEGORY_MAP: dict[EventType, EventCategory] = {
    # Training
    EventType.TRAINING_HOURS_LOGGED: EventCategory.TRAINING,
    EventType.TRAINING_THRESHOLD_MET: EventCategory.TRAINING,
    EventType.PROFICIENCY_ADVANCED: EventCategory.TRAINING,
    EventType.TRAINING_COMPLETED: EventCategory.TRAINING,
    # Certification
    EventType.CERT_ADDED: EventCategory.CERTIFICATION,
    EventType.CERT_RENEWED: EventCategory.CERTIFICATION,
    EventType.CERT_EXPIRED: EventCategory.CERTIFICATION,
    EventType.CERT_EXPIRING_SOON: EventCategory.CERTIFICATION,
    EventType.CERT_REVOKED: EventCategory.CERTIFICATION,
    # Document
    EventType.DOC_UPLOADED: EventCategory.DOCUMENT,
    EventType.DOC_VERIFIED: EventCategory.DOCUMENT,
    EventType.DOC_REJECTED: EventCategory.DOCUMENT,
    EventType.DOC_EXPIRED: EventCategory.DOCUMENT,
    EventType.ALL_DOCS_VERIFIED: EventCategory.DOCUMENT,
    # Assignment
    EventType.ASSIGNMENT_CREATED: EventCategory.ASSIGNMENT,
    EventType.ASSIGNMENT_STARTED: EventCategory.ASSIGNMENT,
    EventType.ASSIGNMENT_ENDED: EventCategory.ASSIGNMENT,
    EventType.ASSIGNMENT_CANCELLED: EventCategory.ASSIGNMENT,
    EventType.TECHNICIAN_ROLLING_OFF: EventCategory.ASSIGNMENT,
    EventType.CONFIRMATION_REQUESTED: EventCategory.ASSIGNMENT,
    EventType.CONFIRMATION_CONFIRMED: EventCategory.ASSIGNMENT,
    EventType.CONFIRMATION_DECLINED: EventCategory.ASSIGNMENT,
    EventType.CONFIRMATION_ESCALATED: EventCategory.ASSIGNMENT,
    EventType.ESCALATION_RESOLVED: EventCategory.ASSIGNMENT,
    EventType.ESCALATION_SCAN_TRIGGERED: EventCategory.BATCH,
    # Technician
    EventType.TECHNICIAN_CREATED: EventCategory.TECHNICIAN,
    EventType.TECHNICIAN_STATUS_CHANGED: EventCategory.TECHNICIAN,
    EventType.TECHNICIAN_AVAILABILITY_CHANGED: EventCategory.TECHNICIAN,
    # Project
    EventType.PROJECT_CREATED: EventCategory.PROJECT,
    EventType.PROJECT_STATUS_CHANGED: EventCategory.PROJECT,
    EventType.HEADCOUNT_REQUESTED: EventCategory.PROJECT,
    EventType.HEADCOUNT_APPROVED: EventCategory.PROJECT,
    EventType.ROLE_UNFILLED: EventCategory.PROJECT,
    # Recommendation
    EventType.RECOMMENDATION_CREATED: EventCategory.RECOMMENDATION,
    EventType.RECOMMENDATION_APPROVED: EventCategory.RECOMMENDATION,
    EventType.RECOMMENDATION_REJECTED: EventCategory.RECOMMENDATION,
    EventType.RECOMMENDATION_DISMISSED: EventCategory.RECOMMENDATION,
    # Preference
    EventType.PREFERENCE_RULE_CREATED: EventCategory.PREFERENCE,
    EventType.PREFERENCE_RULE_UPDATED: EventCategory.PREFERENCE,
    EventType.PREFERENCE_RULE_DELETED: EventCategory.PREFERENCE,
    # Timesheet
    EventType.TIMESHEET_SUBMITTED: EventCategory.TIMESHEET,
    EventType.TIMESHEET_APPROVED: EventCategory.TIMESHEET,
    EventType.TIMESHEET_FLAGGED: EventCategory.TIMESHEET,
    EventType.TIMESHEET_PARTNER_APPROVED: EventCategory.TIMESHEET,
    EventType.TIMESHEET_PARTNER_FLAGGED: EventCategory.TIMESHEET,
    EventType.TIMESHEET_RESOLVED: EventCategory.TIMESHEET,
    # Forward Staffing
    EventType.FORWARD_STAFFING_SCAN_TRIGGERED: EventCategory.FORWARD_STAFFING,
    EventType.FORWARD_STAFFING_GAP_DETECTED: EventCategory.FORWARD_STAFFING,
    # Skill Breakdown
    EventType.SKILL_BREAKDOWN_SUBMITTED: EventCategory.SKILL_BREAKDOWN,
    EventType.SKILL_BREAKDOWN_APPROVED: EventCategory.SKILL_BREAKDOWN,
    EventType.SKILL_BREAKDOWN_REJECTED: EventCategory.SKILL_BREAKDOWN,
    EventType.SKILL_BREAKDOWN_REVISION_REQUESTED: EventCategory.SKILL_BREAKDOWN,
    # Transitional state
    EventType.TRANSITIONAL_STATE_ENTERED: EventCategory.TRANSITIONAL,
    EventType.TRANSITIONAL_STATE_RESOLVED: EventCategory.TRANSITIONAL,
    EventType.TRANSITIONAL_STATE_EXPIRED: EventCategory.TRANSITIONAL,
    EventType.TRANSITIONAL_SCAN_TRIGGERED: EventCategory.BATCH,
    # Batch
    EventType.NIGHTLY_BATCH_TRIGGERED: EventCategory.BATCH,
    EventType.NIGHTLY_READINESS_TRIGGERED: EventCategory.BATCH,
    EventType.CERT_EXPIRY_SCAN_TRIGGERED: EventCategory.BATCH,
    EventType.SCORE_REFRESH_TRIGGERED: EventCategory.BATCH,
}


# ---------------------------------------------------------------------------
# Event Payload
# ---------------------------------------------------------------------------

@dataclass
class EventPayload:
    """Structured payload for domain events dispatched through the system.

    Attributes:
        event_type: The specific event that occurred.
        entity_type: Kind of entity (technician, project, assignment, etc.).
        entity_id: Primary key of the affected entity.
        actor_id: Who/what triggered the event (user id or 'system').
        data: Arbitrary event-specific data dict.
        correlation_id: Unique id for tracing cascading events.
        timestamp: When the event was created.
    """

    event_type: EventType
    entity_type: str
    entity_id: str
    actor_id: str = "system"
    data: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for Celery task kwargs / JSON transport."""
        return {
            "event_type": self.event_type.value,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "actor_id": self.actor_id,
            "data": self.data,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EventPayload":
        """Deserialize from Celery task kwargs / JSON transport."""
        return cls(
            event_type=EventType(d["event_type"]),
            entity_type=d["entity_type"],
            entity_id=d["entity_id"],
            actor_id=d.get("actor_id", "system"),
            data=d.get("data", {}),
            correlation_id=d.get("correlation_id", str(uuid.uuid4())),
            timestamp=d.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )
