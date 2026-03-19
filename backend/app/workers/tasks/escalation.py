"""24-hour escalation window reactive agent tasks.

Handles:
  - scan_overdue_confirmations: Periodic scan (every 15 min) for confirmations
    that have been pending > 24 hours without partner response. Escalates them
    to the Project Staffing Page for ops intervention.
  - handle_escalation_triggered: Creates suggested actions and recommendations
    when a confirmation is escalated.
  - handle_escalation_resolved: Cleans up after ops resolves an escalation.

The 24-hour window is a hard business rule: partners must confirm or decline
assignment dates within 24 hours of the request. If they don't, ops takes over
the decision via the Project Staffing Page.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.workers.dispatcher import dispatch_event_safe
from app.database import SessionLocal
from app.models.assignment_confirmation import (
    AssignmentConfirmation,
    ConfirmationStatus,
    EscalationStatus,
)
from app.models.assignment import Assignment
from app.models.project import Project, ProjectRole
from app.models.technician import Technician
from app.models.user import Partner
from app.models.recommendation import Recommendation, RecommendationStatus
from app.models.audit import SuggestedAction, AuditLog

logger = logging.getLogger("deployable.workers.escalation")

# The escalation window in hours
ESCALATION_WINDOW_HOURS = 24


def _enum_val(v):
    return v.value if hasattr(v, "value") else str(v) if v else ""


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.escalation.scan_overdue_confirmations",
)
def scan_overdue_confirmations(self, event_dict: dict[str, Any] = None) -> dict[str, Any]:
    """Periodic scan for partner confirmations that have exceeded the 24-hour window.

    Runs every 15 minutes via Celery Beat. Finds all PENDING confirmations where
    requested_at + 24h < now, marks them as escalated, and dispatches escalation
    events for each one.

    This is the core enforcement mechanism for the 24-hour escalation window.
    """
    if event_dict is None:
        event_dict = EventPayload(
            event_type=EventType.ESCALATION_SCAN_TRIGGERED,
            entity_type="system",
            entity_id="escalation_scan",
            actor_id="system",
        ).to_dict()

    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=ESCALATION_WINDOW_HOURS)

        # Find all pending confirmations that have exceeded the 24-hour window
        # and have not yet been escalated
        overdue_confirmations = (
            session.query(AssignmentConfirmation)
            .filter(
                AssignmentConfirmation.status == ConfirmationStatus.PENDING,
                AssignmentConfirmation.escalated.is_(False),
                AssignmentConfirmation.requested_at <= cutoff,
            )
            .all()
        )

        escalated_count = 0
        cascade_events = []

        for confirmation in overdue_confirmations:
            # Mark as escalated
            confirmation.escalated = True
            confirmation.escalated_at = datetime.utcnow()
            confirmation.status = ConfirmationStatus.ESCALATED
            confirmation.escalation_status = EscalationStatus.ESCALATED

            hours_overdue = round(
                (datetime.utcnow() - confirmation.requested_at).total_seconds() / 3600, 1
            )

            # Gather context for the escalation event
            assignment = session.get(Assignment, confirmation.assignment_id)
            role = session.get(ProjectRole, assignment.role_id) if assignment else None
            project = session.get(Project, role.project_id) if role else None
            technician = session.get(Technician, assignment.technician_id) if assignment else None
            partner = session.get(Partner, confirmation.partner_id)

            event_data = {
                "confirmation_id": str(confirmation.id),
                "assignment_id": str(confirmation.assignment_id),
                "partner_id": str(confirmation.partner_id),
                "partner_name": partner.name if partner else "Unknown",
                "confirmation_type": _enum_val(confirmation.confirmation_type),
                "requested_date": str(confirmation.requested_date),
                "requested_at": str(confirmation.requested_at),
                "hours_overdue": hours_overdue,
                "technician_id": str(assignment.technician_id) if assignment else None,
                "technician_name": technician.full_name if technician else "Unknown",
                "role_id": str(role.id) if role else None,
                "role_name": role.role_name if role else "Unknown",
                "project_id": str(project.id) if project else None,
                "project_name": project.name if project else "Unknown",
            }

            cascade_events.append(
                EventPayload(
                    event_type=EventType.CONFIRMATION_ESCALATED,
                    entity_type="assignment_confirmation",
                    entity_id=str(confirmation.id),
                    actor_id="system",
                    data=event_data,
                ).to_dict()
            )

            escalated_count += 1

            logger.info(
                "Escalated confirmation %s (assignment=%s, partner=%s, %.1fh overdue)",
                confirmation.id,
                confirmation.assignment_id,
                partner.name if partner else "?",
                hours_overdue,
            )

        session.commit()

        # Dispatch escalation events for each overdue confirmation
        for event_dict_item in cascade_events:
            dispatch_event_safe(EventPayload.from_dict(event_dict_item))

        logger.info(
            "Escalation scan complete: %d confirmations escalated out of %d checked",
            escalated_count,
            len(overdue_confirmations),
        )

        return {
            "status": "completed",
            "scanned": len(overdue_confirmations),
            "escalated": escalated_count,
            "cascade_events": cascade_events,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.escalation.handle_escalation_triggered",
)
def handle_escalation_triggered(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Handle a single confirmation escalation — create staffing page items.

    When a confirmation is escalated, this task:
    1. Creates a high-priority SuggestedAction for ops on the dashboard
    2. Creates a staffing recommendation linked to the project role
    3. Writes an audit log entry
    4. Logs context for the Project Staffing Page API
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        data = payload.data
        confirmation_id = data.get("confirmation_id", payload.entity_id)
        assignment_id = data.get("assignment_id")
        partner_name = data.get("partner_name", "Unknown Partner")
        technician_name = data.get("technician_name", "Unknown Technician")
        role_name = data.get("role_name", "Unknown Role")
        project_name = data.get("project_name", "Unknown Project")
        hours_overdue = data.get("hours_overdue", 24)
        role_id = data.get("role_id")
        project_id = data.get("project_id")
        technician_id = data.get("technician_id")
        confirmation_type = data.get("confirmation_type", "start_date")

        # 1. Create high-priority SuggestedAction for ops dashboard
        action = SuggestedAction(
            target_role="ops",
            action_type="escalation_24h",
            title=f"Escalation: No partner response for {technician_name}",
            description=(
                f"{partner_name} has not responded to the {confirmation_type.replace('_', ' ')} "
                f"confirmation for {technician_name} on {project_name} / {role_name}. "
                f"Overdue by {hours_overdue:.0f} hours. "
                f"Action required on the Project Staffing Page."
            ),
            link=f"/projects/{project_id}/staffing" if project_id else "/staffing",
            priority=9,  # Very high priority
            metadata_={
                "confirmation_id": confirmation_id,
                "assignment_id": assignment_id,
                "escalation_type": "24h_no_response",
                "partner_name": partner_name,
                "technician_name": technician_name,
                "role_name": role_name,
                "project_name": project_name,
                "hours_overdue": hours_overdue,
            },
        )
        session.add(action)

        # 2. Create a staffing recommendation for the escalated role
        if role_id:
            existing_rec = (
                session.query(Recommendation)
                .filter(
                    Recommendation.role_id == role_id,
                    Recommendation.recommendation_type == "staffing",
                    Recommendation.status == RecommendationStatus.PENDING.value,
                    Recommendation.agent_name == "escalation_agent",
                )
                .first()
            )

            if not existing_rec:
                rec = Recommendation(
                    recommendation_type="staffing",
                    target_entity_type="project_role",
                    target_entity_id=role_id,
                    role_id=role_id,
                    project_id=project_id,
                    technician_id=technician_id,
                    scorecard={
                        "escalation": True,
                        "escalation_reason": "24h_no_partner_response",
                        "partner_name": partner_name,
                        "technician_name": technician_name,
                        "role_name": role_name,
                        "project_name": project_name,
                        "hours_overdue": hours_overdue,
                        "confirmation_type": confirmation_type,
                        "confirmation_id": confirmation_id,
                    },
                    explanation=(
                        f"ESCALATION: {partner_name} has not responded to the "
                        f"{confirmation_type.replace('_', ' ')} confirmation for "
                        f"{technician_name} ({role_name} on {project_name}) after "
                        f"{hours_overdue:.0f} hours. The 24-hour response window has expired. "
                        f"Options: (1) Confirm on partner's behalf, (2) Reassign the technician, "
                        f"(3) Cancel the assignment. Review on the Project Staffing Page."
                    ),
                    status=RecommendationStatus.PENDING.value,
                    agent_name="escalation_agent",
                    batch_id=f"escalation_{confirmation_id}",
                    metadata_={
                        "source": "24h_escalation",
                        "confirmation_id": confirmation_id,
                        "assignment_id": assignment_id,
                        "partner_name": partner_name,
                    },
                )
                session.add(rec)

        # 3. Audit log
        audit = AuditLog(
            user_id="system",
            action="confirmation_escalated",
            entity_type="assignment_confirmation",
            entity_id=confirmation_id,
            details={
                "assignment_id": assignment_id,
                "partner_name": partner_name,
                "technician_name": technician_name,
                "role_name": role_name,
                "project_name": project_name,
                "hours_overdue": hours_overdue,
                "reason": "24-hour partner response window exceeded",
            },
            agent_name="escalation_agent",
        )
        session.add(audit)

        session.commit()

        logger.info(
            "Escalation handled: confirmation=%s, assignment=%s, partner=%s",
            confirmation_id,
            assignment_id,
            partner_name,
        )

        return {
            "status": "escalation_handled",
            "confirmation_id": confirmation_id,
            "assignment_id": assignment_id,
            "partner_name": partner_name,
            "actions_created": True,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.escalation.handle_escalation_resolved",
)
def handle_escalation_resolved(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Handle resolution of an escalated confirmation by ops.

    Updates the confirmation record and creates audit trail.
    """
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        data = payload.data
        confirmation_id = payload.entity_id
        resolution = data.get("resolution", "resolved_confirmed")
        resolved_by = data.get("resolved_by", payload.actor_id)
        resolution_note = data.get("resolution_note", "")

        confirmation = session.get(AssignmentConfirmation, confirmation_id)
        if not confirmation:
            return {"status": "skipped", "reason": "Confirmation not found"}

        # Map resolution to escalation status
        resolution_map = {
            "confirmed": EscalationStatus.RESOLVED_CONFIRMED,
            "reassigned": EscalationStatus.RESOLVED_REASSIGNED,
            "cancelled": EscalationStatus.RESOLVED_CANCELLED,
            "resolved_confirmed": EscalationStatus.RESOLVED_CONFIRMED,
            "resolved_reassigned": EscalationStatus.RESOLVED_REASSIGNED,
            "resolved_cancelled": EscalationStatus.RESOLVED_CANCELLED,
        }
        new_escalation_status = resolution_map.get(
            resolution, EscalationStatus.RESOLVED_CONFIRMED
        )

        confirmation.escalation_status = new_escalation_status
        confirmation.escalation_resolved_at = datetime.utcnow()
        confirmation.escalation_resolved_by = resolved_by
        confirmation.escalation_resolution_note = resolution_note

        # If resolved by confirming, also update confirmation status
        if new_escalation_status == EscalationStatus.RESOLVED_CONFIRMED:
            confirmation.status = ConfirmationStatus.CONFIRMED
            confirmation.responded_at = datetime.utcnow()

            # Update assignment confirmation flags
            assignment = session.get(Assignment, confirmation.assignment_id)
            if assignment:
                if _enum_val(confirmation.confirmation_type) == "start_date":
                    assignment.partner_confirmed_start = True
                elif _enum_val(confirmation.confirmation_type) == "end_date":
                    assignment.partner_confirmed_end = True

        # Supersede any pending escalation recommendations
        escalation_recs = (
            session.query(Recommendation)
            .filter(
                Recommendation.agent_name == "escalation_agent",
                Recommendation.status == RecommendationStatus.PENDING.value,
                Recommendation.batch_id == f"escalation_{confirmation_id}",
            )
            .all()
        )
        for rec in escalation_recs:
            rec.status = RecommendationStatus.SUPERSEDED.value
            rec.updated_at = datetime.now(timezone.utc)

        # Audit log
        audit = AuditLog(
            user_id=resolved_by,
            action="escalation_resolved",
            entity_type="assignment_confirmation",
            entity_id=confirmation_id,
            details={
                "resolution": resolution,
                "resolution_note": resolution_note,
                "escalation_status": _enum_val(new_escalation_status),
            },
            agent_name="escalation_agent",
        )
        session.add(audit)

        session.commit()

        logger.info(
            "Escalation resolved: confirmation=%s, resolution=%s, by=%s",
            confirmation_id,
            resolution,
            resolved_by,
        )

        return {
            "status": "resolved",
            "confirmation_id": confirmation_id,
            "resolution": resolution,
            "escalation_status": _enum_val(new_escalation_status),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
