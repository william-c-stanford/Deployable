"""Partner confirmation endpoints for assignment start/end dates.

Ops users can create confirmation requests; partners can confirm or decline.
Both roles can list confirmations scoped to their visibility.
WebSocket broadcasts notify connected clients of status changes in real-time.
"""

import asyncio
import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.assignment import Assignment
from app.models.assignment_confirmation import (
    AssignmentConfirmation,
    ConfirmationStatus,
    ConfirmationType,
    EscalationStatus,
)
from app.models.project import Project, ProjectRole
from app.models.technician import Technician
from app.models.user import Partner
from app.schemas.partner_confirmation import (
    ConfirmationCreateRequest,
    ConfirmationRespondRequest,
    ConfirmationResponse,
    ConfirmationListResponse,
    ConfirmationActionResult,
)
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType
from app.websocket import manager as ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/partner-confirmations", tags=["partner-confirmations"])


# ---------------------------------------------------------------------------
# WebSocket broadcast helpers
# ---------------------------------------------------------------------------

async def _broadcast_confirmation_event(event_type: str, confirmation_data: dict):
    """Broadcast a confirmation status change via WebSocket."""
    event = {
        "event_type": event_type,
        "topic": "confirmations",
        "confirmation": confirmation_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await ws_manager.broadcast("confirmations", event)
    await ws_manager.broadcast("dashboard", event)
    logger.info(f"Broadcast {event_type} for confirmation {confirmation_data.get('id')}")


def _broadcast_sync(event_type: str, confirmation_data: dict):
    """Fire-and-forget WS broadcast from a sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_broadcast_confirmation_event(event_type, confirmation_data))
        else:
            asyncio.run(_broadcast_confirmation_event(event_type, confirmation_data))
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_uuid(val: Union[str, _uuid.UUID]) -> _uuid.UUID:
    """Safely convert a string to UUID for DB queries."""
    if isinstance(val, _uuid.UUID):
        return val
    return _uuid.UUID(str(val))


def _enrich_confirmation(conf: AssignmentConfirmation, db: Session) -> ConfirmationResponse:
    """Build a ConfirmationResponse with enriched names from related entities."""
    technician_name = None
    project_name = None
    role_name = None

    if conf.assignment:
        tech = db.query(Technician).filter(Technician.id == conf.assignment.technician_id).first()
        if tech:
            technician_name = f"{tech.first_name} {tech.last_name}"
        if conf.assignment.role:
            role_name = conf.assignment.role.role_name
            if conf.assignment.role.project:
                project_name = conf.assignment.role.project.name

    return ConfirmationResponse(
        id=conf.id,
        assignment_id=conf.assignment_id,
        partner_id=conf.partner_id,
        confirmation_type=conf.confirmation_type.value if isinstance(conf.confirmation_type, ConfirmationType) else conf.confirmation_type,
        status=conf.status.value if isinstance(conf.status, ConfirmationStatus) else conf.status,
        requested_date=conf.requested_date,
        proposed_date=conf.proposed_date,
        response_note=conf.response_note,
        requested_at=conf.requested_at,
        responded_at=conf.responded_at,
        escalated=conf.escalated,
        escalated_at=conf.escalated_at,
        escalation_status=conf.escalation_status.value if isinstance(conf.escalation_status, EscalationStatus) else (conf.escalation_status or "none"),
        hours_waiting=conf.hours_waiting,
        technician_name=technician_name,
        project_name=project_name,
        role_name=role_name,
    )


def _confirmation_to_ws_dict(response: ConfirmationResponse) -> dict:
    """Convert a ConfirmationResponse to a dict suitable for WS broadcast."""
    return {
        "id": str(response.id),
        "assignment_id": str(response.assignment_id),
        "partner_id": str(response.partner_id),
        "confirmation_type": response.confirmation_type,
        "status": response.status,
        "requested_date": str(response.requested_date),
        "proposed_date": str(response.proposed_date) if response.proposed_date else None,
        "response_note": response.response_note,
        "requested_at": str(response.requested_at),
        "responded_at": str(response.responded_at) if response.responded_at else None,
        "technician_name": response.technician_name,
        "project_name": response.project_name,
        "role_name": response.role_name,
    }


# ---------------------------------------------------------------------------
# Create confirmation request (ops only)
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=ConfirmationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a confirmation request",
    description="Ops creates a date-confirmation request for a partner to accept or decline.",
)
def create_confirmation(
    data: ConfirmationCreateRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Create a new assignment confirmation request."""
    # Validate assignment exists
    assignment = db.query(Assignment).filter(Assignment.id == data.assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Validate partner exists
    partner = db.query(Partner).filter(Partner.id == data.partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    # Check for existing pending confirmation of same type
    existing = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.assignment_id == data.assignment_id,
            AssignmentConfirmation.confirmation_type == data.confirmation_type,
            AssignmentConfirmation.status == ConfirmationStatus.PENDING,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A pending {data.confirmation_type} confirmation already exists for this assignment",
        )

    confirmation = AssignmentConfirmation(
        assignment_id=data.assignment_id,
        partner_id=data.partner_id,
        confirmation_type=data.confirmation_type,
        status=ConfirmationStatus.PENDING,
        requested_date=data.requested_date,
    )
    db.add(confirmation)
    db.commit()
    db.refresh(confirmation)

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.CONFIRMATION_REQUESTED,
        entity_type="assignment_confirmation",
        entity_id=str(confirmation.id),
        actor_id=current_user.user_id,
        data={
            "assignment_id": str(data.assignment_id),
            "partner_id": str(data.partner_id),
            "confirmation_type": data.confirmation_type,
            "requested_date": str(data.requested_date),
        },
    ))

    enriched = _enrich_confirmation(confirmation, db)

    # Broadcast new confirmation via WebSocket
    _broadcast_sync("confirmation.created", _confirmation_to_ws_dict(enriched))

    return enriched


# ---------------------------------------------------------------------------
# List confirmations (ops sees all, partner sees own)
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=ConfirmationListResponse,
    summary="List confirmation requests",
    description="Ops sees all; partners see only confirmations addressed to them.",
)
def list_confirmations(
    assignment_id: Optional[str] = Query(None, description="Filter by assignment"),
    partner_id: Optional[str] = Query(None, description="Filter by partner"),
    conf_status: Optional[str] = Query(None, alias="status", description="Filter by status"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "partner")),
):
    """List assignment confirmations with role-based scoping."""
    query = db.query(AssignmentConfirmation)

    # Partners can only see their own confirmations
    if current_user.role == "partner":
        try:
            pid = _to_uuid(current_user.user_id)
            query = query.filter(AssignmentConfirmation.partner_id == pid)
        except ValueError:
            pass  # will return empty
    elif partner_id:
        try:
            query = query.filter(AssignmentConfirmation.partner_id == _to_uuid(partner_id))
        except ValueError:
            pass

    if assignment_id:
        try:
            query = query.filter(AssignmentConfirmation.assignment_id == _to_uuid(assignment_id))
        except ValueError:
            pass

    if conf_status:
        query = query.filter(AssignmentConfirmation.status == conf_status)

    confirmations = query.order_by(AssignmentConfirmation.requested_at.desc()).all()
    pending_count = sum(1 for c in confirmations if c.status == ConfirmationStatus.PENDING or c.status == "pending")

    return ConfirmationListResponse(
        confirmations=[_enrich_confirmation(c, db) for c in confirmations],
        total=len(confirmations),
        pending_count=pending_count,
    )


# ---------------------------------------------------------------------------
# Get single confirmation
# ---------------------------------------------------------------------------

@router.get(
    "/{confirmation_id}",
    response_model=ConfirmationResponse,
    summary="Get a confirmation request",
)
def get_confirmation(
    confirmation_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "partner")),
):
    """Retrieve a single confirmation by ID."""
    try:
        cid = _to_uuid(confirmation_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Confirmation not found")

    confirmation = (
        db.query(AssignmentConfirmation)
        .filter(AssignmentConfirmation.id == cid)
        .first()
    )
    if not confirmation:
        raise HTTPException(status_code=404, detail="Confirmation not found")

    # Partners can only view their own
    if current_user.role == "partner" and str(confirmation.partner_id) != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this confirmation")

    return _enrich_confirmation(confirmation, db)


# ---------------------------------------------------------------------------
# Partner responds to confirmation (confirm/decline)
# ---------------------------------------------------------------------------

@router.post(
    "/{confirmation_id}/respond",
    response_model=ConfirmationActionResult,
    summary="Respond to a confirmation",
    description="Partner confirms or declines an assignment date. Human approval gate.",
)
def respond_to_confirmation(
    confirmation_id: str,
    data: ConfirmationRespondRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner", "ops")),
):
    """Partner confirms or declines an assignment date confirmation request."""
    try:
        cid = _to_uuid(confirmation_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Confirmation not found")

    confirmation = (
        db.query(AssignmentConfirmation)
        .filter(AssignmentConfirmation.id == cid)
        .first()
    )
    if not confirmation:
        raise HTTPException(status_code=404, detail="Confirmation not found")

    # Partners can only respond to their own confirmations
    if current_user.role == "partner" and str(confirmation.partner_id) != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to respond to this confirmation")

    # Must be pending
    if confirmation.status != ConfirmationStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Confirmation already {confirmation.status.value if isinstance(confirmation.status, ConfirmationStatus) else confirmation.status}",
        )

    # Validate decline requires proposed_date
    if data.action == "decline" and not data.proposed_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A proposed_date is required when declining",
        )

    # Update confirmation
    now = datetime.utcnow()
    assignment_updated = False

    if data.action == "confirm":
        confirmation.status = ConfirmationStatus.CONFIRMED
        confirmation.responded_at = now
        confirmation.response_note = data.response_note

        # Update the assignment's partner_confirmed flags
        assignment = db.query(Assignment).filter(Assignment.id == confirmation.assignment_id).first()
        if assignment:
            if confirmation.confirmation_type == ConfirmationType.START_DATE or confirmation.confirmation_type == "start_date":
                assignment.partner_confirmed_start = True
                assignment_updated = True
            elif confirmation.confirmation_type == ConfirmationType.END_DATE or confirmation.confirmation_type == "end_date":
                assignment.partner_confirmed_end = True
                assignment_updated = True

        event_type = EventType.CONFIRMATION_CONFIRMED
        ws_event_type = "confirmation.confirmed"
        message = "Assignment date confirmed by partner."

    else:  # decline
        confirmation.status = ConfirmationStatus.DECLINED
        confirmation.responded_at = now
        confirmation.proposed_date = data.proposed_date
        confirmation.response_note = data.response_note

        event_type = EventType.CONFIRMATION_DECLINED
        ws_event_type = "confirmation.declined"
        message = f"Assignment date declined. Partner proposed alternative: {data.proposed_date}."

    db.commit()
    db.refresh(confirmation)

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=event_type,
        entity_type="assignment_confirmation",
        entity_id=str(confirmation.id),
        actor_id=current_user.user_id,
        data={
            "assignment_id": str(confirmation.assignment_id),
            "partner_id": str(confirmation.partner_id),
            "confirmation_type": confirmation.confirmation_type.value if isinstance(confirmation.confirmation_type, ConfirmationType) else confirmation.confirmation_type,
            "action": data.action,
            "proposed_date": str(data.proposed_date) if data.proposed_date else None,
        },
    ))

    enriched = _enrich_confirmation(confirmation, db)

    # Broadcast status change via WebSocket
    _broadcast_sync(ws_event_type, _confirmation_to_ws_dict(enriched))

    return ConfirmationActionResult(
        confirmation=enriched,
        assignment_updated=assignment_updated,
        message=message,
    )


# ---------------------------------------------------------------------------
# Bulk: get pending confirmations for a partner
# ---------------------------------------------------------------------------

@router.get(
    "/partner/{partner_id}/pending",
    response_model=ConfirmationListResponse,
    summary="Get pending confirmations for a partner",
    description="Returns all pending confirmations for a specific partner.",
)
def get_pending_for_partner(
    partner_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "partner")),
):
    """Get all pending confirmations for a given partner.

    Partners can only access their own; ops can query any partner.
    """
    if current_user.role == "partner" and current_user.user_id != partner_id:
        raise HTTPException(status_code=403, detail="Not authorized to view other partner's confirmations")

    try:
        pid = _to_uuid(partner_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid partner ID")

    confirmations = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.partner_id == pid,
            AssignmentConfirmation.status == ConfirmationStatus.PENDING,
        )
        .order_by(AssignmentConfirmation.requested_at.desc())
        .all()
    )

    return ConfirmationListResponse(
        confirmations=[_enrich_confirmation(c, db) for c in confirmations],
        total=len(confirmations),
        pending_count=len(confirmations),
    )
