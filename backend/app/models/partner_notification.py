"""Partner notification model — tracks 48-hour advance visibility alerts."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, ForeignKey, Enum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class NotificationType(str, enum.Enum):
    ASSIGNMENT_STARTING = "assignment_starting"
    ASSIGNMENT_ENDING = "assignment_ending"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class PartnerNotification(Base):
    """Notification surfaced to partners 48 hours before assignment start/end.

    Partners must confirm they acknowledge upcoming assignment changes.
    """

    __tablename__ = "partner_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    partner_id = Column(
        UUID(as_uuid=True), ForeignKey("partners.id"), nullable=False, index=True,
    )
    assignment_id = Column(
        UUID(as_uuid=True), ForeignKey("assignments.id"), nullable=False, index=True,
    )
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True,
    )
    technician_id = Column(
        UUID(as_uuid=True), ForeignKey("technicians.id"), nullable=False, index=True,
    )
    notification_type = Column(
        Enum(NotificationType, name="notification_type_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    status = Column(
        Enum(NotificationStatus, name="notification_status_enum",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=NotificationStatus.PENDING,
    )
    title = Column(String(300), nullable=False)
    message = Column(Text, nullable=True)
    target_date = Column(DateTime, nullable=False, comment="The start or end date being alerted about")
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    partner = relationship("Partner", backref="notifications")
    assignment = relationship("Assignment", backref="partner_notifications")
    technician = relationship("Technician", backref="partner_notifications")

    def __repr__(self):
        return f"<PartnerNotification {self.notification_type} assignment={self.assignment_id}>"
