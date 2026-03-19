"""Partner 48-hour advance visibility scheduler.

Celery task that runs periodically to surface upcoming assignments to partners
48 hours before start/end dates. Generates PartnerNotification records and
avoids creating duplicate notifications for the same assignment event.
"""

import logging
from datetime import date, timedelta, datetime, timezone
from typing import Any

from sqlalchemy import and_, or_

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.assignment import Assignment
from app.models.project import Project, ProjectRole
from app.models.technician import Technician
from app.models.partner_notification import (
    PartnerNotification,
    NotificationType,
    NotificationStatus,
)
from app.models.audit import SuggestedAction

logger = logging.getLogger("deployable.workers.partner_visibility")


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.partner_visibility.scan_upcoming_assignments",
)
def scan_upcoming_assignments(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Scan for assignments starting or ending within 48 hours.

    For each qualifying assignment:
      1. Check if a PartnerNotification already exists (dedup)
      2. Create a new PartnerNotification record
      3. Create a SuggestedAction for the partner dashboard
      4. Log the notification event

    This task is idempotent — running it multiple times will not create
    duplicate notifications for the same assignment event.
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.NIGHTLY_BATCH_TRIGGERED,
            entity_type="system",
            entity_id="partner_visibility_scan",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        today = date.today()
        horizon = today + timedelta(hours=48)
        # Use 48-hour window: today <= target_date <= today + 2 days
        horizon_date = today + timedelta(days=2)

        stats = {
            "starting_notifications": 0,
            "ending_notifications": 0,
            "skipped_duplicates": 0,
            "total_assignments_scanned": 0,
        }

        # --- Find assignments starting within 48 hours ---
        starting_soon = session.query(Assignment).filter(
            Assignment.status == "Active",
            Assignment.start_date >= today,
            Assignment.start_date <= horizon_date,
            Assignment.partner_confirmed_start == False,
        ).all()

        stats["total_assignments_scanned"] += len(starting_soon)

        for assignment in starting_soon:
            # Dedup: check if notification already exists
            existing = session.query(PartnerNotification).filter(
                PartnerNotification.assignment_id == assignment.id,
                PartnerNotification.notification_type == NotificationType.ASSIGNMENT_STARTING,
            ).first()

            if existing:
                stats["skipped_duplicates"] += 1
                continue

            # Resolve partner through role -> project -> partner
            role = session.get(ProjectRole, assignment.role_id)
            if not role:
                continue
            project = session.get(Project, role.project_id)
            if not project:
                continue
            technician = session.get(Technician, assignment.technician_id)
            if not technician:
                continue

            tech_name = technician.full_name
            days_until = (assignment.start_date - today).days

            notification = PartnerNotification(
                partner_id=project.partner_id,
                assignment_id=assignment.id,
                project_id=project.id,
                technician_id=technician.id,
                notification_type=NotificationType.ASSIGNMENT_STARTING,
                status=NotificationStatus.PENDING,
                title=f"{tech_name} starting on {project.name}",
                message=(
                    f"{tech_name} is scheduled to start on {role.role_name} "
                    f"for project {project.name} on {assignment.start_date.isoformat()}. "
                    f"That's {days_until} day(s) from now. "
                    f"Please confirm you are ready to receive this technician."
                ),
                target_date=datetime.combine(assignment.start_date, datetime.min.time()),
            )
            session.add(notification)

            # Also create a suggested action for the partner
            action = SuggestedAction(
                target_role="partner",
                target_user_id=str(project.partner_id),
                action_type="assignment_starting_soon",
                title=f"Arriving Soon: {tech_name}",
                description=(
                    f"{tech_name} starts on {role.role_name} ({project.name}) "
                    f"in {days_until} day(s). Please confirm."
                ),
                link=f"/partner/assignments/{assignment.id}",
                priority=5,
            )
            session.add(action)
            stats["starting_notifications"] += 1

        # --- Find assignments ending within 48 hours ---
        ending_soon = session.query(Assignment).filter(
            Assignment.status == "Active",
            Assignment.end_date != None,
            Assignment.end_date >= today,
            Assignment.end_date <= horizon_date,
            Assignment.partner_confirmed_end == False,
        ).all()

        stats["total_assignments_scanned"] += len(ending_soon)

        for assignment in ending_soon:
            # Dedup
            existing = session.query(PartnerNotification).filter(
                PartnerNotification.assignment_id == assignment.id,
                PartnerNotification.notification_type == NotificationType.ASSIGNMENT_ENDING,
            ).first()

            if existing:
                stats["skipped_duplicates"] += 1
                continue

            role = session.get(ProjectRole, assignment.role_id)
            if not role:
                continue
            project = session.get(Project, role.project_id)
            if not project:
                continue
            technician = session.get(Technician, assignment.technician_id)
            if not technician:
                continue

            tech_name = technician.full_name
            days_until = (assignment.end_date - today).days

            notification = PartnerNotification(
                partner_id=project.partner_id,
                assignment_id=assignment.id,
                project_id=project.id,
                technician_id=technician.id,
                notification_type=NotificationType.ASSIGNMENT_ENDING,
                status=NotificationStatus.PENDING,
                title=f"{tech_name} ending on {project.name}",
                message=(
                    f"{tech_name}'s assignment as {role.role_name} on {project.name} "
                    f"ends on {assignment.end_date.isoformat()} ({days_until} day(s) from now). "
                    f"Please confirm the end date or request an extension."
                ),
                target_date=datetime.combine(assignment.end_date, datetime.min.time()),
            )
            session.add(notification)

            action = SuggestedAction(
                target_role="partner",
                target_user_id=str(project.partner_id),
                action_type="assignment_ending_soon",
                title=f"Ending Soon: {tech_name}",
                description=(
                    f"{tech_name}'s assignment on {project.name} ends "
                    f"in {days_until} day(s). Confirm or request extension."
                ),
                link=f"/partner/assignments/{assignment.id}",
                priority=5,
            )
            session.add(action)
            stats["ending_notifications"] += 1

        session.commit()

        logger.info(
            "Partner visibility scan: %d starting, %d ending, %d duplicates skipped, %d total scanned",
            stats["starting_notifications"],
            stats["ending_notifications"],
            stats["skipped_duplicates"],
            stats["total_assignments_scanned"],
        )

        return {"status": "completed", **stats}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
