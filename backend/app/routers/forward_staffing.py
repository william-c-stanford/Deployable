"""Forward Staffing API — 90-day schedule management with assignment chaining.

Endpoints for:
- Viewing the 90-day forward staffing schedule
- Creating / updating / confirming pre-booked assignments
- Creating and managing assignment chains
- Per-technician schedule summaries with gap analysis
"""

import uuid
from datetime import date, timedelta, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.assignment import Assignment, AssignmentType, AssignmentStatus, ChainPriority
from app.models.project import ProjectRole, Project
from app.models.technician import Technician
from app.models.recommendation import Recommendation, RecommendationStatus
from app.workers.events import EventPayload, EventType
from app.workers.dispatcher import dispatch_event_safe
from app.schemas.forward_staffing import (
    ForwardScheduleResponse,
    ForwardScheduleEntry,
    ForwardAssignmentCreate,
    ForwardAssignmentUpdate,
    AssignmentConfirmRequest,
    AssignmentChain,
    AssignmentChainLink,
    ChainCreateRequest,
    TechnicianScheduleSummary,
    TechnicianGap,
)

router = APIRouter(prefix="/api/forward-staffing", tags=["forward-staffing"])

DEFAULT_LOOKAHEAD_DAYS = 90


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assignment_to_entry(a: Assignment) -> ForwardScheduleEntry:
    """Convert an Assignment ORM object to a ForwardScheduleEntry schema."""
    role = a.role
    project = role.project if role else None
    tech = a.technician
    return ForwardScheduleEntry(
        assignment_id=str(a.id),
        technician_id=str(a.technician_id),
        technician_name=tech.full_name if tech else "Unknown",
        role_id=str(a.role_id),
        role_name=role.role_name if role else "Unknown",
        project_id=str(project.id) if project else "",
        project_name=project.name if project else "Unknown",
        start_date=a.start_date,
        end_date=a.end_date,
        status=a.status or "Active",
        assignment_type=a.assignment_type.value if a.assignment_type else "Active",
        is_forward_booked=bool(a.is_forward_booked),
        booking_confidence=a.booking_confidence,
        chain_id=str(a.chain_id) if a.chain_id else None,
        chain_position=a.chain_position,
        gap_days=a.gap_days,
        partner_confirmed_start=bool(a.partner_confirmed_start),
    )


def _build_chain(assignments: List[Assignment]) -> AssignmentChain:
    """Build an AssignmentChain from a list of chain-linked assignments."""
    assignments = sorted(assignments, key=lambda a: (a.chain_position or 0, a.start_date))
    first = assignments[0]
    tech = first.technician

    links = []
    total_gap = 0
    for a in assignments:
        role = a.role
        project = role.project if role else None
        links.append(AssignmentChainLink(
            assignment_id=str(a.id),
            technician_id=str(a.technician_id),
            technician_name=tech.full_name if tech else "Unknown",
            role_id=str(a.role_id),
            role_name=role.role_name if role else "Unknown",
            project_id=str(project.id) if project else None,
            project_name=project.name if project else None,
            start_date=a.start_date,
            end_date=a.end_date,
            status=a.status or "Active",
            assignment_type=a.assignment_type.value if a.assignment_type else "Active",
            chain_position=a.chain_position,
            gap_days=a.gap_days,
            chain_notes=a.chain_notes,
            booking_confidence=a.booking_confidence,
            is_forward_booked=bool(a.is_forward_booked),
            confirmed_at=a.confirmed_at,
        ))
        if a.gap_days:
            total_gap += a.gap_days

    first_start = assignments[0].start_date
    last_end = assignments[-1].end_date
    total_dur = (last_end - first_start).days if first_start and last_end else None

    return AssignmentChain(
        chain_id=str(first.chain_id),
        technician_id=str(first.technician_id),
        technician_name=tech.full_name if tech else "Unknown",
        chain_priority=first.chain_priority.value if first.chain_priority else None,
        total_duration_days=total_dur,
        total_gap_days=total_gap,
        links=links,
    )


def _find_gaps(
    assignments: List[Assignment],
    window_start: date,
    window_end: date,
) -> List[TechnicianGap]:
    """Identify scheduling gaps for a technician within a date window."""
    if not assignments:
        return []

    gaps = []
    sorted_a = sorted(assignments, key=lambda a: a.start_date)
    tech = sorted_a[0].technician

    for i in range(len(sorted_a) - 1):
        current = sorted_a[i]
        next_a = sorted_a[i + 1]
        if current.end_date and next_a.start_date:
            gap_start = current.end_date + timedelta(days=1)
            gap_end = next_a.start_date - timedelta(days=1)
            gap_days = (gap_end - gap_start).days + 1
            if gap_days > 0:
                cur_role = current.role
                cur_proj = cur_role.project if cur_role else None
                next_role = next_a.role
                next_proj = next_role.project if next_role else None
                gaps.append(TechnicianGap(
                    technician_id=str(current.technician_id),
                    technician_name=tech.full_name if tech else "Unknown",
                    gap_start=gap_start,
                    gap_end=gap_end,
                    gap_days=gap_days,
                    previous_assignment_id=str(current.id),
                    next_assignment_id=str(next_a.id),
                    previous_project_name=cur_proj.name if cur_proj else None,
                    next_project_name=next_proj.name if next_proj else None,
                ))

    return gaps


# ---------------------------------------------------------------------------
# 90-Day Forward Schedule
# ---------------------------------------------------------------------------

@router.get(
    "/schedule",
    response_model=ForwardScheduleResponse,
    summary="Get 90-day forward staffing schedule",
    description=(
        "Returns all assignments (active + pre-booked) within a configurable "
        "lookahead window. Includes gap analysis across all technicians."
    ),
)
def get_forward_schedule(
    lookahead_days: int = Query(DEFAULT_LOOKAHEAD_DAYS, ge=1, le=365),
    project_id: Optional[str] = Query(None, description="Filter by project"),
    technician_id: Optional[str] = Query(None, description="Filter by technician"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    include_gaps: bool = Query(True, description="Include gap analysis"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    today = date.today()
    window_end = today + timedelta(days=lookahead_days)

    query = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(
            or_(
                # Assignments overlapping the window
                and_(
                    Assignment.start_date <= window_end,
                    or_(Assignment.end_date >= today, Assignment.end_date.is_(None)),
                ),
                # Forward-booked within window
                and_(
                    Assignment.is_forward_booked.is_(True),
                    Assignment.start_date <= window_end,
                ),
            )
        )
        .filter(Assignment.status != "Cancelled")
    )

    if project_id:
        query = query.join(Assignment.role).filter(ProjectRole.project_id == project_id)
    if technician_id:
        query = query.filter(Assignment.technician_id == technician_id)
    if status_filter:
        query = query.filter(Assignment.status == status_filter)

    assignments = query.order_by(Assignment.start_date).all()

    entries = [_assignment_to_entry(a) for a in assignments]
    active_count = sum(1 for e in entries if e.assignment_type == "Active")
    pre_booked_count = sum(1 for e in entries if e.is_forward_booked)
    chained_count = sum(1 for e in entries if e.chain_id is not None)

    # Gap analysis
    gaps: List[TechnicianGap] = []
    if include_gaps and assignments:
        tech_groups: dict = {}
        for a in assignments:
            tid = str(a.technician_id)
            tech_groups.setdefault(tid, []).append(a)
        for tid, tech_assignments in tech_groups.items():
            gaps.extend(_find_gaps(tech_assignments, today, window_end))

    return ForwardScheduleResponse(
        schedule_start=today,
        schedule_end=window_end,
        total_assignments=len(entries),
        active_count=active_count,
        pre_booked_count=pre_booked_count,
        chained_count=chained_count,
        entries=entries,
        gaps=sorted(gaps, key=lambda g: g.gap_start),
    )


# ---------------------------------------------------------------------------
# CRUD: Forward (pre-booked) assignments
# ---------------------------------------------------------------------------

@router.post(
    "/assignments",
    response_model=ForwardScheduleEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Create a forward-booked assignment",
)
def create_forward_assignment(
    data: ForwardAssignmentCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Create a pre-booked assignment for forward staffing.

    Optionally chains it to an existing assignment by specifying
    `chain_to_assignment_id`.
    """
    # Validate technician exists
    tech = db.query(Technician).filter(Technician.id == data.technician_id).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")

    # Validate role exists
    role = (
        db.query(ProjectRole)
        .options(joinedload(ProjectRole.project))
        .filter(ProjectRole.id == data.role_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Project role not found")

    # Check for date conflicts
    conflicts = (
        db.query(Assignment)
        .filter(
            Assignment.technician_id == data.technician_id,
            Assignment.status != "Cancelled",
            Assignment.start_date <= (data.end_date or date(2099, 12, 31)),
            or_(
                Assignment.end_date >= data.start_date,
                Assignment.end_date.is_(None),
            ),
        )
        .all()
    )
    if conflicts:
        conflict_ids = [str(c.id) for c in conflicts]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "Date conflict with existing assignments",
                "conflicting_assignment_ids": conflict_ids,
            },
        )

    # Build the assignment
    assignment = Assignment(
        technician_id=data.technician_id,
        role_id=data.role_id,
        start_date=data.start_date,
        end_date=data.end_date,
        hourly_rate=data.hourly_rate or role.hourly_rate,
        per_diem=data.per_diem or role.per_diem,
        assignment_type=AssignmentType.PRE_BOOKED,
        status="Pre-Booked",
        is_forward_booked=True,
        booking_confidence=data.booking_confidence,
    )

    # Handle chaining
    if data.chain_to_assignment_id:
        prev = db.query(Assignment).filter(
            Assignment.id == data.chain_to_assignment_id
        ).first()
        if not prev:
            raise HTTPException(status_code=404, detail="Previous assignment not found for chaining")
        if str(prev.technician_id) != data.technician_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot chain assignments for different technicians",
            )

        # Determine chain ID and position
        chain_id = prev.chain_id or uuid.uuid4()
        if not prev.chain_id:
            prev.chain_id = chain_id
            prev.chain_position = 1

        # Find max position in this chain
        max_pos = (
            db.query(Assignment.chain_position)
            .filter(Assignment.chain_id == chain_id)
            .order_by(Assignment.chain_position.desc())
            .first()
        )
        next_pos = (max_pos[0] or 0) + 1 if max_pos else 2

        assignment.chain_id = chain_id
        assignment.chain_position = next_pos
        assignment.previous_assignment_id = prev.id
        assignment.chain_priority = (
            ChainPriority(data.chain_priority) if data.chain_priority else prev.chain_priority
        )
        assignment.chain_notes = data.chain_notes

        # Calculate gap days
        if prev.end_date:
            assignment.gap_days = (data.start_date - prev.end_date).days - 1

        # Link previous → next
        prev.next_assignment_id = assignment.id

    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    return _assignment_to_entry(assignment)


@router.get(
    "/assignments/{assignment_id}",
    response_model=ForwardScheduleEntry,
    summary="Get a specific assignment",
)
def get_assignment(
    assignment_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "partner")),
):
    assignment = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(Assignment.id == assignment_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return _assignment_to_entry(assignment)


@router.patch(
    "/assignments/{assignment_id}",
    response_model=ForwardScheduleEntry,
    summary="Update a forward-booked assignment",
)
def update_forward_assignment(
    assignment_id: str,
    data: ForwardAssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    assignment = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(Assignment.id == assignment_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "chain_priority" and value is not None:
            setattr(assignment, field, ChainPriority(value))
        else:
            setattr(assignment, field, value)

    db.commit()
    db.refresh(assignment)
    return _assignment_to_entry(assignment)


@router.delete(
    "/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a forward-booked assignment",
)
def cancel_forward_assignment(
    assignment_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    assignment.status = "Cancelled"

    # Unlink chain references
    if assignment.previous_assignment_id:
        prev = db.query(Assignment).filter(
            Assignment.id == assignment.previous_assignment_id
        ).first()
        if prev:
            prev.next_assignment_id = assignment.next_assignment_id

    if assignment.next_assignment_id:
        nxt = db.query(Assignment).filter(
            Assignment.id == assignment.next_assignment_id
        ).first()
        if nxt:
            nxt.previous_assignment_id = assignment.previous_assignment_id

    db.commit()
    return None


# ---------------------------------------------------------------------------
# Confirm a pre-booked assignment → active
# ---------------------------------------------------------------------------

@router.post(
    "/assignments/{assignment_id}/confirm",
    response_model=ForwardScheduleEntry,
    summary="Confirm a pre-booked assignment to active status",
)
def confirm_assignment(
    assignment_id: str,
    data: AssignmentConfirmRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Human approval gate: Confirm a forward-booked assignment to active.

    This transitions the assignment from Pre-Booked to Active status,
    records the confirming user, and updates the booking confidence to 1.0.
    """
    assignment = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(Assignment.id == assignment_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if assignment.status == "Cancelled":
        raise HTTPException(status_code=400, detail="Cannot confirm a cancelled assignment")

    if assignment.status == "Active" and not assignment.is_forward_booked:
        raise HTTPException(status_code=400, detail="Assignment is already active")

    assignment.assignment_type = AssignmentType.ACTIVE
    assignment.status = "Active"
    assignment.is_forward_booked = False
    assignment.booking_confidence = 1.0
    assignment.confirmed_at = datetime.utcnow()
    assignment.confirmed_by = data.confirmed_by or current_user.user_id

    db.commit()
    db.refresh(assignment)
    return _assignment_to_entry(assignment)


# ---------------------------------------------------------------------------
# Assignment Chains
# ---------------------------------------------------------------------------

@router.post(
    "/chains",
    response_model=AssignmentChain,
    status_code=status.HTTP_201_CREATED,
    summary="Create an assignment chain",
)
def create_chain(
    data: ChainCreateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Create a chain of back-to-back assignments for a technician.

    The chain links assignments sequentially so the technician rolls
    from one project to the next with minimal downtime.
    """
    tech = db.query(Technician).filter(Technician.id == data.technician_id).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")

    if len(data.assignments) < 2:
        raise HTTPException(
            status_code=400,
            detail="A chain must contain at least 2 assignments",
        )

    # Sort by start_date
    sorted_entries = sorted(data.assignments, key=lambda e: e.start_date)

    chain_id = uuid.uuid4()
    chain_priority = ChainPriority(data.chain_priority) if data.chain_priority else ChainPriority.MEDIUM
    created_assignments: List[Assignment] = []

    for i, entry in enumerate(sorted_entries):
        role = (
            db.query(ProjectRole)
            .options(joinedload(ProjectRole.project))
            .filter(ProjectRole.id == entry.role_id)
            .first()
        )
        if not role:
            raise HTTPException(
                status_code=404,
                detail=f"Project role not found: {entry.role_id}",
            )

        gap_days = None
        if i > 0 and sorted_entries[i - 1].end_date:
            gap_days = (entry.start_date - sorted_entries[i - 1].end_date).days - 1

        assignment = Assignment(
            technician_id=data.technician_id,
            role_id=entry.role_id,
            start_date=entry.start_date,
            end_date=entry.end_date,
            hourly_rate=entry.hourly_rate or role.hourly_rate,
            per_diem=entry.per_diem or role.per_diem,
            assignment_type=AssignmentType.PRE_BOOKED,
            status="Pre-Booked",
            is_forward_booked=True,
            booking_confidence=entry.booking_confidence,
            chain_id=chain_id,
            chain_position=i + 1,
            chain_priority=chain_priority,
            gap_days=max(gap_days, 0) if gap_days is not None else None,
            chain_notes=entry.chain_notes,
        )
        db.add(assignment)
        db.flush()  # get the ID
        created_assignments.append(assignment)

    # Link previous/next pointers
    for i in range(len(created_assignments)):
        if i > 0:
            created_assignments[i].previous_assignment_id = created_assignments[i - 1].id
        if i < len(created_assignments) - 1:
            created_assignments[i].next_assignment_id = created_assignments[i + 1].id

    db.commit()

    # Refresh all to get relationships
    for a in created_assignments:
        db.refresh(a)

    return _build_chain(created_assignments)


@router.get(
    "/chains/{chain_id}",
    response_model=AssignmentChain,
    summary="Get an assignment chain",
)
def get_chain(
    chain_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "partner")),
):
    assignments = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(Assignment.chain_id == chain_id)
        .order_by(Assignment.chain_position)
        .all()
    )
    if not assignments:
        raise HTTPException(status_code=404, detail="Chain not found")
    return _build_chain(assignments)


@router.get(
    "/chains",
    response_model=List[AssignmentChain],
    summary="List all active assignment chains",
)
def list_chains(
    technician_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    query = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(Assignment.chain_id.isnot(None))
        .filter(Assignment.status != "Cancelled")
    )
    if technician_id:
        query = query.filter(Assignment.technician_id == technician_id)

    all_chained = query.order_by(Assignment.chain_id, Assignment.chain_position).all()

    # Group by chain_id
    chains_map: dict = {}
    for a in all_chained:
        cid = str(a.chain_id)
        chains_map.setdefault(cid, []).append(a)

    return [_build_chain(assignments) for assignments in chains_map.values()]


# ---------------------------------------------------------------------------
# Per-technician schedule summary
# ---------------------------------------------------------------------------

@router.get(
    "/technician/{technician_id}/schedule",
    response_model=TechnicianScheduleSummary,
    summary="Get a technician's forward schedule summary",
)
def get_technician_schedule(
    technician_id: str,
    lookahead_days: int = Query(DEFAULT_LOOKAHEAD_DAYS, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "technician")),
):
    tech = db.query(Technician).filter(Technician.id == technician_id).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")

    today = date.today()
    window_end = today + timedelta(days=lookahead_days)

    assignments = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(
            Assignment.technician_id == technician_id,
            Assignment.status != "Cancelled",
            Assignment.start_date <= window_end,
            or_(Assignment.end_date >= today, Assignment.end_date.is_(None)),
        )
        .order_by(Assignment.start_date)
        .all()
    )

    entries = [_assignment_to_entry(a) for a in assignments]

    # Current vs upcoming
    current = None
    upcoming = []
    for e in entries:
        if e.start_date <= today and (e.end_date is None or e.end_date >= today):
            current = e
        elif e.start_date > today:
            upcoming.append(e)

    # Build chains
    chain_groups: dict = {}
    for a in assignments:
        if a.chain_id:
            cid = str(a.chain_id)
            chain_groups.setdefault(cid, []).append(a)
    chains = [_build_chain(group) for group in chain_groups.values()]

    # Calculate utilization
    total_booked = 0
    for a in assignments:
        a_start = max(a.start_date, today)
        a_end = min(a.end_date, window_end) if a.end_date else window_end
        if a_end >= a_start:
            total_booked += (a_end - a_start).days + 1

    utilization = round((total_booked / lookahead_days) * 100, 1) if lookahead_days > 0 else 0

    # Determine available_from
    available_from = tech.available_from
    if current and current.end_date:
        available_from = current.end_date + timedelta(days=1)
    elif not current and not upcoming:
        available_from = today

    return TechnicianScheduleSummary(
        technician_id=str(tech.id),
        technician_name=tech.full_name,
        current_assignment=current,
        upcoming_assignments=upcoming,
        chains=chains,
        available_from=available_from,
        total_booked_days=total_booked,
        utilization_pct=utilization,
    )


# ---------------------------------------------------------------------------
# Forward Staffing Agent — Background Recommendation Endpoints
# ---------------------------------------------------------------------------

import logging

_fwd_logger = logging.getLogger("deployable.routers.forward_staffing.agent")


@router.post(
    "/scan",
    summary="Trigger a forward staffing recommendation scan",
    description=(
        "Dispatches a background Celery task that analyzes the 90-day window "
        "for assignment gaps, matches available technicians using the scoring engine, "
        "generates LangChain/Claude Haiku explanations, creates pending recommendations, "
        "and pushes results via WebSocket to the ops dashboard."
    ),
)
def trigger_forward_staffing_scan(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    task_ids = dispatch_event_safe(
        EventPayload(
            event_type=EventType.FORWARD_STAFFING_SCAN_TRIGGERED,
            entity_type="system",
            entity_id="manual_scan",
            actor_id=str(current_user.user_id),
            data={"trigger": "manual", "requested_by": current_user.user_id},
        )
    )

    return {
        "status": "scan_dispatched",
        "task_ids": task_ids,
        "message": "Forward staffing scan initiated. Results will be pushed via WebSocket.",
    }


@router.get(
    "/gaps",
    summary="Get current forward staffing gaps (synchronous)",
    description=(
        "Runs a lightweight 90-day gap analysis synchronously (no LLM). "
        "Returns rolloff gaps and unfilled roles with matched candidates. "
        "For full LLM-powered analysis with NL summaries, use POST /scan."
    ),
)
def get_forward_staffing_gaps(
    urgency: Optional[str] = Query(None, description="Filter by urgency: critical, high, medium, low"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    from app.services.forward_staffing_service import (
        run_forward_staffing_scan,
        serialize_scan_result,
    )

    result = run_forward_staffing_scan(db)
    serialized = serialize_scan_result(result)

    if urgency:
        serialized["gaps"] = [
            g for g in serialized["gaps"]
            if g.get("role", {}).get("urgency") == urgency
        ]

    return serialized


@router.get(
    "/recommendations",
    summary="Get forward staffing recommendations",
    description=(
        "Returns recommendations generated by the forward staffing background agent, "
        "ordered by urgency score descending. Filter by role, project, urgency, or status."
    ),
)
def get_forward_staffing_recommendations(
    role_id: Optional[str] = Query(None, description="Filter by role ID"),
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
    urgency: Optional[str] = Query(None, description="Filter by urgency"),
    rec_status: Optional[str] = Query("Pending", alias="status", description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    query = db.query(Recommendation).filter(
        Recommendation.recommendation_type == "forward_staffing",
    )

    if rec_status:
        query = query.filter(Recommendation.status == rec_status)
    if role_id:
        query = query.filter(Recommendation.role_id == role_id)
    if project_id:
        query = query.filter(Recommendation.project_id == project_id)

    recs = query.order_by(
        Recommendation.overall_score.desc(),
        Recommendation.rank.asc(),
    ).limit(limit).all()

    results = []
    for rec in recs:
        meta = rec.metadata_ or {}
        if urgency and meta.get("urgency") != urgency:
            continue

        results.append({
            "id": str(rec.id),
            "recommendation_type": rec.recommendation_type,
            "technician_id": rec.technician_id,
            "role_id": rec.role_id,
            "project_id": rec.project_id,
            "rank": rec.rank,
            "overall_score": rec.overall_score,
            "scorecard": rec.scorecard,
            "explanation": rec.explanation,
            "status": rec.status,
            "batch_id": rec.batch_id,
            "metadata": meta,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
        })

    return {
        "recommendations": results,
        "total": len(results),
    }


@router.get(
    "/summary",
    summary="Get forward staffing posture summary",
    description=(
        "Aggregates pending forward staffing recommendations and gap counts "
        "for the ops dashboard forward staffing widget."
    ),
)
def get_forward_staffing_summary(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    pending_recs = db.query(Recommendation).filter(
        Recommendation.recommendation_type == "forward_staffing",
        Recommendation.status == RecommendationStatus.PENDING.value,
    ).all()

    roles_with_gaps = set()
    urgency_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    for rec in pending_recs:
        if rec.role_id:
            roles_with_gaps.add(rec.role_id)
        meta = rec.metadata_ or {}
        urg = meta.get("urgency", "medium")
        if urg in urgency_counts:
            urgency_counts[urg] += 1

    latest_rec = db.query(Recommendation).filter(
        Recommendation.recommendation_type == "forward_staffing",
    ).order_by(Recommendation.created_at.desc()).first()

    last_scan = latest_rec.created_at.isoformat() if latest_rec and latest_rec.created_at else None

    return {
        "pending_recommendations": len(pending_recs),
        "roles_with_gaps": len(roles_with_gaps),
        "urgency_breakdown": urgency_counts,
        "last_scan": last_scan,
        "window_days": 90,
    }
