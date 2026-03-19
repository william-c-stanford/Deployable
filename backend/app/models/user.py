"""User and Partner models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class UserRole(str, enum.Enum):
    OPS = "ops"
    TECHNICIAN = "technician"
    PARTNER = "partner"


class User(Base):
    """Application user (ops, technician, or partner persona)."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    role = Column(String(30), nullable=False)  # ops, technician, partner
    scoped_to = Column(String(200), nullable=True)  # tech_id or partner_id
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Partner(Base):
    """External partner / client organization."""

    __tablename__ = "partners"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(30), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    projects = relationship("Project", back_populates="partner", lazy="select")
