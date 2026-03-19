"""Pydantic schemas for skill breakdown endpoints."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SkillBreakdownItemCreate(BaseModel):
    """Schema for a single skill entry in a breakdown submission."""

    skill_name: str = Field(..., min_length=1, max_length=200, description="Name of the skill performed")
    skill_id: Optional[uuid.UUID] = Field(None, description="Optional FK to skills taxonomy")
    hours_applied: Optional[float] = Field(None, ge=0, le=5000, description="Hours spent on this skill")
    proficiency_rating: str = Field(
        ...,
        description="One of: Below Expectations, Meets Expectations, Exceeds Expectations, Expert",
    )
    notes: Optional[str] = Field(None, max_length=2000, description="Notes for this skill")


class SkillBreakdownCreate(BaseModel):
    """Schema for submitting a skill breakdown at assignment completion."""

    items: list[SkillBreakdownItemCreate] = Field(
        ..., min_length=1, description="At least one skill entry is required"
    )
    overall_notes: Optional[str] = Field(None, max_length=5000, description="General performance notes")
    overall_rating: Optional[str] = Field(
        None,
        description="Overall rating: Below Expectations, Meets Expectations, Exceeds Expectations, Expert",
    )


class SkillBreakdownItemResponse(BaseModel):
    """Response schema for a single skill breakdown item."""

    id: uuid.UUID
    skill_name: str
    skill_id: Optional[uuid.UUID] = None
    hours_applied: Optional[float] = None
    proficiency_rating: str
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillBreakdownResponse(BaseModel):
    """Response schema for a full skill breakdown."""

    id: uuid.UUID
    assignment_id: uuid.UUID
    technician_id: uuid.UUID
    submitted_by: str
    overall_notes: Optional[str] = None
    overall_rating: Optional[str] = None
    submitted_at: datetime
    updated_at: datetime
    items: list[SkillBreakdownItemResponse] = []
    # Partner review fields
    partner_review_status: Optional[str] = None
    partner_review_note: Optional[str] = None
    partner_reviewed_at: Optional[datetime] = None
    partner_reviewed_by: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Partner skill breakdown review schemas
# ---------------------------------------------------------------------------

class PartnerSkillBreakdownReview(BaseModel):
    """Schema for a partner reviewing/acting on a skill breakdown.

    Partners can approve, reject, or request revision of a skill breakdown
    as part of the hours approval flow.
    """

    action: str = Field(
        ...,
        description="One of: approve, reject, request_revision",
    )
    note: Optional[str] = Field(
        None, max_length=2000,
        description="Optional note explaining the review decision",
    )


class PartnerSkillBreakdownSummary(BaseModel):
    """Compact skill breakdown summary embedded in partner timesheet responses.

    Partners see skill names, hours applied, and proficiency ratings but NOT
    internal ops notes or submitted_by details.
    """

    id: uuid.UUID
    overall_rating: Optional[str] = None
    partner_review_status: Optional[str] = None
    partner_review_note: Optional[str] = None
    partner_reviewed_at: Optional[datetime] = None
    items: list[SkillBreakdownItemResponse] = []

    model_config = {"from_attributes": True}
