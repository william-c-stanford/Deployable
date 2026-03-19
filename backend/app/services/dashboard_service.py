from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from datetime import date, timedelta, datetime, timezone
from backend.app.models.models import (
    Technician, Project, Assignment, Recommendation, Timesheet,
    PendingHeadcountRequest, Certification, SuggestedAction,
    ProjectRole, DeployabilityStatus, ProjectStatus, RecommendationStatus,
    TimesheetStatus
)
from backend.app.schemas.dashboard import KPICard, SuggestedActionItem


async def get_technician_counts(db: AsyncSession) -> dict:
    """Get technician counts by deployability status."""
    result = await db.execute(
        select(
            Technician.deployability_status,
            func.count(Technician.id)
        ).group_by(Technician.deployability_status)
    )
    counts = dict(result.all())
    total = sum(counts.values())
    return {
        "total": total,
        "ready_now": counts.get(DeployabilityStatus.READY_NOW.value, 0),
        "in_training": counts.get(DeployabilityStatus.IN_TRAINING.value, 0),
        "currently_assigned": counts.get(DeployabilityStatus.CURRENTLY_ASSIGNED.value, 0),
        "missing_cert": counts.get(DeployabilityStatus.MISSING_CERT.value, 0),
        "missing_docs": counts.get(DeployabilityStatus.MISSING_DOCS.value, 0),
        "rolling_off_soon": counts.get(DeployabilityStatus.ROLLING_OFF_SOON.value, 0),
        "inactive": counts.get(DeployabilityStatus.INACTIVE.value, 0),
    }


async def get_project_counts(db: AsyncSession) -> dict:
    """Get project counts by status."""
    result = await db.execute(
        select(
            Project.status,
            func.count(Project.id)
        ).group_by(Project.status)
    )
    counts = dict(result.all())
    total = sum(counts.values())
    return {
        "total": total,
        "active": counts.get(ProjectStatus.ACTIVE.value, 0),
        "staffing": counts.get(ProjectStatus.STAFFING.value, 0),
        "wrapping_up": counts.get(ProjectStatus.WRAPPING_UP.value, 0),
        "draft": counts.get(ProjectStatus.DRAFT.value, 0),
    }


async def get_open_roles_count(db: AsyncSession) -> int:
    """Get count of unfilled role slots."""
    result = await db.execute(
        select(func.sum(ProjectRole.quantity - ProjectRole.filled)).join(
            Project, ProjectRole.project_id == Project.id
        ).where(Project.status.in_([ProjectStatus.STAFFING.value, ProjectStatus.ACTIVE.value]))
    )
    val = result.scalar()
    return int(val) if val else 0


async def get_pending_recommendations_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(Recommendation.id)).where(
            Recommendation.status == RecommendationStatus.PENDING.value
        )
    )
    return result.scalar() or 0


async def get_pending_timesheets_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(Timesheet.id)).where(
            Timesheet.status == TimesheetStatus.SUBMITTED.value
        )
    )
    return result.scalar() or 0


async def get_flagged_timesheets_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(Timesheet.id)).where(
            Timesheet.status == TimesheetStatus.FLAGGED.value
        )
    )
    return result.scalar() or 0


async def get_expiring_certs_count(db: AsyncSession) -> int:
    cutoff = date.today() + timedelta(days=30)
    result = await db.execute(
        select(func.count(Certification.id)).where(
            and_(
                Certification.expiry_date <= cutoff,
                Certification.expiry_date >= date.today(),
                Certification.status != "Expired"
            )
        )
    )
    return result.scalar() or 0


async def get_pending_headcount_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(PendingHeadcountRequest.id)).where(
            PendingHeadcountRequest.status == "Pending"
        )
    )
    return result.scalar() or 0


async def get_kpi_cards(db: AsyncSession) -> list[KPICard]:
    tech_counts = await get_technician_counts(db)
    project_counts = await get_project_counts(db)
    open_roles = await get_open_roles_count(db)
    pending_recs = await get_pending_recommendations_count(db)
    pending_timesheets = await get_pending_timesheets_count(db)
    flagged_timesheets = await get_flagged_timesheets_count(db)
    expiring_certs = await get_expiring_certs_count(db)
    pending_headcount = await get_pending_headcount_count(db)

    return [
        KPICard(
            id="total-technicians",
            label="Total Technicians",
            value=tech_counts["total"],
            icon="Users",
            color="blue",
            link="/technicians",
            sub_items=[
                {"label": "Ready Now", "value": tech_counts["ready_now"], "color": "emerald"},
                {"label": "In Training", "value": tech_counts["in_training"], "color": "amber"},
                {"label": "Currently Assigned", "value": tech_counts["currently_assigned"], "color": "blue"},
                {"label": "Rolling Off Soon", "value": tech_counts["rolling_off_soon"], "color": "orange"},
            ]
        ),
        KPICard(
            id="ready-to-deploy",
            label="Ready to Deploy",
            value=tech_counts["ready_now"],
            icon="UserCheck",
            color="emerald",
            link="/technicians?status=Ready+Now",
        ),
        KPICard(
            id="active-projects",
            label="Active Projects",
            value=project_counts["active"],
            icon="Briefcase",
            color="violet",
            link="/projects?status=Active",
            sub_items=[
                {"label": "Staffing", "value": project_counts["staffing"], "color": "amber"},
                {"label": "Wrapping Up", "value": project_counts["wrapping_up"], "color": "orange"},
            ]
        ),
        KPICard(
            id="open-roles",
            label="Open Roles",
            value=open_roles,
            icon="ClipboardList",
            color="amber",
            link="/projects?tab=staffing",
        ),
        KPICard(
            id="pending-recommendations",
            label="Pending Actions",
            value=pending_recs,
            icon="Inbox",
            color="rose",
            link="/inbox",
        ),
        KPICard(
            id="pending-timesheets",
            label="Timesheets to Review",
            value=pending_timesheets,
            icon="Clock",
            color="cyan",
            link="/projects?tab=timesheets",
            sub_items=[
                {"label": "Flagged", "value": flagged_timesheets, "color": "red"},
            ]
        ),
        KPICard(
            id="expiring-certs",
            label="Certs Expiring (30d)",
            value=expiring_certs,
            icon="AlertTriangle",
            color="orange",
            link="/technicians?filter=expiring_certs",
        ),
        KPICard(
            id="headcount-requests",
            label="Headcount Requests",
            value=pending_headcount,
            icon="UserPlus",
            color="indigo",
            link="/inbox?tab=headcount",
        ),
    ]


async def get_suggested_actions(db: AsyncSession) -> list[SuggestedActionItem]:
    result = await db.execute(
        select(SuggestedAction)
        .where(
            SuggestedAction.target_role == "ops",
            # Only return active actions (not dismissed or acted upon)
            SuggestedAction.status.in_(["active", None]),
        )
        .order_by(SuggestedAction.priority.desc())
        .limit(10)
    )
    actions = result.scalars().all()
    return [
        SuggestedActionItem(
            id=str(a.id),
            action_type=a.action_type,
            title=a.title,
            description=a.description,
            link=a.link,
            priority=a.priority,
            agent_name=getattr(a, "agent_name", None),
            entity_type=getattr(a, "entity_type", None),
            entity_id=getattr(a, "entity_id", None),
            created_at=a.created_at.isoformat() if a.created_at else None,
        )
        for a in actions
    ]


async def get_recent_activity(db: AsyncSession) -> list[dict]:
    """Get recent audit log entries for dashboard activity feed."""
    from backend.app.models.models import AuditLog
    result = await db.execute(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
    )
    entries = result.scalars().all()
    return [
        {
            "id": e.id,
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "details": e.details,
            "agent_name": e.agent_name,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]
