"""Partner portal endpoints — scoped views for partner users.

Provides partner-scoped access to their projects, assignments, and notifications.
All data is filtered by the authenticated partner's ID.
"""

from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.project import Project, ProjectRole
from app.models.assignment import Assignment
from app.models.technician import Technician
from app.models.user import Partner
from app.models.partner_notification import PartnerNotification

router = APIRouter(prefix="/api/partner", tags=["partner-portal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class PartnerProjectSummary(BaseModel):
    id: str
    name: str
    status: str
    location_region: str
    location_city: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    total_roles: int
    filled_roles: int
    active_assignments: int

    class Config:
        from_attributes = True


class PartnerAssignmentDetail(BaseModel):
    id: str
    technician_name: str
    technician_id: str
    project_name: str
    project_id: str
    role_name: str
    start_date: date
    end_date: Optional[date] = None
    status: str
    assignment_type: str
    partner_confirmed_start: bool
    partner_confirmed_end: bool

    class Config:
        from_attributes = True


class PartnerNotificationItem(BaseModel):
    id: str
    notification_type: str
    status: str
    title: str
    message: Optional[str] = None
    target_date: str
    technician_name: Optional[str] = None
    project_name: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True


class PartnerDashboardResponse(BaseModel):
    partner_name: str
    partner_id: str
    projects: List[PartnerProjectSummary]
    assignments: List[PartnerAssignmentDetail]
    notifications: List[PartnerNotificationItem]
    stats: dict


# ---------------------------------------------------------------------------
# Get partner dashboard data (single call for the portal)
# ---------------------------------------------------------------------------

@router.get(
    "/dashboard",
    response_model=PartnerDashboardResponse,
    summary="Partner dashboard data",
    description="Returns all projects, assignments, and notifications for the authenticated partner.",
)
def get_partner_dashboard(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Get the partner's complete dashboard data."""
    partner_id = current_user.user_id

    # Get partner info
    partner = db.query(Partner).filter(Partner.id == partner_id).first()
    partner_name = partner.name if partner else "Partner"

    # Get partner's projects
    projects = (
        db.query(Project)
        .filter(Project.partner_id == partner_id)
        .options(joinedload(Project.roles))
        .all()
    )

    project_summaries = []
    all_role_ids = []

    for proj in projects:
        total_roles = sum(r.quantity for r in proj.roles) if proj.roles else 0
        filled = sum(r.filled for r in proj.roles) if proj.roles else 0
        role_ids = [str(r.id) for r in proj.roles] if proj.roles else []
        all_role_ids.extend(role_ids)

        # Count active assignments for this project
        active_count = (
            db.query(Assignment)
            .filter(
                Assignment.role_id.in_([r.id for r in proj.roles]),
                Assignment.status.in_(["Active", "Pending Confirmation"]),
            )
            .count()
            if proj.roles else 0
        )

        project_summaries.append(PartnerProjectSummary(
            id=str(proj.id),
            name=proj.name,
            status=proj.status.value if hasattr(proj.status, 'value') else str(proj.status),
            location_region=proj.location_region,
            location_city=proj.location_city,
            start_date=proj.start_date,
            end_date=proj.end_date,
            total_roles=total_roles,
            filled_roles=filled,
            active_assignments=active_count,
        ))

    # Get all assignments for partner's projects
    assignments_query = (
        db.query(Assignment)
        .join(ProjectRole, Assignment.role_id == ProjectRole.id)
        .join(Project, ProjectRole.project_id == Project.id)
        .filter(Project.partner_id == partner_id)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .all()
    )

    assignment_details = []
    for a in assignments_query:
        tech_name = "Unknown"
        if a.technician:
            tech_name = f"{a.technician.first_name} {a.technician.last_name}"

        proj_name = ""
        if a.role and a.role.project:
            proj_name = a.role.project.name

        role_name = a.role.role_name if a.role else "Unknown"
        proj_id = str(a.role.project_id) if a.role else ""

        assignment_details.append(PartnerAssignmentDetail(
            id=str(a.id),
            technician_name=tech_name,
            technician_id=str(a.technician_id),
            project_name=proj_name,
            project_id=proj_id,
            role_name=role_name,
            start_date=a.start_date,
            end_date=a.end_date,
            status=a.status if isinstance(a.status, str) else a.status.value,
            assignment_type=a.assignment_type.value if hasattr(a.assignment_type, 'value') else str(a.assignment_type),
            partner_confirmed_start=a.partner_confirmed_start or False,
            partner_confirmed_end=a.partner_confirmed_end or False,
        ))

    # Get partner notifications
    notifications = (
        db.query(PartnerNotification)
        .filter(PartnerNotification.partner_id == partner_id)
        .order_by(PartnerNotification.created_at.desc())
        .limit(20)
        .all()
    )

    notification_items = []
    for n in notifications:
        tech_name = None
        if n.technician:
            tech_name = f"{n.technician.first_name} {n.technician.last_name}"

        notification_items.append(PartnerNotificationItem(
            id=str(n.id),
            notification_type=n.notification_type.value if hasattr(n.notification_type, 'value') else str(n.notification_type),
            status=n.status.value if hasattr(n.status, 'value') else str(n.status),
            title=n.title,
            message=n.message,
            target_date=str(n.target_date),
            technician_name=tech_name,
            created_at=str(n.created_at),
        ))

    # Compute stats
    active_projects = sum(1 for p in project_summaries if p.status in ("Active", "Staffing"))
    total_assignments = len(assignment_details)
    pending_confirmations_count = sum(
        1 for a in assignment_details
        if not a.partner_confirmed_start or not a.partner_confirmed_end
    )
    upcoming_starts = sum(
        1 for a in assignment_details
        if a.start_date and a.start_date > date.today()
    )

    return PartnerDashboardResponse(
        partner_name=partner_name,
        partner_id=partner_id,
        projects=project_summaries,
        assignments=assignment_details,
        notifications=notification_items,
        stats={
            "active_projects": active_projects,
            "total_assignments": total_assignments,
            "pending_confirmations": pending_confirmations_count,
            "upcoming_starts": upcoming_starts,
        },
    )


# ---------------------------------------------------------------------------
# Get partner's projects list
# ---------------------------------------------------------------------------

@router.get(
    "/projects",
    response_model=List[PartnerProjectSummary],
    summary="Partner's projects",
)
def get_partner_projects(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """List all projects belonging to the authenticated partner."""
    projects = (
        db.query(Project)
        .filter(Project.partner_id == current_user.user_id)
        .options(joinedload(Project.roles))
        .all()
    )

    results = []
    for proj in projects:
        total_roles = sum(r.quantity for r in proj.roles) if proj.roles else 0
        filled = sum(r.filled for r in proj.roles) if proj.roles else 0

        active_count = (
            db.query(Assignment)
            .filter(
                Assignment.role_id.in_([r.id for r in proj.roles]),
                Assignment.status.in_(["Active", "Pending Confirmation"]),
            )
            .count()
            if proj.roles else 0
        )

        results.append(PartnerProjectSummary(
            id=str(proj.id),
            name=proj.name,
            status=proj.status.value if hasattr(proj.status, 'value') else str(proj.status),
            location_region=proj.location_region,
            location_city=proj.location_city,
            start_date=proj.start_date,
            end_date=proj.end_date,
            total_roles=total_roles,
            filled_roles=filled,
            active_assignments=active_count,
        ))

    return results


# ---------------------------------------------------------------------------
# Get partner's assignments timeline
# ---------------------------------------------------------------------------

@router.get(
    "/assignments",
    response_model=List[PartnerAssignmentDetail],
    summary="Partner's assignments",
)
def get_partner_assignments(
    status_filter: Optional[str] = Query(None, alias="status"),
    upcoming_only: bool = Query(False, description="Only show upcoming (future start date)"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """List all assignments for the authenticated partner's projects."""
    query = (
        db.query(Assignment)
        .join(ProjectRole, Assignment.role_id == ProjectRole.id)
        .join(Project, ProjectRole.project_id == Project.id)
        .filter(Project.partner_id == current_user.user_id)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
    )

    if status_filter:
        query = query.filter(Assignment.status == status_filter)

    if upcoming_only:
        query = query.filter(Assignment.start_date > date.today())

    assignments = query.order_by(Assignment.start_date.asc()).all()

    results = []
    for a in assignments:
        tech_name = "Unknown"
        if a.technician:
            tech_name = f"{a.technician.first_name} {a.technician.last_name}"

        proj_name = a.role.project.name if a.role and a.role.project else ""
        role_name = a.role.role_name if a.role else "Unknown"
        proj_id = str(a.role.project_id) if a.role else ""

        results.append(PartnerAssignmentDetail(
            id=str(a.id),
            technician_name=tech_name,
            technician_id=str(a.technician_id),
            project_name=proj_name,
            project_id=proj_id,
            role_name=role_name,
            start_date=a.start_date,
            end_date=a.end_date,
            status=a.status if isinstance(a.status, str) else a.status.value,
            assignment_type=a.assignment_type.value if hasattr(a.assignment_type, 'value') else str(a.assignment_type),
            partner_confirmed_start=a.partner_confirmed_start or False,
            partner_confirmed_end=a.partner_confirmed_end or False,
        ))

    return results
