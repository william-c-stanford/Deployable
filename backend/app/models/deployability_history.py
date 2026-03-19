"""Deployability status history model for tracking all status changes over time."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum, Float, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class StatusChangeSource(str, enum.Enum):
    """How the status change was initiated."""
    AUTO_COMPUTED = "auto_computed"      # Readiness engine auto-computed
    MANUAL_OVERRIDE = "manual_override"  # Ops user manually set status
    TRAINING_ADVANCEMENT = "training_advancement"  # Deterministic training gate
    EVENT_TRIGGERED = "event_triggered"  # Reactive event (cert expired, etc.)
    BATCH_REFRESH = "batch_refresh"      # Nightly batch re-evaluation
    SYSTEM = "system"                    # System initialization


class DeployabilityStatusHistory(Base):
    """Immutable audit trail of every deployability status change for a technician."""

    __tablename__ = "deployability_status_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True),
        ForeignKey("technicians.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    old_status = Column(String(100), nullable=True)
    new_status = Column(String(100), nullable=False)
    source = Column(
        Enum(StatusChangeSource, name="status_change_source_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=StatusChangeSource.SYSTEM,
    )
    reason = Column(Text, nullable=True)
    actor_id = Column(String(200), nullable=True, comment="User ID who initiated the change")
    actor_name = Column(String(200), nullable=True)
    readiness_score_at_change = Column(Float, nullable=True)
    dimension_scores = Column(JSON, nullable=True, comment="Snapshot of dimension scores at time of change")
    extra_metadata = Column("metadata", JSON, nullable=True, comment="Additional context about the change")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationship
    technician = relationship("Technician", backref="status_history")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "technician_id": str(self.technician_id),
            "old_status": self.old_status,
            "new_status": self.new_status,
            "source": self.source.value if hasattr(self.source, 'value') else self.source,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "readiness_score_at_change": self.readiness_score_at_change,
            "dimension_scores": self.dimension_scores,
            "metadata": self.extra_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
