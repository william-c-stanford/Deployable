"""Partner timesheet approve/flag endpoints.

Partners can:
  - GET  /api/partner/timesheets          — list timesheets scoped to their projects
  - GET  /api/partner/timesheets/{id}     — get a single timesheet (scoped)
  - PUT  /api/partner/timesheets/{id}/approve — approve a submitted timesheet
  - PUT  /api/partner/timesheets/{id}/flag    — flag a timesheet with a comment
  - PUT  /api/partner/timesheets/{id}/resolve — resolve a previously flagged timesheet

Status transition rules:
  - Submitted → Approved  (partner or ops)
  - Submitted → Flagged   (partner or ops)
  - Flagged   → Resolved  (ops or original flagger)
  - Flagged   → Approved  (ops only — override)

All queries are scoped: partners only see timesheets for assignments
on their own projects. This is enforced via Project.partner_id join filtering.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import CurrentUser, get_current_user, require_role
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.assignment import Assignment
from app.models.project import Project, ProjectRole
from app.models.technician import Technician, TechnicianSkill
from app.models.skill_breakdown import SkillBreakdown, PartnerReviewStatus
from app.schemas.timesheet import (
    TimesheetApprove,
    TimesheetFlag,
    TimesheetResolve,
    PartnerTimesheetResponse,
    PartnerTimesheetListResponse,
)
from app.schemas.skill_breakdown import (
    PartnerSkillBreakdownSummary,
    SkillBreakdownItemResponse,
    SkillBreakdownResponse,
    PartnerSkillBreakdownReview,
)
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

logger = logging.getLogger("deployable.routers.partner_timesheets")

router = APIRouter(prefix="/api/partner/timesheets", tags=["partner-timesheets"])


# ---------------------------------------------------------------------------
# Helpers — partner scoping
# ---------------------------------------------------------------------------

def _to_uuid(val: str) -> uuid.UUID:
    """Coerce a string to UUID for safe column comparison."""
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def _get_partner_project_ids(db: Session, partner_id: str) -> list[uuid.UUID]:
    """Return all project IDs belonging to this partner."""
    rows = (
        db.query(Project.id)
        .filter(Project.partner_id == _to_uuid(partner_id))
        .all()
    )
    return [r[0] for r in rows]


def _get_partner_role_ids(db: Session, partner_id: str) -> list[uuid.UUID]:
    """Return all ProjectRole IDs for projects belonging to this partner."""
    rows = (
        db.query(ProjectRole.id)
        .join(Project, ProjectRole.project_id == Project.id)
        .filter(Project.partner_id == _to_uuid(partner_id))
        .all()
    )
    return [r[0] for r in rows]


def _partner_scoped_timesheets(db: Session, partner_id: str):
    """Return a SQLAlchemy query of timesheets scoped to the partner's projects."""
    return (
        db.query(Timesheet)
        .join(Assignment, Timesheet.assignment_id == Assignment.id)
        .join(ProjectRole, Assignment.role_id == ProjectRole.id)
        .join(Project, ProjectRole.project_id == Project.id)
        .filter(Project.partner_id == _to_uuid(partner_id))
    )


def _get_partner_scoped_timesheet(
    db: Session, partner_id: str, timesheet_id: uuid.UUID,
) -> Timesheet:
    """Fetch a single timesheet, enforcing partner project scope."""
    ts = (
        _partner_scoped_timesheets(db, partner_id)
        .filter(Timesheet.id == timesheet_id)
        .first()
    )
    if not ts:
        raise HTTPException(
            status_code=404,
            detail="Timesheet not found or does not belong to your projects",
        )
    return ts


def _get_skill_breakdown_summary(
    db: Session, assignment_id: uuid.UUID,
) -> Optional[PartnerSkillBreakdownSummary]:
    """Fetch the skill breakdown for an assignment and return a partner-facing summary."""
    breakdown = (
        db.query(SkillBreakdown)
        .filter(SkillBreakdown.assignment_id == assignment_id)
        .first()
    )
    if not breakdown:
        return None

    review_status = None
    if breakdown.partner_review_status:
        review_status = (
            breakdown.partner_review_status.value
            if hasattr(breakdown.partner_review_status, "value")
            else str(breakdown.partner_review_status)
        )

    return PartnerSkillBreakdownSummary(
        id=breakdown.id,
        overall_rating=(
            breakdown.overall_rating.value
            if breakdown.overall_rating and hasattr(breakdown.overall_rating, "value")
            else breakdown.overall_rating
        ),
        partner_review_status=review_status,
        partner_review_note=breakdown.partner_review_note,
        partner_reviewed_at=breakdown.partner_reviewed_at,
        items=[
            SkillBreakdownItemResponse(
                id=item.id,
                skill_name=item.skill_name,
                skill_id=item.skill_id,
                hours_applied=item.hours_applied,
                proficiency_rating=(
                    item.proficiency_rating.value
                    if hasattr(item.proficiency_rating, "value")
                    else str(item.proficiency_rating)
                ),
                notes=item.notes,
                created_at=item.created_at,
            )
            for item in (breakdown.items or [])
        ],
    )


def _enrich_partner_timesheet(
    ts: Timesheet, db: Session,
) -> PartnerTimesheetResponse:
    """Convert a Timesheet ORM object to a partner-facing response with context."""
    assignment = db.query(Assignment).filter(Assignment.id == ts.assignment_id).first()
    tech_name = None
    project_name = None
    role_name = None

    if assignment:
        tech = db.query(Technician).filter(Technician.id == assignment.technician_id).first()
        if tech:
            tech_name = f"{tech.first_name} {tech.last_name}"

        role = db.query(ProjectRole).filter(ProjectRole.id == assignment.role_id).first()
        if role:
            role_name = role.role_name
            project = db.query(Project).filter(Project.id == role.project_id).first()
            if project:
                project_name = project.name

    status_val = ts.status.value if hasattr(ts.status, "value") else str(ts.status)

    # Fetch skill breakdown summary for this assignment
    skill_breakdown = _get_skill_breakdown_summary(db, ts.assignment_id)

    return PartnerTimesheetResponse(
        id=ts.id,
        assignment_id=ts.assignment_id,
        week_start=ts.week_start,
        hours=ts.hours,
        status=status_val,
        flag_comment=ts.flag_comment,
        submitted_at=ts.submitted_at,
        reviewed_at=ts.reviewed_at,
        reviewed_by_role=ts.reviewed_by_role,
        skill_name=ts.skill_name,
        technician_name=tech_name,
        project_name=project_name,
        role_name=role_name,
        skill_breakdown=skill_breakdown,
    )


# ---------------------------------------------------------------------------
# Valid status transitions
# ---------------------------------------------------------------------------

PARTNER_TRANSITIONS = {
    # (current_status, action) → target_status
    (TimesheetStatus.SUBMITTED, "approve"): TimesheetStatus.APPROVED,
    (TimesheetStatus.SUBMITTED, "flag"): TimesheetStatus.FLAGGED,
    # Partners can resolve timesheets they themselves flagged
    (TimesheetStatus.FLAGGED, "resolve"): TimesheetStatus.RESOLVED,
}


def _validate_transition(
    ts: Timesheet, action: str, *, allow_ops_override: bool = False,
) -> TimesheetStatus:
    """Validate and return the target status for a transition.

    Raises HTTPException 400 if the transition is not allowed.
    """
    current = ts.status
    if isinstance(current, str):
        current = TimesheetStatus(current)

    key = (current, action)
    target = PARTNER_TRANSITIONS.get(key)

    if target is None and allow_ops_override:
        # Ops can force Flagged → Approved
        if current == TimesheetStatus.FLAGGED and action == "approve":
            target = TimesheetStatus.APPROVED

    if target is None:
        current_label = current.value if hasattr(current, "value") else str(current)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot {action} a timesheet with status '{current_label}'. "
                f"Check the allowed status transitions."
            ),
        )
    return target


# ---------------------------------------------------------------------------
# GET — list timesheets (partner-scoped)
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=PartnerTimesheetListResponse,
    summary="List timesheets for the partner's projects",
)
def list_partner_timesheets(
    project_id: Optional[uuid.UUID] = Query(None, description="Filter by project"),
    assignment_id: Optional[uuid.UUID] = Query(None, description="Filter by assignment"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    week_start: Optional[str] = Query(None, description="Filter by week_start (YYYY-MM-DD)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """List timesheets scoped to the partner's projects with optional filters."""
    partner_id = current_user.user_id

    query = _partner_scoped_timesheets(db, partner_id)

    # Apply optional filters
    if project_id:
        query = query.filter(Project.id == project_id)
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
            raise HTTPException(
                status_code=400,
                detail="Invalid week_start format. Use YYYY-MM-DD.",
            )

    total = query.count()

    # Counts for summary
    pending_count = (
        _partner_scoped_timesheets(db, partner_id)
        .filter(Timesheet.status == TimesheetStatus.SUBMITTED)
        .count()
    )
    flagged_count = (
        _partner_scoped_timesheets(db, partner_id)
        .filter(Timesheet.status == TimesheetStatus.FLAGGED)
        .count()
    )

    items = (
        query.order_by(Timesheet.submitted_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return PartnerTimesheetListResponse(
        items=[_enrich_partner_timesheet(ts, db) for ts in items],
        total=total,
        skip=skip,
        limit=limit,
        pending_count=pending_count,
        flagged_count=flagged_count,
    )


# ---------------------------------------------------------------------------
# GET — single timesheet (partner-scoped)
# ---------------------------------------------------------------------------

@router.get(
    "/{timesheet_id}",
    response_model=PartnerTimesheetResponse,
    summary="Get a single timesheet (partner-scoped)",
)
def get_partner_timesheet(
    timesheet_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Retrieve a single timesheet, enforcing partner project scope."""
    ts = _get_partner_scoped_timesheet(db, current_user.user_id, timesheet_id)
    return _enrich_partner_timesheet(ts, db)


# ---------------------------------------------------------------------------
# PUT — approve timesheet (partner)
# ---------------------------------------------------------------------------

@router.put(
    "/{timesheet_id}/approve",
    response_model=PartnerTimesheetResponse,
    summary="Partner approves a submitted timesheet",
)
def partner_approve_timesheet(
    timesheet_id: uuid.UUID,
    body: Optional[TimesheetApprove] = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Approve a submitted timesheet.

    Only timesheets in 'Submitted' status can be approved by a partner.
    On approval, dispatches TIMESHEET_PARTNER_APPROVED which triggers
    training hours accumulation and proficiency advancement checks.

    Status transition: Submitted → Approved
    """
    partner_id = current_user.user_id
    ts = _get_partner_scoped_timesheet(db, partner_id, timesheet_id)

    target_status = _validate_transition(ts, "approve")

    ts.status = target_status
    ts.reviewed_at = datetime.utcnow()
    ts.reviewed_by = current_user.user_id
    ts.reviewed_by_role = "partner"
    db.commit()
    db.refresh(ts)

    # Look up technician_id from the assignment
    assignment = db.query(Assignment).filter(Assignment.id == ts.assignment_id).first()
    technician_id = str(assignment.technician_id) if assignment else None

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.TIMESHEET_PARTNER_APPROVED,
        entity_type="timesheet",
        entity_id=str(ts.id),
        actor_id=current_user.user_id,
        data={
            "assignment_id": str(ts.assignment_id),
            "technician_id": technician_id,
            "hours": ts.hours,
            "week_start": str(ts.week_start),
            "skill_name": ts.skill_name,
            "partner_id": partner_id,
        },
    ))

    logger.info(
        "Timesheet %s approved by partner %s",
        timesheet_id, partner_id,
    )

    return _enrich_partner_timesheet(ts, db)


# ---------------------------------------------------------------------------
# PUT — flag timesheet (partner)
# ---------------------------------------------------------------------------

@router.put(
    "/{timesheet_id}/flag",
    response_model=PartnerTimesheetResponse,
    summary="Partner flags a submitted timesheet",
)
def partner_flag_timesheet(
    timesheet_id: uuid.UUID,
    body: TimesheetFlag,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Flag a submitted timesheet for review.

    Partners must provide a flag_comment explaining the issue.

    Status transition: Submitted → Flagged
    """
    partner_id = current_user.user_id
    ts = _get_partner_scoped_timesheet(db, partner_id, timesheet_id)

    target_status = _validate_transition(ts, "flag")

    ts.status = target_status
    ts.flag_comment = body.flag_comment
    ts.reviewed_at = datetime.utcnow()
    ts.reviewed_by = current_user.user_id
    ts.reviewed_by_role = "partner"
    db.commit()
    db.refresh(ts)

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.TIMESHEET_PARTNER_FLAGGED,
        entity_type="timesheet",
        entity_id=str(ts.id),
        actor_id=current_user.user_id,
        data={
            "assignment_id": str(ts.assignment_id),
            "flag_comment": body.flag_comment,
            "partner_id": partner_id,
        },
    ))

    logger.info(
        "Timesheet %s flagged by partner %s: %s",
        timesheet_id, partner_id, body.flag_comment[:100],
    )

    return _enrich_partner_timesheet(ts, db)


# ---------------------------------------------------------------------------
# PUT — resolve flagged timesheet (partner)
# ---------------------------------------------------------------------------

@router.put(
    "/{timesheet_id}/resolve",
    response_model=PartnerTimesheetResponse,
    summary="Partner resolves a flagged timesheet",
)
def partner_resolve_timesheet(
    timesheet_id: uuid.UUID,
    body: Optional[TimesheetResolve] = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Resolve a previously flagged timesheet.

    Optionally provide corrected hours. If not provided, the original hours
    are kept. The timesheet transitions to 'Resolved' status and dispatches
    a TIMESHEET_RESOLVED event which triggers training advancement.

    Status transition: Flagged → Resolved
    """
    partner_id = current_user.user_id
    ts = _get_partner_scoped_timesheet(db, partner_id, timesheet_id)

    target_status = _validate_transition(ts, "resolve")

    # Apply corrected hours if provided
    if body and body.corrected_hours is not None:
        ts.hours = body.corrected_hours

    resolution_note = body.resolution_note if body else None
    if resolution_note:
        ts.flag_comment = f"{ts.flag_comment or ''}\n[Resolved] {resolution_note}".strip()

    ts.status = target_status
    ts.reviewed_at = datetime.utcnow()
    ts.reviewed_by = current_user.user_id
    ts.reviewed_by_role = "partner"
    db.commit()
    db.refresh(ts)

    # Look up technician_id from the assignment
    assignment = db.query(Assignment).filter(Assignment.id == ts.assignment_id).first()
    technician_id = str(assignment.technician_id) if assignment else None

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.TIMESHEET_RESOLVED,
        entity_type="timesheet",
        entity_id=str(ts.id),
        actor_id=current_user.user_id,
        data={
            "assignment_id": str(ts.assignment_id),
            "technician_id": technician_id,
            "hours": ts.hours,
            "week_start": str(ts.week_start),
            "skill_name": ts.skill_name,
            "partner_id": partner_id,
            "corrected_hours": body.corrected_hours if body else None,
        },
    ))

    logger.info(
        "Timesheet %s resolved by partner %s",
        timesheet_id, partner_id,
    )

    return _enrich_partner_timesheet(ts, db)


# ---------------------------------------------------------------------------
# PUT — review skill breakdown (partner)
# ---------------------------------------------------------------------------

VALID_SKILL_REVIEW_ACTIONS = {"approve", "reject", "request_revision"}

SKILL_REVIEW_ACTION_MAP = {
    "approve": PartnerReviewStatus.APPROVED,
    "reject": PartnerReviewStatus.REJECTED,
    "request_revision": PartnerReviewStatus.REVISION_REQUESTED,
}


@router.put(
    "/{timesheet_id}/skill-breakdown/review",
    response_model=PartnerTimesheetResponse,
    summary="Partner reviews the skill breakdown associated with a timesheet",
)
def partner_review_skill_breakdown(
    timesheet_id: uuid.UUID,
    body: PartnerSkillBreakdownReview,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Review the skill breakdown associated with a timesheet's assignment.

    Partners can approve, reject, or request revision of the skill breakdown
    as part of the joint hours + skill review flow. The skill breakdown review
    status is tracked independently from the timesheet approval status.

    Actions:
      - approve: Partner confirms the skill breakdown is accurate
      - reject: Partner disagrees with the skill assessment
      - request_revision: Partner asks ops to revise the skill breakdown

    If the skill breakdown is rejected or revision-requested, it does NOT block
    the hours approval — the two are reviewed jointly but tracked separately.
    """
    partner_id = current_user.user_id

    # Validate action
    if body.action not in VALID_SKILL_REVIEW_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action '{body.action}'. Must be one of: {sorted(VALID_SKILL_REVIEW_ACTIONS)}",
        )

    # Fetch the timesheet (partner-scoped)
    ts = _get_partner_scoped_timesheet(db, partner_id, timesheet_id)

    # Find the skill breakdown for this assignment
    breakdown = (
        db.query(SkillBreakdown)
        .filter(SkillBreakdown.assignment_id == ts.assignment_id)
        .first()
    )
    if not breakdown:
        raise HTTPException(
            status_code=404,
            detail="No skill breakdown exists for this timesheet's assignment",
        )

    # Apply the review
    target_status = SKILL_REVIEW_ACTION_MAP[body.action]
    breakdown.partner_review_status = target_status
    breakdown.partner_review_note = body.note
    breakdown.partner_reviewed_at = datetime.utcnow()
    breakdown.partner_reviewed_by = partner_id

    db.commit()
    db.refresh(breakdown)
    db.refresh(ts)

    # Look up technician_id from the assignment
    assignment = db.query(Assignment).filter(Assignment.id == ts.assignment_id).first()
    technician_id = str(assignment.technician_id) if assignment else None

    # Dispatch event for skill breakdown review
    dispatch_event_safe(EventPayload(
        event_type=EventType.TIMESHEET_PARTNER_APPROVED,  # Re-use existing event to trigger downstream
        entity_type="skill_breakdown",
        entity_id=str(breakdown.id),
        actor_id=current_user.user_id,
        data={
            "action": body.action,
            "assignment_id": str(ts.assignment_id),
            "timesheet_id": str(ts.id),
            "technician_id": technician_id,
            "partner_id": partner_id,
            "partner_review_status": target_status.value,
            "partner_review_note": body.note,
        },
    ))

    logger.info(
        "Skill breakdown %s for timesheet %s %s by partner %s",
        breakdown.id, timesheet_id, body.action, partner_id,
    )

    return _enrich_partner_timesheet(ts, db)


# ---------------------------------------------------------------------------
# GET — skill breakdown for a timesheet (partner-scoped)
# ---------------------------------------------------------------------------

@router.get(
    "/{timesheet_id}/skill-breakdown",
    response_model=SkillBreakdownResponse,
    summary="Get skill breakdown for a timesheet's assignment (partner-scoped)",
)
def get_partner_timesheet_skill_breakdown(
    timesheet_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Get the full skill breakdown associated with a timesheet's assignment.

    Returns the detailed breakdown including all skill items, proficiency
    ratings, hours applied, and partner review status.
    """
    partner_id = current_user.user_id
    ts = _get_partner_scoped_timesheet(db, partner_id, timesheet_id)

    breakdown = (
        db.query(SkillBreakdown)
        .filter(SkillBreakdown.assignment_id == ts.assignment_id)
        .first()
    )
    if not breakdown:
        raise HTTPException(
            status_code=404,
            detail="No skill breakdown found for this timesheet's assignment",
        )

    return breakdown
