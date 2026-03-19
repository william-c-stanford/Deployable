"""Timesheet CRUD endpoints with approval workflow.

When a timesheet is approved, a TIMESHEET_APPROVED event is dispatched
which triggers training hours accumulation and proficiency advancement checks.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.assignment import Assignment
from app.schemas.timesheet import (
    TimesheetCreate,
    TimesheetApprove,
    TimesheetFlag,
    TimesheetResponse,
    TimesheetListResponse,
)
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

router = APIRouter(prefix="/api/timesheets", tags=["timesheets"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_timesheet_or_404(db: Session, ts_id: uuid.UUID) -> Timesheet:
    ts = db.query(Timesheet).filter(Timesheet.id == ts_id).first()
    if not ts:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    return ts


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=TimesheetListResponse)
def list_timesheets(
    assignment_id: Optional[uuid.UUID] = Query(None),
    technician_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    week_start: Optional[str] = Query(None, description="Filter by week_start date (YYYY-MM-DD)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List timesheets, optionally filtered by assignment, technician, status, or week."""
    query = db.query(Timesheet)

    # Role-based scoping: technicians can only see their own timesheets
    if current_user.role == "technician":
        query = query.filter(Timesheet.technician_id == current_user.user_id)
    elif technician_id:
        query = query.filter(Timesheet.technician_id == technician_id)

    if assignment_id:
        query = query.filter(Timesheet.assignment_id == assignment_id)
    if status_filter:
        query = query.filter(Timesheet.status == status_filter)
    if week_start:
        from datetime import date as date_type
        try:
            ws = date_type.fromisoformat(week_start)
            query = query.filter(Timesheet.week_start == ws)
        except ValueError:
            pass

    total = query.count()
    items = query.order_by(Timesheet.submitted_at.desc()).offset(skip).limit(limit).all()
    return TimesheetListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/my", response_model=TimesheetListResponse)
def list_my_timesheets(
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List the current technician's own timesheets."""
    query = db.query(Timesheet).filter(
        Timesheet.technician_id == current_user.user_id
    )
    if status_filter:
        query = query.filter(Timesheet.status == status_filter)
    total = query.count()
    items = query.order_by(Timesheet.submitted_at.desc()).offset(skip).limit(limit).all()
    return TimesheetListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{timesheet_id}", response_model=TimesheetResponse)
def get_timesheet(
    timesheet_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    ts = _get_timesheet_or_404(db, timesheet_id)
    # Technicians can only view their own timesheets
    if current_user.role == "technician" and str(ts.technician_id) != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this timesheet")
    return ts


@router.post("", response_model=TimesheetResponse, status_code=status.HTTP_201_CREATED)
def submit_timesheet(
    data: TimesheetCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Submit a new timesheet for an assignment."""
    # Verify assignment exists
    assignment = db.query(Assignment).filter(Assignment.id == data.assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    ts = Timesheet(
        technician_id=assignment.technician_id,
        assignment_id=data.assignment_id,
        week_start=data.week_start,
        hours=data.hours,
        status=TimesheetStatus.SUBMITTED,
        skill_name=data.skill_name,
    )
    db.add(ts)
    db.commit()
    db.refresh(ts)

    # Dispatch: timesheet submitted
    dispatch_event_safe(EventPayload(
        event_type=EventType.TIMESHEET_SUBMITTED,
        entity_type="timesheet",
        entity_id=str(ts.id),
        actor_id=str(current_user.user_id),
        data={
            "assignment_id": str(data.assignment_id),
            "technician_id": str(assignment.technician_id),
            "hours": data.hours,
            "week_start": str(data.week_start),
            "skill_name": data.skill_name,
        },
    ))

    return ts


@router.post("/{timesheet_id}/approve", response_model=TimesheetResponse)
def approve_timesheet(
    timesheet_id: uuid.UUID,
    data: TimesheetApprove = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Approve a submitted timesheet. Only ops users can approve.

    When approved, dispatches TIMESHEET_APPROVED which triggers:
    1. Training hours accumulation on the technician's skill
    2. Proficiency advancement check (100h → Intermediate, 300h → Advanced)
    3. WebSocket notification if level changes
    """
    ts = _get_timesheet_or_404(db, timesheet_id)

    if ts.status != TimesheetStatus.SUBMITTED and ts.status != TimesheetStatus.FLAGGED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve timesheet with status '{ts.status.value if hasattr(ts.status, 'value') else ts.status}'. "
            f"Only Submitted or Flagged timesheets can be approved.",
        )

    ts.status = TimesheetStatus.APPROVED
    ts.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(ts)

    # Dispatch: timesheet approved → triggers training advancement checks
    dispatch_event_safe(EventPayload(
        event_type=EventType.TIMESHEET_APPROVED,
        entity_type="timesheet",
        entity_id=str(ts.id),
        actor_id=str(current_user.user_id),
        data={
            "assignment_id": str(ts.assignment_id),
            "technician_id": str(ts.technician_id),
            "hours": ts.hours,
            "week_start": str(ts.week_start),
            "skill_name": ts.skill_name,
        },
    ))

    return ts


@router.post("/{timesheet_id}/flag", response_model=TimesheetResponse)
def flag_timesheet(
    timesheet_id: uuid.UUID,
    data: TimesheetFlag,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Flag a timesheet for review."""
    ts = _get_timesheet_or_404(db, timesheet_id)

    if ts.status != TimesheetStatus.SUBMITTED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot flag timesheet with status '{ts.status.value if hasattr(ts.status, 'value') else ts.status}'",
        )

    ts.status = TimesheetStatus.FLAGGED
    ts.flag_comment = data.flag_comment
    ts.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(ts)

    # Dispatch: timesheet flagged
    dispatch_event_safe(EventPayload(
        event_type=EventType.TIMESHEET_FLAGGED,
        entity_type="timesheet",
        entity_id=str(ts.id),
        actor_id=str(current_user.user_id),
        data={
            "assignment_id": str(ts.assignment_id),
            "flag_comment": data.flag_comment,
        },
    ))

    return ts
