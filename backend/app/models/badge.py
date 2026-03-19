"""Badge models — ManualBadge for site/client badges and MilestoneBadge for auto-generated."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    Enum,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ManualBadgeCategory(str, enum.Enum):
    """Categories for manually-assigned badges."""
    SITE = "site"
    CLIENT = "client"
    SAFETY = "safety"
    RECOGNITION = "recognition"


class MilestoneType(str, enum.Enum):
    """Types of milestones that trigger auto-badges."""
    HOURS_THRESHOLD = "hours_threshold"
    PROJECTS_COMPLETED = "projects_completed"
    CERTS_EARNED = "certs_earned"
    TRAINING_COMPLETED = "training_completed"
    PERFECT_ATTENDANCE = "perfect_attendance"
    TENURE = "tenure"


# ---------------------------------------------------------------------------
# Manual Badge — site/client badges assigned by ops
# ---------------------------------------------------------------------------

class ManualBadge(Base):
    """A site or client badge manually granted by ops to a technician.

    These represent site-specific clearances, client recognitions,
    and other manually-assigned achievements that ops users manage.
    """

    __tablename__ = "manual_badges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True),
        ForeignKey("technicians.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category = Column(
        Enum(ManualBadgeCategory, name="manual_badge_category_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ManualBadgeCategory.SITE,
    )
    badge_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    # Which site or client this badge relates to
    site_name = Column(String(200), nullable=True)
    client_name = Column(String(200), nullable=True)
    # Link to project if applicable
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Who granted it
    granted_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Optional expiry for site clearances
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    # Arbitrary metadata (e.g., clearance level, badge number)
    metadata_json = Column(JSON, default=dict)

    # Relationships
    technician = relationship("Technician", back_populates="manual_badges")
    project = relationship("Project", back_populates="manual_badges")
    granter = relationship("User", foreign_keys=[granted_by])

    def __repr__(self):
        return f"<ManualBadge {self.badge_name} -> tech={self.technician_id}>"


# ---------------------------------------------------------------------------
# Milestone Badge — auto-generated badges
# ---------------------------------------------------------------------------

class MilestoneBadge(Base):
    """An auto-generated badge awarded when a technician hits a milestone.

    These are system-generated based on deterministic rules (hours thresholds,
    project counts, certifications earned, etc.). The system proposes them
    and they are auto-granted based on thresholds.
    """

    __tablename__ = "milestone_badges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True),
        ForeignKey("technicians.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    milestone_type = Column(
        Enum(MilestoneType, name="milestone_type_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    badge_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    # The threshold value that triggered this badge
    threshold_value = Column(Float, nullable=False, comment="e.g. 500 hours, 10 projects")
    # The actual value at time of award
    actual_value = Column(Float, nullable=False, comment="Actual value when badge was earned")
    # Reference to the triggering entity (program, skill, etc.)
    reference_entity_type = Column(String(100), nullable=True, comment="e.g. training_program, skill")
    reference_entity_id = Column(UUID(as_uuid=True), nullable=True)
    # Icon/display
    icon = Column(String(100), nullable=True, default="award")
    tier = Column(Integer, nullable=False, default=1, comment="1=bronze, 2=silver, 3=gold")
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    technician = relationship("Technician", back_populates="milestone_badges")

    __table_args__ = (
        UniqueConstraint(
            "technician_id", "milestone_type", "threshold_value",
            "reference_entity_type", "reference_entity_id",
            name="uq_milestone_badge_tech_type_threshold_ref",
        ),
    )

    def __repr__(self):
        return f"<MilestoneBadge {self.badge_name} tier={self.tier} -> tech={self.technician_id}>"
