"""Ops dispute section — GET endpoint for flagged timesheets with project staffing context.

Returns timesheets that have been flagged (or resolved), joined with
assignment → technician, project role → project details so ops users can
see the full staffing context of each disputed timesheet.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.assignment import Assignment
from app.models.project import Project, ProjectRole
from app.models.technician import Technician
from app.schemas.timesheet import (
    DisputeTimesheetResponse,
    DisputeListResponse,
    DisputeAssignmentSummary,
    DisputeTechnicianSummary,
    DisputeProjectSummary,
    DisputeRoleSummary,
)

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


def _build_dispute_response(ts: Timesheet) -> DisputeTimesheetResponse:
    """Construct the enriched dispute response from a loaded Timesheet row."""
    assignment = ts.assignment
    technician = assignment.technician
    role = assignment.role
    project = role.project

    return DisputeTimesheetResponse(
        id=ts.id,
        assignment_id=ts.assignment_id,
        week_start=ts.week_start,
        hours=ts.hours,
        status=ts.status.value if hasattr(ts.status, "value") else str(ts.status),
        flag_comment=ts.flag_comment,
        submitted_at=ts.submitted_at,
        reviewed_at=ts.reviewed_at,
        skill_name=ts.skill_name,
        assignment=DisputeAssignmentSummary(
            id=assignment.id,
            start_date=assignment.start_date,
            end_date=assignment.end_date,
            technician=DisputeTechnicianSummary(
                id=technician.id,
                first_name=technician.first_name,
                last_name=technician.last_name,
                full_name=technician.full_name,
                email=technician.email,
                deployability_status=(
                    technician.deployability_status.value
                    if hasattr(technician.deployability_status, "value")
                    else str(technician.deployability_status)
                )
                if technician.deployability_status
                else None,
            ),
            role=DisputeRoleSummary(
                id=role.id,
                role_name=role.role_name,
            ),
            project=DisputeProjectSummary(
                id=project.id,
                name=project.name,
                partner_id=project.partner_id,
                status=(
                    project.status.value
                    if hasattr(project.status, "value")
                    else str(project.status)
                )
                if project.status
                else None,
                location_region=project.location_region,
            ),
        ),
    )


@router.get("", response_model=DisputeListResponse)
def list_disputes(
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by status: Flagged, Resolved, or omit for both",
    ),
    project_id: Optional[uuid.UUID] = Query(
        None,
        description="Filter disputes by project ID",
    ),
    technician_id: Optional[uuid.UUID] = Query(
        None,
        description="Filter disputes by technician ID",
    ),
    partner_id: Optional[uuid.UUID] = Query(
        None,
        description="Filter disputes by partner (project owner) ID",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """List flagged/disputed timesheets with full project staffing context.

    Only accessible by ops users.  By default returns timesheets with status
    Flagged or Resolved (the two dispute-related states).  Supports filtering
    by a single status, project, technician, or partner.
    """

    # Base query: timesheets joined with assignment → technician/role → project
    query = (
        db.query(Timesheet)
        .join(Assignment, Timesheet.assignment_id == Assignment.id)
        .join(Technician, Assignment.technician_id == Technician.id)
        .join(ProjectRole, Assignment.role_id == ProjectRole.id)
        .join(Project, ProjectRole.project_id == Project.id)
        .options(
            joinedload(Timesheet.assignment)
            .joinedload(Assignment.technician),
            joinedload(Timesheet.assignment)
            .joinedload(Assignment.role)
            .joinedload(ProjectRole.project),
        )
    )

    # Status filter — default to Flagged + Resolved
    if status_filter:
        # Validate the status value
        valid_dispute_statuses = {
            TimesheetStatus.FLAGGED.value,
            TimesheetStatus.RESOLVED.value,
        }
        if status_filter not in valid_dispute_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid dispute status filter: '{status_filter}'. "
                f"Must be one of: {sorted(valid_dispute_statuses)}",
            )
        query = query.filter(Timesheet.status == status_filter)
    else:
        query = query.filter(
            Timesheet.status.in_([
                TimesheetStatus.FLAGGED,
                TimesheetStatus.RESOLVED,
            ])
        )

    # Project filter
    if project_id:
        query = query.filter(Project.id == project_id)

    # Technician filter
    if technician_id:
        query = query.filter(Technician.id == technician_id)

    # Partner filter
    if partner_id:
        query = query.filter(Project.partner_id == partner_id)

    # Counts (before pagination)
    total = query.count()

    # Also compute separate counts for flagged vs resolved
    flagged_count = (
        query.filter(Timesheet.status == TimesheetStatus.FLAGGED).count()
    )
    resolved_count = (
        query.filter(Timesheet.status == TimesheetStatus.RESOLVED).count()
    )

    # Paginate, ordered by most recently reviewed/flagged first
    items = (
        query
        .order_by(Timesheet.reviewed_at.desc().nullslast(), Timesheet.submitted_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return DisputeListResponse(
        items=[_build_dispute_response(ts) for ts in items],
        total=total,
        skip=skip,
        limit=limit,
        flagged_count=flagged_count,
        resolved_count=resolved_count,
    )


@router.get("/{timesheet_id}", response_model=DisputeTimesheetResponse)
def get_dispute(
    timesheet_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get a single flagged timesheet with full staffing context."""
    ts = (
        db.query(Timesheet)
        .options(
            joinedload(Timesheet.assignment)
            .joinedload(Assignment.technician),
            joinedload(Timesheet.assignment)
            .joinedload(Assignment.role)
            .joinedload(ProjectRole.project),
        )
        .filter(Timesheet.id == timesheet_id)
        .first()
    )

    if not ts:
        raise HTTPException(status_code=404, detail="Timesheet not found")

    if ts.status not in (TimesheetStatus.FLAGGED, TimesheetStatus.RESOLVED):
        raise HTTPException(
            status_code=400,
            detail=f"Timesheet status is '{ts.status.value if hasattr(ts.status, 'value') else ts.status}', "
            f"not a disputed timesheet (Flagged or Resolved).",
        )

    return _build_dispute_response(ts)


@router.post("/{timesheet_id}/resolve", response_model=DisputeTimesheetResponse)
def resolve_dispute(
    timesheet_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Resolve a flagged timesheet dispute.

    Moves the timesheet from Flagged → Resolved.  This is distinct from
    approving (which moves to Approved and triggers training advancement).
    """
    from datetime import datetime
    from app.workers.dispatcher import dispatch_event_safe
    from app.workers.events import EventPayload, EventType

    ts = (
        db.query(Timesheet)
        .options(
            joinedload(Timesheet.assignment)
            .joinedload(Assignment.technician),
            joinedload(Timesheet.assignment)
            .joinedload(Assignment.role)
            .joinedload(ProjectRole.project),
        )
        .filter(Timesheet.id == timesheet_id)
        .first()
    )

    if not ts:
        raise HTTPException(status_code=404, detail="Timesheet not found")

    if ts.status != TimesheetStatus.FLAGGED:
        raise HTTPException(
            status_code=400,
            detail=f"Can only resolve Flagged timesheets. Current status: "
            f"'{ts.status.value if hasattr(ts.status, 'value') else ts.status}'",
        )

    ts.status = TimesheetStatus.RESOLVED
    ts.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(ts)

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.TIMESHEET_RESOLVED,
        entity_type="timesheet",
        entity_id=str(ts.id),
        actor_id=str(current_user.user_id),
        data={
            "assignment_id": str(ts.assignment_id),
            "flag_comment": ts.flag_comment,
        },
    ))

    return _build_dispute_response(ts)
