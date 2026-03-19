"""Pydantic schemas for the recommendation domain."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.recommendation import RecommendationStatus, RecommendationType


# ---------------------------------------------------------------------------
# Scorecard dimension schema
# ---------------------------------------------------------------------------

class ScorecardDimension(BaseModel):
    """A single dimension of the 5-dimension scorecard."""
    skills_match: Optional[float] = Field(None, ge=0, le=1, description="0-1 score for skills match")
    availability: Optional[float] = Field(None, ge=0, le=1, description="0-1 score for availability")
    location: Optional[float] = Field(None, ge=0, le=1, description="0-1 score for location fit")
    experience: Optional[float] = Field(None, ge=0, le=1, description="0-1 score for experience level")
    certifications: Optional[float] = Field(None, ge=0, le=1, description="0-1 score for certifications")


# ---------------------------------------------------------------------------
# Recommendation schemas
# ---------------------------------------------------------------------------

class RecommendationBase(BaseModel):
    recommendation_type: str
    target_entity_type: Optional[str] = None
    target_entity_id: Optional[str] = None
    role_id: Optional[str] = None
    scorecard: Optional[Dict[str, Any]] = None
    explanation: Optional[str] = None
    agent_name: Optional[str] = None
    technician_id: Optional[str] = None
    project_id: Optional[str] = None
    overall_score: Optional[float] = None
    rank: Optional[str] = None
    batch_id: Optional[str] = None


class RecommendationCreate(RecommendationBase):
    """Schema for creating a new recommendation (typically by an agent)."""
    model_config = ConfigDict(populate_by_name=True)

    status: str = RecommendationStatus.PENDING.value
    metadata: Optional[Dict[str, Any]] = None


class RecommendationUpdate(BaseModel):
    """Schema for updating a recommendation (typically status changes)."""
    model_config = ConfigDict(populate_by_name=True)

    status: Optional[str] = None
    rejection_reason: Optional[str] = None
    explanation: Optional[str] = None
    scorecard: Optional[Dict[str, Any]] = None
    overall_score: Optional[float] = None
    rank: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recommendation_type: str
    target_entity_type: Optional[str] = None
    target_entity_id: Optional[str] = None
    role_id: Optional[str] = None
    scorecard: Optional[Dict[str, Any]] = None
    explanation: Optional[str] = None
    status: str
    agent_name: Optional[str] = None
    technician_id: Optional[str] = None
    project_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    overall_score: Optional[float] = None
    rank: Optional[str] = None
    batch_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(None, validation_alias="metadata_")
    created_at: datetime
    updated_at: datetime


class RecommendationListResponse(BaseModel):
    items: List[RecommendationResponse]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Action schemas (for approve/reject/dismiss)
# ---------------------------------------------------------------------------

class RecommendationActionRequest(BaseModel):
    """Request body for acting on a recommendation."""
    action: str = Field(
        ...,
        description="One of: approve, reject, dismiss",
        pattern="^(approve|reject|dismiss)$",
    )
    reason: Optional[str] = Field(None, description="Optional reason for rejection/dismissal")


class RecommendationActionResponse(BaseModel):
    id: uuid.UUID
    previous_status: str
    new_status: str
    message: str


# ---------------------------------------------------------------------------
# WebSocket event schemas
# ---------------------------------------------------------------------------

class WebSocketRecommendationEvent(BaseModel):
    """Event pushed to WebSocket clients when a recommendation changes."""
    event_type: str = Field(
        ...,
        description="One of: recommendation.created, recommendation.updated, recommendation.status_changed",
    )
    recommendation: RecommendationResponse
    timestamp: datetime
