"""Pydantic schemas for the Staffing Sub-Agent interface contract."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Agent Input Contract
# ---------------------------------------------------------------------------

class SkillRequirement(BaseModel):
    """A single skill requirement for a role."""
    skill: str
    min_level: str = "Apprentice"  # Apprentice | Intermediate | Advanced


class StaffingRequest(BaseModel):
    """Input contract for the Staffing Sub-Agent orchestrator.

    Callers provide a role_id to rank candidates for, or specify
    requirements inline for ad-hoc ranking.
    """
    role_id: Optional[str] = Field(None, description="ProjectRole UUID to rank candidates for")
    project_id: Optional[str] = Field(None, description="Project UUID (required if role_id given)")

    # Inline requirements (used when role_id is not provided)
    required_skills: List[SkillRequirement] = Field(default_factory=list)
    required_certs: List[str] = Field(default_factory=list)
    preferred_region: Optional[str] = None
    available_by: Optional[date] = None

    # Control knobs
    max_candidates: int = Field(default=10, ge=1, le=50, description="Max candidates to return")
    include_explanation: bool = Field(default=True, description="Include NL explanation per candidate")
    apply_preference_rules: bool = Field(default=True, description="Apply ops preference rules")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "role_id": "role_phx_lead_splicer",
            "project_id": "proj_phx_ftth",
            "max_candidates": 5,
            "include_explanation": True,
        }
    })


# ---------------------------------------------------------------------------
# Scorecard Dimensions
# ---------------------------------------------------------------------------

class ScorecardDimension(BaseModel):
    """A single scoring dimension within the 5-dimension scorecard."""
    name: str
    score: float = Field(..., ge=0.0, le=10.0, description="0-10 score")
    weight: float = Field(default=1.0, ge=0.0, description="Relative weight")
    rationale: str = Field(default="", description="Brief explanation for this score")


class Scorecard(BaseModel):
    """5-dimension scorecard for a candidate."""
    skills_match: ScorecardDimension = Field(
        ..., description="How well candidate skills meet role requirements"
    )
    certification_coverage: ScorecardDimension = Field(
        ..., description="Percentage of required certs the candidate holds"
    )
    availability_fit: ScorecardDimension = Field(
        ..., description="Availability alignment with project timeline"
    )
    geographic_proximity: ScorecardDimension = Field(
        ..., description="Travel distance / regional match"
    )
    experience_depth: ScorecardDimension = Field(
        ..., description="Years of experience and project history"
    )

    @property
    def weighted_total(self) -> float:
        dims = [
            self.skills_match, self.certification_coverage,
            self.availability_fit, self.geographic_proximity,
            self.experience_depth,
        ]
        total_weight = sum(d.weight for d in dims)
        if total_weight == 0:
            return 0.0
        return sum(d.score * d.weight for d in dims) / total_weight

    def to_dict(self) -> dict:
        dims = [
            self.skills_match, self.certification_coverage,
            self.availability_fit, self.geographic_proximity,
            self.experience_depth,
        ]
        return {
            "dimensions": [d.model_dump() for d in dims],
            "weighted_total": round(self.weighted_total, 2),
        }


# ---------------------------------------------------------------------------
# Candidate Result
# ---------------------------------------------------------------------------

class CandidateRanking(BaseModel):
    """A ranked candidate with scorecard and explanation."""
    rank: int
    technician_id: str
    technician_name: str
    overall_score: float = Field(..., ge=0.0, le=10.0)
    scorecard: Scorecard
    explanation: str = Field(default="", description="NL explanation differentiating this candidate")
    disqualifiers: List[str] = Field(default_factory=list, description="Reasons candidate might not qualify")
    highlights: List[str] = Field(default_factory=list, description="Key strengths for this role")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "rank": 1,
            "technician_id": "abc-123",
            "technician_name": "Marcus Johnson",
            "overall_score": 8.7,
            "scorecard": {
                "skills_match": {"name": "Skills Match", "score": 9.2, "weight": 2.0, "rationale": "Advanced fiber splicing, advanced OTDR"},
                "certification_coverage": {"name": "Certification Coverage", "score": 10.0, "weight": 1.5, "rationale": "Has FOA CFOT and OSHA 10"},
                "availability_fit": {"name": "Availability Fit", "score": 8.0, "weight": 1.0, "rationale": "Available 2 weeks before start"},
                "geographic_proximity": {"name": "Geographic Proximity", "score": 7.5, "weight": 1.0, "rationale": "Based in AZ, no travel needed"},
                "experience_depth": {"name": "Experience Depth", "score": 8.5, "weight": 1.0, "rationale": "8 years, 12 projects completed"},
            },
            "explanation": "Marcus is the top match due to advanced splicing skills and local availability.",
            "disqualifiers": [],
            "highlights": ["Advanced fiber splicing", "Local to project region"],
        }
    })


# ---------------------------------------------------------------------------
# Agent Output Contract
# ---------------------------------------------------------------------------

class StaffingResponse(BaseModel):
    """Output contract for the Staffing Sub-Agent orchestrator."""
    role_id: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    role_name: Optional[str] = None
    candidates: List[CandidateRanking]
    total_evaluated: int = Field(..., description="Total candidates considered before filtering")
    total_prefiltered: int = Field(..., description="Candidates passing pre-filter")
    batch_id: str = Field(..., description="Unique ID for this recommendation batch")
    agent_name: str = "staffing_sub_agent"
    preference_rules_applied: List[str] = Field(default_factory=list)
    fallback_used: bool = Field(default=False, description="True if LLM re-ranker was unavailable and fallback scoring was used")
    errors: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StaffingErrorResponse(BaseModel):
    """Error response when the agent cannot complete the request."""
    error: str
    detail: Optional[str] = None
    fallback_available: bool = False


# ---------------------------------------------------------------------------
# Preference rule schemas for the API
# ---------------------------------------------------------------------------

class PreferenceRuleCreate(BaseModel):
    """Schema for creating a new preference rule."""
    template_type: str = Field(
        default="custom",
        description="Template type: skill_minimum, cert_required, cert_recency, "
                    "region_preference, region_exclusion, availability_window, "
                    "experience_minimum, project_history, travel_willingness, "
                    "client_history, score_threshold, custom",
    )
    rule_type: str = Field(..., description="Human-readable rule type label")
    description: Optional[str] = Field(None, description="Human-readable description")
    threshold: Optional[str] = None
    scope: str = "global"
    scope_target_id: Optional[str] = None
    effect: str = "demote"
    score_modifier: Optional[float] = Field(default=0.0, description="Scoring modifier value")
    priority: int = Field(default=0, description="Higher priority rules take precedence")
    parameters: Dict[str, Any] = Field(default_factory=dict)
    created_by_type: str = Field(default="ops", description="Who created: agent or ops")
    created_by_id: Optional[str] = None
    rejection_id: Optional[str] = Field(None, description="Linked recommendation rejection ID")


class PreferenceRuleUpdate(BaseModel):
    """Schema for updating an existing preference rule."""
    template_type: Optional[str] = None
    rule_type: Optional[str] = None
    description: Optional[str] = None
    threshold: Optional[str] = None
    scope: Optional[str] = None
    scope_target_id: Optional[str] = None
    effect: Optional[str] = None
    score_modifier: Optional[float] = None
    priority: Optional[int] = None
    parameters: Optional[Dict[str, Any]] = None
    status: Optional[str] = Field(None, description="Lifecycle status: proposed, active, disabled, archived")
    active: Optional[bool] = None


class PreferenceRuleApproval(BaseModel):
    """Schema for approving an agent-proposed preference rule."""
    approved_by_id: str = Field(..., description="Ops user ID approving the rule")
    parameters: Optional[Dict[str, Any]] = Field(
        None, description="Optional parameter overrides before approval"
    )
    score_modifier: Optional[float] = Field(
        None, description="Optional score modifier override before approval"
    )


class PreferenceRuleResponse(BaseModel):
    """Full preference rule response schema."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    template_type: str
    rule_type: str
    description: Optional[str] = None
    threshold: Optional[str] = None
    scope: str
    scope_target_id: Optional[str] = None
    effect: str
    score_modifier: Optional[float] = None
    priority: int = 0
    parameters: Dict[str, Any] = Field(default_factory=dict)
    status: str
    active: bool
    rejection_id: Optional[str] = None
    source_recommendation_id: Optional[str] = None
    proposed_reason: Optional[str] = None
    created_by_type: str = "ops"
    created_by_id: Optional[str] = None
    approved_by_id: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
