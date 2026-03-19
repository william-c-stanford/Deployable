"""Technician model and related join tables."""

import enum
import uuid
from datetime import date, datetime

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
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CareerStage(str, enum.Enum):
    SOURCED = "Sourced"
    SCREENED = "Screened"
    IN_TRAINING = "In Training"
    TRAINING_COMPLETED = "Training Completed"
    AWAITING_ASSIGNMENT = "Awaiting Assignment"
    DEPLOYED = "Deployed"


class DeployabilityStatus(str, enum.Enum):
    READY_NOW = "Ready Now"
    IN_TRAINING = "In Training"
    CURRENTLY_ASSIGNED = "Currently Assigned"
    MISSING_CERT = "Missing Cert"
    MISSING_DOCS = "Missing Docs"
    ROLLING_OFF_SOON = "Rolling Off Soon"
    INACTIVE = "Inactive"
    # Transitional statuses — auto-resolve after conditions met or timeout
    ONBOARDING = "Onboarding"
    PENDING_REVIEW = "Pending Review"
    SUSPENDED = "Suspended"


class TransitionalTrigger(str, enum.Enum):
    """What caused the transitional state to be entered."""
    MANUAL = "manual"               # Ops user set it explicitly
    TECHNICIAN_CREATED = "technician_created"  # New tech onboarding
    CERT_EXPIRED = "cert_expired"    # Cert lapse triggered review
    DOC_REJECTED = "doc_rejected"    # Doc rejection triggered review
    PERFORMANCE_FLAG = "performance_flag"  # Performance concern
    COMPLIANCE_HOLD = "compliance_hold"    # Compliance issue
    ASSIGNMENT_GAP = "assignment_gap"      # Extended gap between assignments


class TransitionalResolutionType(str, enum.Enum):
    """How a transitional state should be resolved."""
    TIMEOUT = "timeout"             # Auto-resolve after timeout_hours
    EVENT = "event"                 # Auto-resolve when specific event fires
    CONDITION = "condition"         # Auto-resolve when conditions are met
    MANUAL = "manual"               # Only ops can resolve manually


class ProficiencyLevel(str, enum.Enum):
    APPRENTICE = "Apprentice"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class CertStatus(str, enum.Enum):
    ACTIVE = "Active"
    EXPIRED = "Expired"
    PENDING = "Pending"
    REVOKED = "Revoked"


class VerificationStatus(str, enum.Enum):
    NOT_SUBMITTED = "Not Submitted"
    PENDING_REVIEW = "Pending Review"
    VERIFIED = "Verified"
    EXPIRED = "Expired"


class BadgeType(str, enum.Enum):
    SITE = "site"
    CLIENT = "client"
    MILESTONE = "milestone"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Technician(Base):
    """A fiber/data-center field technician managed by the platform."""

    __tablename__ = "technicians"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name = Column(String(120), nullable=False)
    last_name = Column(String(120), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(30), nullable=True)
    home_base_city = Column(String(200), nullable=True)
    home_base_state = Column(String(60), nullable=True)
    approved_regions = Column(JSON, default=list)
    willing_to_travel = Column(Boolean, default=True)
    max_travel_radius_miles = Column(Integer, nullable=True)
    career_stage = Column(
        Enum(CareerStage, name="career_stage_enum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=CareerStage.SOURCED,
    )
    deployability_status = Column(
        Enum(DeployabilityStatus, name="deployability_status_enum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=DeployabilityStatus.IN_TRAINING,
    )
    deployability_locked = Column(Boolean, default=False)
    inactive_locked_at = Column(DateTime, nullable=True, comment="When the manual Inactive override was applied")
    inactive_locked_by = Column(String(200), nullable=True, comment="User ID who set the manual Inactive override")
    inactive_lock_reason = Column(Text, nullable=True, comment="Reason for manually setting Inactive status")
    available_from = Column(Date, nullable=True)
    archetype = Column(
        String(60),
        nullable=True,
        comment="senior_specialist | generalist | apprentice | traveling_tech | local_only",
    )
    years_experience = Column(Float, default=0)
    total_project_count = Column(Integer, default=0)
    total_approved_hours = Column(Float, default=0)
    hourly_rate_min = Column(Float, nullable=True)
    hourly_rate_max = Column(Float, nullable=True)
    docs_verified = Column(Boolean, default=False)
    hire_date = Column(Date, nullable=True)
    ops_notes = Column(Text, nullable=True)
    bio = Column(Text, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    skills = relationship(
        "TechnicianSkill", back_populates="technician", cascade="all, delete-orphan", lazy="select",
    )
    certifications = relationship(
        "TechnicianCertification", back_populates="technician", cascade="all, delete-orphan", lazy="select",
    )
    documents = relationship(
        "TechnicianDocument", back_populates="technician", cascade="all, delete-orphan", lazy="select",
    )
    badges = relationship(
        "TechnicianBadge", back_populates="technician", cascade="all, delete-orphan", lazy="select",
    )
    training_enrollments = relationship(
        "TrainingEnrollment", back_populates="technician", cascade="all, delete-orphan", lazy="select",
    )
    training_hours_logs = relationship(
        "TrainingHoursLog", back_populates="technician", cascade="all, delete-orphan", lazy="select",
    )
    manual_badges = relationship(
        "ManualBadge", back_populates="technician", cascade="all, delete-orphan", lazy="select",
    )
    milestone_badges = relationship(
        "MilestoneBadge", back_populates="technician", cascade="all, delete-orphan", lazy="select",
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __repr__(self):
        return f"<Technician {self.full_name} [{self.deployability_status}]>"


class TechnicianSkill(Base):
    """Technician skill with proficiency and hours tracking."""

    __tablename__ = "technician_skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    skill_name = Column(String(200), nullable=False)
    proficiency_level = Column(
        Enum(ProficiencyLevel, name="proficiency_level_enum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ProficiencyLevel.APPRENTICE,
    )
    training_hours_accumulated = Column(Float, default=0.0)
    last_used_date = Column(Date, nullable=True)

    technician = relationship("Technician", back_populates="skills")


class TechnicianCertification(Base):
    """Technician certification with dates and status."""

    __tablename__ = "technician_certifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    cert_name = Column(String(300), nullable=False)
    issue_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    status = Column(
        Enum(CertStatus, name="cert_status_enum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=CertStatus.PENDING,
    )
    credential_number = Column(String(100), nullable=True)

    technician = relationship("Technician", back_populates="certifications")


class TechnicianDocument(Base):
    """Required documents for a technician (background check, ID, etc.)."""

    __tablename__ = "technician_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    doc_type = Column(String(200), nullable=False)
    verification_status = Column(
        Enum(VerificationStatus, name="verification_status_enum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=VerificationStatus.NOT_SUBMITTED,
    )

    technician = relationship("Technician", back_populates="documents")


class TechnicianBadge(Base):
    """Site/milestone badges granted to technicians."""

    __tablename__ = "technician_badges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    badge_type = Column(
        Enum(BadgeType, name="badge_type_enum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    badge_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    technician = relationship("Technician", back_populates="badges")
