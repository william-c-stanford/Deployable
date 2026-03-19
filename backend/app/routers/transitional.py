"""API endpoints for managing transitional deployability states.

Transitional states (Onboarding, PendingReview, Suspended) are temporary
statuses that auto-resolve based on timeout, events, conditions, or manual action.
These endpoints allow ops users to:
- Enter a technician into a transitional state
- View active/resolved transitional states
- Manually resolve a transitional state
- Query transitional state history
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, CurrentUser, require_role
from app.models.technician import (
    Technician,
    DeployabilityStatus,
    TransitionalTrigger,
    TransitionalResolutionType,
)
from app.models.transitional_state import TransitionalState
from app.schemas.transitional import (
    TransitionalStateCreate,
    TransitionalStateResolve,
    TransitionalStateResponse,
    TransitionalStateListResponse,
)
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

router = APIRouter(prefix="/api/transitional-states", tags=["transitional-states"])

# Default timeout hours per transitional status type
DEFAULT_TIMEOUTS = {
    "Onboarding": 168.0,      # 7 days
    "Pending Review": 48.0,   # 2 days
    "Suspended": 720.0,       # 30 days
}

VALID_TRANSITIONAL_STATUSES = {"Onboarding", "Pending Review", "Suspended"}


def _enum_value(enum_val) -> str:
    """Extract string value from enum or return as-is."""
    return enum_val.value if hasattr(enum_val, "value") else str(enum_val)


# ---------------------------------------------------------------------------
# Create: enter a technician into a transitional state
# ---------------------------------------------------------------------------

@router.post("", response_model=TransitionalStateResponse, status_code=status.HTTP_201_CREATED)
def create_transitional_state(
    data: TransitionalStateCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Enter a technician into a transitional deployability state.

    This sets the technician's deployability_status to the specified transitional
    status and creates a tracking record with resolution configuration.
    Only ops users can create transitional states.
    """
    # Validate transitional status
    if data.transitional_status not in VALID_TRANSITIONAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transitional status. Must be one of: {', '.join(VALID_TRANSITIONAL_STATUSES)}",
        )

    # Find the technician
    technician = db.query(Technician).filter(Technician.id == data.technician_id).first()
    if not technician:
        raise HTTPException(status_code=404, detail="Technician not found")

    # Check for existing active transitional state
    existing = (
        db.query(TransitionalState)
        .filter(
            TransitionalState.technician_id == data.technician_id,
            TransitionalState.is_active == True,  # noqa: E712
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Technician already has an active transitional state: {_enum_value(existing.transitional_status)}",
        )

    # Resolve enum values
    transitional_status_enum = None
    for member in DeployabilityStatus:
        if member.value == data.transitional_status:
            transitional_status_enum = member
            break
    if not transitional_status_enum:
        raise HTTPException(status_code=400, detail=f"Unknown status: {data.transitional_status}")

    trigger_enum = None
    for member in TransitionalTrigger:
        if member.value == data.trigger:
            trigger_enum = member
            break
    if not trigger_enum:
        trigger_enum = TransitionalTrigger.MANUAL

    resolution_type_enum = None
    for member in TransitionalResolutionType:
        if member.value == data.resolution_type:
            resolution_type_enum = member
            break
    if not resolution_type_enum:
        resolution_type_enum = TransitionalResolutionType.TIMEOUT

    # Compute timeout
    timeout_hours = data.timeout_hours
    if timeout_hours is None and resolution_type_enum == TransitionalResolutionType.TIMEOUT:
        timeout_hours = DEFAULT_TIMEOUTS.get(data.transitional_status, 48.0)

    # Compute expires_at
    now = datetime.utcnow()
    expires_at = None
    if timeout_hours is not None:
        expires_at = now + timedelta(hours=timeout_hours)

    # Resolve fallback status
    fallback_status_enum = None
    if data.fallback_status:
        for member in DeployabilityStatus:
            if member.value == data.fallback_status:
                fallback_status_enum = member
                break

    # Snapshot current status
    previous_status = technician.deployability_status

    # Create the transitional state record
    ts = TransitionalState(
        technician_id=data.technician_id,
        transitional_status=transitional_status_enum,
        previous_status=previous_status,
        trigger=trigger_enum,
        trigger_detail=data.trigger_detail,
        resolution_type=resolution_type_enum,
        timeout_hours=timeout_hours,
        resolution_events=data.resolution_events,
        resolution_conditions=data.resolution_conditions,
        fallback_status=fallback_status_enum,
        is_active=True,
        entered_at=now,
        expires_at=expires_at,
        notes=data.notes,
    )
    db.add(ts)

    # Update technician's deployability status
    if not technician.deployability_locked:
        technician.deployability_status = transitional_status_enum
    db.commit()
    db.refresh(ts)

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.TRANSITIONAL_STATE_ENTERED,
        entity_type="transitional_state",
        entity_id=str(ts.id),
        actor_id=str(current_user.id),
        data={
            "technician_id": str(data.technician_id),
            "transitional_status": data.transitional_status,
            "trigger": data.trigger,
            "resolution_type": data.resolution_type,
            "timeout_hours": timeout_hours,
            "previous_status": _enum_value(previous_status),
        },
    ))

    return _to_response(ts)


# ---------------------------------------------------------------------------
# List transitional states
# ---------------------------------------------------------------------------

@router.get("", response_model=TransitionalStateListResponse)
def list_transitional_states(
    technician_id: Optional[uuid.UUID] = Query(None),
    is_active: Optional[bool] = Query(None),
    transitional_status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List transitional state records with optional filters."""
    query = db.query(TransitionalState)

    if technician_id:
        query = query.filter(TransitionalState.technician_id == technician_id)
    if is_active is not None:
        query = query.filter(TransitionalState.is_active == is_active)
    if transitional_status:
        # Match against enum value
        for member in DeployabilityStatus:
            if member.value == transitional_status:
                query = query.filter(TransitionalState.transitional_status == member)
                break

    total = query.count()
    items = (
        query.order_by(TransitionalState.entered_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return TransitionalStateListResponse(
        items=[_to_response(ts) for ts in items],
        total=total,
        skip=skip,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Get single transitional state
# ---------------------------------------------------------------------------

@router.get("/{ts_id}", response_model=TransitionalStateResponse)
def get_transitional_state(
    ts_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a single transitional state record by ID."""
    ts = db.query(TransitionalState).filter(TransitionalState.id == ts_id).first()
    if not ts:
        raise HTTPException(status_code=404, detail="Transitional state not found")
    return _to_response(ts)


# ---------------------------------------------------------------------------
# Manually resolve a transitional state
# ---------------------------------------------------------------------------

@router.post("/{ts_id}/resolve", response_model=TransitionalStateResponse)
def resolve_transitional_state(
    ts_id: uuid.UUID,
    data: TransitionalStateResolve,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Manually resolve an active transitional state.

    Ops can explicitly resolve a transitional state, overriding the automatic
    resolution mechanism. If resolved_to_status is not provided, the system
    will compute the appropriate status.
    """
    from sqlalchemy.orm import joinedload

    ts = db.query(TransitionalState).filter(TransitionalState.id == ts_id).first()
    if not ts:
        raise HTTPException(status_code=404, detail="Transitional state not found")
    if not ts.is_active:
        raise HTTPException(status_code=409, detail="Transitional state is already resolved")

    technician = (
        db.query(Technician)
        .options(
            joinedload(Technician.certifications),
            joinedload(Technician.documents),
            joinedload(Technician.skills),
        )
        .filter(Technician.id == ts.technician_id)
        .first()
    )
    if not technician:
        raise HTTPException(status_code=404, detail="Technician not found")

    # Determine resolved-to status
    from app.workers.tasks.transitional import _compute_resolved_status

    if data.resolved_to_status:
        resolved_to = data.resolved_to_status
    else:
        resolved_to = _compute_resolved_status(db, technician, ts.fallback_status)

    # Update the record
    ts.is_active = False
    ts.resolved_at = datetime.utcnow()
    ts.resolved_by = str(current_user.id)
    ts.resolution_reason = data.resolution_reason

    # Set resolved_to_status enum
    for member in DeployabilityStatus:
        if member.value == resolved_to:
            ts.resolved_to_status = member
            break

    # Update technician status
    old_status = _enum_value(technician.deployability_status)
    if not technician.deployability_locked:
        for member in DeployabilityStatus:
            if member.value == resolved_to:
                technician.deployability_status = member
                break

    db.commit()
    db.refresh(ts)

    # Dispatch events
    dispatch_event_safe(EventPayload(
        event_type=EventType.TRANSITIONAL_STATE_RESOLVED,
        entity_type="transitional_state",
        entity_id=str(ts.id),
        actor_id=str(current_user.id),
        data={
            "technician_id": str(technician.id),
            "technician_name": technician.full_name,
            "transitional_status": _enum_value(ts.transitional_status),
            "resolved_to": resolved_to,
            "resolution_reason": data.resolution_reason,
            "resolution_type": "manual",
            "old_status": old_status,
        },
    ))

    if old_status != resolved_to:
        dispatch_event_safe(EventPayload(
            event_type=EventType.TECHNICIAN_STATUS_CHANGED,
            entity_type="technician",
            entity_id=str(technician.id),
            actor_id=str(current_user.id),
            data={
                "field": "deployability_status",
                "old_value": old_status,
                "new_value": resolved_to,
                "source": "transitional_manual_resolve",
            },
        ))

    return _to_response(ts)


# ---------------------------------------------------------------------------
# Get active transitional state for a technician
# ---------------------------------------------------------------------------

@router.get("/technician/{tech_id}/active", response_model=Optional[TransitionalStateResponse])
def get_active_transitional_state(
    tech_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the active transitional state for a technician, if any."""
    ts = (
        db.query(TransitionalState)
        .filter(
            TransitionalState.technician_id == tech_id,
            TransitionalState.is_active == True,  # noqa: E712
        )
        .first()
    )
    if not ts:
        return None
    return _to_response(ts)


# ---------------------------------------------------------------------------
# Helper: convert model to response schema
# ---------------------------------------------------------------------------

def _to_response(ts: TransitionalState) -> TransitionalStateResponse:
    """Convert a TransitionalState model instance to a response schema."""
    return TransitionalStateResponse(
        id=ts.id,
        technician_id=ts.technician_id,
        transitional_status=_enum_value(ts.transitional_status),
        previous_status=_enum_value(ts.previous_status) if ts.previous_status else None,
        trigger=_enum_value(ts.trigger),
        trigger_detail=ts.trigger_detail,
        resolution_type=_enum_value(ts.resolution_type),
        timeout_hours=ts.timeout_hours,
        resolution_events=ts.resolution_events,
        resolution_conditions=ts.resolution_conditions,
        fallback_status=_enum_value(ts.fallback_status) if ts.fallback_status else None,
        is_active=ts.is_active,
        entered_at=ts.entered_at,
        expires_at=ts.expires_at,
        resolved_at=ts.resolved_at,
        resolved_by=ts.resolved_by,
        resolution_reason=ts.resolution_reason,
        resolution_event_type=ts.resolution_event_type,
        resolved_to_status=_enum_value(ts.resolved_to_status) if ts.resolved_to_status else None,
        notes=ts.notes,
        created_at=ts.created_at,
        updated_at=ts.updated_at,
    )
