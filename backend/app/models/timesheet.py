"""Timesheet model."""

import enum
import uuid
from datetime import datetime, date

from sqlalchemy import Column, String, Float, Text, Date, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class TimesheetStatus(str, enum.Enum):
    SUBMITTED = "Submitted"
    APPROVED = "Approved"
    FLAGGED = "Flagged"
    RESOLVED = "Resolved"


class Timesheet(Base):
    """Weekly timesheet entry for an assignment."""

    __tablename__ = "timesheets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id"), nullable=False, index=True,
        comment="Direct FK to technician for convenient filtering",
    )
    assignment_id = Column(
        UUID(as_uuid=True), ForeignKey("assignments.id"), nullable=False, index=True,
    )
    week_start = Column(Date, nullable=False)
    hours = Column(Float, nullable=False)
    status = Column(
        Enum(TimesheetStatus, name="timesheet_status_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=TimesheetStatus.SUBMITTED,
    )
    flag_comment = Column(Text, nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(100), nullable=True, comment="User ID of reviewer")
    reviewed_by_role = Column(String(30), nullable=True, comment="Role of reviewer: ops or partner")
    skill_name = Column(String(200), nullable=True, comment="Skill to attribute training hours to")

    technician = relationship("Technician", backref="timesheets")
    assignment = relationship("Assignment", back_populates="timesheets")
