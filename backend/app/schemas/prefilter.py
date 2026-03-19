"""Pydantic schemas for the pre-filtering engine API."""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class PrefilterRequest(BaseModel):
    """Request to run pre-filtering for a specific project role."""
    role_id: str = Field(..., description="UUID of the ProjectRole to evaluate")
    top_n: int = Field(default=20, ge=1, le=100, description="Max candidates to return")
    as_of_date: Optional[date] = Field(default=None, description="Override date for availability checks")
    custom_weights: Optional[dict[str, float]] = Field(
        default=None,
        description="Override scoring weights: {skills_match, cert_match, experience, availability, travel_fit}",
    )
    exclude_technician_ids: Optional[list[str]] = Field(
        default=None,
        description="Technician IDs to exclude (e.g. dismissed candidates)",
    )


class PrefilterBatchRequest(BaseModel):
    """Request to run pre-filtering for all open roles in a project."""
    project_id: str = Field(..., description="UUID of the Project")
    top_n: int = Field(default=20, ge=1, le=100)
    as_of_date: Optional[date] = Field(default=None)
    custom_weights: Optional[dict[str, float]] = Field(default=None)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class SkillScoreResponse(BaseModel):
    skill_name: str
    required_level: str
    technician_level: Optional[str]
    training_hours: float
    score: float
    met: bool


class CertScoreResponse(BaseModel):
    cert_name: str
    has_cert: bool
    is_active: bool
    expiry_date: Optional[date]
    score: float


class PreferenceAdjustmentResponse(BaseModel):
    rule_type: str
    rule_id: str
    reason: str
    modifier: float


class ScorecardResponse(BaseModel):
    skills_match: float
    cert_match: float
    experience: float
    availability: float
    travel_fit: float
    total_weighted: float
    skill_details: list[SkillScoreResponse]
    cert_details: list[CertScoreResponse]
    preference_adjustments: list[PreferenceAdjustmentResponse]
    disqualified: bool
    disqualification_reasons: list[str]


class CandidateResponse(BaseModel):
    technician_id: str
    technician_name: str
    rank: int
    scorecard: ScorecardResponse
    explanation: str


class PrefilterResultResponse(BaseModel):
    role_id: str
    role_name: str
    project_id: str
    project_name: str
    candidates: list[CandidateResponse]
    total_evaluated: int
    total_passed_hard_filter: int
    total_shortlisted: int
    weights_used: dict[str, float]
    timestamp: str


# ---------------------------------------------------------------------------
# Preference Rule CRUD schemas
# ---------------------------------------------------------------------------

class PreferenceRuleCreate(BaseModel):
    rule_type: str = Field(..., description="Type: experience_threshold, skill_level_minimum, archetype_preference, cert_bonus, rate_cap, project_count_minimum")
    template_type: str = Field(default="custom", description="Template type enum: skill_minimum, cert_required, region_preference, etc.")
    description: Optional[str] = None
    threshold: Optional[str] = None
    scope: str = Field(default="global", description="global, client, or project_type")
    scope_target_id: Optional[str] = None
    effect: str = Field(default="demote", description="exclude, demote, or boost")
    score_modifier: Optional[float] = 0.0
    priority: int = 0
    parameters: dict = Field(default_factory=dict)
    active: bool = True


class PreferenceRuleUpdate(BaseModel):
    rule_type: Optional[str] = None
    template_type: Optional[str] = None
    description: Optional[str] = None
    threshold: Optional[str] = None
    scope: Optional[str] = None
    scope_target_id: Optional[str] = None
    effect: Optional[str] = None
    score_modifier: Optional[float] = None
    priority: Optional[int] = None
    parameters: Optional[dict] = None
    active: Optional[bool] = None


class PreferenceRuleResponse(BaseModel):
    id: str
    rule_type: str
    template_type: str = "custom"
    description: Optional[str] = None
    threshold: Optional[str] = None
    scope: str
    scope_target_id: Optional[str] = None
    effect: str
    score_modifier: Optional[float] = 0.0
    priority: int = 0
    parameters: dict
    active: bool
    status: str = "active"
    created_by_type: str = "ops"
    created_by_id: Optional[str] = None
    proposed_reason: Optional[str] = None
    rejection_id: Optional[str] = None
    approved_by_id: Optional[str] = None
    approved_at: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class ProposedRuleApproveRequest(BaseModel):
    """Request body for approving a proposed preference rule.
    Allows ops to optionally modify parameters before approving."""
    parameters: Optional[dict] = None
    threshold: Optional[str] = None
    effect: Optional[str] = None
    score_modifier: Optional[float] = None
    priority: Optional[int] = None


class ProposedRuleRejectRequest(BaseModel):
    """Request body for rejecting a proposed preference rule."""
    reason: Optional[str] = Field(None, description="Optional reason for rejecting the proposed rule")
