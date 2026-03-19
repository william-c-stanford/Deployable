"""Pydantic schemas for partner-facing API endpoints."""

from datetime import datetime, date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Partner notifications (48-hour advance visibility)
# ---------------------------------------------------------------------------

class PartnerNotificationOut(BaseModel):
    """Serialized partner notification for API responses."""

    id: UUID
    partner_id: UUID
    assignment_id: UUID
    project_id: UUID
    technician_id: UUID
    notification_type: str
    status: str
    title: str
    message: Optional[str] = None
    target_date: datetime
    confirmed_at: Optional[datetime] = None
    confirmed_by: Optional[str] = None
    created_at: datetime

    # Enriched fields (joined from related tables)
    technician_name: Optional[str] = None
    project_name: Optional[str] = None
    role_name: Optional[str] = None
    assignment_start_date: Optional[date] = None
    assignment_end_date: Optional[date] = None

    class Config:
        from_attributes = True


class PartnerNotificationConfirm(BaseModel):
    """Request body for confirming a partner notification."""

    confirmed_by: Optional[str] = Field(None, description="Name/ID of person confirming")
    note: Optional[str] = Field(None, max_length=1000, description="Optional confirmation note")


class PartnerNotificationDismiss(BaseModel):
    """Request body for dismissing a partner notification."""

    reason: Optional[str] = Field(None, max_length=1000, description="Optional dismissal reason")


class PartnerUpcomingAssignmentOut(BaseModel):
    """Upcoming assignment details for partner view."""

    assignment_id: UUID
    technician_id: UUID
    technician_name: str
    project_id: UUID
    project_name: str
    role_name: str
    start_date: date
    end_date: Optional[date] = None
    hourly_rate: Optional[float] = None
    per_diem: Optional[float] = None
    status: str
    partner_confirmed_start: bool
    partner_confirmed_end: bool
    days_until_start: Optional[int] = None
    days_until_end: Optional[int] = None
    notifications: list[PartnerNotificationOut] = []

    class Config:
        from_attributes = True


class PartnerNotificationListOut(BaseModel):
    """Paginated list of partner notifications."""

    notifications: list[PartnerNotificationOut]
    total: int
    pending_count: int
