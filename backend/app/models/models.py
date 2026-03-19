import uuid
from datetime import datetime, date, timezone
from sqlalchemy import (
    Column, String, Boolean, Integer, Float, Text, Date, DateTime,
    ForeignKey, JSON, Enum as SAEnum
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from backend.app.core.database import Base
import enum


# ─── Enums ───────────────────────────────────────────────────────────

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


class ProficiencyLevel(str, enum.Enum):
    APPRENTICE = "Apprentice"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class ProjectStatus(str, enum.Enum):
    DRAFT = "Draft"
    STAFFING = "Staffing"
    ACTIVE = "Active"
    WRAPPING_UP = "Wrapping Up"
    CLOSED = "Closed"


class TimesheetStatus(str, enum.Enum):
    SUBMITTED = "Submitted"
    APPROVED = "Approved"
    FLAGGED = "Flagged"
    RESOLVED = "Resolved"


class RecommendationStatus(str, enum.Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    DISMISSED = "Dismissed"
    SUPERSEDED = "Superseded"


class AssignmentType(str, enum.Enum):
    ACTIVE = "Active"
    PRE_BOOKED = "Pre-Booked"


class UserRole(str, enum.Enum):
    OPS = "ops"
    TECHNICIAN = "technician"
    PARTNER = "partner"


class DocVerificationStatus(str, enum.Enum):
    NOT_SUBMITTED = "Not Submitted"
    PENDING_REVIEW = "Pending Review"
    VERIFIED = "Verified"
    EXPIRED = "Expired"


# ─── Helper ──────────────────────────────────────────────────────────

def gen_uuid():
    return str(uuid.uuid4())


# ─── Models ──────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)  # ops, technician, partner
    scoped_to = Column(String, nullable=True)  # tech_id or partner_id
    email = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Partner(Base):
    __tablename__ = "partners"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    contact_email = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    projects = relationship("Project", back_populates="partner")


class Technician(Base):
    __tablename__ = "technicians"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    home_base_city = Column(String, nullable=False)
    approved_regions = Column(JSON, default=list)  # list of states
    career_stage = Column(String, default=CareerStage.SOURCED.value)
    deployability_status = Column(String, default=DeployabilityStatus.IN_TRAINING.value)
    deployability_locked = Column(Boolean, default=False)
    available_from = Column(Date, nullable=True)
    profile_image_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    skills = relationship("TechnicianSkill", back_populates="technician", cascade="all, delete-orphan")
    certifications = relationship("Certification", back_populates="technician", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="technician", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="technician")
    site_badges = relationship("SiteBadge", back_populates="technician", cascade="all, delete-orphan")
    milestone_badges = relationship("MilestoneBadge", back_populates="technician", cascade="all, delete-orphan")


class Skill(Base):
    __tablename__ = "skills"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, unique=True, nullable=False)
    category = Column(String, nullable=True)  # e.g., "Fiber", "Data Center", "Safety"


class TechnicianSkill(Base):
    __tablename__ = "technician_skills"
    id = Column(String, primary_key=True, default=gen_uuid)
    technician_id = Column(String, ForeignKey("technicians.id"), nullable=False)
    skill_id = Column(String, ForeignKey("skills.id"), nullable=False)
    proficiency_level = Column(String, default=ProficiencyLevel.APPRENTICE.value)
    training_hours = Column(Float, default=0.0)

    technician = relationship("Technician", back_populates="skills")
    skill = relationship("Skill")


class Certification(Base):
    __tablename__ = "certifications"
    id = Column(String, primary_key=True, default=gen_uuid)
    technician_id = Column(String, ForeignKey("technicians.id"), nullable=False)
    cert_name = Column(String, nullable=False)
    issue_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    status = Column(String, default="Active")  # Active, Expired, Expiring Soon

    technician = relationship("Technician", back_populates="certifications")


class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True, default=gen_uuid)
    technician_id = Column(String, ForeignKey("technicians.id"), nullable=False)
    doc_type = Column(String, nullable=False)  # e.g., "Background Check", "Drug Test", "W-4"
    verification_status = Column(String, default=DocVerificationStatus.NOT_SUBMITTED.value)
    uploaded_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)

    technician = relationship("Technician", back_populates="documents")


class SiteBadge(Base):
    __tablename__ = "site_badges"
    id = Column(String, primary_key=True, default=gen_uuid)
    technician_id = Column(String, ForeignKey("technicians.id"), nullable=False)
    badge_name = Column(String, nullable=False)
    granted_by = Column(String, nullable=True)
    granted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    technician = relationship("Technician", back_populates="site_badges")


class MilestoneBadge(Base):
    __tablename__ = "milestone_badges"
    id = Column(String, primary_key=True, default=gen_uuid)
    technician_id = Column(String, ForeignKey("technicians.id"), nullable=False)
    badge_name = Column(String, nullable=False)
    badge_type = Column(String, nullable=False)  # hours, projects, role
    earned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    technician = relationship("Technician", back_populates="milestone_badges")


class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    partner_id = Column(String, ForeignKey("partners.id"), nullable=False)
    status = Column(String, default=ProjectStatus.DRAFT.value)
    location_region = Column(String, nullable=False)
    location_city = Column(String, nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    partner = relationship("Partner", back_populates="projects")
    roles = relationship("ProjectRole", back_populates="project", cascade="all, delete-orphan")


class ProjectRole(Base):
    __tablename__ = "project_roles"
    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    role_name = Column(String, nullable=False)
    required_skills = Column(JSON, default=list)  # [{skill_id, min_proficiency}]
    required_certs = Column(JSON, default=list)  # [cert_name]
    skill_weights = Column(JSON, default=dict)  # {skill_id: weight}
    quantity = Column(Integer, default=1)
    filled = Column(Integer, default=0)

    project = relationship("Project", back_populates="roles")
    assignments = relationship("Assignment", back_populates="role")


class Assignment(Base):
    __tablename__ = "assignments"
    id = Column(String, primary_key=True, default=gen_uuid)
    technician_id = Column(String, ForeignKey("technicians.id"), nullable=False)
    role_id = Column(String, ForeignKey("project_roles.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    hourly_rate = Column(Float, nullable=True)
    per_diem = Column(Float, nullable=True)
    assignment_type = Column(String, default=AssignmentType.ACTIVE.value)
    status = Column(String, default="Active")  # Active, Completed, Cancelled
    partner_confirmed_start = Column(Boolean, default=False)
    partner_confirmed_end = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    technician = relationship("Technician", back_populates="assignments")
    role = relationship("ProjectRole", back_populates="assignments")
    timesheets = relationship("Timesheet", back_populates="assignment")


class Timesheet(Base):
    __tablename__ = "timesheets"
    id = Column(String, primary_key=True, default=gen_uuid)
    assignment_id = Column(String, ForeignKey("assignments.id"), nullable=False)
    week_start = Column(Date, nullable=False)
    hours = Column(Float, nullable=False)
    status = Column(String, default=TimesheetStatus.SUBMITTED.value)
    flag_comment = Column(Text, nullable=True)
    submitted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    reviewed_at = Column(DateTime, nullable=True)

    assignment = relationship("Assignment", back_populates="timesheets")


class Recommendation(Base):
    __tablename__ = "recommendations"
    id = Column(String, primary_key=True, default=gen_uuid)
    recommendation_type = Column(String, nullable=False)  # staffing, training, cert_renewal, backfill, next_step
    target_entity_type = Column(String, nullable=True)  # technician, project, role
    target_entity_id = Column(String, nullable=True)
    role_id = Column(String, nullable=True)
    scorecard = Column(JSON, nullable=True)
    explanation = Column(Text, nullable=True)
    status = Column(String, default=RecommendationStatus.PENDING.value)
    agent_name = Column(String, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PreferenceRule(Base):
    __tablename__ = "preference_rules"
    id = Column(String, primary_key=True, default=gen_uuid)
    rule_type = Column(String, nullable=False)  # experience_threshold, skill_level_minimum, etc.
    threshold = Column(String, nullable=True)
    scope = Column(String, default="global")  # global, client, project_type
    effect = Column(String, default="demote")  # exclude, demote
    parameters = Column(JSON, default=dict)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PendingHeadcountRequest(Base):
    __tablename__ = "pending_headcount_requests"
    id = Column(String, primary_key=True, default=gen_uuid)
    partner_id = Column(String, ForeignKey("partners.id"), nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    role_name = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    start_date = Column(Date, nullable=True)
    constraints = Column(Text, nullable=True)
    status = Column(String, default="Pending")  # Pending, Approved, Rejected
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SuggestedAction(Base):
    __tablename__ = "suggested_actions"
    id = Column(String, primary_key=True, default=gen_uuid)
    target_role = Column(String, default="ops")  # ops, technician
    target_user_id = Column(String, nullable=True)
    action_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    link = Column(String, nullable=True)
    priority = Column(Integer, default=0)
    status = Column(String, default="active")  # active, dismissed, acted
    agent_name = Column(String, nullable=True)
    entity_type = Column(String, nullable=True)
    entity_id = Column(String, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=True)
    entity_id = Column(String, nullable=True)
    details = Column(JSON, default=dict)
    agent_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False)
    role = Column(String, nullable=False)  # user, assistant
    content = Column(Text, nullable=False)
    ui_commands = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
