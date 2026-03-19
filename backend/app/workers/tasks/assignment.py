"""Assignment lifecycle reactive agent tasks.

Handles:
  - ASSIGNMENT_CREATED / STARTED: Update technician status
  - ASSIGNMENT_ENDED: Handle end-of-assignment flows
  - ASSIGNMENT_CANCELLED: Revert technician availability
  - TECHNICIAN_ROLLING_OFF: Create proactive alerts
"""

import logging
from datetime import date, timedelta
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.base_task import ReactiveAgentTask
from app.workers.events import EventPayload, EventType
from app.database import SessionLocal
from app.models.technician import Technician, DeployabilityStatus
from app.models.project import ProjectRole, Project, ProjectStatus
from app.models.assignment import Assignment
from app.models.recommendation import Recommendation, RecommendationStatus
from app.models.audit import SuggestedAction
from app.services.agent_llm import generate_backfill_explanation

logger = logging.getLogger("deployable.workers.assignment")


def _enum_val(v):
    return v.value if hasattr(v, "value") else str(v) if v else ""


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.assignment.update_tech_status_for_assignment",
)
def update_tech_status_for_assignment(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Update technician deployability when assignment is created/started."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        assignment_id = payload.entity_id
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return {"status": "skipped", "reason": "Assignment not found"}

        technician = session.get(Technician, assignment.technician_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        if technician.deployability_locked:
            return {"status": "skipped", "reason": "Deployability locked"}

        old_status = _enum_val(technician.deployability_status)
        if assignment.status == "Active":
            technician.deployability_status = DeployabilityStatus.CURRENTLY_ASSIGNED
            session.commit()

        return {
            "status": "updated",
            "technician_id": str(assignment.technician_id),
            "old_status": old_status,
            "new_status": _enum_val(technician.deployability_status),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.assignment.notify_assignment_created",
)
def notify_assignment_created(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Create suggested actions when a new assignment is created."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        assignment_id = payload.entity_id
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return {"status": "skipped", "reason": "Assignment not found"}

        technician = session.get(Technician, assignment.technician_id)
        role = session.get(ProjectRole, assignment.role_id)
        tech_name = technician.full_name if technician else "Unknown"
        role_name = role.role_name if role else "Unknown Role"

        ops_action = SuggestedAction(
            target_role="ops",
            action_type="assignment_created",
            title=f"New Assignment: {tech_name}",
            description=f"{tech_name} assigned to {role_name}. Starts {assignment.start_date}.",
            link=f"/assignments/{assignment_id}",
            priority=2,
        )
        session.add(ops_action)

        if technician:
            tech_action = SuggestedAction(
                target_role="technician",
                target_user_id=str(technician.id),
                action_type="new_assignment",
                title=f"New Assignment: {role_name}",
                description=f"You've been assigned to {role_name} starting {assignment.start_date}.",
                link="/my-assignments",
                priority=5,
            )
            session.add(tech_action)

        if role:
            role.filled = min(role.quantity, (role.filled or 0) + 1)

        session.commit()
        return {
            "status": "notified",
            "assignment_id": str(assignment_id),
            "technician_id": str(assignment.technician_id),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.assignment.handle_assignment_end",
)
def handle_assignment_end(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Handle end of assignment — update technician status, check backfill needs."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        assignment_id = payload.entity_id
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return {"status": "skipped", "reason": "Assignment not found"}

        technician = session.get(Technician, assignment.technician_id)
        cascade_events = []

        if technician and not technician.deployability_locked:
            other_active = session.query(Assignment).filter(
                Assignment.technician_id == technician.id,
                Assignment.id != assignment.id,
                Assignment.status == "Active",
            ).count()

            if other_active == 0:
                old_status = _enum_val(technician.deployability_status)
                technician.deployability_status = DeployabilityStatus.READY_NOW
                technician.available_from = date.today()
                session.commit()

                cascade_events.append(
                    EventPayload(
                        event_type=EventType.TECHNICIAN_AVAILABILITY_CHANGED,
                        entity_type="technician",
                        entity_id=str(technician.id),
                        actor_id="system",
                        data={"old_status": old_status, "available_from": str(date.today())},
                    ).to_dict()
                )

        role = session.get(ProjectRole, assignment.role_id)
        if role:
            role.filled = max(0, (role.filled or 0) - 1)
            if role.filled < role.quantity:
                project = session.get(Project, role.project_id)
                ps = _enum_val(project.status) if project else ""
                if project and ps in (ProjectStatus.ACTIVE.value, ProjectStatus.STAFFING.value):
                    tech_name = technician.full_name if technician else "Unknown"
                    explanation = generate_backfill_explanation(
                        technician_name=tech_name,
                        role_name=role.role_name,
                        reason=f"Assignment ended, {role.quantity - role.filled} slot(s) unfilled",
                    )

                    rec = Recommendation(
                        recommendation_type="backfill",
                        target_entity_type="project_role",
                        target_entity_id=str(role.id),
                        role_id=str(role.id),
                        project_id=str(role.project_id),
                        scorecard={
                            "project_name": project.name,
                            "role_name": role.role_name,
                            "unfilled": role.quantity - role.filled,
                            "reason": "assignment_ended",
                        },
                        explanation=explanation,
                        status=RecommendationStatus.PENDING.value,
                        agent_name="assignment_agent",
                    )
                    session.add(rec)

            session.commit()

        return {
            "status": "handled",
            "assignment_id": str(assignment_id),
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
    name="app.workers.tasks.assignment.handle_assignment_cancellation",
)
def handle_assignment_cancellation(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Handle a cancelled assignment — revert technician availability."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        assignment_id = payload.entity_id
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return {"status": "skipped", "reason": "Assignment not found"}

        technician = session.get(Technician, assignment.technician_id)

        if technician and not technician.deployability_locked:
            other_active = session.query(Assignment).filter(
                Assignment.technician_id == technician.id,
                Assignment.id != assignment.id,
                Assignment.status == "Active",
            ).count()

            if other_active == 0:
                technician.deployability_status = DeployabilityStatus.READY_NOW
                technician.available_from = date.today()

        role = session.get(ProjectRole, assignment.role_id)
        if role:
            role.filled = max(0, (role.filled or 0) - 1)

        session.commit()

        action = SuggestedAction(
            target_role="ops",
            action_type="assignment_cancelled",
            title="Assignment Cancelled",
            description=f"Assignment {assignment_id} was cancelled. Check for backfill needs.",
            link=f"/assignments/{assignment_id}",
            priority=4,
        )
        session.add(action)
        session.commit()

        return {
            "status": "handled",
            "assignment_id": str(assignment_id),
            "technician_id": str(assignment.technician_id),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    base=ReactiveAgentTask,
    name="app.workers.tasks.assignment.create_rolling_off_alert",
)
def create_rolling_off_alert(self, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Create proactive alert when a technician is rolling off soon."""
    payload = EventPayload.from_dict(event_dict)
    session = SessionLocal()
    try:
        tech_id = payload.entity_id
        technician = session.get(Technician, tech_id)
        if not technician:
            return {"status": "skipped", "reason": "Technician not found"}

        roll_off_date = payload.data.get("roll_off_date", str(date.today() + timedelta(days=14)))

        rec = Recommendation(
            recommendation_type="next_step",
            target_entity_type="technician",
            target_entity_id=str(tech_id),
            technician_id=str(tech_id),
            scorecard={
                "technician_name": technician.full_name,
                "roll_off_date": roll_off_date,
                "career_stage": _enum_val(technician.career_stage),
                "deployability_status": _enum_val(technician.deployability_status),
            },
            explanation=(
                f"{technician.full_name} is rolling off on {roll_off_date}. "
                f"Consider pre-booking their next assignment to minimize idle time. "
                f"Current status: {_enum_val(technician.deployability_status)}."
            ),
            status=RecommendationStatus.PENDING.value,
            agent_name="assignment_agent",
        )
        session.add(rec)

        action = SuggestedAction(
            target_role="ops",
            action_type="rolling_off",
            title=f"Rolling Off: {technician.full_name}",
            description=f"{technician.full_name} rolls off on {roll_off_date}. Pre-book next assignment.",
            link=f"/technicians/{tech_id}",
            priority=4,
        )
        session.add(action)
        session.commit()

        return {
            "status": "alert_created",
            "technician_id": str(tech_id),
            "roll_off_date": roll_off_date,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
