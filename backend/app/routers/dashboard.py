"""Ops Dashboard API — KPI cards, suggested actions, and activity feed."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models import SuggestedAction
from app.schemas.dashboard import DashboardResponse, KPICard, SuggestedActionItem

from typing import List

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Service helpers (inlined to avoid broken backend.app imports in the
# existing dashboard_service.py — those will be fixed separately)
# ---------------------------------------------------------------------------

async def _get_technician_counts(db: AsyncSession) -> dict:
    from app.models import Technician, DeployabilityStatus
    from sqlalchemy import func
    result = await db.execute(
        select(Technician.deployability_status, func.count(Technician.id))
        .group_by(Technician.deployability_status)
    )
    counts = dict(result.all())
    total = sum(counts.values())
    return {
        "total": total,
        "ready_now": counts.get(DeployabilityStatus.READY_NOW.value, 0),
        "in_training": counts.get(DeployabilityStatus.IN_TRAINING.value, 0),
        "currently_assigned": counts.get(DeployabilityStatus.CURRENTLY_ASSIGNED.value, 0),
        "rolling_off_soon": counts.get(DeployabilityStatus.ROLLING_OFF_SOON.value, 0),
        "missing_cert": counts.get(DeployabilityStatus.MISSING_CERT.value, 0),
        "missing_docs": counts.get(DeployabilityStatus.MISSING_DOCS.value, 0),
        "inactive": counts.get(DeployabilityStatus.INACTIVE.value, 0),
    }


async def _get_project_counts(db: AsyncSession) -> dict:
    from app.models import Project, ProjectStatus
    from sqlalchemy import func
    result = await db.execute(
        select(Project.status, func.count(Project.id))
        .group_by(Project.status)
    )
    counts = dict(result.all())
    return {
        "total": sum(counts.values()),
        "active": counts.get(ProjectStatus.ACTIVE.value, 0),
        "staffing": counts.get(ProjectStatus.STAFFING.value, 0),
        "wrapping_up": counts.get(ProjectStatus.WRAPPING_UP.value, 0),
        "draft": counts.get(ProjectStatus.DRAFT.value, 0),
    }


async def _get_open_roles_count(db: AsyncSession) -> int:
    from app.models import Project, ProjectRole, ProjectStatus
    from sqlalchemy import func
    result = await db.execute(
        select(func.sum(ProjectRole.quantity - ProjectRole.filled))
        .join(Project, ProjectRole.project_id == Project.id)
        .where(Project.status.in_([ProjectStatus.STAFFING.value, ProjectStatus.ACTIVE.value]))
    )
    val = result.scalar()
    return int(val) if val else 0


async def _get_pending_recommendations_count(db: AsyncSession) -> int:
    from app.models import Recommendation, RecommendationStatus
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(Recommendation.id)).where(
            Recommendation.status == RecommendationStatus.PENDING.value
        )
    )
    return result.scalar() or 0


async def _get_pending_timesheets_count(db: AsyncSession) -> int:
    from app.models import Timesheet, TimesheetStatus
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(Timesheet.id)).where(
            Timesheet.status == TimesheetStatus.SUBMITTED.value
        )
    )
    return result.scalar() or 0


async def _get_flagged_timesheets_count(db: AsyncSession) -> int:
    from app.models import Timesheet, TimesheetStatus
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(Timesheet.id)).where(
            Timesheet.status == TimesheetStatus.FLAGGED.value
        )
    )
    return result.scalar() or 0


async def _get_expiring_certs_count(db: AsyncSession) -> int:
    from app.models import TechnicianCertification, CertStatus
    from sqlalchemy import func, and_
    from datetime import date, timedelta

    cutoff = date.today() + timedelta(days=30)
    result = await db.execute(
        select(func.count(TechnicianCertification.id)).where(
            and_(
                TechnicianCertification.expiry_date <= cutoff,
                TechnicianCertification.expiry_date >= date.today(),
                TechnicianCertification.status != CertStatus.EXPIRED.value,
            )
        )
    )
    return result.scalar() or 0


async def _get_pending_headcount_count(db: AsyncSession) -> int:
    from app.models import PendingHeadcountRequest, HeadcountRequestStatus
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(PendingHeadcountRequest.id)).where(
            PendingHeadcountRequest.status == HeadcountRequestStatus.PENDING.value
        )
    )
    return result.scalar() or 0


async def _build_kpi_cards(db: AsyncSession) -> list[KPICard]:
    tech = await _get_technician_counts(db)
    proj = await _get_project_counts(db)
    open_roles = await _get_open_roles_count(db)
    pending_recs = await _get_pending_recommendations_count(db)
    pending_ts = await _get_pending_timesheets_count(db)
    flagged_ts = await _get_flagged_timesheets_count(db)
    expiring = await _get_expiring_certs_count(db)
    headcount = await _get_pending_headcount_count(db)

    return [
        KPICard(
            id="total-technicians",
            label="Total Technicians",
            value=tech["total"],
            icon="Users",
            color="blue",
            link="/ops/technicians",
            sub_items=[
                {"label": "Ready Now", "value": tech["ready_now"], "color": "emerald"},
                {"label": "In Training", "value": tech["in_training"], "color": "amber"},
                {"label": "Currently Assigned", "value": tech["currently_assigned"], "color": "blue"},
                {"label": "Rolling Off Soon", "value": tech["rolling_off_soon"], "color": "orange"},
            ],
        ),
        KPICard(
            id="ready-to-deploy",
            label="Ready to Deploy",
            value=tech["ready_now"],
            icon="UserCheck",
            color="emerald",
            link="/ops/technicians?status=Ready+Now",
        ),
        KPICard(
            id="active-projects",
            label="Active Projects",
            value=proj["active"],
            icon="Briefcase",
            color="violet",
            link="/ops/projects?status=Active",
            sub_items=[
                {"label": "Staffing", "value": proj["staffing"], "color": "amber"},
                {"label": "Wrapping Up", "value": proj["wrapping_up"], "color": "orange"},
            ],
        ),
        KPICard(
            id="open-roles",
            label="Open Roles",
            value=open_roles,
            icon="ClipboardList",
            color="amber",
            link="/ops/projects?tab=staffing",
        ),
        KPICard(
            id="pending-recommendations",
            label="Pending Actions",
            value=pending_recs,
            icon="Inbox",
            color="rose",
            link="/ops/inbox",
        ),
        KPICard(
            id="pending-timesheets",
            label="Timesheets to Review",
            value=pending_ts,
            icon="Clock",
            color="cyan",
            link="/ops/projects?tab=timesheets",
            sub_items=[
                {"label": "Flagged", "value": flagged_ts, "color": "red"},
            ],
        ),
        KPICard(
            id="expiring-certs",
            label="Certs Expiring (30d)",
            value=expiring,
            icon="AlertTriangle",
            color="orange",
            link="/ops/technicians?filter=expiring_certs",
        ),
        KPICard(
            id="headcount-requests",
            label="Headcount Requests",
            value=headcount,
            icon="UserPlus",
            color="indigo",
            link="/ops/inbox?tab=headcount",
        ),
    ]


async def _get_suggested_actions(db: AsyncSession) -> list[SuggestedActionItem]:
    result = await db.execute(
        select(SuggestedAction)
        .where(
            SuggestedAction.target_role == "ops",
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
            agent_name=a.agent_name,
            entity_type=a.entity_type,
            entity_id=a.entity_id,
            created_at=a.created_at.isoformat() if a.created_at else None,
        )
        for a in actions
    ]


async def _get_recent_activity(db: AsyncSession) -> list[dict]:
    from app.models import AuditLog
    result = await db.execute(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
    )
    entries = result.scalars().all()
    return [
        {
            "id": str(e.id),
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "details": e.details,
            "agent_name": e.agent_name,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Get ops dashboard with KPI cards, suggested actions, and activity feed."""
    kpi_cards = await _build_kpi_cards(db)
    suggested_actions = await _get_suggested_actions(db)
    recent_activity = await _get_recent_activity(db)

    return DashboardResponse(
        kpi_cards=kpi_cards,
        suggested_actions=suggested_actions,
        recent_activity=recent_activity,
    )


@router.get("/kpis", response_model=List[KPICard])
async def get_dashboard_kpis(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Get just the KPI cards for partial refresh."""
    return await _build_kpi_cards(db)


@router.get("/suggested-actions", response_model=List[SuggestedActionItem])
async def get_dashboard_suggested_actions(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Get suggested actions for the ops dashboard widget."""
    return await _get_suggested_actions(db)


@router.post("/suggested-actions/{action_id}/dismiss")
async def dismiss_suggested_action(
    action_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Dismiss a suggested action so it no longer appears on the dashboard."""
    result = await db.execute(
        select(SuggestedAction).where(SuggestedAction.id == action_id)
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Suggested action not found")

    action.status = "dismissed"
    await db.commit()
    return {"status": "dismissed", "action_id": action_id}


@router.post("/suggested-actions/{action_id}/act")
async def act_on_suggested_action(
    action_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Mark a suggested action as acted upon."""
    result = await db.execute(
        select(SuggestedAction).where(SuggestedAction.id == action_id)
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Suggested action not found")

    action.status = "acted"
    await db.commit()
    return {"status": "acted", "action_id": action_id}
