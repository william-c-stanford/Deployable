"""Post-approval workflow for headcount requests.

Core business logic for converting an approved PendingHeadcountRequest
into actual ProjectRole slot records and emitting real-time notifications.

Separated from the Celery task wrapper for testability.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis

from app.workers.events import EventPayload, EventType
from app.models.audit import PendingHeadcountRequest, AuditLog, SuggestedAction
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.user import Partner

logger = logging.getLogger("deployable.services.headcount_approval")

# Redis pub/sub channel for WebSocket broadcast relay
WS_BROADCAST_CHANNEL = "deployable:ws_broadcast"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def broadcast_ws_notification(topic: str, event: dict[str, Any]) -> None:
    """Push a WebSocket notification via Redis pub/sub.

    Celery workers run in a sync context without access to the FastAPI
    WebSocket ConnectionManager. We publish to a Redis channel that the
    web process subscribes to and relays to connected WebSocket clients.
    """
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        message = json.dumps({"topic": topic, "event": event}, default=str)
        r.publish(WS_BROADCAST_CHANNEL, message)
        logger.info(
            "Published WS notification to topic '%s': %s",
            topic,
            event.get("event_type"),
        )
    except Exception:
        logger.warning("Failed to publish WS notification", exc_info=True)


def execute_headcount_approval(
    session: Any,
    payload: EventPayload,
    *,
    ws_broadcaster=broadcast_ws_notification,
) -> dict[str, Any]:
    """Convert an approved PendingHeadcountRequest into ProjectRole slot records.

    Post-approval workflow:
    1. Load the approved headcount request
    2. Resolve or create the target project
    3. Create ProjectRole records (one with quantity N)
    4. Optionally update project status to 'Staffing' if it was in 'Draft'
    5. Emit WebSocket notification to the requesting partner
    6. Create audit trail entries
    7. Return cascade events to trigger staffing recommendations for new roles

    Args:
        session: SQLAlchemy session (sync).
        payload: Deserialized event payload with entity_id = headcount request ID.
        ws_broadcaster: Callable for broadcasting WebSocket notifications (injectable for testing).

    Returns:
        Dict with result data including cascade_events for downstream processing.
    """
    request_id = payload.entity_id
    hr = session.get(PendingHeadcountRequest, request_id)
    if not hr:
        return {"status": "skipped", "reason": f"HeadcountRequest {request_id} not found"}

    if hr.status != "Approved":
        return {
            "status": "skipped",
            "reason": f"HeadcountRequest {request_id} is '{hr.status}', not 'Approved'",
        }

    # --- Resolve target project ---
    project = None
    if hr.project_id:
        project = session.get(Project, hr.project_id)

    if not project:
        # No project linked — find the most recent active/staffing/draft project
        partner_projects = (
            session.query(Project)
            .filter(
                Project.partner_id == hr.partner_id,
                Project.status.in_([
                    ProjectStatus.STAFFING.value,
                    ProjectStatus.ACTIVE.value,
                    ProjectStatus.DRAFT.value,
                ]),
            )
            .order_by(Project.created_at.desc())
            .first()
        )
        if partner_projects:
            project = partner_projects
            logger.info(
                "HeadcountRequest %s had no project_id; defaulting to project %s",
                request_id, project.id,
            )
        else:
            return {
                "status": "skipped",
                "reason": (
                    f"No active project found for partner {hr.partner_id}. "
                    "Cannot create role slots without a project."
                ),
            }

    # --- Get partner info for notifications ---
    partner = session.get(Partner, hr.partner_id)
    partner_name = partner.name if partner else "Unknown Partner"

    # --- Create ProjectRole slot record(s) ---
    created_roles = []
    role = ProjectRole(
        project_id=project.id,
        role_name=hr.role_name,
        required_skills=hr.required_skills or [],
        required_certs=hr.required_certs or [],
        skill_weights={},
        quantity=hr.quantity,
        filled=0,
        hourly_rate=None,
        per_diem=None,
    )
    session.add(role)
    session.flush()  # Get the role ID

    created_roles.append({
        "role_id": str(role.id),
        "role_name": role.role_name,
        "project_id": str(project.id),
        "project_name": project.name,
        "quantity": role.quantity,
        "open_slots": role.quantity,
    })

    # --- Update project status to 'Staffing' if it was 'Draft' ---
    project_status_updated = False
    if project.status in (ProjectStatus.DRAFT.value, ProjectStatus.DRAFT):
        project.status = ProjectStatus.STAFFING
        project_status_updated = True
        logger.info(
            "Project %s status updated from Draft to Staffing",
            project.id,
        )

    # --- Audit trail ---
    audit = AuditLog(
        user_id=payload.actor_id,
        action="headcount_request.roles_created",
        entity_type="headcount_request",
        entity_id=str(request_id),
        details={
            "partner_id": str(hr.partner_id),
            "partner_name": partner_name,
            "project_id": str(project.id),
            "project_name": project.name,
            "roles_created": created_roles,
            "total_quantity": hr.quantity,
            "project_status_updated": project_status_updated,
        },
    )
    session.add(audit)

    # --- Create SuggestedAction for ops to start staffing ---
    action = SuggestedAction(
        target_role="ops",
        action_type="new_role_slots_created",
        title=f"New role: {hr.role_name} \u00d7{hr.quantity} on {project.name}",
        description=(
            f"Headcount request from {partner_name} approved. "
            f"{hr.quantity} slot(s) for '{hr.role_name}' created on "
            f"'{project.name}'. Run staffing agent to find candidates."
        ),
        link=f"/projects/{project.id}/roles/{role.id}/recommendations",
        priority=5,
        metadata_={
            "headcount_request_id": str(request_id),
            "role_id": str(role.id),
            "project_id": str(project.id),
        },
    )
    session.add(action)

    session.commit()

    # --- WebSocket notification to the requesting partner ---
    ws_event = {
        "event_type": "headcount.approved",
        "topic": "partner",
        "headcount_request_id": str(request_id),
        "partner_id": str(hr.partner_id),
        "partner_name": partner_name,
        "project_id": str(project.id),
        "project_name": project.name,
        "role_name": hr.role_name,
        "quantity": hr.quantity,
        "roles_created": created_roles,
        "message": (
            f"Your headcount request for {hr.quantity} '{hr.role_name}' "
            f"on '{project.name}' has been approved. "
            f"Role slots have been created and staffing is underway."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ws_broadcaster("partner", ws_event)

    # Also broadcast to the dashboard topic so ops sees the update
    dashboard_event = {
        "event_type": "headcount.roles_created",
        "topic": "dashboard",
        "headcount_request_id": str(request_id),
        "project_id": str(project.id),
        "project_name": project.name,
        "role_name": hr.role_name,
        "quantity": hr.quantity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ws_broadcaster("dashboard", dashboard_event)

    # --- Cascade: trigger staffing recommendations for the new role ---
    cascade_events = []
    for role_info in created_roles:
        cascade_events.append(
            EventPayload(
                event_type=EventType.ROLE_UNFILLED,
                entity_type="project_role",
                entity_id=role_info["role_id"],
                actor_id=payload.actor_id,
                data={
                    "role_id": role_info["role_id"],
                    "project_id": role_info["project_id"],
                    "role_name": role_info["role_name"],
                    "quantity": role_info["quantity"],
                    "reason": "headcount_approved",
                    "headcount_request_id": str(request_id),
                },
            ).to_dict()
        )

    logger.info(
        "Approved headcount request %s: created %d role slot(s) for '%s' on project '%s'",
        request_id,
        len(created_roles),
        hr.role_name,
        project.name,
    )

    return {
        "status": "completed",
        "headcount_request_id": str(request_id),
        "partner_id": str(hr.partner_id),
        "partner_name": partner_name,
        "project_id": str(project.id),
        "project_name": project.name,
        "roles_created": created_roles,
        "project_status_updated": project_status_updated,
        "cascade_events": cascade_events,
    }
