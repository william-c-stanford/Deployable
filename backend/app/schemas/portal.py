"""Pydantic schemas for the portal recommendation views.

Tech Portal: "Your Next Step" — personalized recommendations for individual technicians.
Ops Dashboard: "Suggested Actions" — aggregated action items for ops users.
Partner Portal: Filtered view of project-relevant recommendations.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared scorecard dimension (mirrors recommendation scorecard)
# ---------------------------------------------------------------------------

class ScorecardSummary(BaseModel):
    """Compact 5-dimension scorecard for portal cards."""
    skills_match: Optional[float] = Field(None, ge=0, le=1)
    availability: Optional[float] = Field(None, ge=0, le=1)
    location: Optional[float] = Field(None, ge=0, le=1)
    experience: Optional[float] = Field(None, ge=0, le=1)
    certifications: Optional[float] = Field(None, ge=0, le=1)


# ---------------------------------------------------------------------------
# Tech Portal: "Your Next Step" schemas
# ---------------------------------------------------------------------------

class NextStepItem(BaseModel):
    """A single actionable next-step recommendation for a technician."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    recommendation_type: str  # training, cert_renewal, next_step, staffing
    title: str
    description: Optional[str] = None
    explanation: Optional[str] = None
    priority: int = 0  # Higher = more urgent
    action_type: str = "view"  # view, start_training, renew_cert, accept_assignment
    action_link: Optional[str] = None
    scorecard: Optional[Dict[str, Any]] = None
    overall_score: Optional[float] = None
    status: str = "Pending"
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


class NextStepResponse(BaseModel):
    """Response for the technician's "Your Next Step" panel."""
    technician_id: str
    technician_name: Optional[str] = None
    career_stage: Optional[str] = None
    deployability_status: Optional[str] = None
    next_steps: List[NextStepItem]
    total: int
    # Summary counters for the tech dashboard
    pending_trainings: int = 0
    expiring_certs: int = 0
    available_assignments: int = 0


# ---------------------------------------------------------------------------
# Ops Dashboard: "Suggested Actions" schemas
# ---------------------------------------------------------------------------

class SuggestedActionItem(BaseModel):
    """A single suggested action card for the ops dashboard."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    action_type: str  # review_recommendation, approve_timesheet, resolve_escalation, etc.
    title: str
    description: Optional[str] = None
    link: Optional[str] = None
    priority: int = 0  # Higher = more urgent
    category: str = "general"  # staffing, training, compliance, timesheets, escalations
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    target_role: str = "ops"
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


class SuggestedActionsResponse(BaseModel):
    """Response for the ops dashboard "Suggested Actions" panel."""
    actions: List[SuggestedActionItem]
    total: int
    # Breakdown by category
    by_category: Dict[str, int] = {}
    # Breakdown by priority
    urgent_count: int = 0
    high_count: int = 0
    normal_count: int = 0


# ---------------------------------------------------------------------------
# Ops Dashboard: Pending Recommendations Summary
# ---------------------------------------------------------------------------

class PendingRecommendationSummary(BaseModel):
    """Summary of a single pending recommendation for ops review."""
    id: str
    recommendation_type: str
    technician_id: Optional[str] = None
    technician_name: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    role_id: Optional[str] = None
    role_title: Optional[str] = None
    overall_score: Optional[float] = None
    rank: Optional[str] = None
    scorecard: Optional[Dict[str, Any]] = None
    explanation: Optional[str] = None
    agent_name: Optional[str] = None
    created_at: Optional[datetime] = None


class PendingRecommendationsResponse(BaseModel):
    """Aggregated pending recommendations for the ops dashboard."""
    recommendations: List[PendingRecommendationSummary]
    total: int
    by_type: Dict[str, int] = {}
    # Grouped by project for staffing board view
    by_project: Dict[str, List[PendingRecommendationSummary]] = {}


# ---------------------------------------------------------------------------
# Partner Portal: Project Recommendations
# ---------------------------------------------------------------------------

class PartnerRecommendationItem(BaseModel):
    """A recommendation visible to a partner for their project."""
    id: str
    recommendation_type: str
    role_title: Optional[str] = None
    technician_summary: Optional[str] = None  # Anonymized or partial name
    overall_score: Optional[float] = None
    scorecard: Optional[Dict[str, Any]] = None
    status: str = "Pending"
    explanation: Optional[str] = None
    created_at: Optional[datetime] = None


class PartnerRecommendationsResponse(BaseModel):
    """Filtered recommendations for a partner's projects."""
    partner_id: str
    project_id: Optional[str] = None
    recommendations: List[PartnerRecommendationItem]
    total: int


# ---------------------------------------------------------------------------
# WebSocket push event schemas
# ---------------------------------------------------------------------------

class PortalNextStepEvent(BaseModel):
    """WebSocket event when a technician's next steps change."""
    event_type: str = "portal.next_step_updated"
    technician_id: str
    next_step: Optional[NextStepItem] = None
    removed_step_id: Optional[str] = None
    total_steps: int = 0


class PortalSuggestedActionEvent(BaseModel):
    """WebSocket event when ops suggested actions change."""
    event_type: str = "portal.suggested_action_updated"
    action: Optional[SuggestedActionItem] = None
    removed_action_id: Optional[str] = None
    total_actions: int = 0
