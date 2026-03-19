"""Pydantic schemas for transitional state management API."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TransitionalStateCreate(BaseModel):
    """Request body for entering a technician into a transitional state."""

    technician_id: UUID
    transitional_status: str = Field(
        ...,
        description="One of: Onboarding, Pending Review, Suspended",
    )
    trigger: str = Field(
        default="manual",
        description="What caused the transition: manual, technician_created, cert_expired, doc_rejected, performance_flag, compliance_hold, assignment_gap",
    )
    trigger_detail: Optional[str] = Field(
        None,
        description="Human-readable explanation for the transition",
    )
    resolution_type: str = Field(
        default="timeout",
        description="How to resolve: timeout, event, condition, manual",
    )
    timeout_hours: Optional[float] = Field(
        None,
        description="Hours until auto-resolution (for timeout type). Defaults: Onboarding=168 (7d), PendingReview=48, Suspended=720 (30d)",
    )
    resolution_events: Optional[list[str]] = Field(
        None,
        description='Event types that will resolve this state, e.g. ["cert.added", "doc.verified"]',
    )
    resolution_conditions: Optional[dict] = Field(
        None,
        description='Conditions to check periodically, e.g. {"all_docs_verified": true, "min_certs": 2}',
    )
    fallback_status: Optional[str] = Field(
        None,
        description="Status to resolve to. Null = auto-compute based on readiness.",
    )
    notes: Optional[str] = None


class TransitionalStateResolve(BaseModel):
    """Request body for manually resolving a transitional state."""

    resolution_reason: str = Field(..., description="Why this is being resolved")
    resolved_to_status: Optional[str] = Field(
        None,
        description="Target status. Null = auto-compute based on readiness.",
    )


class TransitionalStateResponse(BaseModel):
    """Response for a transitional state record."""

    id: UUID
    technician_id: UUID
    transitional_status: str
    previous_status: Optional[str] = None
    trigger: str
    trigger_detail: Optional[str] = None
    resolution_type: str
    timeout_hours: Optional[float] = None
    resolution_events: Optional[list[str]] = None
    resolution_conditions: Optional[dict] = None
    fallback_status: Optional[str] = None
    is_active: bool
    entered_at: datetime
    expires_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_reason: Optional[str] = None
    resolution_event_type: Optional[str] = None
    resolved_to_status: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TransitionalStateListResponse(BaseModel):
    """Paginated list of transitional state records."""

    items: list[TransitionalStateResponse]
    total: int
    skip: int = 0
    limit: int = 20
