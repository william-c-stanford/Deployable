"""Skill and SkillCategory models — the core skills taxonomy."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    Integer,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class SkillCategory(Base):
    """Top-level grouping for skills (e.g. Fiber Optic, Structured Cabling)."""

    __tablename__ = "skill_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(120), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    skills = relationship("Skill", back_populates="category", lazy="selectin")

    def __repr__(self):
        return f"<SkillCategory {self.name}>"


class Skill(Base):
    """Individual skill within the taxonomy (e.g. Fiber Splicing, OTDR Testing)."""

    __tablename__ = "skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(120), unique=True, nullable=False)
    slug = Column(String(120), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(
        UUID(as_uuid=True), ForeignKey("skill_categories.id"), nullable=False
    )
    # Hours thresholds for automatic proficiency advancement
    intermediate_hours_threshold = Column(Integer, default=100)
    advanced_hours_threshold = Column(Integer, default=300)
    # Whether a certification gate is required in addition to hours
    cert_gate_intermediate = Column(String(120), nullable=True)
    cert_gate_advanced = Column(String(120), nullable=True)
    is_active = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("SkillCategory", back_populates="skills")

    def __repr__(self):
        return f"<Skill {self.name}>"
