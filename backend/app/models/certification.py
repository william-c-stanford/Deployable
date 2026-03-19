"""Certification model — industry-standard certifications for technicians."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    Integer,
    DateTime,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Certification(Base):
    """An industry-standard certification that technicians can earn.

    Examples: FOA CFOT, BICSI Technician, OSHA 30.
    This is the *definition* of a cert; individual technician holdings
    are stored in TechnicianCertification (a separate join model).
    """

    __tablename__ = "certifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), unique=True, nullable=False)
    slug = Column(String(200), unique=True, nullable=False)
    issuing_body = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    # Validity period in months (0 = never expires)
    validity_months = Column(Integer, default=0)
    # Category for UI grouping
    cert_category = Column(
        String(80),
        nullable=False,
        default="industry",
        comment="One of: industry, safety, vendor, government",
    )
    is_active = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Certification {self.name}>"
