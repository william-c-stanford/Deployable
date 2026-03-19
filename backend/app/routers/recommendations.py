"""Full CRUD + action endpoints for recommendations with WebSocket broadcast.

Includes:
- Standard recommendation CRUD
- Pre-filtering engine endpoints
- Preference rule CRUD
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.recommendation import (
    Recommendation,
    PreferenceRule,
    RecommendationStatus,
    RecommendationType,
)
from app.schemas.recommendation import (
    RecommendationCreate,
    RecommendationUpdate,
    RecommendationResponse,
    RecommendationListResponse,
    RecommendationActionRequest,
    RecommendationActionResponse,
)
from app.schemas.prefilter import (
    PrefilterRequest,
    PrefilterBatchRequest,
    PrefilterResultResponse,
    PreferenceRuleCreate,
    PreferenceRuleUpdate,
    PreferenceRuleResponse,
    ProposedRuleApproveRequest,
    ProposedRuleRejectRequest,
)
from app.models.recommendation import PreferenceRuleStatus
from app.services.prefilter_engine import run_prefilter, run_prefilter_batch
from app.services.sql_scoring import get_sql_scoring_summary, get_supported_rule_types
from app.websocket import broadcast_recommendation_event
from app.services.ws_broadcast import broadcast_to_topic, publish_badge_count_update
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_recommendation_or_404(db: Session, rec_id: uuid.UUID) -> Recommendation:
    rec = db.query(Recommendation).filter(Recommendation.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return rec


def _serialize_recommendation(rec: Recommendation) -> dict:
    """Convert a Recommendation ORM instance to a dict for WS broadcast."""
    return RecommendationResponse.model_validate(rec).model_dump(mode="json")


def _get_pending_count(db: Session) -> int:
    """Get current count of pending recommendations."""
    return db.query(func.count(Recommendation.id)).filter(
        Recommendation.status == RecommendationStatus.PENDING.value,
    ).scalar() or 0


async def _broadcast_badge_update(db: Session):
    """Broadcast updated badge count for pending recommendations via WebSocket."""
    count = _get_pending_count(db)
    # Use sync publish (goes through Redis pub/sub -> relay)
    publish_badge_count_update(
        badge_type="pending_recommendations",
        count=count,
        role="ops",
    )
    # Also broadcast directly to connected clients (async path)
    await broadcast_to_topic("notifications", {
        "event_type": "badge_count.updated",
        "badge_type": "pending_recommendations",
        "count": count,
        "role": "ops",
    })


# ---------------------------------------------------------------------------
# List / Query recommendations
# ---------------------------------------------------------------------------

@router.get("", response_model=RecommendationListResponse)
def list_recommendations(
    recommendation_type: Optional[str] = Query(
        None, description="Filter by type: staffing, training, cert_renewal, backfill, next_step"
    ),
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status: Pending, Approved, Rejected, Dismissed, Superseded"
    ),
    target_entity_type: Optional[str] = Query(None, description="Filter by target entity type"),
    target_entity_id: Optional[str] = Query(None, description="Filter by target entity id"),
    role_id: Optional[str] = Query(None, description="Filter by role id"),
    technician_id: Optional[str] = Query(None, description="Filter by technician id"),
    project_id: Optional[str] = Query(None, description="Filter by project id"),
    agent_name: Optional[str] = Query(None, description="Filter by agent name"),
    batch_id: Optional[str] = Query(None, description="Filter by batch id"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List recommendations with filtering. Ops sees all; partners see only
    recommendations linked to their projects; technicians see their own."""

    query = db.query(Recommendation)

    # Role-based scoping
    if current_user.role == "partner":
        # Partners only see recommendations for their projects
        # For now, filter by project_id if available on the user
        query = query.filter(Recommendation.project_id.isnot(None))
    elif current_user.role == "technician":
        # Technicians only see recommendations about themselves
        query = query.filter(Recommendation.technician_id == current_user.user_id)

    # Apply filters
    if recommendation_type:
        query = query.filter(Recommendation.recommendation_type == recommendation_type)
    if status_filter:
        query = query.filter(Recommendation.status == status_filter)
    if target_entity_type:
        query = query.filter(Recommendation.target_entity_type == target_entity_type)
    if target_entity_id:
        query = query.filter(Recommendation.target_entity_id == target_entity_id)
    if role_id:
        query = query.filter(Recommendation.role_id == role_id)
    if technician_id:
        query = query.filter(Recommendation.technician_id == technician_id)
    if project_id:
        query = query.filter(Recommendation.project_id == project_id)
    if agent_name:
        query = query.filter(Recommendation.agent_name == agent_name)
    if batch_id:
        query = query.filter(Recommendation.batch_id == batch_id)

    total = query.count()
    recs = (
        query.order_by(desc(Recommendation.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )

    return RecommendationListResponse(
        items=[RecommendationResponse.model_validate(r) for r in recs],
        total=total,
        skip=skip,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Get pending recommendations count (for dashboard badges)
# NOTE: Static path routes MUST come before /{rec_id} to avoid conflicts
# ---------------------------------------------------------------------------

@router.get("/stats/pending-count")
def get_pending_count(
    recommendation_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return the count of pending recommendations, optionally filtered by type."""
    query = db.query(func.count(Recommendation.id)).filter(
        Recommendation.status == RecommendationStatus.PENDING.value
    )
    if recommendation_type:
        query = query.filter(Recommendation.recommendation_type == recommendation_type)
    count = query.scalar() or 0
    return {"pending_count": count}


# ---------------------------------------------------------------------------
# Next-step recommendations (cached, per technician)
# ---------------------------------------------------------------------------

@router.get("/next-steps/{technician_id}")
def get_next_steps(
    technician_id: str,
    use_cache: bool = Query(True, description="Use Redis cache if available"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get next-step recommendations for a technician.

    Returns prioritized list of recommended actions (cert renewals, training
    progress, document submissions, etc.). Results are served from Redis cache
    when available, falling back to live generation.

    Technicians can only see their own next steps; ops can see anyone's.
    """
    from app.services.recommendation_cache import get_cached_next_steps, cache_next_steps
    from app.services.next_step_engine import generate_next_steps_for_technician
    from app.models.technician import Technician

    # Role scoping
    if current_user.role == "technician" and current_user.user_id != technician_id:
        raise HTTPException(status_code=403, detail="Not authorized to view these next steps")

    # Try cache first
    if use_cache:
        cached = get_cached_next_steps(technician_id)
        if cached is not None:
            return {
                "technician_id": technician_id,
                "next_steps": cached,
                "count": len(cached),
                "source": "cache",
            }

    # Fall back to live generation
    technician = db.query(Technician).filter(Technician.id == technician_id).first()
    if not technician:
        raise HTTPException(status_code=404, detail="Technician not found")

    steps = generate_next_steps_for_technician(db, technician)

    # Cache the result
    cache_next_steps(technician_id, steps)

    return {
        "technician_id": technician_id,
        "next_steps": steps,
        "count": len(steps),
        "source": "live",
    }


# ---------------------------------------------------------------------------
# Suggested actions for dashboard (cached, per role)
# ---------------------------------------------------------------------------

@router.get("/suggested-actions")
def get_suggested_actions(
    role: Optional[str] = Query(None, description="Filter by target role: ops, technician, partner"),
    use_cache: bool = Query(True, description="Use Redis cache if available"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get suggested actions for the dashboard.

    Returns a prioritized list of actionable items based on system state.
    Ops users get staffing/cert/training actions; technicians get their own
    next steps; partners see project-specific actions.

    Results are served from Redis cache when available.
    """
    from app.services.recommendation_cache import (
        get_cached_suggested_actions,
        cache_suggested_actions,
    )
    from app.services.next_step_engine import generate_ops_suggested_actions
    from app.models.audit import SuggestedAction as SuggestedActionModel

    target_role = role or current_user.role

    # Try cache first
    if use_cache:
        cached = get_cached_suggested_actions(target_role)
        if cached is not None:
            return {
                "role": target_role,
                "actions": cached,
                "count": len(cached),
                "source": "cache",
            }

    # Fall back to live generation or DB query
    if target_role == "ops":
        actions = generate_ops_suggested_actions(db)
        cache_suggested_actions(target_role, None, actions)
    else:
        # For non-ops roles, read from DB
        query = db.query(SuggestedActionModel).filter(
            SuggestedActionModel.target_role == target_role,
        )
        if current_user.role == "technician":
            query = query.filter(
                (SuggestedActionModel.target_user_id == current_user.user_id)
                | (SuggestedActionModel.target_user_id.is_(None))
            )
        db_actions = query.order_by(SuggestedActionModel.priority).limit(20).all()
        actions = [
            {
                "action_key": a.action_type,
                "title": a.title,
                "description": a.description,
                "link": a.link,
                "priority": a.priority,
                "category": (a.metadata_ or {}).get("category", "general"),
                "urgency": (a.metadata_ or {}).get("urgency", "info"),
            }
            for a in db_actions
        ]

    return {
        "role": target_role,
        "actions": actions,
        "count": len(actions),
        "source": "live",
    }


# ---------------------------------------------------------------------------
# Cache management endpoints
# ---------------------------------------------------------------------------

@router.get("/cache/status")
def get_cache_status(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get recommendation cache status and metadata."""
    from app.services.recommendation_cache import get_last_refresh_timestamp

    last_refresh = get_last_refresh_timestamp()
    return {
        "last_refresh": last_refresh,
        "cache_ttl_hours": 25,
    }


@router.post("/cache/refresh")
def trigger_cache_refresh(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Manually trigger a next-step recommendation refresh.

    Dispatches the nightly_next_step_batch task immediately.
    Returns the Celery task ID for tracking.
    """
    from app.workers.celery_app import celery_app

    result = celery_app.send_task(
        "app.workers.tasks.next_step.nightly_next_step_batch",
        args=[None],
        queue="batch",
    )
    return {
        "status": "dispatched",
        "task_id": result.id,
        "message": "Next-step recommendation refresh has been queued",
    }


# ---------------------------------------------------------------------------
# Get recommendations grouped by role (for staffing board)
# ---------------------------------------------------------------------------

@router.get("/by-role/{role_id}", response_model=RecommendationListResponse)
def get_recommendations_by_role(
    role_id: str,
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get recommendations for a specific project role, ordered by score."""
    query = db.query(Recommendation).filter(Recommendation.role_id == role_id)

    if status_filter:
        query = query.filter(Recommendation.status == status_filter)

    total = query.count()
    recs = (
        query.order_by(
            desc(Recommendation.overall_score),
            desc(Recommendation.created_at),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )

    return RecommendationListResponse(
        items=[RecommendationResponse.model_validate(r) for r in recs],
        total=total,
        skip=skip,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Get single recommendation (MUST come after static path routes)
# ---------------------------------------------------------------------------

@router.get("/{rec_id}", response_model=RecommendationResponse)
def get_recommendation(
    rec_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    rec = _get_recommendation_or_404(db, rec_id)

    # Role scoping: technicians can only see their own
    if current_user.role == "technician" and rec.technician_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this recommendation")

    return rec


# ---------------------------------------------------------------------------
# Create recommendation (agent-facing)
# ---------------------------------------------------------------------------

@router.post("", response_model=RecommendationResponse, status_code=status.HTTP_201_CREATED)
async def create_recommendation(
    data: RecommendationCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a new recommendation. Typically called by an agent via API.
    Broadcasts the new recommendation to connected WebSocket clients."""

    rec_data = data.model_dump(exclude_none=True)
    # Handle metadata field name mismatch
    if "metadata" in rec_data:
        rec_data["metadata_"] = rec_data.pop("metadata")

    rec = Recommendation(**rec_data)
    db.add(rec)
    db.commit()
    db.refresh(rec)

    # Broadcast to WebSocket subscribers
    serialized = _serialize_recommendation(rec)
    await broadcast_recommendation_event("recommendation.created", serialized)

    # Broadcast updated badge count
    await _broadcast_badge_update(db)

    return rec


# ---------------------------------------------------------------------------
# Bulk create recommendations (agent batch)
# ---------------------------------------------------------------------------

@router.post("/batch", response_model=List[RecommendationResponse], status_code=status.HTTP_201_CREATED)
async def create_recommendations_batch(
    data: List[RecommendationCreate],
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create multiple recommendations at once (for batch agent runs).
    Broadcasts each new recommendation to connected WebSocket clients."""

    created = []
    batch_id = str(uuid.uuid4())

    for item in data:
        rec_data = item.model_dump(exclude_none=True)
        if "metadata" in rec_data:
            rec_data["metadata_"] = rec_data.pop("metadata")
        if not rec_data.get("batch_id"):
            rec_data["batch_id"] = batch_id
        rec = Recommendation(**rec_data)
        db.add(rec)
        created.append(rec)

    db.commit()

    # Refresh and broadcast each
    results = []
    for rec in created:
        db.refresh(rec)
        serialized = _serialize_recommendation(rec)
        await broadcast_recommendation_event("recommendation.created", serialized)
        results.append(rec)

    # Broadcast updated badge count once for the batch
    await _broadcast_badge_update(db)

    return results


# ---------------------------------------------------------------------------
# Update recommendation
# ---------------------------------------------------------------------------

@router.patch("/{rec_id}", response_model=RecommendationResponse)
async def update_recommendation(
    rec_id: uuid.UUID,
    data: RecommendationUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Update a recommendation. Ops only."""
    rec = _get_recommendation_or_404(db, rec_id)

    update_data = data.model_dump(exclude_unset=True)
    if "metadata" in update_data:
        update_data["metadata_"] = update_data.pop("metadata")

    old_status = rec.status
    for field, value in update_data.items():
        setattr(rec, field, value)

    db.commit()
    db.refresh(rec)

    # Determine event type
    event_type = "recommendation.updated"
    if "status" in update_data and update_data["status"] != old_status:
        event_type = "recommendation.status_changed"

    serialized = _serialize_recommendation(rec)
    await broadcast_recommendation_event(event_type, serialized)

    return rec


# ---------------------------------------------------------------------------
# Act on recommendation (approve / reject / dismiss)
# ---------------------------------------------------------------------------

@router.post("/{rec_id}/action", response_model=RecommendationActionResponse)
async def act_on_recommendation(
    rec_id: uuid.UUID,
    body: RecommendationActionRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Approve, reject, or dismiss a recommendation. Ops only.
    This is the human approval gate — the key control point."""

    rec = _get_recommendation_or_404(db, rec_id)

    if rec.status != RecommendationStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot act on recommendation with status '{rec.status}'. Only 'Pending' recommendations can be acted upon.",
        )

    previous_status = rec.status

    action_map = {
        "approve": RecommendationStatus.APPROVED.value,
        "reject": RecommendationStatus.REJECTED.value,
        "dismiss": RecommendationStatus.DISMISSED.value,
    }

    new_status = action_map[body.action]
    rec.status = new_status

    if body.reason:
        rec.rejection_reason = body.reason

    db.commit()
    db.refresh(rec)

    # Broadcast status change
    serialized = _serialize_recommendation(rec)
    await broadcast_recommendation_event("recommendation.status_changed", serialized)

    # Broadcast updated badge count (status changed → pending count may have changed)
    await _broadcast_badge_update(db)

    # Dispatch event for recommendation action (especially rejections for preference rule learning)
    event_type_map = {
        "approve": EventType.RECOMMENDATION_APPROVED,
        "reject": EventType.RECOMMENDATION_REJECTED,
        "dismiss": EventType.RECOMMENDATION_DISMISSED,
    }
    event_type = event_type_map.get(body.action)
    if event_type:
        dispatch_event_safe(EventPayload(
            event_type=event_type,
            entity_type="recommendation",
            entity_id=str(rec.id),
            actor_id=str(current_user.id),
            data={
                "recommendation_type": rec.recommendation_type,
                "previous_status": previous_status,
                "new_status": new_status,
                "rejection_reason": body.reason,
                "technician_id": str(rec.technician_id) if rec.technician_id else None,
                "role_id": str(rec.role_id) if rec.role_id else None,
            },
        ))

    return RecommendationActionResponse(
        id=rec.id,
        previous_status=previous_status,
        new_status=new_status,
        message=f"Recommendation {body.action}d successfully",
    )


# ---------------------------------------------------------------------------
# Supersede old recommendations (used by nightly batch refresh)
# ---------------------------------------------------------------------------

@router.post("/supersede-batch")
async def supersede_batch(
    role_id: Optional[str] = Query(None, description="Supersede all pending recs for this role"),
    batch_id: Optional[str] = Query(None, description="Supersede all recs in this batch"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Mark pending recommendations as superseded (for nightly refresh cycle)."""
    if not role_id and not batch_id:
        raise HTTPException(
            status_code=400,
            detail="Must provide either role_id or batch_id",
        )

    query = db.query(Recommendation).filter(
        Recommendation.status == RecommendationStatus.PENDING.value
    )
    if role_id:
        query = query.filter(Recommendation.role_id == role_id)
    if batch_id:
        query = query.filter(Recommendation.batch_id == batch_id)

    recs = query.all()
    count = 0
    for rec in recs:
        rec.status = RecommendationStatus.SUPERSEDED.value
        count += 1

    db.commit()

    # Broadcast superseded events
    for rec in recs:
        db.refresh(rec)
        serialized = _serialize_recommendation(rec)
        await broadcast_recommendation_event("recommendation.status_changed", serialized)

    # Broadcast updated badge count
    if count > 0:
        await _broadcast_badge_update(db)

    return {"superseded_count": count}


# ---------------------------------------------------------------------------
# Delete recommendation (admin cleanup)
# ---------------------------------------------------------------------------

@router.delete("/{rec_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recommendation(
    rec_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Delete a recommendation. Ops only."""
    rec = _get_recommendation_or_404(db, rec_id)
    db.delete(rec)
    db.commit()
    return None


# ===========================================================================
# PRE-FILTERING ENGINE ENDPOINTS
# ===========================================================================

@router.post("/prefilter", response_model=PrefilterResultResponse)
def prefilter_for_role(
    body: PrefilterRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Run the deterministic pre-filtering engine for a specific project role.

    Produces a ranked top-N shortlist of technicians with 5-dimension scorecards,
    NL explanations, and preference rule adjustments.
    """
    try:
        result = run_prefilter(
            db=db,
            role_id=body.role_id,
            top_n=body.top_n,
            as_of_date=body.as_of_date,
            custom_weights=body.custom_weights,
            exclude_technician_ids=body.exclude_technician_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return result.to_dict()


@router.post("/prefilter/batch", response_model=list[PrefilterResultResponse])
def prefilter_for_project(
    body: PrefilterBatchRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Run pre-filtering for ALL open roles in a project.

    Returns one PrefilterResult per role with open slots.
    """
    try:
        results = run_prefilter_batch(
            db=db,
            project_id=body.project_id,
            top_n=body.top_n,
            as_of_date=body.as_of_date,
            custom_weights=body.custom_weights,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return [r.to_dict() for r in results]


# ===========================================================================
# PREFERENCE RULE CRUD ENDPOINTS
# ===========================================================================

def _serialize_rule(r: PreferenceRule) -> PreferenceRuleResponse:
    """Serialize a PreferenceRule ORM instance to response schema."""
    return PreferenceRuleResponse(
        id=str(r.id),
        rule_type=r.rule_type,
        template_type=getattr(r, "template_type", None) or "custom",
        description=getattr(r, "description", None),
        threshold=r.threshold,
        scope=r.scope or "global",
        scope_target_id=getattr(r, "scope_target_id", None),
        effect=r.effect or "demote",
        score_modifier=getattr(r, "score_modifier", 0.0),
        priority=getattr(r, "priority", 0),
        parameters=r.parameters or {},
        active=r.active if r.active is not None else True,
        status=getattr(r, "status", "active") or "active",
        created_by_type=getattr(r, "created_by_type", "ops") or "ops",
        created_by_id=getattr(r, "created_by_id", None),
        proposed_reason=getattr(r, "proposed_reason", None),
        rejection_id=str(r.rejection_id) if getattr(r, "rejection_id", None) else None,
        approved_by_id=getattr(r, "approved_by_id", None),
        approved_at=r.approved_at.isoformat() if getattr(r, "approved_at", None) else None,
        created_at=r.created_at.isoformat() if r.created_at else "",
        updated_at=r.updated_at.isoformat() if getattr(r, "updated_at", None) else None,
    )


@router.get("/preference-rules", response_model=list[PreferenceRuleResponse])
def list_preference_rules(
    active_only: bool = Query(default=False, description="Only return active rules"),
    status_filter: Optional[str] = Query(
        default=None, alias="status",
        description="Filter by status: proposed, active, disabled, archived",
    ),
    scope: Optional[str] = Query(default=None, description="Filter by scope"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """List all preference rules, including agent-proposed ones."""
    query = db.query(PreferenceRule)
    if active_only:
        query = query.filter(PreferenceRule.active == True)  # noqa: E712
    if status_filter:
        query = query.filter(PreferenceRule.status == status_filter)
    if scope:
        query = query.filter(PreferenceRule.scope == scope)
    rules = query.order_by(PreferenceRule.created_at.desc()).all()
    return [_serialize_rule(r) for r in rules]


@router.get("/preference-rules/proposed", response_model=list[PreferenceRuleResponse])
def list_proposed_rules(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """List all proposed preference rules awaiting ops review.

    These are rules auto-generated by the rejection-learning agent when
    a recommendation is rejected. Ops can review, edit parameters, and
    approve or reject them.
    """
    rules = (
        db.query(PreferenceRule)
        .filter(PreferenceRule.status == PreferenceRuleStatus.PROPOSED.value)
        .order_by(PreferenceRule.created_at.desc())
        .all()
    )
    return [_serialize_rule(r) for r in rules]


@router.post("/preference-rules", response_model=PreferenceRuleResponse, status_code=201)
def create_preference_rule(
    body: PreferenceRuleCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Create a new preference rule. Rules modify scoring in the pre-filter engine.

    Ops-created rules start in ACTIVE status by default.
    """
    rule = PreferenceRule(
        rule_type=body.rule_type,
        template_type=body.template_type,
        description=body.description,
        threshold=body.threshold,
        scope=body.scope,
        scope_target_id=body.scope_target_id,
        effect=body.effect,
        score_modifier=body.score_modifier,
        priority=body.priority,
        parameters=body.parameters,
        active=body.active,
        status=PreferenceRuleStatus.ACTIVE.value if body.active else PreferenceRuleStatus.DISABLED.value,
        created_by_type="ops",
        created_by_id=str(user.id),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    # Dispatch: preference rule created -> triggers score refresh
    dispatch_event_safe(EventPayload(
        event_type=EventType.PREFERENCE_RULE_CREATED,
        entity_type="preference_rule",
        entity_id=str(rule.id),
        actor_id=str(user.id),
        data={
            "rule_type": rule.rule_type,
            "template_type": rule.template_type,
            "scope": rule.scope,
            "effect": rule.effect,
        },
    ))

    return _serialize_rule(rule)


@router.put("/preference-rules/{rule_id}", response_model=PreferenceRuleResponse)
def update_preference_rule(
    rule_id: str,
    body: PreferenceRuleUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Update an existing preference rule. Changes take effect on next pre-filter run.

    Can update any field including parameters, template_type, effect, etc.
    For proposed rules, use this to edit parameters before approving.
    """
    rule = db.query(PreferenceRule).filter(PreferenceRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Preference rule not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(rule, key, value)

    # Keep active flag in sync with status
    if "active" in update_data:
        if update_data["active"] and rule.status == PreferenceRuleStatus.DISABLED.value:
            rule.status = PreferenceRuleStatus.ACTIVE.value
        elif not update_data["active"] and rule.status == PreferenceRuleStatus.ACTIVE.value:
            rule.status = PreferenceRuleStatus.DISABLED.value

    db.commit()
    db.refresh(rule)

    # Dispatch: preference rule updated -> triggers score refresh
    dispatch_event_safe(EventPayload(
        event_type=EventType.PREFERENCE_RULE_UPDATED,
        entity_type="preference_rule",
        entity_id=str(rule.id),
        actor_id=str(user.id),
        data={
            "rule_type": rule.rule_type,
            "scope": rule.scope,
            "effect": rule.effect,
            "updated_fields": list(update_data.keys()),
        },
    ))

    return _serialize_rule(rule)


@router.post("/preference-rules/{rule_id}/approve", response_model=PreferenceRuleResponse)
def approve_proposed_rule(
    rule_id: str,
    body: ProposedRuleApproveRequest = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Approve a proposed preference rule, making it active.

    Transitions a rule from PROPOSED -> ACTIVE status. Ops can optionally
    override parameters, threshold, effect, score_modifier, or priority
    as part of the approval.
    """
    rule = db.query(PreferenceRule).filter(PreferenceRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Preference rule not found")

    if rule.status != PreferenceRuleStatus.PROPOSED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Rule is in '{rule.status}' status, only 'proposed' rules can be approved",
        )

    # Apply optional parameter overrides from the approval body
    if body:
        if body.parameters is not None:
            rule.parameters = body.parameters
        if body.threshold is not None:
            rule.threshold = body.threshold
        if body.effect is not None:
            rule.effect = body.effect
        if body.score_modifier is not None:
            rule.score_modifier = body.score_modifier
        if body.priority is not None:
            rule.priority = body.priority

    # Transition to active
    rule.status = PreferenceRuleStatus.ACTIVE.value
    rule.active = True
    rule.approved_by_id = str(user.id)
    rule.approved_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(rule)

    # Dispatch: preference rule approved -> triggers score refresh
    dispatch_event_safe(EventPayload(
        event_type=EventType.PREFERENCE_RULE_CREATED,
        entity_type="preference_rule",
        entity_id=str(rule.id),
        actor_id=str(user.id),
        data={
            "rule_type": rule.rule_type,
            "template_type": rule.template_type,
            "scope": rule.scope,
            "effect": rule.effect,
            "action": "approved_proposed",
        },
    ))

    return _serialize_rule(rule)


@router.post("/preference-rules/{rule_id}/reject", response_model=PreferenceRuleResponse)
def reject_proposed_rule(
    rule_id: str,
    body: ProposedRuleRejectRequest = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Reject a proposed preference rule, archiving it.

    Transitions a rule from PROPOSED -> ARCHIVED status. The rule will
    not be applied to scoring and cannot be re-proposed.
    """
    rule = db.query(PreferenceRule).filter(PreferenceRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Preference rule not found")

    if rule.status != PreferenceRuleStatus.PROPOSED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Rule is in '{rule.status}' status, only 'proposed' rules can be rejected",
        )

    rule.status = PreferenceRuleStatus.ARCHIVED.value
    rule.active = False
    rule.approved_by_id = str(user.id)
    rule.approved_at = datetime.now(timezone.utc)

    # Store rejection reason if provided
    if body and body.reason:
        existing = rule.proposed_reason or ""
        rule.proposed_reason = f"{existing}\n[REJECTED: {body.reason}]".strip()

    db.commit()
    db.refresh(rule)

    # Dispatch: preference rule rejected -> cleanup
    dispatch_event_safe(EventPayload(
        event_type=EventType.PREFERENCE_RULE_DELETED,
        entity_type="preference_rule",
        entity_id=str(rule.id),
        actor_id=str(user.id),
        data={
            "rule_type": rule.rule_type,
            "scope": rule.scope,
            "action": "rejected_proposed",
            "reason": body.reason if body else None,
        },
    ))

    return _serialize_rule(rule)


@router.delete("/preference-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Delete a preference rule permanently."""
    rule = db.query(PreferenceRule).filter(PreferenceRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Preference rule not found")

    # Dispatch: preference rule deleted -> triggers score refresh
    dispatch_event_safe(EventPayload(
        event_type=EventType.PREFERENCE_RULE_DELETED,
        entity_type="preference_rule",
        entity_id=str(rule.id),
        actor_id=str(user.id),
        data={"rule_type": rule.rule_type, "scope": rule.scope},
    ))

    db.delete(rule)
    db.commit()
    return None


# ===========================================================================
# SQL SCORING LAYER ENDPOINTS
# ===========================================================================

@router.get("/scoring/summary")
def scoring_summary(
    scope: str = Query(default="global", description="Rule scope to summarise"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Return a summary of all active SQL scoring modifiers.

    Shows each active preference rule, its effect, modifier value,
    description, and whether it evaluates at the SQL or Python level.
    Useful for debugging and ops transparency.
    """
    summary = get_sql_scoring_summary(db, scope)
    return {
        "scope": scope,
        "active_modifiers": summary,
        "supported_rule_types": get_supported_rule_types(),
        "total_active": len(summary),
    }


@router.post("/scoring/re-evaluate/{role_id}")
def re_evaluate_role_scoring(
    role_id: str,
    top_n: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_role("ops")),
):
    """Re-evaluate scoring for a role using current preference rules.

    Triggers a fresh prefilter run incorporating the latest approved
    preference rules as SQL scoring modifiers. Returns the updated
    ranked shortlist.
    """
    try:
        result = run_prefilter(db=db, role_id=role_id, top_n=top_n)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return result.to_dict()
