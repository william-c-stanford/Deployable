from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from backend.app.core.database import get_db
from backend.app.core.auth import get_current_user, require_role
from backend.app.schemas.dashboard import DashboardResponse, SuggestedActionItem
from backend.app.services.dashboard_service import (
    get_kpi_cards, get_suggested_actions, get_recent_activity
)
from backend.app.models.models import SuggestedAction
from typing import List

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("ops")),
):
    """Get ops dashboard with KPI cards, suggested actions, and activity feed."""
    kpi_cards = await get_kpi_cards(db)
    suggested_actions = await get_suggested_actions(db)
    recent_activity = await get_recent_activity(db)

    return DashboardResponse(
        kpi_cards=kpi_cards,
        suggested_actions=suggested_actions,
        recent_activity=recent_activity,
    )


@router.get("/kpis")
async def get_dashboard_kpis(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("ops")),
):
    """Get just the KPI cards for partial refresh."""
    return await get_kpi_cards(db)


@router.get("/suggested-actions", response_model=List[SuggestedActionItem])
async def get_dashboard_suggested_actions(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("ops")),
):
    """Get suggested actions for the ops dashboard widget.

    Returns actions sorted by priority (highest first), limited to 10.
    Only returns active (non-dismissed, non-acted) actions for the ops role.
    """
    return await get_suggested_actions(db)


@router.post("/suggested-actions/{action_id}/dismiss")
async def dismiss_suggested_action(
    action_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("ops")),
):
    """Dismiss a suggested action so it no longer appears on the dashboard.

    This is a soft-dismiss — the action is marked as dismissed but not deleted,
    so it won't be re-surfaced by the nightly batch.
    """
    result = await db.execute(
        select(SuggestedAction).where(SuggestedAction.id == action_id)
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Suggested action not found")

    # Mark as dismissed
    action.status = "dismissed"
    await db.commit()

    return {"status": "dismissed", "action_id": action_id}


@router.post("/suggested-actions/{action_id}/act")
async def act_on_suggested_action(
    action_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("ops")),
):
    """Mark a suggested action as acted upon.

    Called when an ops user clicks through to the action's target.
    Removes the action from the dashboard and logs the interaction.
    """
    result = await db.execute(
        select(SuggestedAction).where(SuggestedAction.id == action_id)
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Suggested action not found")

    # Mark as acted upon
    action.status = "acted"
    await db.commit()

    return {"status": "acted", "action_id": action_id}
