"""Pydantic schemas for PendingHeadcountRequest CRUD endpoints."""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class HeadcountRequestCreate(BaseModel):
    """Create a new headcount request."""

    partner_id: UUID
    project_id: Optional[UUID] = None
    role_name: str = Field(..., min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=100)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    required_skills: List[dict] = Field(default_factory=list)
    required_certs: List[str] = Field(default_factory=list)
    constraints: Optional[str] = None
    notes: Optional[str] = None


class HeadcountRequestUpdate(BaseModel):
    """Partial update of a headcount request (ops can edit before approving)."""

    role_name: Optional[str] = Field(None, min_length=1, max_length=200)
    quantity: Optional[int] = Field(None, ge=1, le=100)
    priority: Optional[str] = Field(None, pattern="^(low|normal|high|urgent)$")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    required_skills: Optional[List[dict]] = None
    required_certs: Optional[List[str]] = None
    constraints: Optional[str] = None
    notes: Optional[str] = None


class HeadcountRequestAction(BaseModel):
    """Approve or reject a pending headcount request."""

    action: str = Field(..., pattern="^(approve|reject)$")
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class HeadcountRequestResponse(BaseModel):
    """Full headcount request response."""

    id: UUID
    partner_id: UUID
    project_id: Optional[UUID] = None
    role_name: str
    quantity: int
    priority: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    required_skills: List[dict] = []
    required_certs: List[str] = []
    constraints: Optional[str] = None
    notes: Optional[str] = None
    status: str
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Joined fields (populated when relationships are loaded)
    partner_name: Optional[str] = None
    project_name: Optional[str] = None

    model_config = {"from_attributes": True}


class HeadcountRequestListResponse(BaseModel):
    """Paginated list of headcount requests."""

    items: List[HeadcountRequestResponse]
    total: int
    skip: int
    limit: int
