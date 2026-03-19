"""Assignment model — links technicians to project roles.

Supports forward staffing (90-day lookahead) with pre-booking status
and assignment chaining (back-to-back project sequencing).
"""

import enum
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column, String, Float, Boolean, Date, DateTime, ForeignKey, Enum,
    Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class AssignmentType(str, enum.Enum):
    ACTIVE = "Active"
    PRE_BOOKED = "Pre-Booked"


class AssignmentStatus(str, enum.Enum):
    ACTIVE = "Active"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    PRE_BOOKED = "Pre-Booked"
    PENDING_CONFIRMATION = "Pending Confirmation"


class ChainPriority(str, enum.Enum):
    """Priority level for chained assignments in forward staffing."""
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class Assignment(Base):
    """A technician assigned to a specific project role.

    Supports forward staffing via pre-booking and assignment chaining.
    - Pre-booked assignments have future start dates and require confirmation.
    - Chained assignments link sequentially so a technician rolls from one
      project directly into the next with minimal downtime.
    """

    __tablename__ = "assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id"), nullable=False, index=True,
    )
    role_id = Column(
        UUID(as_uuid=True), ForeignKey("project_roles.id"), nullable=False, index=True,
    )
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    hourly_rate = Column(Float, nullable=True)
    per_diem = Column(Float, nullable=True)
    assignment_type = Column(
        Enum(AssignmentType, name="assignment_type_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=AssignmentType.ACTIVE,
    )
    status = Column(String(30), default="Active")
    partner_confirmed_start = Column(Boolean, default=False)
    partner_confirmed_end = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ── Forward staffing / pre-booking fields ──────────────────────────
    is_forward_booked = Column(
        Boolean, default=False,
        comment="True when this assignment is part of the 90-day forward staffing plan",
    )
    booking_confidence = Column(
        Float, nullable=True,
        comment="0.0-1.0 confidence score that this pre-booking will convert to active",
    )
    confirmed_at = Column(
        DateTime, nullable=True,
        comment="Timestamp when ops confirmed the forward booking",
    )
    confirmed_by = Column(
        UUID(as_uuid=True), nullable=True,
        comment="User ID of the ops user who confirmed the forward booking",
    )

    # ── Assignment chaining fields ─────────────────────────────────────
    previous_assignment_id = Column(
        UUID(as_uuid=True), ForeignKey("assignments.id"), nullable=True, index=True,
        comment="Links to the preceding assignment in a chain",
    )
    next_assignment_id = Column(
        UUID(as_uuid=True), ForeignKey("assignments.id"), nullable=True,
        comment="Links to the following assignment in a chain",
    )
    chain_id = Column(
        UUID(as_uuid=True), nullable=True, index=True,
        comment="Groups all assignments in the same chain under one ID",
    )
    chain_position = Column(
        Integer, nullable=True,
        comment="Order position within the chain (1-based)",
    )
    chain_priority = Column(
        Enum(ChainPriority, name="chain_priority_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=True,
        comment="Priority for this chained assignment",
    )
    gap_days = Column(
        Integer, nullable=True,
        comment="Days gap between previous assignment end and this start (0 = seamless)",
    )
    chain_notes = Column(
        Text, nullable=True,
        comment="Ops notes about this chain link (travel time, ramp-up, etc.)",
    )

    # ── Relationships ──────────────────────────────────────────────────
    technician = relationship("Technician", backref="assignments")
    role = relationship("ProjectRole", back_populates="assignments")
    timesheets = relationship("Timesheet", back_populates="assignment", lazy="select")

    previous_assignment = relationship(
        "Assignment",
        foreign_keys=[previous_assignment_id],
        remote_side="Assignment.id",
        uselist=False,
        post_update=True,
    )
    next_assignment = relationship(
        "Assignment",
        foreign_keys=[next_assignment_id],
        remote_side="Assignment.id",
        uselist=False,
        post_update=True,
    )

    # ── Composite indexes for forward staffing queries ─────────────────
    __table_args__ = (
        Index("ix_assignments_tech_dates", "technician_id", "start_date", "end_date"),
        Index("ix_assignments_forward", "is_forward_booked", "start_date"),
        Index("ix_assignments_chain", "chain_id", "chain_position"),
    )

    @property
    def is_pre_booked(self) -> bool:
        """True if this is a future pre-booked assignment."""
        return self.assignment_type == AssignmentType.PRE_BOOKED

    @property
    def is_chained(self) -> bool:
        """True if this assignment is part of a chain."""
        return self.chain_id is not None

    @property
    def duration_days(self) -> int | None:
        """Number of days in this assignment, or None if open-ended."""
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days
        return None

    def __repr__(self):
        return (
            f"<Assignment tech={self.technician_id} role={self.role_id} "
            f"{self.start_date}→{self.end_date} [{self.status}]>"
        )
