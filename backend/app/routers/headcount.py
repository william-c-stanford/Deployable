"""CRUD + approve/reject endpoints for PendingHeadcountRequest.

All mutating endpoints require ops role. Partners can create requests
but only ops can list, update, approve, or reject them.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.audit import (
    PendingHeadcountRequest,
    HeadcountRequestStatus,
    AuditLog,
)
from app.models.user import Partner
from app.models.project import Project
from app.schemas.headcount import (
    HeadcountRequestCreate,
    HeadcountRequestUpdate,
    HeadcountRequestAction,
    HeadcountRequestResponse,
    HeadcountRequestListResponse,
)
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

router = APIRouter(prefix="/api/headcount-requests", tags=["headcount-requests"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_request_or_404(db: Session, request_id: uuid.UUID) -> PendingHeadcountRequest:
    """Fetch a headcount request by ID or raise 404."""
    hr = (
        db.query(PendingHeadcountRequest)
        .filter(PendingHeadcountRequest.id == request_id)
        .first()
    )
    if not hr:
        raise HTTPException(status_code=404, detail="Headcount request not found")
    return hr


def _serialize(hr: PendingHeadcountRequest) -> HeadcountRequestResponse:
    """Convert ORM instance to response schema with joined names."""
    return HeadcountRequestResponse(
        id=hr.id,
        partner_id=hr.partner_id,
        project_id=hr.project_id,
        role_name=hr.role_name,
        quantity=hr.quantity,
        priority=hr.priority or "normal",
        start_date=hr.start_date,
        end_date=hr.end_date,
        required_skills=hr.required_skills or [],
        required_certs=hr.required_certs or [],
        constraints=hr.constraints,
        notes=hr.notes,
        status=hr.status,
        reviewed_by=hr.reviewed_by,
        reviewed_at=hr.reviewed_at,
        rejection_reason=hr.rejection_reason,
        created_at=hr.created_at,
        updated_at=hr.updated_at,
        partner_name=hr.partner.name if hr.partner else None,
        project_name=hr.project.name if hr.project else None,
    )


def _create_audit_log(
    db: Session,
    user_id: str,
    action: str,
    entity_id: str,
    details: dict,
) -> None:
    """Write an audit log entry for headcount request actions."""
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity_type="headcount_request",
        entity_id=entity_id,
        details=details,
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Create headcount request
# ---------------------------------------------------------------------------

@router.post("", response_model=HeadcountRequestResponse, status_code=status.HTTP_201_CREATED)
def create_headcount_request(
    body: HeadcountRequestCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a new headcount request.

    Partners can create requests for their own projects.
    Ops can create requests on behalf of any partner.
    Technicians cannot create headcount requests.
    """
    if current_user.role == "technician":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Technicians cannot create headcount requests",
        )

    # Validate partner exists
    partner = db.query(Partner).filter(Partner.id == body.partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail=f"Partner {body.partner_id} not found")

    # Partners can only create requests for their own partner_id
    if current_user.role == "partner" and current_user.account_id:
        if str(body.partner_id) != current_user.account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Partners can only create requests for their own organization",
            )

    # Validate project exists if provided
    if body.project_id:
        project = db.query(Project).filter(Project.id == body.project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")
        # Ensure project belongs to the specified partner
        if project.partner_id != body.partner_id:
            raise HTTPException(
                status_code=400,
                detail="Project does not belong to the specified partner",
            )

    hr = PendingHeadcountRequest(
        partner_id=body.partner_id,
        project_id=body.project_id,
        role_name=body.role_name,
        quantity=body.quantity,
        priority=body.priority,
        start_date=body.start_date,
        end_date=body.end_date,
        required_skills=body.required_skills,
        required_certs=body.required_certs,
        constraints=body.constraints,
        notes=body.notes,
    )
    db.add(hr)

    _create_audit_log(
        db,
        user_id=current_user.user_id,
        action="headcount_request.created",
        entity_id=str(hr.id),
        details={
            "partner_id": str(body.partner_id),
            "project_id": str(body.project_id) if body.project_id else None,
            "role_name": body.role_name,
            "quantity": body.quantity,
        },
    )

    db.commit()
    db.refresh(hr)

    return _serialize(hr)


# ---------------------------------------------------------------------------
# List headcount requests (ops only)
# ---------------------------------------------------------------------------

@router.get("", response_model=HeadcountRequestListResponse)
def list_headcount_requests(
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by status: Pending, Approved, Rejected, Cancelled",
    ),
    partner_id: Optional[str] = Query(None, description="Filter by partner UUID"),
    project_id: Optional[str] = Query(None, description="Filter by project UUID"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """List headcount requests with optional filters. Ops only.

    Partners should use /api/partner-portal/headcount-requests for their scoped view.
    """
    query = db.query(PendingHeadcountRequest)

    if status_filter:
        query = query.filter(PendingHeadcountRequest.status == status_filter)
    if partner_id:
        try:
            pid = uuid.UUID(partner_id)
            query = query.filter(PendingHeadcountRequest.partner_id == pid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid partner_id format")
    if project_id:
        try:
            prid = uuid.UUID(project_id)
            query = query.filter(PendingHeadcountRequest.project_id == prid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid project_id format")
    if priority:
        query = query.filter(PendingHeadcountRequest.priority == priority)

    total = query.count()
    items = (
        query.order_by(desc(PendingHeadcountRequest.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )

    return HeadcountRequestListResponse(
        items=[_serialize(hr) for hr in items],
        total=total,
        skip=skip,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Get single headcount request (ops only)
# ---------------------------------------------------------------------------

@router.get("/{request_id}", response_model=HeadcountRequestResponse)
def get_headcount_request(
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get a single headcount request by ID. Ops only."""
    hr = _get_request_or_404(db, request_id)
    return _serialize(hr)


# ---------------------------------------------------------------------------
# Update headcount request (ops only, only while Pending)
# ---------------------------------------------------------------------------

@router.patch("/{request_id}", response_model=HeadcountRequestResponse)
def update_headcount_request(
    request_id: uuid.UUID,
    body: HeadcountRequestUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Update a pending headcount request. Ops only.

    Only requests in 'Pending' status can be edited.
    """
    hr = _get_request_or_404(db, request_id)

    if hr.status != HeadcountRequestStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit request with status '{hr.status}'. Only 'Pending' requests can be modified.",
        )

    update_data = body.model_dump(exclude_unset=True)
    old_values = {}
    for field, value in update_data.items():
        old_values[field] = getattr(hr, field)
        setattr(hr, field, value)

    _create_audit_log(
        db,
        user_id=current_user.user_id,
        action="headcount_request.updated",
        entity_id=str(hr.id),
        details={"updated_fields": list(update_data.keys()), "old_values": {k: str(v) for k, v in old_values.items()}},
    )

    db.commit()
    db.refresh(hr)

    return _serialize(hr)


# ---------------------------------------------------------------------------
# Approve / Reject headcount request (ops only)
# ---------------------------------------------------------------------------

@router.post("/{request_id}/action", response_model=HeadcountRequestResponse)
def act_on_headcount_request(
    request_id: uuid.UUID,
    body: HeadcountRequestAction,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Approve or reject a pending headcount request. Ops only.

    This is the human approval gate for headcount changes. Approved requests
    may trigger downstream staffing agent runs to find suitable technicians.
    """
    hr = _get_request_or_404(db, request_id)

    if hr.status != HeadcountRequestStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot act on request with status '{hr.status}'. Only 'Pending' requests can be approved or rejected.",
        )

    previous_status = hr.status

    if body.action == "approve":
        hr.status = HeadcountRequestStatus.APPROVED.value
    elif body.action == "reject":
        hr.status = HeadcountRequestStatus.REJECTED.value
        if body.reason:
            hr.rejection_reason = body.reason

    hr.reviewed_by = current_user.user_id
    hr.reviewed_at = datetime.utcnow()

    _create_audit_log(
        db,
        user_id=current_user.user_id,
        action=f"headcount_request.{body.action}d",
        entity_id=str(hr.id),
        details={
            "previous_status": previous_status,
            "new_status": hr.status,
            "rejection_reason": body.reason,
            "partner_id": str(hr.partner_id),
            "project_id": str(hr.project_id) if hr.project_id else None,
            "role_name": hr.role_name,
            "quantity": hr.quantity,
        },
    )

    db.commit()
    db.refresh(hr)

    # --- Post-approval: dispatch event to create role slots + notify partner ---
    if body.action == "approve":
        dispatch_event_safe(EventPayload(
            event_type=EventType.HEADCOUNT_APPROVED,
            entity_type="headcount_request",
            entity_id=str(hr.id),
            actor_id=current_user.user_id,
            data={
                "partner_id": str(hr.partner_id),
                "project_id": str(hr.project_id) if hr.project_id else None,
                "role_name": hr.role_name,
                "quantity": hr.quantity,
                "priority": hr.priority,
                "start_date": str(hr.start_date) if hr.start_date else None,
                "end_date": str(hr.end_date) if hr.end_date else None,
                "required_skills": hr.required_skills or [],
                "required_certs": hr.required_certs or [],
            },
        ))

    return _serialize(hr)


# ---------------------------------------------------------------------------
# Cancel headcount request (ops only)
# ---------------------------------------------------------------------------

@router.post("/{request_id}/cancel", response_model=HeadcountRequestResponse)
def cancel_headcount_request(
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Cancel a pending headcount request. Ops only."""
    hr = _get_request_or_404(db, request_id)

    if hr.status != HeadcountRequestStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel request with status '{hr.status}'. Only 'Pending' requests can be cancelled.",
        )

    hr.status = HeadcountRequestStatus.CANCELLED.value
    hr.reviewed_by = current_user.user_id
    hr.reviewed_at = datetime.utcnow()

    _create_audit_log(
        db,
        user_id=current_user.user_id,
        action="headcount_request.cancelled",
        entity_id=str(hr.id),
        details={
            "partner_id": str(hr.partner_id),
            "role_name": hr.role_name,
        },
    )

    db.commit()
    db.refresh(hr)

    return _serialize(hr)
