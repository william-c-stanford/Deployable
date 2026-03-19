"""Project and ProjectRole models."""

import enum
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column, String, Text, Integer, Float, Date, DateTime,
    ForeignKey, JSON, Enum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ProjectStatus(str, enum.Enum):
    DRAFT = "Draft"
    STAFFING = "Staffing"
    ACTIVE = "Active"
    WRAPPING_UP = "Wrapping Up"
    ON_HOLD = "On Hold"
    CLOSED = "Closed"


class Project(Base):
    """A fiber / data-center project managed on the platform."""

    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(300), nullable=False)
    partner_id = Column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=False)
    status = Column(
        Enum(ProjectStatus, name="project_status_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ProjectStatus.DRAFT,
    )
    location_region = Column(String(60), nullable=False)
    location_city = Column(String(200), nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    budget_hours = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    partner = relationship("Partner", back_populates="projects")
    roles = relationship("ProjectRole", back_populates="project", cascade="all, delete-orphan", lazy="select")
    manual_badges = relationship("ManualBadge", back_populates="project", lazy="select")


class ProjectRole(Base):
    """A staffing slot within a project (e.g. 'Lead Splicer x2')."""

    __tablename__ = "project_roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    role_name = Column(String(200), nullable=False)
    required_skills = Column(JSON, default=list)   # [{"skill": "Fiber Splicing", "min_level": "Advanced"}]
    required_certs = Column(JSON, default=list)     # ["FOA CFOT", "OSHA 10"]
    skill_weights = Column(JSON, default=dict)      # {"skill_name": weight_float}
    quantity = Column(Integer, default=1)
    filled = Column(Integer, default=0)
    hourly_rate = Column(Float, nullable=True)
    per_diem = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="roles")
    assignments = relationship("Assignment", back_populates="role", lazy="select")

    @property
    def open_slots(self) -> int:
        return max(0, self.quantity - self.filled)
