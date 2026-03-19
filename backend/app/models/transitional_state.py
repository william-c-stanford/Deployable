"""TransitionalState model — tracks auto-resolving transitional statuses.

Transitional states (Onboarding, PendingReview, Suspended) are temporary
deployability statuses that auto-resolve to a computed status when:
- A timeout expires (time-based resolution)
- A specific domain event fires (event-based resolution)
- Conditions are met (condition-based resolution, checked periodically)
- Ops manually resolves them

Each transitional state record holds the metadata needed for the resolution
engine (Celery periodic task) to determine when and how to resolve.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    Float,
    Integer,
    DateTime,
    ForeignKey,
    Enum,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.technician import (
    DeployabilityStatus,
    TransitionalTrigger,
    TransitionalResolutionType,
)


class TransitionalState(Base):
    """Tracks an active or resolved transitional deployability status.

    When a technician enters a transitional status (Onboarding, PendingReview,
    Suspended), a TransitionalState record is created to track:
    - What triggered the transitional state
    - How it should be resolved (timeout, event, condition, manual)
    - The fallback status to apply on resolution
    - Resolution metadata (timeout hours, required events, conditions)

    The Celery periodic task `resolve_transitional_states` checks all active
    records and resolves those whose conditions are met or timeout has expired.
    """

    __tablename__ = "transitional_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True),
        ForeignKey("technicians.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Which transitional status was entered
    transitional_status = Column(
        Enum(DeployabilityStatus, name="deployability_status_enum", create_type=False),
        nullable=False,
    )

    # What was the status before entering the transitional state
    previous_status = Column(
        Enum(DeployabilityStatus, name="deployability_status_enum", create_type=False),
        nullable=True,
    )

    # What triggered entering this transitional state
    trigger = Column(
        Enum(TransitionalTrigger, name="transitional_trigger_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=TransitionalTrigger.MANUAL,
    )
    trigger_detail = Column(Text, nullable=True, comment="Human-readable trigger context")

    # Resolution configuration
    resolution_type = Column(
        Enum(TransitionalResolutionType, name="transitional_resolution_type_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=TransitionalResolutionType.TIMEOUT,
    )

    # Timeout-based resolution
    timeout_hours = Column(
        Float,
        nullable=True,
        comment="Hours after entered_at when this auto-resolves (for TIMEOUT type)",
    )

    # Event-based resolution: list of event type values that will resolve this
    resolution_events = Column(
        JSON,
        nullable=True,
        comment='List of event type strings that resolve this state, e.g. ["cert.added", "doc.verified"]',
    )

    # Condition-based resolution: JSON conditions to check
    resolution_conditions = Column(
        JSON,
        nullable=True,
        comment="Conditions dict checked by the resolution engine, e.g. {\"all_docs_verified\": true, \"min_certs\": 2}",
    )

    # The status to transition to upon resolution. If null, the readiness
    # evaluator will compute the appropriate status.
    fallback_status = Column(
        Enum(DeployabilityStatus, name="deployability_status_enum", create_type=False),
        nullable=True,
        comment="Status to set on resolution. Null = compute via readiness evaluator.",
    )

    # Lifecycle tracking
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    entered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(
        DateTime,
        nullable=True,
        index=True,
        comment="Computed from entered_at + timeout_hours for quick queries",
    )
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(
        String(200),
        nullable=True,
        comment="User ID or 'system' if auto-resolved",
    )
    resolution_reason = Column(Text, nullable=True)
    resolution_event_type = Column(
        String(100),
        nullable=True,
        comment="Which event triggered resolution (for event-based)",
    )

    # The final status that was applied on resolution
    resolved_to_status = Column(
        Enum(DeployabilityStatus, name="deployability_status_enum", create_type=False),
        nullable=True,
    )

    # Ops notes
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship
    technician = relationship("Technician", backref="transitional_states")

    def __repr__(self):
        return (
            f"<TransitionalState tech={self.technician_id} "
            f"status={self.transitional_status} active={self.is_active}>"
        )

    @property
    def is_expired(self) -> bool:
        """Check if this transitional state has exceeded its timeout."""
        if not self.expires_at:
            return False
        return datetime.utcnow() >= self.expires_at
