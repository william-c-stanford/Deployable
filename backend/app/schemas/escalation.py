"""Pydantic schemas for escalation management on the Project Staffing Page."""

from datetime import date, datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class EscalationResolveRequest(BaseModel):
    """Ops resolves an escalated confirmation."""
    resolution: str = Field(
        ..., pattern="^(confirm|reassign|cancel)$",
        description="How ops resolves this: confirm, reassign, or cancel",
    )
    resolution_note: Optional[str] = Field(
        None, max_length=1000,
        description="Ops explanation of the resolution",
    )
    # For reassign resolution
    new_technician_id: Optional[UUID] = Field(
        None,
        description="New technician to assign (required for 'reassign' resolution)",
    )
    new_start_date: Optional[date] = Field(
        None,
        description="New start date if reassigning",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "resolution": "reassign",
                "resolution_note": "Partner unresponsive. Reassigning to available tech.",
                "new_technician_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "new_start_date": "2026-04-20",
            }
        }


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class EscalationSummary(BaseModel):
    """Summary of a single escalated confirmation for the staffing page."""
    id: UUID
    confirmation_id: UUID
    assignment_id: UUID
    partner_id: UUID
    partner_name: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    role_id: Optional[str] = None
    role_name: Optional[str] = None
    technician_id: Optional[str] = None
    technician_name: Optional[str] = None
    confirmation_type: str
    requested_date: date
    requested_at: datetime
    escalated_at: Optional[datetime] = None
    hours_waiting: float = 0.0
    escalation_status: str
    resolution_note: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None

    class Config:
        from_attributes = True


class EscalationListResponse(BaseModel):
    """Escalation list returned to the staffing page."""
    escalations: List[EscalationSummary]
    total: int
    open_count: int
    resolved_count: int


class EscalationResolveResponse(BaseModel):
    """Response after resolving an escalation."""
    escalation: EscalationSummary
    assignment_updated: bool
    new_assignment_id: Optional[str] = None
    message: str


class ReassignmentCandidate(BaseModel):
    """A technician available for reassignment."""
    technician_id: str
    technician_name: str
    home_base_city: Optional[str] = None
    career_stage: Optional[str] = None
    deployability_status: Optional[str] = None
    available_from: Optional[str] = None
    matching_skills: List[str] = Field(default_factory=list)
    matching_certs: List[str] = Field(default_factory=list)


class ReassignmentCandidateList(BaseModel):
    """List of available technicians for reassignment."""
    candidates: List[ReassignmentCandidate]
    total: int
    role_name: Optional[str] = None
    required_skills: List[str] = Field(default_factory=list)
    required_certs: List[str] = Field(default_factory=list)
