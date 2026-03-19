"""Skill breakdown CRUD endpoints for assignment completion reviews.

Skill breakdowns capture which skills a technician performed during an
assignment, their proficiency ratings, hours per skill, and notes. They can
be submitted by both ops users and technicians (for their own assignments)
when the assignment is marked as complete.

Partners can approve, reject, or request revisions to skill breakdowns.
All lifecycle events emit real-time WebSocket notifications.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.assignment import Assignment, AssignmentStatus
from app.models.skill_breakdown import (
    SkillBreakdown,
    SkillBreakdownItem,
    SkillProficiencyRating,
    PartnerReviewStatus,
)
from app.schemas.skill_breakdown import (
    SkillBreakdownCreate,
    SkillBreakdownResponse,
    PartnerSkillBreakdownReview,
)
from app.workers.events import EventPayload, EventType
from app.workers.dispatcher import dispatch_event_safe

router = APIRouter(prefix="/api/assignments", tags=["skill-breakdowns"])

VALID_RATINGS = {r.value for r in SkillProficiencyRating}
VALID_REVIEW_ACTIONS = {"approve", "reject", "request_revision"}


def _get_assignment_or_404(db: Session, assignment_id: uuid.UUID) -> Assignment:
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return assignment


def _get_breakdown_or_404(db: Session, assignment_id: uuid.UUID) -> SkillBreakdown:
    breakdown = db.query(SkillBreakdown).filter(
        SkillBreakdown.assignment_id == assignment_id
    ).first()
    if not breakdown:
        raise HTTPException(
            status_code=404,
            detail="No skill breakdown found for this assignment",
        )
    return breakdown


def _serialize_breakdown(breakdown: SkillBreakdown) -> dict:
    """Serialize a skill breakdown for WebSocket broadcast."""
    return {
        "id": str(breakdown.id),
        "assignment_id": str(breakdown.assignment_id),
        "technician_id": str(breakdown.technician_id),
        "submitted_by": breakdown.submitted_by,
        "overall_rating": (
            breakdown.overall_rating.value
            if hasattr(breakdown.overall_rating, "value")
            else breakdown.overall_rating
        ),
        "overall_notes": breakdown.overall_notes,
        "partner_review_status": (
            breakdown.partner_review_status.value
            if hasattr(breakdown.partner_review_status, "value")
            else breakdown.partner_review_status
        ),
        "partner_review_note": breakdown.partner_review_note,
        "partner_reviewed_by": breakdown.partner_reviewed_by,
        "submitted_at": (
            breakdown.submitted_at.isoformat() if breakdown.submitted_at else None
        ),
        "updated_at": (
            breakdown.updated_at.isoformat() if breakdown.updated_at else None
        ),
        "partner_reviewed_at": (
            breakdown.partner_reviewed_at.isoformat()
            if breakdown.partner_reviewed_at
            else None
        ),
        "item_count": len(breakdown.items) if breakdown.items else 0,
        "items": [
            {
                "id": str(item.id),
                "skill_name": item.skill_name,
                "skill_id": str(item.skill_id) if item.skill_id else None,
                "hours_applied": item.hours_applied,
                "proficiency_rating": (
                    item.proficiency_rating.value
                    if hasattr(item.proficiency_rating, "value")
                    else item.proficiency_rating
                ),
                "notes": item.notes,
            }
            for item in (breakdown.items or [])
        ],
    }


async def _broadcast_skill_breakdown_event(
    event_type_str: str,
    breakdown: SkillBreakdown,
    partner_id: str | None = None,
):
    """Fire WebSocket broadcast for skill breakdown events."""
    from app.websocket import broadcast_skill_breakdown_event

    breakdown_data = _serialize_breakdown(breakdown)
    await broadcast_skill_breakdown_event(
        event_type=event_type_str,
        breakdown_data=breakdown_data,
        technician_id=str(breakdown.technician_id),
        partner_id=partner_id,
    )


def _get_partner_id_for_assignment(db: Session, assignment: Assignment) -> str | None:
    """Get the partner ID associated with an assignment's project."""
    from app.models.project import Project

    if assignment.project_id:
        project = (
            db.query(Project).filter(Project.id == assignment.project_id).first()
        )
        if project and hasattr(project, "partner_id") and project.partner_id:
            return str(project.partner_id)
    return None


@router.post(
    "/{assignment_id}/complete",
    status_code=status.HTTP_200_OK,
)
def mark_assignment_complete(
    assignment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Mark an assignment as Completed.

    Technicians can mark their own assignments complete.
    Ops users can mark any assignment complete.
    """
    assignment = _get_assignment_or_404(db, assignment_id)

    # Technicians can only complete their own assignments
    if current_user.role == "technician":
        if str(assignment.technician_id) != current_user.user_id:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to complete this assignment",
            )

    assignment_status = assignment.status
    if hasattr(assignment_status, "value"):
        assignment_status = assignment_status.value

    if assignment_status == "Completed":
        raise HTTPException(status_code=400, detail="Assignment is already completed")

    if assignment_status not in ("Active", AssignmentStatus.ACTIVE.value):
        raise HTTPException(
            status_code=400,
            detail=f"Only active assignments can be marked complete. Current status: '{assignment_status}'",
        )

    assignment.status = "Completed"
    db.commit()
    db.refresh(assignment)

    return {
        "id": str(assignment.id),
        "status": "Completed",
        "message": "Assignment marked as complete. Please submit your skill breakdown.",
    }


@router.post(
    "/{assignment_id}/skill-breakdown",
    response_model=SkillBreakdownResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_skill_breakdown(
    assignment_id: uuid.UUID,
    data: SkillBreakdownCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Submit a skill breakdown for a completed assignment.

    Both ops users and technicians (for their own assignments) can submit.

    Validates:
    - Assignment exists
    - Assignment status is 'Completed'
    - Technicians can only submit for their own assignments
    - No skill breakdown already exists for this assignment
    - All proficiency ratings are valid enum values

    Emits: SKILL_BREAKDOWN_SUBMITTED WebSocket event to both skill_breakdowns
    and partner topics for real-time dashboard updates.
    """
    assignment = _get_assignment_or_404(db, assignment_id)

    # Technicians can only submit for their own assignments
    if current_user.role == "technician":
        if str(assignment.technician_id) != current_user.user_id:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to submit a skill breakdown for this assignment",
            )

    # Validate assignment is completed
    assignment_status = assignment.status
    if hasattr(assignment_status, "value"):
        assignment_status = assignment_status.value

    if (
        assignment_status != AssignmentStatus.COMPLETED.value
        and assignment_status != "Completed"
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Skill breakdown can only be submitted for completed assignments. "
            f"Current status: '{assignment_status}'",
        )

    # Check for existing breakdown
    existing = (
        db.query(SkillBreakdown)
        .filter(SkillBreakdown.assignment_id == assignment_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A skill breakdown already exists for this assignment",
        )

    # Validate ratings
    for item in data.items:
        if item.proficiency_rating not in VALID_RATINGS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid proficiency rating '{item.proficiency_rating}'. "
                f"Valid values: {sorted(VALID_RATINGS)}",
            )

    if data.overall_rating and data.overall_rating not in VALID_RATINGS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid overall rating '{data.overall_rating}'. "
            f"Valid values: {sorted(VALID_RATINGS)}",
        )

    # Validate total hours if provided
    total_hours = sum(item.hours_applied or 0 for item in data.items)
    if total_hours < 0:
        raise HTTPException(
            status_code=422,
            detail="Total hours across all skills cannot be negative",
        )

    # Validate no duplicate skills
    skill_names = [item.skill_name for item in data.items]
    if len(skill_names) != len(set(skill_names)):
        raise HTTPException(
            status_code=422,
            detail="Duplicate skill entries are not allowed",
        )

    # Create skill breakdown with Pending partner review status
    breakdown = SkillBreakdown(
        assignment_id=assignment_id,
        technician_id=assignment.technician_id,
        submitted_by=current_user.user_id,
        overall_notes=data.overall_notes,
        overall_rating=data.overall_rating,
        partner_review_status=PartnerReviewStatus.PENDING,
    )
    db.add(breakdown)
    db.flush()  # Get the breakdown ID for items

    # Create items
    for item_data in data.items:
        item = SkillBreakdownItem(
            skill_breakdown_id=breakdown.id,
            skill_name=item_data.skill_name,
            skill_id=item_data.skill_id,
            hours_applied=item_data.hours_applied,
            proficiency_rating=item_data.proficiency_rating,
            notes=item_data.notes,
        )
        db.add(item)

    db.commit()
    db.refresh(breakdown)

    # Dispatch domain event (for Celery tasks)
    partner_id = _get_partner_id_for_assignment(db, assignment)
    dispatch_event_safe(
        EventPayload(
            event_type=EventType.SKILL_BREAKDOWN_SUBMITTED,
            entity_type="skill_breakdown",
            entity_id=str(breakdown.id),
            actor_id=current_user.user_id,
            data={
                "assignment_id": str(assignment_id),
                "technician_id": str(assignment.technician_id),
                "partner_id": partner_id,
                "item_count": len(data.items),
            },
        )
    )

    # WebSocket broadcast (async, in background)
    background_tasks.add_task(
        _broadcast_skill_breakdown_event,
        "skill_breakdown.submitted",
        breakdown,
        partner_id,
    )

    return breakdown


@router.get(
    "/{assignment_id}/skill-breakdown",
    response_model=SkillBreakdownResponse,
)
def get_skill_breakdown(
    assignment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the skill breakdown for an assignment.

    Ops users can view any breakdown. Technicians can only view their own.
    Partners can view breakdowns for assignments in their projects.
    """
    assignment = _get_assignment_or_404(db, assignment_id)

    # Role-based access control
    if current_user.role == "technician":
        if str(assignment.technician_id) != current_user.user_id:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to view this skill breakdown",
            )

    breakdown = _get_breakdown_or_404(db, assignment_id)
    return breakdown


@router.post(
    "/{assignment_id}/skill-breakdown/review",
    response_model=SkillBreakdownResponse,
)
async def partner_review_skill_breakdown(
    assignment_id: uuid.UUID,
    review: PartnerSkillBreakdownReview,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Partner reviews a skill breakdown: approve, reject, or request revision.

    Only partners can call this endpoint. The breakdown must be in 'Pending'
    or 'Revision Requested' status.

    Emits: SKILL_BREAKDOWN_APPROVED, SKILL_BREAKDOWN_REJECTED, or
           SKILL_BREAKDOWN_REVISION_REQUESTED WebSocket events to both
           skill_breakdowns and partner topics for real-time dashboard updates.
    """
    if review.action not in VALID_REVIEW_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid review action '{review.action}'. "
            f"Valid actions: {sorted(VALID_REVIEW_ACTIONS)}",
        )

    _get_assignment_or_404(db, assignment_id)
    breakdown = _get_breakdown_or_404(db, assignment_id)

    # Validate current status allows review
    current_status = breakdown.partner_review_status
    if hasattr(current_status, "value"):
        current_status = current_status.value

    reviewable_statuses = {
        PartnerReviewStatus.PENDING.value,
        PartnerReviewStatus.REVISION_REQUESTED.value,
        None,
    }
    if current_status not in reviewable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Skill breakdown cannot be reviewed in current status: "
            f"'{current_status}'. Only 'Pending' or 'Revision Requested' "
            f"breakdowns can be reviewed.",
        )

    # Map action to status and event type
    now = datetime.now(timezone.utc)
    action_map = {
        "approve": (
            PartnerReviewStatus.APPROVED,
            EventType.SKILL_BREAKDOWN_APPROVED,
        ),
        "reject": (
            PartnerReviewStatus.REJECTED,
            EventType.SKILL_BREAKDOWN_REJECTED,
        ),
        "request_revision": (
            PartnerReviewStatus.REVISION_REQUESTED,
            EventType.SKILL_BREAKDOWN_REVISION_REQUESTED,
        ),
    }
    new_status, event_type = action_map[review.action]

    # Update breakdown
    breakdown.partner_review_status = new_status
    breakdown.partner_review_note = review.note
    breakdown.partner_reviewed_at = now
    breakdown.partner_reviewed_by = current_user.user_id
    breakdown.updated_at = now

    db.commit()
    db.refresh(breakdown)

    # Dispatch domain event
    partner_id = (
        current_user.scoped_to
        if hasattr(current_user, "scoped_to") and current_user.scoped_to
        else current_user.user_id
    )
    dispatch_event_safe(
        EventPayload(
            event_type=event_type,
            entity_type="skill_breakdown",
            entity_id=str(breakdown.id),
            actor_id=current_user.user_id,
            data={
                "assignment_id": str(assignment_id),
                "technician_id": str(breakdown.technician_id),
                "partner_id": partner_id,
                "action": review.action,
                "note": review.note,
                "new_status": new_status.value,
            },
        )
    )

    # WebSocket broadcast — map action to event type string
    ws_event_type = {
        "approve": "skill_breakdown.approved",
        "reject": "skill_breakdown.rejected",
        "request_revision": "skill_breakdown.revision_requested",
    }[review.action]

    background_tasks.add_task(
        _broadcast_skill_breakdown_event,
        ws_event_type,
        breakdown,
        partner_id,
    )

    return breakdown


@router.get(
    "/skill-breakdowns/pending",
    response_model=list[SkillBreakdownResponse],
)
def list_pending_skill_breakdowns(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List skill breakdowns pending partner review.

    Ops sees all pending. Partners see only those for their projects.
    Technicians see only their own pending breakdowns.
    """
    query = db.query(SkillBreakdown).filter(
        SkillBreakdown.partner_review_status == PartnerReviewStatus.PENDING
    )

    if current_user.role == "technician":
        query = query.filter(
            SkillBreakdown.technician_id == current_user.user_id
        )

    return query.order_by(SkillBreakdown.submitted_at.desc()).all()
