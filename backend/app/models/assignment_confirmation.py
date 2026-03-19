"""AssignmentConfirmation model — partner confirmation of assignment dates.

Includes 24-hour escalation window logic: if a partner does not respond
within 24 hours, the confirmation is escalated to the Project Staffing Page
for ops intervention.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Date, DateTime, Boolean, ForeignKey, Enum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ConfirmationStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    ESCALATED = "escalated"


class ConfirmationType(str, enum.Enum):
    START_DATE = "start_date"
    END_DATE = "end_date"


class EscalationStatus(str, enum.Enum):
    """Tracks the lifecycle of an escalated confirmation."""
    NONE = "none"                        # Not escalated
    ESCALATED = "escalated"              # 24h window expired, escalated to staffing page
    OPS_REVIEWING = "ops_reviewing"      # Ops has acknowledged and is reviewing
    RESOLVED_CONFIRMED = "resolved_confirmed"    # Ops resolved by confirming
    RESOLVED_REASSIGNED = "resolved_reassigned"  # Ops resolved by reassigning
    RESOLVED_CANCELLED = "resolved_cancelled"    # Ops resolved by cancelling


class AssignmentConfirmation(Base):
    """Tracks partner confirmation of assignment start/end dates.

    Each row represents a single confirmation request sent to a partner
    for either the start or end date of an assignment. The partner can
    confirm or decline, optionally providing a reason and a proposed
    alternative date.

    After 24 hours without a response, the confirmation is automatically
    escalated to the Project Staffing Page for ops intervention.
    """

    __tablename__ = "assignment_confirmations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assignments.id"),
        nullable=False,
        index=True,
    )
    partner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("partners.id"),
        nullable=False,
        index=True,
    )
    confirmation_type = Column(
        Enum(ConfirmationType, name="confirmation_type_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    status = Column(
        Enum(ConfirmationStatus, name="confirmation_status_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ConfirmationStatus.PENDING,
    )
    requested_date = Column(Date, nullable=False, doc="The date being confirmed")
    proposed_date = Column(Date, nullable=True, doc="Partner's counter-proposed date if declined")
    response_note = Column(Text, nullable=True, doc="Partner note on confirm/decline")
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    responded_at = Column(DateTime, nullable=True)

    # --- Escalation tracking fields ---
    escalated = Column(Boolean, default=False, nullable=False, doc="Whether this has been escalated")
    escalated_at = Column(DateTime, nullable=True, doc="When the 24h window expired and escalation triggered")
    escalation_status = Column(
        Enum(EscalationStatus, name="escalation_status_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=EscalationStatus.NONE,
    )
    escalation_resolved_at = Column(DateTime, nullable=True, doc="When ops resolved the escalation")
    escalation_resolved_by = Column(String(200), nullable=True, doc="User who resolved the escalation")
    escalation_resolution_note = Column(Text, nullable=True, doc="Ops note on how escalation was resolved")

    # Relationships
    assignment = relationship("Assignment", backref="confirmations")
    partner = relationship("Partner", backref="assignment_confirmations")

    @property
    def is_overdue(self) -> bool:
        """Check if this confirmation has exceeded the 24-hour window."""
        if self.status != ConfirmationStatus.PENDING:
            return False
        if self.requested_at is None:
            return False
        elapsed = (datetime.utcnow() - self.requested_at).total_seconds()
        return elapsed >= 24 * 3600  # 24 hours in seconds

    @property
    def hours_waiting(self) -> float:
        """Hours since confirmation was requested."""
        if self.requested_at is None:
            return 0.0
        elapsed = (datetime.utcnow() - self.requested_at).total_seconds()
        return round(elapsed / 3600, 1)
