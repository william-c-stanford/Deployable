"""Event log model for audit trail of all processed domain events."""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class EventLog(Base):
    """Audit trail of every domain event processed by reactive agent tasks."""

    __tablename__ = "event_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(String(100), nullable=False, index=True)
    actor_id = Column(String(100), nullable=True)
    correlation_id = Column(String(100), nullable=True, index=True)
    task_name = Column(String(200), nullable=True)
    status = Column(String(50), nullable=False, default="completed")
    result_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<EventLog {self.event_type} {self.entity_type}/{self.entity_id}>"
