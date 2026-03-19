"""Pydantic schemas for partner assignment confirmation flow."""

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ConfirmationCreateRequest(BaseModel):
    """Ops creates a confirmation request for a partner."""
    assignment_id: UUID
    partner_id: UUID
    confirmation_type: str = Field(
        ..., pattern="^(start_date|end_date)$",
        description="Type of date being confirmed: start_date or end_date",
    )
    requested_date: date

    class Config:
        json_schema_extra = {
            "example": {
                "assignment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "partner_id": "f1e2d3c4-b5a6-7890-abcd-ef1234567890",
                "confirmation_type": "start_date",
                "requested_date": "2026-04-15",
            }
        }


class ConfirmationRespondRequest(BaseModel):
    """Partner confirms or declines an assignment date."""
    action: str = Field(
        ..., pattern="^(confirm|decline)$",
        description="Partner action: confirm or decline",
    )
    proposed_date: Optional[date] = Field(
        None,
        description="Counter-proposed date (required when declining)",
    )
    response_note: Optional[str] = Field(
        None, max_length=1000,
        description="Optional note from partner",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "action": "confirm",
                "response_note": "Confirmed. Team will be ready.",
            }
        }


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ConfirmationResponse(BaseModel):
    """Full confirmation record returned to clients."""
    id: UUID
    assignment_id: UUID
    partner_id: UUID
    confirmation_type: str
    status: str
    requested_date: date
    proposed_date: Optional[date] = None
    response_note: Optional[str] = None
    requested_at: datetime
    responded_at: Optional[datetime] = None

    # Escalation tracking
    escalated: bool = False
    escalated_at: Optional[datetime] = None
    escalation_status: str = "none"
    hours_waiting: Optional[float] = None

    # Enriched fields (optional, populated when joining)
    technician_name: Optional[str] = None
    project_name: Optional[str] = None
    role_name: Optional[str] = None

    class Config:
        from_attributes = True


class ConfirmationListResponse(BaseModel):
    """List wrapper for confirmation records."""
    confirmations: list[ConfirmationResponse]
    total: int
    pending_count: int


class ConfirmationActionResult(BaseModel):
    """Result after a partner acts on a confirmation."""
    confirmation: ConfirmationResponse
    assignment_updated: bool
    message: str
