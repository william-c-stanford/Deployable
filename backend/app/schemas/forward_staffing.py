"""Pydantic schemas for forward staffing schedule and assignment chaining."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Assignment chain schemas
# ---------------------------------------------------------------------------

class AssignmentChainLink(BaseModel):
    """A single link in an assignment chain."""
    model_config = ConfigDict(from_attributes=True)

    assignment_id: str
    technician_id: str
    technician_name: Optional[str] = None
    role_id: str
    role_name: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    status: str
    assignment_type: str
    chain_position: Optional[int] = None
    gap_days: Optional[int] = None
    chain_notes: Optional[str] = None
    booking_confidence: Optional[float] = None
    is_forward_booked: bool = False
    confirmed_at: Optional[datetime] = None


class AssignmentChain(BaseModel):
    """A full chain of sequential assignments for a technician."""
    chain_id: str
    technician_id: str
    technician_name: Optional[str] = None
    chain_priority: Optional[str] = None
    total_duration_days: Optional[int] = None
    total_gap_days: int = 0
    links: List[AssignmentChainLink]


# ---------------------------------------------------------------------------
# Forward staffing schedule
# ---------------------------------------------------------------------------

class ForwardScheduleEntry(BaseModel):
    """A single entry in the 90-day forward staffing schedule."""
    model_config = ConfigDict(from_attributes=True)

    assignment_id: str
    technician_id: str
    technician_name: str
    role_id: str
    role_name: str
    project_id: str
    project_name: str
    start_date: date
    end_date: Optional[date] = None
    status: str
    assignment_type: str
    is_forward_booked: bool
    booking_confidence: Optional[float] = None
    chain_id: Optional[str] = None
    chain_position: Optional[int] = None
    gap_days: Optional[int] = None
    partner_confirmed_start: bool = False


class ForwardScheduleResponse(BaseModel):
    """90-day forward staffing schedule."""
    schedule_start: date
    schedule_end: date
    total_assignments: int
    active_count: int
    pre_booked_count: int
    chained_count: int
    entries: List[ForwardScheduleEntry]
    gaps: List[TechnicianGap] = Field(default_factory=list)


class TechnicianGap(BaseModel):
    """A gap in a technician's schedule where they are unassigned."""
    technician_id: str
    technician_name: str
    gap_start: date
    gap_end: date
    gap_days: int
    previous_assignment_id: Optional[str] = None
    next_assignment_id: Optional[str] = None
    previous_project_name: Optional[str] = None
    next_project_name: Optional[str] = None


# Fix forward reference
ForwardScheduleResponse.model_rebuild()


# ---------------------------------------------------------------------------
# Create / update schemas
# ---------------------------------------------------------------------------

class ForwardAssignmentCreate(BaseModel):
    """Create a new forward (pre-booked) assignment."""
    technician_id: str
    role_id: str
    start_date: date
    end_date: Optional[date] = None
    hourly_rate: Optional[float] = None
    per_diem: Optional[float] = None
    booking_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    chain_to_assignment_id: Optional[str] = Field(
        None, description="If provided, chains this assignment after the specified one"
    )
    chain_priority: Optional[str] = None
    chain_notes: Optional[str] = None


class ForwardAssignmentUpdate(BaseModel):
    """Update a forward assignment."""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    hourly_rate: Optional[float] = None
    per_diem: Optional[float] = None
    booking_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    chain_priority: Optional[str] = None
    chain_notes: Optional[str] = None
    status: Optional[str] = None


class AssignmentConfirmRequest(BaseModel):
    """Confirm a forward-booked assignment to active."""
    confirmed_by: Optional[str] = None


class ChainCreateRequest(BaseModel):
    """Create a chain of assignments for a technician."""
    technician_id: str
    assignments: List[ChainAssignmentEntry]
    chain_priority: Optional[str] = "Medium"


class ChainAssignmentEntry(BaseModel):
    """A single assignment entry when creating a chain."""
    role_id: str
    start_date: date
    end_date: Optional[date] = None
    hourly_rate: Optional[float] = None
    per_diem: Optional[float] = None
    booking_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    chain_notes: Optional[str] = None


# Fix forward reference
ChainCreateRequest.model_rebuild()


class TechnicianScheduleSummary(BaseModel):
    """Summary of a technician's forward schedule."""
    technician_id: str
    technician_name: str
    current_assignment: Optional[ForwardScheduleEntry] = None
    upcoming_assignments: List[ForwardScheduleEntry] = Field(default_factory=list)
    chains: List[AssignmentChain] = Field(default_factory=list)
    available_from: Optional[date] = None
    total_booked_days: int = 0
    utilization_pct: Optional[float] = Field(
        None, description="Percentage of 90-day window with assignments"
    )
