"""Pydantic schemas for timesheet endpoints."""

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.skill_breakdown import PartnerSkillBreakdownSummary


class TimesheetCreate(BaseModel):
    """Schema for submitting a new timesheet."""

    assignment_id: uuid.UUID
    week_start: date
    hours: float = Field(..., gt=0, le=168, description="Hours worked (1-168)")
    skill_name: Optional[str] = Field(
        None,
        description="Skill to attribute training hours to. "
        "If provided, approved hours accumulate toward proficiency advancement.",
    )


class TimesheetApprove(BaseModel):
    """Schema for approving a timesheet."""

    reviewer_notes: Optional[str] = None


class TimesheetFlag(BaseModel):
    """Schema for flagging a timesheet."""

    flag_comment: str = Field(..., min_length=1, max_length=1000)


class TimesheetResolve(BaseModel):
    """Schema for resolving a flagged timesheet (resubmit with corrected hours)."""

    corrected_hours: Optional[float] = Field(
        None, gt=0, le=168, description="Corrected hours (if different from original)"
    )
    resolution_note: Optional[str] = Field(None, max_length=1000)


class TimesheetResponse(BaseModel):
    """Response schema for a timesheet."""

    id: uuid.UUID
    technician_id: uuid.UUID
    assignment_id: uuid.UUID
    week_start: date
    hours: float
    status: str
    flag_comment: Optional[str] = None
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_by_role: Optional[str] = None
    skill_name: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Partner-scoped timesheet schemas
# ---------------------------------------------------------------------------

class PartnerTimesheetResponse(BaseModel):
    """Timesheet response enriched with context for partner views.

    Partners see technician name and project/role info but NOT internal
    ops data like scoring, deployability status, or training details.
    """

    id: uuid.UUID
    assignment_id: uuid.UUID
    week_start: date
    hours: float
    status: str
    flag_comment: Optional[str] = None
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by_role: Optional[str] = None
    skill_name: Optional[str] = None
    # Enriched context
    technician_name: Optional[str] = None
    project_name: Optional[str] = None
    role_name: Optional[str] = None
    # Skill breakdown for joint review
    skill_breakdown: Optional[PartnerSkillBreakdownSummary] = None

    model_config = {"from_attributes": True}


class PartnerTimesheetListResponse(BaseModel):
    """Paginated list of timesheets for partner view."""

    items: list[PartnerTimesheetResponse]
    total: int
    skip: int
    limit: int
    pending_count: int = 0
    flagged_count: int = 0


class TimesheetListResponse(BaseModel):
    """Paginated list of timesheets."""

    items: list[TimesheetResponse]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Dispute / flagged-timesheet schemas (ops view)
# ---------------------------------------------------------------------------

class DisputeTechnicianSummary(BaseModel):
    """Minimal technician info embedded in a dispute record."""

    id: uuid.UUID
    first_name: str
    last_name: str
    full_name: str
    email: str
    deployability_status: Optional[str] = None

    model_config = {"from_attributes": True}


class DisputeProjectSummary(BaseModel):
    """Minimal project info embedded in a dispute record."""

    id: uuid.UUID
    name: str
    partner_id: uuid.UUID
    status: Optional[str] = None
    location_region: Optional[str] = None

    model_config = {"from_attributes": True}


class DisputeRoleSummary(BaseModel):
    """Minimal project role info embedded in a dispute record."""

    id: uuid.UUID
    role_name: str

    model_config = {"from_attributes": True}


class DisputeAssignmentSummary(BaseModel):
    """Assignment context for a disputed timesheet."""

    id: uuid.UUID
    technician: DisputeTechnicianSummary
    role: DisputeRoleSummary
    project: DisputeProjectSummary
    start_date: date
    end_date: Optional[date] = None

    model_config = {"from_attributes": True}


class DisputeTimesheetResponse(BaseModel):
    """A flagged timesheet with full staffing context for ops dispute view."""

    id: uuid.UUID
    assignment_id: uuid.UUID
    week_start: date
    hours: float
    status: str
    flag_comment: Optional[str] = None
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    skill_name: Optional[str] = None
    assignment: DisputeAssignmentSummary

    model_config = {"from_attributes": True}


class DisputeListResponse(BaseModel):
    """Paginated list of disputed/flagged timesheets for ops."""

    items: list[DisputeTimesheetResponse]
    total: int
    skip: int
    limit: int
    flagged_count: int
    resolved_count: int
