"""SkillBreakdown model — captures skills performed during an assignment.

Submitted at assignment completion to record which skills a technician
used, their proficiency ratings, and supervisor notes. This data feeds
into career passport generation and proficiency tracking.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Text, DateTime, ForeignKey, Enum,
    Integer, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class SkillProficiencyRating(str, enum.Enum):
    """Proficiency rating assigned during skill breakdown review."""
    BELOW_EXPECTATIONS = "Below Expectations"
    MEETS_EXPECTATIONS = "Meets Expectations"
    EXCEEDS_EXPECTATIONS = "Exceeds Expectations"
    EXPERT = "Expert"


class PartnerReviewStatus(str, enum.Enum):
    """Status of a partner's review of a skill breakdown."""
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    REVISION_REQUESTED = "Revision Requested"


class SkillBreakdown(Base):
    """Top-level skill breakdown submission for a completed assignment.

    One SkillBreakdown per assignment, containing multiple SkillBreakdownItems.
    Can only be created when the assignment status is 'Completed'.
    """

    __tablename__ = "skill_breakdowns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id = Column(
        UUID(as_uuid=True), ForeignKey("assignments.id"), nullable=False, unique=True, index=True,
    )
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id"), nullable=False, index=True,
    )
    submitted_by = Column(String(200), nullable=False, comment="User ID of the ops user who submitted")
    overall_notes = Column(Text, nullable=True, comment="General notes about the technician's performance")
    overall_rating = Column(
        Enum(SkillProficiencyRating, name="skill_proficiency_rating_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=True,
        comment="Overall proficiency rating for the assignment",
    )
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Partner review fields — partners review the skill breakdown alongside hours
    partner_review_status = Column(
        Enum(PartnerReviewStatus, name="partner_review_status_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=True,
        default=None,
        comment="Partner's review status of the skill breakdown",
    )
    partner_review_note = Column(Text, nullable=True, comment="Partner's notes on the skill breakdown")
    partner_reviewed_at = Column(DateTime, nullable=True, comment="When the partner reviewed")
    partner_reviewed_by = Column(String(200), nullable=True, comment="Partner user ID who reviewed")

    # Relationships
    assignment = relationship("Assignment", backref="skill_breakdown", uselist=False)
    technician = relationship("Technician", backref="skill_breakdowns")
    items = relationship(
        "SkillBreakdownItem", back_populates="skill_breakdown",
        cascade="all, delete-orphan", lazy="selectin",
    )

    def __repr__(self):
        return f"<SkillBreakdown assignment={self.assignment_id} tech={self.technician_id}>"


class SkillBreakdownItem(Base):
    """Individual skill entry within a skill breakdown.

    Records which skill was performed, hours spent, proficiency rating,
    and optional notes for that specific skill.
    """

    __tablename__ = "skill_breakdown_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_breakdown_id = Column(
        UUID(as_uuid=True), ForeignKey("skill_breakdowns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    skill_name = Column(String(200), nullable=False, comment="Name of the skill performed")
    skill_id = Column(
        UUID(as_uuid=True), ForeignKey("skills.id"), nullable=True,
        comment="Optional FK to skills taxonomy",
    )
    hours_applied = Column(Float, nullable=True, comment="Hours spent on this skill during the assignment")
    proficiency_rating = Column(
        Enum(SkillProficiencyRating, name="skill_proficiency_rating_enum",
             values_callable=lambda e: [m.value for m in e], create_type=False),
        nullable=False,
    )
    notes = Column(Text, nullable=True, comment="Notes specific to this skill performance")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    skill_breakdown = relationship("SkillBreakdown", back_populates="items")
    skill = relationship("Skill", lazy="select")

    __table_args__ = (
        Index("ix_skill_breakdown_items_breakdown_skill", "skill_breakdown_id", "skill_name"),
    )

    def __repr__(self):
        return f"<SkillBreakdownItem {self.skill_name} [{self.proficiency_rating}]>"
