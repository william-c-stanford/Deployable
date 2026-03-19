"""Staffing Sub-Agent API endpoints.

Exposes the combined ranking pipeline for consumption by:
- Frontend staffing views (on-demand ranking)
- Background agent workers (nightly batch)
- Chat sidebar (conversational queries)
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.schemas.staffing import (
    StaffingRequest,
    StaffingResponse,
    StaffingErrorResponse,
    PreferenceRuleCreate,
    PreferenceRuleResponse,
)
from app.services.staffing_agent import (
    rank_candidates_for_role,
    refresh_recommendations_for_role,
    get_recommendations_for_role,
    handle_recommendation_action,
    StaffingAgentError,
)
from app.models.recommendation import PreferenceRule
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

router = APIRouter(prefix="/api/staffing", tags=["staffing"])


# ---------------------------------------------------------------------------
# Ranking endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/rank",
    response_model=StaffingResponse,
    responses={400: {"model": StaffingErrorResponse}, 500: {"model": StaffingErrorResponse}},
    summary="Rank candidates for a role",
    description=(
        "Runs the full staffing pipeline: pre-filter → LLM re-ranker → "
        "persist recommendations. Provide either a role_id or inline requirements."
    ),
)
def rank_candidates(
    request: StaffingRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Rank technician candidates for a project role.

    Requires ops role. Returns ranked candidates with 5-dimension scorecards
    and natural-language explanations.
    """
    try:
        response = rank_candidates_for_role(db, request)
        return response
    except StaffingAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "detail": e.detail, "fallback_available": e.fallback_available},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal staffing agent error", "detail": str(e)},
        )


@router.post(
    "/rank/{role_id}/refresh",
    response_model=StaffingResponse,
    summary="Refresh recommendations for a role",
)
def refresh_role_recommendations(
    role_id: str,
    max_candidates: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Refresh (re-run) the staffing pipeline for a specific role.

    Supersedes old pending recommendations and generates new ones.
    """
    try:
        return refresh_recommendations_for_role(db, role_id, max_candidates)
    except StaffingAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "detail": e.detail},
        )


# ---------------------------------------------------------------------------
# Recommendation CRUD endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/recommendations/{role_id}",
    summary="Get recommendations for a role",
)
def list_recommendations(
    role_id: str,
    rec_status: Optional[str] = Query(None, alias="status", description="Filter by status"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops", "partner")),
):
    """List persisted recommendations for a role, optionally filtered by status."""
    recs = get_recommendations_for_role(db, role_id, status=rec_status)
    return {"role_id": role_id, "recommendations": recs, "total": len(recs)}


@router.post(
    "/recommendations/{recommendation_id}/{action}",
    summary="Act on a recommendation",
    description="Approve, reject, or dismiss a staffing recommendation. Human approval gate.",
)
def act_on_recommendation(
    recommendation_id: str,
    action: str,
    rejection_reason: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Process a human decision on a staffing recommendation.

    Valid actions: approve, reject, dismiss.
    This is the human approval gate — no autonomous state mutations.
    """
    if action not in ("approve", "reject", "dismiss"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action '{action}'. Must be: approve, reject, dismiss",
        )
    try:
        result = handle_recommendation_action(db, recommendation_id, action, rejection_reason)

        # Dispatch event for recommendation action (especially rejections)
        event_type_map = {
            "approve": EventType.RECOMMENDATION_APPROVED,
            "reject": EventType.RECOMMENDATION_REJECTED,
            "dismiss": EventType.RECOMMENDATION_DISMISSED,
        }
        event_type = event_type_map.get(action)
        if event_type:
            dispatch_event_safe(EventPayload(
                event_type=event_type,
                entity_type="recommendation",
                entity_id=recommendation_id,
                actor_id=current_user.user_id,
                data={
                    "action": action,
                    "rejection_reason": rejection_reason,
                },
            ))

        return result
    except StaffingAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message},
        )


# ---------------------------------------------------------------------------
# Preference rule management
# ---------------------------------------------------------------------------

@router.get("/preference-rules", response_model=List[PreferenceRuleResponse])
def list_preference_rules(
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """List all preference rules (ops-defined scoring modifiers)."""
    query = db.query(PreferenceRule)
    if active_only:
        query = query.filter(PreferenceRule.active.is_(True))
    rules = query.order_by(PreferenceRule.created_at.desc()).all()
    return [
        PreferenceRuleResponse(
            id=str(r.id),
            rule_type=r.rule_type,
            threshold=r.threshold,
            scope=r.scope,
            effect=r.effect,
            parameters=r.parameters or {},
            active=r.active,
            created_at=r.created_at,
        )
        for r in rules
    ]


@router.post(
    "/preference-rules",
    response_model=PreferenceRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_preference_rule(
    data: PreferenceRuleCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Create a new preference rule. Triggers re-evaluation on next ranking."""
    rule = PreferenceRule(
        rule_type=data.rule_type,
        threshold=data.threshold,
        scope=data.scope,
        effect=data.effect,
        parameters=data.parameters,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    # Dispatch: preference rule created -> triggers score refresh
    dispatch_event_safe(EventPayload(
        event_type=EventType.PREFERENCE_RULE_CREATED,
        entity_type="preference_rule",
        entity_id=str(rule.id),
        actor_id=current_user.user_id,
        data={"rule_type": rule.rule_type, "scope": rule.scope, "effect": rule.effect},
    ))

    return PreferenceRuleResponse(
        id=str(rule.id),
        rule_type=rule.rule_type,
        threshold=rule.threshold,
        scope=rule.scope,
        effect=rule.effect,
        parameters=rule.parameters or {},
        active=rule.active,
        created_at=rule.created_at,
    )


@router.delete(
    "/preference-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def deactivate_preference_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Deactivate (soft-delete) a preference rule."""
    rule = db.query(PreferenceRule).filter(PreferenceRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Preference rule not found")
    rule.active = False
    db.commit()

    # Dispatch: preference rule updated (deactivated) -> triggers score refresh
    dispatch_event_safe(EventPayload(
        event_type=EventType.PREFERENCE_RULE_UPDATED,
        entity_type="preference_rule",
        entity_id=str(rule.id),
        actor_id=current_user.user_id,
        data={"rule_type": rule.rule_type, "action": "deactivated"},
    ))

    return None
