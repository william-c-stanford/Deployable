"""Portal recommendation endpoints for tech, ops, and partner views.

Provides role-scoped recommendation feeds:
- GET /api/portal/tech/next-steps — Technician's "Your Next Step" panel
- GET /api/portal/ops/suggested-actions — Ops dashboard "Suggested Actions"
- GET /api/portal/ops/pending-recommendations — Ops pending recommendations summary
- GET /api/portal/partner/recommendations — Partner's project recommendations
- POST /api/portal/tech/next-steps/{step_id}/acknowledge — Technician acknowledges a step
- POST /api/portal/ops/suggested-actions/{action_id}/complete — Ops completes an action

WebSocket broadcasts use existing topics:
- "recommendations" topic for ops
- "training" topic for tech next steps
- "partner" topic for partner recs
"""

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, and_, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.recommendation import (
    Recommendation,
    RecommendationStatus,
    RecommendationType,
)
from app.models.audit import SuggestedAction
from app.models.technician import Technician, TechnicianCertification, TechnicianSkill
from app.models.project import Project, ProjectRole
from app.models.assignment import Assignment
from app.models.training import TrainingEnrollment
from app.schemas.portal import (
    NextStepItem,
    NextStepResponse,
    SuggestedActionItem,
    SuggestedActionsResponse,
    PendingRecommendationSummary,
    PendingRecommendationsResponse,
    PartnerRecommendationItem,
    PartnerRecommendationsResponse,
)
from app.services.ws_broadcast import (
    broadcast_to_topic,
    publish_ws_event,
    publish_notification,
)

router = APIRouter(prefix="/api/portal", tags=["portal"])


# ===========================================================================
# Helpers
# ===========================================================================

def _build_next_step_item(rec: Recommendation, title: str, action_type: str = "view") -> NextStepItem:
    """Convert a Recommendation ORM object into a NextStepItem for the tech portal."""
    # Determine action link based on recommendation type
    action_link = None
    if rec.recommendation_type == RecommendationType.TRAINING.value:
        action_link = "/training"
        action_type = "start_training"
    elif rec.recommendation_type == RecommendationType.CERT_RENEWAL.value:
        action_link = "/certifications"
        action_type = "renew_cert"
    elif rec.recommendation_type == RecommendationType.STAFFING.value:
        action_link = f"/assignments/{rec.role_id}" if rec.role_id else "/assignments"
        action_type = "view_assignment"
    elif rec.recommendation_type == RecommendationType.NEXT_STEP.value:
        action_link = "/dashboard"
        action_type = "view"

    # Build priority from overall_score (higher score = higher priority)
    priority = int((rec.overall_score or 0.5) * 100)

    return NextStepItem(
        id=str(rec.id),
        recommendation_type=rec.recommendation_type,
        title=title,
        description=rec.explanation,
        explanation=rec.explanation,
        priority=priority,
        action_type=action_type,
        action_link=action_link,
        scorecard=rec.scorecard,
        overall_score=rec.overall_score,
        status=rec.status,
        metadata=rec.metadata_ if hasattr(rec, "metadata_") else None,
        created_at=rec.created_at,
    )


def _build_suggested_action(
    action: SuggestedAction,
) -> SuggestedActionItem:
    """Convert a SuggestedAction ORM object into a SuggestedActionItem."""
    # Parse category from action_type
    category = "general"
    action_type = action.action_type or ""
    if "staffing" in action_type or "recommendation" in action_type:
        category = "staffing"
    elif "training" in action_type:
        category = "training"
    elif "cert" in action_type or "compliance" in action_type:
        category = "compliance"
    elif "timesheet" in action_type:
        category = "timesheets"
    elif "escalation" in action_type:
        category = "escalations"

    return SuggestedActionItem(
        id=str(action.id),
        action_type=action.action_type,
        title=action.title,
        description=action.description,
        link=action.link,
        priority=action.priority or 0,
        category=category,
        entity_type=None,
        entity_id=None,
        target_role=action.target_role or "ops",
        metadata=action.metadata_ if hasattr(action, "metadata_") else None,
        created_at=action.created_at,
    )


def _build_suggested_action_from_rec(
    rec: Recommendation,
) -> SuggestedActionItem:
    """Convert a pending Recommendation into a SuggestedActionItem for ops."""
    # Determine category and link
    category = "staffing"
    action_type = "review_recommendation"
    link = f"/recommendations/{rec.id}"

    if rec.recommendation_type == RecommendationType.TRAINING.value:
        category = "training"
        action_type = "review_training_recommendation"
    elif rec.recommendation_type == RecommendationType.CERT_RENEWAL.value:
        category = "compliance"
        action_type = "review_cert_recommendation"

    title = f"Review: {rec.recommendation_type.replace('_', ' ').title()}"
    if rec.explanation:
        title = rec.explanation[:120] if len(rec.explanation) > 120 else rec.explanation

    return SuggestedActionItem(
        id=str(rec.id),
        action_type=action_type,
        title=title,
        description=rec.explanation,
        link=link,
        priority=int((rec.overall_score or 0.5) * 100),
        category=category,
        entity_type="recommendation",
        entity_id=str(rec.id),
        target_role="ops",
        metadata=rec.scorecard,
        created_at=rec.created_at,
    )


def _get_technician_name(db: Session, tech_id: str) -> Optional[str]:
    """Fetch technician name by ID."""
    tech = db.query(Technician.name).filter(
        Technician.id == tech_id
    ).first()
    return tech[0] if tech else None


def _get_project_name(db: Session, project_id: str) -> Optional[str]:
    """Fetch project name by ID."""
    project = db.query(Project.name).filter(
        Project.id == project_id
    ).first()
    return project[0] if project else None


def _get_role_title(db: Session, role_id: str) -> Optional[str]:
    """Fetch project role title by ID."""
    role = db.query(ProjectRole.title).filter(
        ProjectRole.id == role_id
    ).first()
    return role[0] if role else None


# ===========================================================================
# TECH PORTAL: "Your Next Step"
# ===========================================================================

@router.get("/tech/next-steps", response_model=NextStepResponse)
def get_tech_next_steps(
    limit: int = Query(10, ge=1, le=50, description="Max items to return"),
    recommendation_type: Optional[str] = Query(
        None, description="Filter by type: training, cert_renewal, next_step, staffing"
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("technician", "ops")),
):
    """Get personalized next-step recommendations for a technician.

    Returns a prioritized list of actionable items like:
    - Training programs to start or continue
    - Certifications to renew or acquire
    - Available assignments matching their skills
    - Career advancement suggestions

    Technicians see only their own recommendations.
    Ops can use ?technician_id= to view any technician's steps.
    """
    # Determine which technician to query
    tech_id = current_user.account_id or current_user.user_id

    # Build base query: recommendations for this technician that are active
    query = db.query(Recommendation).filter(
        Recommendation.technician_id == tech_id,
        Recommendation.status.in_([
            RecommendationStatus.PENDING.value,
            RecommendationStatus.APPROVED.value,
        ]),
    )

    if recommendation_type:
        query = query.filter(Recommendation.recommendation_type == recommendation_type)

    recs = (
        query.order_by(
            desc(Recommendation.overall_score),
            desc(Recommendation.created_at),
        )
        .limit(limit)
        .all()
    )

    # Build next step items with appropriate titles
    next_steps = []
    pending_trainings = 0
    expiring_certs = 0
    available_assignments = 0

    for rec in recs:
        # Generate contextual title
        rec_type = rec.recommendation_type
        if rec_type == RecommendationType.TRAINING.value:
            title = "Continue your training"
            if rec.metadata_ and isinstance(rec.metadata_, dict):
                program = rec.metadata_.get("program_name", "")
                if program:
                    title = f"Continue training: {program}"
            pending_trainings += 1
        elif rec_type == RecommendationType.CERT_RENEWAL.value:
            title = "Renew expiring certification"
            if rec.metadata_ and isinstance(rec.metadata_, dict):
                cert = rec.metadata_.get("cert_name", "")
                if cert:
                    title = f"Renew certification: {cert}"
            expiring_certs += 1
        elif rec_type == RecommendationType.STAFFING.value:
            title = "New assignment opportunity"
            role_title = _get_role_title(db, rec.role_id) if rec.role_id else None
            if role_title:
                title = f"Assignment opportunity: {role_title}"
            available_assignments += 1
        elif rec_type == RecommendationType.NEXT_STEP.value:
            title = "Recommended next step"
            if rec.explanation:
                title = rec.explanation[:100]
        else:
            title = f"Action needed: {rec_type.replace('_', ' ').title()}"

        next_steps.append(_build_next_step_item(rec, title))

    # Also check for cert expirations directly (even without recommendations)
    try:
        from datetime import timedelta
        soon = datetime.now(timezone.utc) + timedelta(days=90)
        expiring_cert_count = db.query(func.count(TechnicianCertification.id)).filter(
            TechnicianCertification.technician_id == tech_id,
            TechnicianCertification.expiry_date <= soon.date(),
            TechnicianCertification.status == "Active",
        ).scalar() or 0
        if expiring_cert_count > expiring_certs:
            expiring_certs = expiring_cert_count
    except Exception:
        pass  # Non-critical enrichment

    # Fetch technician info for context
    tech = db.query(Technician).filter(Technician.id == tech_id).first()

    return NextStepResponse(
        technician_id=tech_id,
        technician_name=tech.name if tech else None,
        career_stage=tech.career_stage if tech else None,
        deployability_status=tech.deployability_status if tech else None,
        next_steps=next_steps,
        total=len(next_steps),
        pending_trainings=pending_trainings,
        expiring_certs=expiring_certs,
        available_assignments=available_assignments,
    )


@router.get("/tech/next-steps/{technician_id}", response_model=NextStepResponse)
def get_tech_next_steps_by_id(
    technician_id: str,
    limit: int = Query(10, ge=1, le=50),
    recommendation_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get next-step recommendations for a specific technician.

    - Technicians can only view their own (technician_id must match).
    - Ops can view any technician.
    - Partners cannot access this endpoint.
    """
    if current_user.role == "partner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Partners cannot view technician next steps",
        )
    if current_user.role == "technician":
        own_id = current_user.account_id or current_user.user_id
        if str(technician_id) != str(own_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Technicians can only view their own next steps",
            )

    # Build query
    query = db.query(Recommendation).filter(
        Recommendation.technician_id == technician_id,
        Recommendation.status.in_([
            RecommendationStatus.PENDING.value,
            RecommendationStatus.APPROVED.value,
        ]),
    )
    if recommendation_type:
        query = query.filter(Recommendation.recommendation_type == recommendation_type)

    recs = (
        query.order_by(
            desc(Recommendation.overall_score),
            desc(Recommendation.created_at),
        )
        .limit(limit)
        .all()
    )

    next_steps = []
    counters = {"training": 0, "certs": 0, "assignments": 0}

    for rec in recs:
        rec_type = rec.recommendation_type
        if rec_type == RecommendationType.TRAINING.value:
            title = "Continue your training"
            counters["training"] += 1
        elif rec_type == RecommendationType.CERT_RENEWAL.value:
            title = "Renew expiring certification"
            counters["certs"] += 1
        elif rec_type == RecommendationType.STAFFING.value:
            title = "New assignment opportunity"
            counters["assignments"] += 1
        elif rec_type == RecommendationType.NEXT_STEP.value:
            title = rec.explanation[:100] if rec.explanation else "Recommended next step"
        else:
            title = f"Action needed: {rec_type.replace('_', ' ').title()}"

        next_steps.append(_build_next_step_item(rec, title))

    tech = db.query(Technician).filter(Technician.id == technician_id).first()

    return NextStepResponse(
        technician_id=technician_id,
        technician_name=tech.name if tech else None,
        career_stage=tech.career_stage if tech else None,
        deployability_status=tech.deployability_status if tech else None,
        next_steps=next_steps,
        total=len(next_steps),
        pending_trainings=counters["training"],
        expiring_certs=counters["certs"],
        available_assignments=counters["assignments"],
    )


@router.post("/tech/next-steps/{step_id}/acknowledge")
async def acknowledge_next_step(
    step_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("technician")),
):
    """Technician acknowledges a next-step recommendation.

    This marks the recommendation as "seen" without approving/rejecting
    (which is an ops action). Technicians can track which steps they've
    reviewed.
    """
    rec = db.query(Recommendation).filter(Recommendation.id == step_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Next step not found")

    own_id = current_user.account_id or current_user.user_id
    if rec.technician_id != own_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Can only acknowledge your own next steps",
        )

    # Store acknowledgement in metadata
    metadata = dict(rec.metadata_) if rec.metadata_ else {}
    metadata["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
    metadata["acknowledged_by"] = current_user.user_id
    rec.metadata_ = metadata

    db.commit()
    db.refresh(rec)

    # Broadcast update to training topic (tech-visible)
    await broadcast_to_topic("training", {
        "event_type": "portal.next_step_acknowledged",
        "technician_id": own_id,
        "step_id": step_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "id": step_id,
        "status": "acknowledged",
        "acknowledged_at": metadata["acknowledged_at"],
    }


# ===========================================================================
# OPS DASHBOARD: "Suggested Actions"
# ===========================================================================

@router.get("/ops/suggested-actions", response_model=SuggestedActionsResponse)
def get_ops_suggested_actions(
    category: Optional[str] = Query(
        None, description="Filter by category: staffing, training, compliance, timesheets, escalations"
    ),
    priority_min: Optional[int] = Query(None, ge=0, description="Minimum priority"),
    limit: int = Query(20, ge=1, le=100),
    include_recommendations: bool = Query(
        True, description="Include pending recommendations as suggested actions"
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get suggested actions for the ops dashboard.

    Aggregates from two sources:
    1. SuggestedAction table (created by agents/batch jobs)
    2. Pending Recommendations (optionally included as review-needed actions)

    Returns items sorted by priority (highest first), with category breakdowns.
    """
    all_actions: List[SuggestedActionItem] = []

    # Source 1: SuggestedAction table entries for ops
    sa_query = db.query(SuggestedAction).filter(
        SuggestedAction.target_role == "ops"
    )
    if priority_min is not None:
        sa_query = sa_query.filter(SuggestedAction.priority >= priority_min)

    suggested_actions = (
        sa_query.order_by(desc(SuggestedAction.priority), desc(SuggestedAction.created_at))
        .limit(limit)
        .all()
    )

    for sa in suggested_actions:
        item = _build_suggested_action(sa)
        if category and item.category != category:
            continue
        all_actions.append(item)

    # Source 2: Pending recommendations (if included)
    if include_recommendations:
        rec_query = db.query(Recommendation).filter(
            Recommendation.status == RecommendationStatus.PENDING.value,
        )
        pending_recs = (
            rec_query.order_by(
                desc(Recommendation.overall_score),
                desc(Recommendation.created_at),
            )
            .limit(limit)
            .all()
        )

        for rec in pending_recs:
            item = _build_suggested_action_from_rec(rec)
            if category and item.category != category:
                continue
            all_actions.append(item)

    # Sort combined list by priority descending
    all_actions.sort(key=lambda x: x.priority, reverse=True)

    # Apply final limit
    all_actions = all_actions[:limit]

    # Compute breakdowns
    by_category: dict[str, int] = defaultdict(int)
    urgent_count = 0
    high_count = 0
    normal_count = 0

    for a in all_actions:
        by_category[a.category] += 1
        if a.priority >= 90:
            urgent_count += 1
        elif a.priority >= 70:
            high_count += 1
        else:
            normal_count += 1

    return SuggestedActionsResponse(
        actions=all_actions,
        total=len(all_actions),
        by_category=dict(by_category),
        urgent_count=urgent_count,
        high_count=high_count,
        normal_count=normal_count,
    )


@router.post("/ops/suggested-actions/{action_id}/complete")
async def complete_suggested_action(
    action_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Mark a suggested action as completed/dismissed by ops.

    Removes the action from the dashboard and broadcasts the update.
    """
    action = db.query(SuggestedAction).filter(SuggestedAction.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Suggested action not found")

    # Delete the completed action
    db.delete(action)
    db.commit()

    # Broadcast removal to dashboard topic
    await broadcast_to_topic("dashboard", {
        "event_type": "portal.suggested_action_completed",
        "action_id": action_id,
        "completed_by": current_user.user_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return {"id": action_id, "status": "completed"}


# ===========================================================================
# OPS DASHBOARD: Pending Recommendations Summary
# ===========================================================================

@router.get("/ops/pending-recommendations", response_model=PendingRecommendationsResponse)
def get_ops_pending_recommendations(
    recommendation_type: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get aggregated pending recommendations for ops review.

    Includes enriched data: technician names, project names, role titles.
    Grouped by project for the staffing board view.
    """
    query = db.query(Recommendation).filter(
        Recommendation.status == RecommendationStatus.PENDING.value,
    )

    if recommendation_type:
        query = query.filter(Recommendation.recommendation_type == recommendation_type)
    if project_id:
        query = query.filter(Recommendation.project_id == project_id)

    recs = (
        query.order_by(
            desc(Recommendation.overall_score),
            desc(Recommendation.created_at),
        )
        .limit(limit)
        .all()
    )

    # Enrich with names
    summaries = []
    by_type: dict[str, int] = defaultdict(int)
    by_project: dict[str, list] = defaultdict(list)

    for rec in recs:
        tech_name = _get_technician_name(db, rec.technician_id) if rec.technician_id else None
        proj_name = _get_project_name(db, rec.project_id) if rec.project_id else None
        role_title = _get_role_title(db, rec.role_id) if rec.role_id else None

        summary = PendingRecommendationSummary(
            id=str(rec.id),
            recommendation_type=rec.recommendation_type,
            technician_id=rec.technician_id,
            technician_name=tech_name,
            project_id=rec.project_id,
            project_name=proj_name,
            role_id=rec.role_id,
            role_title=role_title,
            overall_score=rec.overall_score,
            rank=rec.rank,
            scorecard=rec.scorecard,
            explanation=rec.explanation,
            agent_name=rec.agent_name,
            created_at=rec.created_at,
        )
        summaries.append(summary)
        by_type[rec.recommendation_type] += 1

        proj_key = rec.project_id or "unassigned"
        by_project[proj_key].append(summary)

    return PendingRecommendationsResponse(
        recommendations=summaries,
        total=len(summaries),
        by_type=dict(by_type),
        by_project=dict(by_project),
    )


# ===========================================================================
# PARTNER PORTAL: Project Recommendations
# ===========================================================================

@router.get("/partner/recommendations", response_model=PartnerRecommendationsResponse)
def get_partner_recommendations(
    project_id: Optional[str] = Query(None, description="Filter by project"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("partner")),
):
    """Get recommendations visible to a partner for their projects.

    Partners see a filtered, privacy-respecting view of staffing
    recommendations for their projects. Internal ops data (detailed
    scorecards, agent names, rejection reasons) is redacted.
    """
    partner_id = current_user.account_id or current_user.user_id

    # Find projects belonging to this partner
    partner_projects = db.query(Project.id).filter(
        Project.partner_id == partner_id,
    ).all()
    partner_project_ids = [str(p.id) for p in partner_projects]

    if not partner_project_ids:
        return PartnerRecommendationsResponse(
            partner_id=partner_id,
            project_id=project_id,
            recommendations=[],
            total=0,
        )

    # Build query scoped to partner's projects
    query = db.query(Recommendation).filter(
        Recommendation.project_id.in_(partner_project_ids),
        Recommendation.status.in_([
            RecommendationStatus.PENDING.value,
            RecommendationStatus.APPROVED.value,
        ]),
    )

    if project_id:
        if project_id not in partner_project_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this project's recommendations",
            )
        query = query.filter(Recommendation.project_id == project_id)

    recs = (
        query.order_by(
            desc(Recommendation.overall_score),
            desc(Recommendation.created_at),
        )
        .limit(limit)
        .all()
    )

    # Build partner-safe items (redact internal data)
    items = []
    for rec in recs:
        role_title = _get_role_title(db, rec.role_id) if rec.role_id else None

        # Anonymize technician info for pending recs (only show after approval)
        tech_summary = None
        if rec.status == RecommendationStatus.APPROVED.value and rec.technician_id:
            tech_name = _get_technician_name(db, rec.technician_id)
            tech_summary = tech_name

        # Provide limited scorecard (no detailed breakdowns)
        safe_scorecard = None
        if rec.scorecard and isinstance(rec.scorecard, dict):
            safe_scorecard = {
                "overall_fit": rec.overall_score,
                "skills_match": rec.scorecard.get("skills_match"),
                "certifications": rec.scorecard.get("certifications"),
            }

        items.append(PartnerRecommendationItem(
            id=str(rec.id),
            recommendation_type=rec.recommendation_type,
            role_title=role_title,
            technician_summary=tech_summary,
            overall_score=rec.overall_score,
            scorecard=safe_scorecard,
            status=rec.status,
            explanation=None,  # Redact detailed explanations from partners
            created_at=rec.created_at,
        ))

    return PartnerRecommendationsResponse(
        partner_id=partner_id,
        project_id=project_id,
        recommendations=items,
        total=len(items),
    )


# ===========================================================================
# TECH PORTAL: Aggregated stats endpoint
# ===========================================================================

@router.get("/tech/stats")
def get_tech_portal_stats(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("technician", "ops")),
):
    """Get aggregated stats for the technician portal dashboard.

    Returns counts of pending next steps, active training, upcoming
    cert renewals, and available assignments.
    """
    tech_id = current_user.account_id or current_user.user_id

    # Count pending recommendations by type
    type_counts = (
        db.query(
            Recommendation.recommendation_type,
            func.count(Recommendation.id),
        )
        .filter(
            Recommendation.technician_id == tech_id,
            Recommendation.status.in_([
                RecommendationStatus.PENDING.value,
                RecommendationStatus.APPROVED.value,
            ]),
        )
        .group_by(Recommendation.recommendation_type)
        .all()
    )

    counts = {t: c for t, c in type_counts}

    return {
        "technician_id": tech_id,
        "pending_next_steps": sum(counts.values()),
        "training_recommendations": counts.get(RecommendationType.TRAINING.value, 0),
        "cert_renewal_recommendations": counts.get(RecommendationType.CERT_RENEWAL.value, 0),
        "staffing_recommendations": counts.get(RecommendationType.STAFFING.value, 0),
        "next_step_recommendations": counts.get(RecommendationType.NEXT_STEP.value, 0),
    }


# ===========================================================================
# OPS: Aggregated recommendation stats
# ===========================================================================

@router.get("/ops/stats")
def get_ops_portal_stats(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Get aggregated stats for the ops portal dashboard.

    Returns counts of pending recommendations, suggested actions,
    and breakdowns by type and priority.
    """
    # Pending recommendations by type
    rec_type_counts = (
        db.query(
            Recommendation.recommendation_type,
            func.count(Recommendation.id),
        )
        .filter(Recommendation.status == RecommendationStatus.PENDING.value)
        .group_by(Recommendation.recommendation_type)
        .all()
    )

    rec_counts = {t: c for t, c in rec_type_counts}
    total_pending = sum(rec_counts.values())

    # Suggested actions count
    sa_count = db.query(func.count(SuggestedAction.id)).filter(
        SuggestedAction.target_role == "ops"
    ).scalar() or 0

    return {
        "total_pending_recommendations": total_pending,
        "pending_by_type": rec_counts,
        "suggested_actions_count": sa_count,
        "total_action_items": total_pending + sa_count,
    }
