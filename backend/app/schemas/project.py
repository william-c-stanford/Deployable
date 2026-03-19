"""Pydantic schemas for project management and close-validation."""

import uuid
from datetime import datetime, date
from typing import Optional, List
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Close-validation blocking item types
# ---------------------------------------------------------------------------

class BlockingItemType(str, Enum):
    """Categories of items that can block project closure."""
    ACTIVE_ASSIGNMENT = "active_assignment"
    PENDING_TIMESHEET = "pending_timesheet"
    FLAGGED_TIMESHEET = "flagged_timesheet"
    PENDING_CONFIRMATION = "pending_confirmation"
    ESCALATED_CONFIRMATION = "escalated_confirmation"
    PENDING_RECOMMENDATION = "pending_recommendation"
    UNFILLED_ROLE = "unfilled_role"
    PENDING_SKILL_BREAKDOWN = "pending_skill_breakdown"
    OPEN_DISPUTE = "open_dispute"


class BlockingSeverity(str, Enum):
    """How severely this item blocks closure."""
    HARD = "hard"    # Must be resolved before close
    SOFT = "soft"    # Warning — can be overridden by ops


# ---------------------------------------------------------------------------
# Blocking item detail
# ---------------------------------------------------------------------------

class BlockingItem(BaseModel):
    """A single item preventing (or warning about) project closure."""
    type: BlockingItemType
    severity: BlockingSeverity
    entity_id: str = Field(..., description="ID of the blocking entity")
    entity_type: str = Field(..., description="Entity kind (assignment, timesheet, etc.)")
    summary: str = Field(..., description="Human-readable description of the blocking issue")
    detail: Optional[str] = Field(None, description="Additional context or instructions")


# ---------------------------------------------------------------------------
# Close-validation response
# ---------------------------------------------------------------------------

class CloseValidationResponse(BaseModel):
    """Result of running close-validation checks on a project."""
    project_id: str
    project_name: str
    can_close: bool = Field(..., description="True if there are zero HARD blockers")
    has_warnings: bool = Field(False, description="True if there are SOFT blockers")
    hard_blockers: List[BlockingItem] = Field(default_factory=list)
    soft_blockers: List[BlockingItem] = Field(default_factory=list)
    total_blocking_items: int = 0
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Project status transition request/response
# ---------------------------------------------------------------------------

class ProjectStatusUpdate(BaseModel):
    """Request body for updating project status."""
    status: str = Field(..., description="New project status")
    force: bool = Field(
        False,
        description="Force close even with soft blockers (ignored for hard blockers)",
    )
    close_note: Optional[str] = Field(None, description="Optional note when closing a project")


class ProjectCloseError(BaseModel):
    """Structured error response when close-validation fails."""
    error: str = "project_close_blocked"
    message: str
    project_id: str
    blocking_items: List[BlockingItem]
    hard_blocker_count: int
    soft_blocker_count: int
    resolution_hints: List[str] = Field(
        default_factory=list,
        description="Actionable hints for resolving blockers",
    )


# ---------------------------------------------------------------------------
# Basic project response schemas
# ---------------------------------------------------------------------------

class ProjectRoleResponse(BaseModel):
    id: str
    role_name: str
    quantity: int
    filled: int
    open_slots: int
    hourly_rate: Optional[float] = None
    per_diem: Optional[float] = None

    class Config:
        from_attributes = True


class ProjectRoleDetailResponse(ProjectRoleResponse):
    """Extended role response including skill requirements for staffing views."""
    project_id: str
    required_skills: List[dict] = Field(default_factory=list)
    required_certs: List[str] = Field(default_factory=list)
    skill_weights: dict = Field(default_factory=dict)


class ProjectResponse(BaseModel):
    id: str
    name: str
    partner_id: str
    partner_name: str = ""
    status: str
    location_region: str
    location_city: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    budget_hours: Optional[int] = None
    description: Optional[str] = None
    created_at: datetime
    roles: List[ProjectRoleDetailResponse] = []

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    items: List[ProjectResponse]
    total: int


# ---------------------------------------------------------------------------
# Close project request/response (used by the close endpoint)
# ---------------------------------------------------------------------------

class CloseProjectRequest(BaseModel):
    """Request body for closing a project."""
    confirm: bool = Field(
        True,
        description="Must be true to confirm closure intent",
    )
    close_note: Optional[str] = Field(
        None,
        description="Optional note documenting closure reason",
    )


class CloseProjectResponse(BaseModel):
    """Successful close response."""
    project_id: str
    project_name: str
    status: str
    message: str
