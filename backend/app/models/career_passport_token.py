"""Career Passport shareable token model."""

import uuid
import secrets
from datetime import datetime, timedelta

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base

# Default token validity: 30 days
DEFAULT_TOKEN_EXPIRY_DAYS = 30


class CareerPassportToken(Base):
    """A shareable token granting public access to a technician's career passport."""

    __tablename__ = "career_passport_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technician_id = Column(
        UUID(as_uuid=True),
        ForeignKey("technicians.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token = Column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: secrets.token_urlsafe(48),
    )
    label = Column(String(200), nullable=True, comment="Optional human-friendly label, e.g. 'For Acme Corp interview'")
    revoked = Column(Boolean, default=False, nullable=False)
    created_by_user_id = Column(String(200), nullable=False, comment="user_id of creator (ops or technician)")
    created_by_role = Column(String(30), nullable=False, comment="Role of the creator: ops or technician")
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationship
    technician = relationship("Technician", lazy="select")

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def is_active(self) -> bool:
        return not self.revoked and not self.is_expired

    def __repr__(self):
        status = "active" if self.is_active else ("revoked" if self.revoked else "expired")
        return f"<CareerPassportToken {self.token[:8]}... [{status}]>"
