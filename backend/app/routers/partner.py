"""Partner-facing API endpoints.

Provides:
  - GET /api/partner/upcoming-assignments — assignments starting/ending within 48h
  - GET /api/partner/notifications — all partner notifications
  - GET /api/partner/notifications/pending — only pending (unconfirmed) notifications
  - POST /api/partner/notifications/{id}/confirm — confirm a notification
  - POST /api/partner/notifications/{id}/dismiss — dismiss a notification
  - POST /api/partner/scan-now — manually trigger the 48h visibility scan (ops only)
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import CurrentUser, get_current_user, require_role
from app.models.assignment import Assignment
from app.models.project import Project, ProjectRole
from app.models.technician import Technician
from app.models.partner_notification import (
    PartnerNotification,
    NotificationType,
    NotificationStatus,
)
from app.schemas.partner import (
    PartnerNotificationOut,
    PartnerNotificationConfirm,
    PartnerNotificationDismiss,
    PartnerNotificationListOut,
    PartnerUpcomingAssignmentOut,
)

logger = logging.getLogger("deployable.routers.partner")

router = APIRouter(prefix="/api/partner", tags=["partner"])


def _get_partner_id(current_user: CurrentUser) -> str:
    """Extract the partner ID from the current user's scoped_to or user_id."""
    # For demo mode, user_id might be the partner_id directly
    return current_user.user_id


def _enrich_notification(
    notification: PartnerNotification, db: Session
) -> PartnerNotificationOut:
    """Enrich a notification with joined data from related tables."""
    tech = db.get(Technician, notification.technician_id)
    project = db.get(Project, notification.project_id)
    assignment = db.get(Assignment, notification.assignment_id)
    role = db.get(ProjectRole, assignment.role_id) if assignment else None

    return PartnerNotificationOut(
        id=notification.id,
        partner_id=notification.partner_id,
        assignment_id=notification.assignment_id,
        project_id=notification.project_id,
        technician_id=notification.technician_id,
        notification_type=notification.notification_type.value
        if hasattr(notification.notification_type, "value")
        else str(notification.notification_type),
        status=notification.status.value
        if hasattr(notification.status, "value")
        else str(notification.status),
        title=notification.title,
        message=notification.message,
        target_date=notification.target_date,
        confirmed_at=notification.confirmed_at,
        confirmed_by=notification.confirmed_by,
        created_at=notification.created_at,
        technician_name=tech.full_name if tech else None,
        project_name=project.name if project else None,
        role_name=role.role_name if role else None,
        assignment_start_date=assignment.start_date if assignment else None,
        assignment_end_date=assignment.end_date if assignment else None,
    )


# ---------------------------------------------------------------------------
# GET upcoming assignments (partner-scoped)
# ---------------------------------------------------------------------------


@router.get(
    "/upcoming-assignments",
    response_model=list[PartnerUpcomingAssignmentOut],
    summary="Get upcoming assignments for the partner (within 48h window)",
)
def get_upcoming_assignments(
    days_ahead: int = Query(default=2, ge=1, le=30, description="Days to look ahead"),
    current_user: CurrentUser = Depends(require_role("partner", "ops")),
    db: Session = Depends(get_db),
):
    """Return assignments starting or ending within the specified window.

    Partners see only their own projects' assignments.
    Ops users see all assignments.
    """
    today = date.today()
    horizon = today + timedelta(days=days_ahead)

    query = db.query(Assignment).join(
        ProjectRole, Assignment.role_id == ProjectRole.id,
    ).join(
        Project, ProjectRole.project_id == Project.id,
    )

    # Scope to partner's projects
    if current_user.role == "partner":
        partner_id = _get_partner_id(current_user)
        query = query.filter(Project.partner_id == partner_id)

    # Find assignments starting or ending within window
    assignments = query.filter(
        Assignment.status == "Active",
        (
            (Assignment.start_date >= today) & (Assignment.start_date <= horizon)
        ) | (
            (Assignment.end_date != None) & (Assignment.end_date >= today) & (Assignment.end_date <= horizon)
        ),
    ).all()

    results = []
    for assignment in assignments:
        role = db.get(ProjectRole, assignment.role_id)
        project = db.get(Project, role.project_id) if role else None
        tech = db.get(Technician, assignment.technician_id)

        days_until_start = (assignment.start_date - today).days if assignment.start_date >= today else None
        days_until_end = (assignment.end_date - today).days if assignment.end_date and assignment.end_date >= today else None

        # Get related notifications
        notifications = db.query(PartnerNotification).filter(
            PartnerNotification.assignment_id == assignment.id,
        ).all()

        results.append(PartnerUpcomingAssignmentOut(
            assignment_id=assignment.id,
            technician_id=assignment.technician_id,
            technician_name=tech.full_name if tech else "Unknown",
            project_id=project.id if project else assignment.role_id,
            project_name=project.name if project else "Unknown",
            role_name=role.role_name if role else "Unknown",
            start_date=assignment.start_date,
            end_date=assignment.end_date,
            hourly_rate=assignment.hourly_rate,
            per_diem=assignment.per_diem,
            status=assignment.status,
            partner_confirmed_start=assignment.partner_confirmed_start or False,
            partner_confirmed_end=assignment.partner_confirmed_end or False,
            days_until_start=days_until_start,
            days_until_end=days_until_end,
            notifications=[_enrich_notification(n, db) for n in notifications],
        ))

    # Sort by most urgent first (nearest target date)
    results.sort(key=lambda x: x.days_until_start if x.days_until_start is not None else 999)
    return results


# ---------------------------------------------------------------------------
# GET notifications (partner-scoped)
# ---------------------------------------------------------------------------


@router.get(
    "/notifications",
    response_model=PartnerNotificationListOut,
    summary="Get all notifications for the partner",
)
def get_notifications(
    status_filter: Optional[str] = Query(
        default=None,
        description="Filter by status: pending, confirmed, dismissed",
    ),
    notification_type: Optional[str] = Query(
        default=None,
        description="Filter by type: assignment_starting, assignment_ending",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(require_role("partner", "ops")),
    db: Session = Depends(get_db),
):
    """Return paginated partner notifications with optional filters."""
    query = db.query(PartnerNotification)

    if current_user.role == "partner":
        partner_id = _get_partner_id(current_user)
        query = query.filter(PartnerNotification.partner_id == partner_id)

    if status_filter:
        query = query.filter(PartnerNotification.status == status_filter)

    if notification_type:
        query = query.filter(PartnerNotification.notification_type == notification_type)

    total = query.count()
    pending_count = query.filter(
        PartnerNotification.status == NotificationStatus.PENDING
    ).count() if not status_filter else (total if status_filter == "pending" else 0)

    # If we already filtered, re-count pending without status filter
    if status_filter and status_filter != "pending":
        base_query = db.query(PartnerNotification)
        if current_user.role == "partner":
            base_query = base_query.filter(
                PartnerNotification.partner_id == _get_partner_id(current_user)
            )
        pending_count = base_query.filter(
            PartnerNotification.status == NotificationStatus.PENDING
        ).count()

    notifications = (
        query.order_by(PartnerNotification.target_date.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return PartnerNotificationListOut(
        notifications=[_enrich_notification(n, db) for n in notifications],
        total=total,
        pending_count=pending_count,
    )


@router.get(
    "/notifications/pending",
    response_model=PartnerNotificationListOut,
    summary="Get only pending (unconfirmed) notifications for the partner",
)
def get_pending_notifications(
    current_user: CurrentUser = Depends(require_role("partner", "ops")),
    db: Session = Depends(get_db),
):
    """Return only pending notifications requiring partner action."""
    query = db.query(PartnerNotification).filter(
        PartnerNotification.status == NotificationStatus.PENDING,
    )

    if current_user.role == "partner":
        partner_id = _get_partner_id(current_user)
        query = query.filter(PartnerNotification.partner_id == partner_id)

    notifications = query.order_by(PartnerNotification.target_date.asc()).all()
    total = len(notifications)

    return PartnerNotificationListOut(
        notifications=[_enrich_notification(n, db) for n in notifications],
        total=total,
        pending_count=total,
    )


# ---------------------------------------------------------------------------
# POST confirm / dismiss
# ---------------------------------------------------------------------------


@router.post(
    "/notifications/{notification_id}/confirm",
    response_model=PartnerNotificationOut,
    summary="Confirm a partner notification",
)
def confirm_notification(
    notification_id: UUID,
    body: PartnerNotificationConfirm,
    current_user: CurrentUser = Depends(require_role("partner", "ops")),
    db: Session = Depends(get_db),
):
    """Partner confirms they acknowledge the upcoming assignment event."""
    notification = db.get(PartnerNotification, notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    # Scope check for partners
    if current_user.role == "partner":
        partner_id = _get_partner_id(current_user)
        if str(notification.partner_id) != str(partner_id):
            raise HTTPException(status_code=403, detail="Not your notification")

    if notification.status != NotificationStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Notification already {notification.status.value if hasattr(notification.status, 'value') else notification.status}",
        )

    notification.status = NotificationStatus.CONFIRMED
    notification.confirmed_at = datetime.now(timezone.utc)
    notification.confirmed_by = body.confirmed_by or current_user.user_id

    # Also update the assignment's partner_confirmed flags
    assignment = db.get(Assignment, notification.assignment_id)
    if assignment:
        ntype = notification.notification_type
        ntype_val = ntype.value if hasattr(ntype, "value") else str(ntype)
        if ntype_val == NotificationType.ASSIGNMENT_STARTING.value:
            assignment.partner_confirmed_start = True
        elif ntype_val == NotificationType.ASSIGNMENT_ENDING.value:
            assignment.partner_confirmed_end = True

    db.commit()
    db.refresh(notification)

    logger.info(
        "Notification %s confirmed by %s",
        notification_id, current_user.user_id,
    )

    return _enrich_notification(notification, db)


@router.post(
    "/notifications/{notification_id}/dismiss",
    response_model=PartnerNotificationOut,
    summary="Dismiss a partner notification",
)
def dismiss_notification(
    notification_id: UUID,
    body: PartnerNotificationDismiss,
    current_user: CurrentUser = Depends(require_role("partner", "ops")),
    db: Session = Depends(get_db),
):
    """Partner dismisses the notification (acknowledges but takes no action)."""
    notification = db.get(PartnerNotification, notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if current_user.role == "partner":
        partner_id = _get_partner_id(current_user)
        if str(notification.partner_id) != str(partner_id):
            raise HTTPException(status_code=403, detail="Not your notification")

    if notification.status != NotificationStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Notification already {notification.status.value if hasattr(notification.status, 'value') else notification.status}",
        )

    notification.status = NotificationStatus.DISMISSED
    notification.confirmed_at = datetime.now(timezone.utc)
    notification.confirmed_by = current_user.user_id

    db.commit()
    db.refresh(notification)

    return _enrich_notification(notification, db)


# ---------------------------------------------------------------------------
# POST scan trigger (ops only)
# ---------------------------------------------------------------------------


@router.post(
    "/scan-now",
    summary="Manually trigger the 48-hour visibility scan",
    status_code=202,
)
def trigger_scan(
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Ops can manually trigger the partner visibility scan outside the schedule."""
    from app.workers.tasks.partner_visibility import scan_upcoming_assignments

    task = scan_upcoming_assignments.delay()
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "48-hour partner visibility scan has been queued.",
    }
