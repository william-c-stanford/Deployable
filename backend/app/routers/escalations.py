"""Escalation management endpoints for the Project Staffing Page.

Ops users can view, resolve, and reassign escalated unconfirmed assignments.
Escalations are created when partner confirmations exceed the 24-hour window.
"""

import uuid
from datetime import datetime, date as date_type
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.assignment import Assignment, AssignmentStatus
from app.models.assignment_confirmation import (
    AssignmentConfirmation,
    ConfirmationStatus,
    EscalationStatus,
)
from app.models.project import Project, ProjectRole
from app.models.technician import Technician, TechnicianSkill, TechnicianCertification
from app.models.user import Partner
from app.schemas.escalation import (
    EscalationResolveRequest,
    EscalationSummary,
    EscalationListResponse,
    EscalationResolveResponse,
    ReassignmentCandidate,
    ReassignmentCandidateList,
)
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

router = APIRouter(prefix="/api/escalations", tags=["escalations"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_escalation_summary(
    conf: AssignmentConfirmation,
    db: Session,
) -> EscalationSummary:
    """Build an EscalationSummary from a confirmation record with enriched names."""
    technician_name = None
    technician_id = None
    project_name = None
    project_id = None
    role_name = None
    role_id = None
    partner_name = None

    if conf.assignment:
        technician_id = str(conf.assignment.technician_id) if conf.assignment.technician_id else None
        tech = db.query(Technician).filter(Technician.id == conf.assignment.technician_id).first()
        if tech:
            technician_name = f"{tech.first_name} {tech.last_name}"
        if conf.assignment.role:
            role_name = conf.assignment.role.role_name
            role_id = str(conf.assignment.role.id) if conf.assignment.role.id else None
            if conf.assignment.role.project:
                project_name = conf.assignment.role.project.name
                project_id = str(conf.assignment.role.project.id) if conf.assignment.role.project.id else None

    partner = db.query(Partner).filter(Partner.id == conf.partner_id).first()
    if partner:
        partner_name = partner.name

    return EscalationSummary(
        id=conf.id,
        confirmation_id=conf.id,
        assignment_id=conf.assignment_id,
        partner_id=conf.partner_id,
        partner_name=partner_name,
        project_id=project_id,
        project_name=project_name,
        role_id=role_id,
        role_name=role_name,
        technician_id=technician_id,
        technician_name=technician_name,
        confirmation_type=conf.confirmation_type.value if hasattr(conf.confirmation_type, 'value') else str(conf.confirmation_type),
        requested_date=conf.requested_date,
        requested_at=conf.requested_at,
        escalated_at=conf.escalated_at,
        hours_waiting=conf.hours_waiting,
        escalation_status=conf.escalation_status.value if hasattr(conf.escalation_status, 'value') else str(conf.escalation_status),
        resolution_note=conf.escalation_resolution_note,
        resolved_at=conf.escalation_resolved_at,
        resolved_by=conf.escalation_resolved_by,
    )


# ---------------------------------------------------------------------------
# List all escalations (ops only)
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=EscalationListResponse,
    summary="List all escalated confirmations",
    description=(
        "Returns all escalated confirmations for the Project Staffing Page. "
        "Filterable by project, status, and partner."
    ),
)
def list_escalations(
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
    escalation_status: Optional[str] = Query(None, alias="status", description="Filter by escalation status"),
    partner_id: Optional[str] = Query(None, description="Filter by partner ID"),
    include_resolved: bool = Query(False, description="Include resolved escalations"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """List escalated confirmations for ops review on the staffing page."""
    query = db.query(AssignmentConfirmation).filter(
        AssignmentConfirmation.escalated.is_(True)
    )

    # Filter by escalation status
    if escalation_status:
        query = query.filter(AssignmentConfirmation.escalation_status == escalation_status)
    elif not include_resolved:
        # By default, only show open escalations
        query = query.filter(
            AssignmentConfirmation.escalation_status.in_([
                EscalationStatus.ESCALATED,
                EscalationStatus.OPS_REVIEWING,
            ])
        )

    # Filter by partner
    if partner_id:
        query = query.filter(AssignmentConfirmation.partner_id == partner_id)

    # Filter by project (requires joining through assignment -> role -> project)
    if project_id:
        query = (
            query
            .join(Assignment, AssignmentConfirmation.assignment_id == Assignment.id)
            .join(ProjectRole, Assignment.role_id == ProjectRole.id)
            .filter(ProjectRole.project_id == project_id)
        )

    confirmations = query.order_by(
        AssignmentConfirmation.escalated_at.desc().nullslast()
    ).all()

    summaries = [_build_escalation_summary(c, db) for c in confirmations]
    open_count = sum(
        1 for c in confirmations
        if c.escalation_status in (EscalationStatus.ESCALATED, EscalationStatus.OPS_REVIEWING)
    )
    resolved_count = sum(
        1 for c in confirmations
        if c.escalation_status in (
            EscalationStatus.RESOLVED_CONFIRMED,
            EscalationStatus.RESOLVED_REASSIGNED,
            EscalationStatus.RESOLVED_CANCELLED,
        )
    )

    return EscalationListResponse(
        escalations=summaries,
        total=len(summaries),
        open_count=open_count,
        resolved_count=resolved_count,
    )


# ---------------------------------------------------------------------------
# Get escalations for a specific project
# ---------------------------------------------------------------------------

@router.get(
    "/project/{project_id}",
    response_model=EscalationListResponse,
    summary="Get escalations for a project",
)
def get_project_escalations(
    project_id: str,
    include_resolved: bool = Query(True, description="Include resolved escalations"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get all escalated confirmations for a specific project."""
    query = (
        db.query(AssignmentConfirmation)
        .filter(AssignmentConfirmation.escalated.is_(True))
        .join(Assignment, AssignmentConfirmation.assignment_id == Assignment.id)
        .join(ProjectRole, Assignment.role_id == ProjectRole.id)
        .filter(ProjectRole.project_id == project_id)
    )

    if not include_resolved:
        query = query.filter(
            AssignmentConfirmation.escalation_status.in_([
                EscalationStatus.ESCALATED,
                EscalationStatus.OPS_REVIEWING,
            ])
        )

    confirmations = query.order_by(
        AssignmentConfirmation.escalated_at.desc().nullslast()
    ).all()

    summaries = [_build_escalation_summary(c, db) for c in confirmations]
    open_count = sum(
        1 for c in confirmations
        if c.escalation_status in (EscalationStatus.ESCALATED, EscalationStatus.OPS_REVIEWING)
    )
    resolved_count = len(summaries) - open_count

    return EscalationListResponse(
        escalations=summaries,
        total=len(summaries),
        open_count=open_count,
        resolved_count=resolved_count,
    )


# ---------------------------------------------------------------------------
# Get single escalation detail
# ---------------------------------------------------------------------------

@router.get(
    "/{escalation_id}",
    response_model=EscalationSummary,
    summary="Get escalation details",
)
def get_escalation(
    escalation_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get details of a single escalated confirmation."""
    conf = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.id == escalation_id,
            AssignmentConfirmation.escalated.is_(True),
        )
        .first()
    )
    if not conf:
        raise HTTPException(status_code=404, detail="Escalation not found")

    return _build_escalation_summary(conf, db)


# ---------------------------------------------------------------------------
# Resolve an escalation (confirm, reassign, or cancel)
# ---------------------------------------------------------------------------

@router.post(
    "/{escalation_id}/resolve",
    response_model=EscalationResolveResponse,
    summary="Resolve an escalation",
    description=(
        "Ops resolves an escalated confirmation by confirming the assignment, "
        "reassigning to a different technician, or cancelling the assignment. "
        "This is the human approval gate for escalated items."
    ),
)
def resolve_escalation(
    escalation_id: str,
    data: EscalationResolveRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Resolve an escalated confirmation. Human approval gate."""
    conf = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.id == escalation_id,
            AssignmentConfirmation.escalated.is_(True),
        )
        .first()
    )
    if not conf:
        raise HTTPException(status_code=404, detail="Escalation not found")

    # Must be in an open escalation state
    if conf.escalation_status not in (EscalationStatus.ESCALATED, EscalationStatus.OPS_REVIEWING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Escalation already resolved: {conf.escalation_status.value if hasattr(conf.escalation_status, 'value') else conf.escalation_status}",
        )

    now = datetime.utcnow()
    assignment = db.query(Assignment).filter(Assignment.id == conf.assignment_id).first()
    assignment_updated = False
    new_assignment_id = None
    message = ""

    if data.resolution == "confirm":
        # Ops overrides and confirms the assignment despite partner not responding
        conf.escalation_status = EscalationStatus.RESOLVED_CONFIRMED
        conf.status = ConfirmationStatus.CONFIRMED
        conf.responded_at = now

        if assignment:
            assignment.partner_confirmed_start = True
            assignment.status = "Active"
            assignment_updated = True

        message = "Escalation resolved: assignment confirmed by ops override."

    elif data.resolution == "reassign":
        # Validate new technician is provided
        if not data.new_technician_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="new_technician_id is required for reassignment",
            )

        new_tech = db.query(Technician).filter(Technician.id == data.new_technician_id).first()
        if not new_tech:
            raise HTTPException(status_code=404, detail="New technician not found")

        conf.escalation_status = EscalationStatus.RESOLVED_REASSIGNED

        # Cancel the old assignment
        if assignment:
            assignment.status = "Cancelled"
            assignment_updated = True

            # Create new assignment for the replacement technician
            new_assignment = Assignment(
                technician_id=data.new_technician_id,
                role_id=assignment.role_id,
                start_date=data.new_start_date or assignment.start_date,
                end_date=assignment.end_date,
                hourly_rate=assignment.hourly_rate,
                per_diem=assignment.per_diem,
                assignment_type=assignment.assignment_type,
                status="Active",
                partner_confirmed_start=True,  # Ops-confirmed
            )
            db.add(new_assignment)
            db.flush()
            new_assignment_id = str(new_assignment.id)

        tech_name = f"{new_tech.first_name} {new_tech.last_name}"
        message = f"Escalation resolved: reassigned to {tech_name}."

    elif data.resolution == "cancel":
        conf.escalation_status = EscalationStatus.RESOLVED_CANCELLED

        if assignment:
            assignment.status = "Cancelled"
            assignment_updated = True

        message = "Escalation resolved: assignment cancelled."

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid resolution: {data.resolution}",
        )

    # Update common escalation fields
    conf.escalation_resolved_at = now
    conf.escalation_resolved_by = current_user.user_id
    conf.escalation_resolution_note = data.resolution_note

    db.commit()
    db.refresh(conf)

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.ESCALATION_RESOLVED,
        entity_type="assignment_confirmation",
        entity_id=str(conf.id),
        actor_id=current_user.user_id,
        data={
            "resolution": data.resolution,
            "resolution_note": data.resolution_note,
            "assignment_id": str(conf.assignment_id),
            "new_assignment_id": new_assignment_id,
            "new_technician_id": str(data.new_technician_id) if data.new_technician_id else None,
        },
    ))

    return EscalationResolveResponse(
        escalation=_build_escalation_summary(conf, db),
        assignment_updated=assignment_updated,
        new_assignment_id=new_assignment_id,
        message=message,
    )


# ---------------------------------------------------------------------------
# Mark escalation as "ops reviewing" (acknowledge)
# ---------------------------------------------------------------------------

@router.post(
    "/{escalation_id}/acknowledge",
    response_model=EscalationSummary,
    summary="Acknowledge an escalation",
    description="Ops acknowledges they are reviewing this escalation.",
)
def acknowledge_escalation(
    escalation_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Mark an escalation as being reviewed by ops."""
    conf = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.id == escalation_id,
            AssignmentConfirmation.escalated.is_(True),
        )
        .first()
    )
    if not conf:
        raise HTTPException(status_code=404, detail="Escalation not found")

    if conf.escalation_status != EscalationStatus.ESCALATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Escalation is not in 'escalated' state",
        )

    conf.escalation_status = EscalationStatus.OPS_REVIEWING
    db.commit()
    db.refresh(conf)

    return _build_escalation_summary(conf, db)


# ---------------------------------------------------------------------------
# Get reassignment candidates for an escalated role
# ---------------------------------------------------------------------------

@router.get(
    "/{escalation_id}/candidates",
    response_model=ReassignmentCandidateList,
    summary="Get reassignment candidates",
    description=(
        "Returns available technicians who could fill the role of an escalated "
        "assignment. Candidates are matched by skills and certifications."
    ),
)
def get_reassignment_candidates(
    escalation_id: str,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Find technicians eligible to replace the escalated assignment."""
    conf = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.id == escalation_id,
            AssignmentConfirmation.escalated.is_(True),
        )
        .first()
    )
    if not conf:
        raise HTTPException(status_code=404, detail="Escalation not found")

    # Get assignment and role to understand requirements
    assignment = db.query(Assignment).filter(Assignment.id == conf.assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    role = db.query(ProjectRole).filter(ProjectRole.id == assignment.role_id).first()
    role_name = role.role_name if role else None

    # Extract required skills and certs from role
    required_skills: List[str] = []
    required_certs: List[str] = []
    if role:
        if role.required_skills:
            required_skills = [
                s.get("skill", s.get("skill_name", ""))
                for s in (role.required_skills if isinstance(role.required_skills, list) else [])
            ]
        if role.required_certs:
            required_certs = role.required_certs if isinstance(role.required_certs, list) else []

    # Find available technicians (not currently assigned to this assignment)
    techs = (
        db.query(Technician)
        .filter(Technician.id != assignment.technician_id)
        .filter(
            or_(
                Technician.deployability_status == "Ready Now",
                Technician.deployability_status == "Awaiting Assignment",
                Technician.deployability_status == "Rolling Off Soon",
            )
        )
        .limit(limit)
        .all()
    )

    candidates = []
    for tech in techs:
        # Get matching skills
        tech_skills = db.query(TechnicianSkill).filter(TechnicianSkill.technician_id == tech.id).all()
        tech_skill_names = [ts.skill_name for ts in tech_skills]
        matching_skills = [s for s in required_skills if s in tech_skill_names]

        # Get matching certs
        tech_certs = db.query(TechnicianCertification).filter(
            TechnicianCertification.technician_id == tech.id
        ).all()
        tech_cert_names = [tc.cert_name for tc in tech_certs]
        matching_certs = [c for c in required_certs if c in tech_cert_names]

        candidates.append(ReassignmentCandidate(
            technician_id=str(tech.id),
            technician_name=f"{tech.first_name} {tech.last_name}",
            home_base_city=tech.home_base_city,
            career_stage=tech.career_stage.value if hasattr(tech.career_stage, 'value') else str(tech.career_stage) if tech.career_stage else None,
            deployability_status=tech.deployability_status.value if hasattr(tech.deployability_status, 'value') else str(tech.deployability_status) if tech.deployability_status else None,
            available_from=str(tech.available_from) if tech.available_from else None,
            matching_skills=matching_skills,
            matching_certs=matching_certs,
        ))

    # Sort by matching quality (more matches first)
    candidates.sort(key=lambda c: len(c.matching_skills) + len(c.matching_certs), reverse=True)

    return ReassignmentCandidateList(
        candidates=candidates[:limit],
        total=len(candidates),
        role_name=role_name,
        required_skills=required_skills,
        required_certs=required_certs,
    )


# ---------------------------------------------------------------------------
# Escalation stats (for dashboard badges)
# ---------------------------------------------------------------------------

@router.get(
    "/stats/summary",
    summary="Get escalation statistics",
    description="Returns summary stats for the ops dashboard escalation badge.",
)
def get_escalation_stats(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get summary statistics about current escalations."""
    # Total escalated (unresolved)
    total_escalated = db.query(AssignmentConfirmation).filter(
        AssignmentConfirmation.escalated.is_(True),
        AssignmentConfirmation.escalation_status.in_([
            EscalationStatus.ESCALATED,
            EscalationStatus.OPS_REVIEWING,
        ]),
    ).count()

    # Pending resolution (escalated but not yet being reviewed)
    pending_resolution = db.query(AssignmentConfirmation).filter(
        AssignmentConfirmation.escalated.is_(True),
        AssignmentConfirmation.escalation_status == EscalationStatus.ESCALATED,
    ).count()

    # Resolved today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    resolved_today = db.query(AssignmentConfirmation).filter(
        AssignmentConfirmation.escalated.is_(True),
        AssignmentConfirmation.escalation_resolved_at >= today_start,
    ).count()

    # Average hours overdue for unresolved
    unresolved = db.query(AssignmentConfirmation).filter(
        AssignmentConfirmation.escalated.is_(True),
        AssignmentConfirmation.escalation_status.in_([
            EscalationStatus.ESCALATED,
            EscalationStatus.OPS_REVIEWING,
        ]),
    ).all()

    avg_hours = 0.0
    if unresolved:
        total_hours = sum(c.hours_waiting for c in unresolved)
        avg_hours = round(total_hours / len(unresolved), 1)

    # Group by partner
    by_partner: dict[str, int] = {}
    by_project: dict[str, int] = {}
    for c in unresolved:
        p = db.query(Partner).filter(Partner.id == c.partner_id).first()
        pname = p.name if p else "Unknown"
        by_partner[pname] = by_partner.get(pname, 0) + 1

        a = db.query(Assignment).filter(Assignment.id == c.assignment_id).first()
        if a:
            r = db.query(ProjectRole).filter(ProjectRole.id == a.role_id).first()
            if r:
                proj = db.query(Project).filter(Project.id == r.project_id).first()
                if proj:
                    by_project[proj.name] = by_project.get(proj.name, 0) + 1

    return {
        "total_escalated": total_escalated,
        "pending_resolution": pending_resolution,
        "resolved_today": resolved_today,
        "avg_hours_overdue": avg_hours,
        "by_partner": by_partner,
        "by_project": by_project,
    }


# ---------------------------------------------------------------------------
# Manual scan trigger
# ---------------------------------------------------------------------------

@router.post(
    "/scan",
    summary="Manually trigger escalation scan",
    description="Force an immediate scan for overdue confirmations. Ops-only.",
)
def trigger_escalation_scan(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Manually trigger the escalation scan task.

    Normally runs every 15 minutes via Celery Beat, but ops can force
    an immediate check.
    """
    from app.workers.tasks.escalation import scan_overdue_confirmations

    result = scan_overdue_confirmations.delay()

    return {
        "status": "scan_triggered",
        "task_id": result.id,
        "message": "Escalation scan has been queued. Results will appear shortly.",
    }


# ---------------------------------------------------------------------------
# Approaching-deadline monitoring
# ---------------------------------------------------------------------------

@router.get(
    "/approaching-deadline",
    summary="List confirmations approaching the 24-hour deadline",
    description="Shows pending confirmations that haven't been escalated yet but are approaching the 24h window.",
)
def list_approaching_deadline(
    hours_threshold: float = Query(
        12.0, description="Show confirmations older than this many hours"
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """List pending confirmations approaching the 24-hour deadline.

    Useful for proactive monitoring before escalation kicks in.
    """
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(hours=hours_threshold)

    confirmations = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.status == ConfirmationStatus.PENDING,
            AssignmentConfirmation.escalated.is_(False),
            AssignmentConfirmation.requested_at <= cutoff,
        )
        .order_by(AssignmentConfirmation.requested_at.asc())
        .all()
    )

    summaries = [_build_escalation_summary(c, db) for c in confirmations]

    return {
        "threshold_hours": hours_threshold,
        "count": len(summaries),
        "confirmations": summaries,
    }
