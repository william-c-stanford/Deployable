"""Training models — hours tracking, programs, enrollment, and advancement gate configuration.

Supports the deterministic training advancement system:
  - TrainingProgram: defines a structured training track (e.g. "Fiber Optic Apprentice Program")
  - TrainingEnrollment: links a technician to a program with current advancement level
  - TrainingHoursLog: granular log of individual training hours entries
  - AdvancementGateConfig: cert gate + hours requirements per skill per advancement level
  - AdvancementLevel enum: Apprentice → Intermediate → Advanced
"""

import enum
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    Float,
    Date,
    DateTime,
    ForeignKey,
    Enum,
    UniqueConstraint,
    CheckConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AdvancementLevel(str, enum.Enum):
    """The three-tier advancement levels for technician training."""
    APPRENTICE = "Apprentice"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class EnrollmentStatus(str, enum.Enum):
    """Enrollment lifecycle status."""
    ACTIVE = "Active"
    COMPLETED = "Completed"
    PAUSED = "Paused"
    WITHDRAWN = "Withdrawn"


class HoursLogSource(str, enum.Enum):
    """Where a training hours entry originated."""
    TIMESHEET = "Timesheet"         # Approved project timesheet hours
    CLASSROOM = "Classroom"         # In-person training class
    ONLINE = "Online"               # Online/self-paced module
    FIELD_TRAINING = "Field Training"  # Supervised on-the-job training
    ASSESSMENT = "Assessment"       # Practical skills assessment
    MANUAL = "Manual"               # Manual entry by ops


# ---------------------------------------------------------------------------
# Training Program (definition)
# ---------------------------------------------------------------------------

class TrainingProgram(Base):
    """A structured training program / track that technicians can enroll in.

    Examples: "Fiber Optic Foundations", "Data Center Infrastructure",
    "Aerial Construction Safety".
    """

    __tablename__ = "training_programs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), unique=True, nullable=False)
    slug = Column(String(200), unique=True, nullable=False)
    description = Column(Text, nullable=True)

    # Total hours required to complete the full program
    total_hours_required = Column(Float, default=0.0)

    # Advancement thresholds (hours) within this program
    apprentice_hours_min = Column(Float, default=0.0,
                                  comment="Hours to start (usually 0)")
    intermediate_hours_threshold = Column(Float, default=100.0,
                                          comment="Hours required to advance from Apprentice to Intermediate")
    advanced_hours_threshold = Column(Float, default=300.0,
                                      comment="Hours required to advance from Intermediate to Advanced")

    # Associated skill category (optional — program may span multiple)
    skill_category_id = Column(
        UUID(as_uuid=True), ForeignKey("skill_categories.id"), nullable=True,
    )

    is_active = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    enrollments = relationship(
        "TrainingEnrollment", back_populates="program",
        cascade="all, delete-orphan", lazy="select",
    )
    gate_configs = relationship(
        "AdvancementGateConfig", back_populates="program",
        cascade="all, delete-orphan", lazy="select",
    )

    def __repr__(self):
        return f"<TrainingProgram {self.name}>"


# ---------------------------------------------------------------------------
# Training Enrollment (technician ↔ program)
# ---------------------------------------------------------------------------

class TrainingEnrollment(Base):
    """A technician's enrollment in a specific training program.

    Tracks current advancement level and cumulative hours within the program.
    """

    __tablename__ = "training_enrollments"
    __table_args__ = (
        UniqueConstraint("technician_id", "program_id", name="uq_enrollment_tech_program"),
        Index("ix_enrollment_tech_status", "technician_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    program_id = Column(
        UUID(as_uuid=True), ForeignKey("training_programs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Current advancement level within this program
    advancement_level = Column(
        Enum(AdvancementLevel, name="advancement_level_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=AdvancementLevel.APPRENTICE,
    )

    # Cumulative hours in this program (denormalized for fast reads)
    total_hours_logged = Column(Float, default=0.0)

    # Enrollment lifecycle
    status = Column(
        Enum(EnrollmentStatus, name="enrollment_status_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=EnrollmentStatus.ACTIVE,
    )
    enrolled_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    # Last time advancement was checked / level was updated
    last_advancement_check = Column(DateTime, nullable=True)
    last_advanced_at = Column(DateTime, nullable=True)

    # Relationships
    technician = relationship("Technician", back_populates="training_enrollments")
    program = relationship("TrainingProgram", back_populates="enrollments")
    hours_logs = relationship(
        "TrainingHoursLog", back_populates="enrollment",
        cascade="all, delete-orphan", lazy="select",
        order_by="TrainingHoursLog.logged_date.desc()",
    )

    def __repr__(self):
        return (
            f"<TrainingEnrollment tech={self.technician_id} "
            f"program={self.program_id} level={self.advancement_level}>"
        )


# ---------------------------------------------------------------------------
# Training Hours Log (granular entries)
# ---------------------------------------------------------------------------

class TrainingHoursLog(Base):
    """Individual training hours entry — the atomic unit of hours tracking.

    Each row represents a specific block of training hours logged for a
    technician within a program enrollment, optionally linked to a specific
    skill for skill-level hour accumulation.
    """

    __tablename__ = "training_hours_logs"
    __table_args__ = (
        Index("ix_hours_log_tech_date", "technician_id", "logged_date"),
        Index("ix_hours_log_enrollment", "enrollment_id", "logged_date"),
        CheckConstraint("hours > 0", name="ck_hours_positive"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    enrollment_id = Column(
        UUID(as_uuid=True), ForeignKey("training_enrollments.id", ondelete="CASCADE"),
        nullable=True, index=True,
        comment="NULL if hours are general (not tied to a specific program)",
    )

    # Optional link to specific skill for skill-level hour tracking
    skill_id = Column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # Optional link to approved timesheet (if hours came from project work)
    timesheet_id = Column(
        UUID(as_uuid=True), ForeignKey("timesheets.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Hours details
    hours = Column(Float, nullable=False)
    logged_date = Column(Date, nullable=False)
    source = Column(
        Enum(HoursLogSource, name="hours_log_source_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=HoursLogSource.MANUAL,
    )
    description = Column(Text, nullable=True)

    # Approval tracking
    approved = Column(Boolean, default=False)
    approved_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    technician = relationship("Technician", back_populates="training_hours_logs")
    enrollment = relationship("TrainingEnrollment", back_populates="hours_logs")
    skill = relationship("Skill")

    def __repr__(self):
        return (
            f"<TrainingHoursLog tech={self.technician_id} "
            f"hours={self.hours} date={self.logged_date}>"
        )


# ---------------------------------------------------------------------------
# Advancement Gate Configuration (cert gates per skill per level)
# ---------------------------------------------------------------------------

class AdvancementGateConfig(Base):
    """Configuration for advancement gate requirements.

    Defines what certifications (and optional minimum hours) are required
    for a technician to advance to a given level in a specific skill
    or program. This is the formal cert gate system.

    Example: To advance to Advanced in "Fiber Splicing", the technician
    must hold an active "FOA CFOT" certification AND have 300+ hours.
    """

    __tablename__ = "advancement_gate_configs"
    __table_args__ = (
        UniqueConstraint(
            "program_id", "skill_id", "target_level", "certification_id",
            name="uq_gate_config_program_skill_level_cert",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Which program this gate belongs to (NULL = global/all programs)
    program_id = Column(
        UUID(as_uuid=True), ForeignKey("training_programs.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )

    # Which skill this gate applies to (NULL = program-level gate)
    skill_id = Column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )

    # The advancement level this gate guards entry to
    target_level = Column(
        Enum(AdvancementLevel, name="advancement_level_enum",
             values_callable=lambda e: [m.value for m in e],
             create_type=False),
        nullable=False,
    )

    # Required certification to pass this gate
    certification_id = Column(
        UUID(as_uuid=True), ForeignKey("certifications.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Additional hours minimum (on top of program/skill threshold)
    min_hours_override = Column(
        Float, nullable=True,
        comment="Override the default hours threshold for this specific gate",
    )

    # Whether this gate is mandatory (vs. recommended)
    is_mandatory = Column(Boolean, default=True)

    # Description for UI display
    gate_description = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    program = relationship("TrainingProgram", back_populates="gate_configs")
    skill = relationship("Skill")
    certification = relationship("Certification")

    def __repr__(self):
        return (
            f"<AdvancementGateConfig target={self.target_level} "
            f"cert={self.certification_id}>"
        )
