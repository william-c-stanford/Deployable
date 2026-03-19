"""Audit, suggested actions, and headcount request models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, ForeignKey, Date
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class AuditLog(Base):
    """Immutable log of state-changing operations."""

    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(200), nullable=True)
    action = Column(String(200), nullable=False)
    entity_type = Column(String(100), nullable=True)
    entity_id = Column(String(200), nullable=True)
    details = Column(JSON, default=dict)
    agent_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SuggestedAction(Base):
    """Dashboard action cards surfaced to ops or technicians."""

    __tablename__ = "suggested_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_role = Column(String(30), default="ops")
    target_user_id = Column(String(200), nullable=True)
    action_type = Column(String(100), nullable=False)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    link = Column(String(500), nullable=True)
    priority = Column(Integer, default=0)
    status = Column(String(30), default="active")  # active, dismissed, acted
    agent_name = Column(String(200), nullable=True)
    entity_type = Column(String(100), nullable=True)
    entity_id = Column(String(200), nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class HeadcountRequestStatus(str, enum.Enum):
    """Status lifecycle for headcount requests."""
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    CANCELLED = "Cancelled"


class PendingHeadcountRequest(Base):
    """Partner request for additional technicians on a project.

    Lifecycle: partner (or agent) creates → ops reviews → approve/reject.
    Approved requests may trigger downstream staffing agent runs.
    """

    __tablename__ = "pending_headcount_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    partner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("partners.id"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id"),
        nullable=True,
        index=True,
    )
    role_name = Column(String(200), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    priority = Column(String(30), default="normal")  # low, normal, high, urgent
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    required_skills = Column(JSON, default=list)  # [{"skill": "...", "min_level": "..."}]
    required_certs = Column(JSON, default=list)    # ["FOA CFOT", "OSHA 10"]
    constraints = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(
        String(30),
        default=HeadcountRequestStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    reviewed_by = Column(String(200), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    partner = relationship("Partner", lazy="select")
    project = relationship("Project", lazy="select")
