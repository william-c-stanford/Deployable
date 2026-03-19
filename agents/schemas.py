"""
Pydantic schemas for the re-ranking chain input/output.

These schemas define the structured data flowing through the LangChain
re-ranking pipeline: candidate shortlists in, scored+ranked results out.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# 5-Dimension Scorecard
# ---------------------------------------------------------------------------

class DimensionScore(BaseModel):
    """A single dimension score within the 5-dimension scorecard."""

    dimension: str = Field(
        ...,
        description="One of: skill_match, proximity, availability, cost_efficiency, past_performance",
    )
    score: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Score from 0-10 for this dimension",
    )
    reasoning: str = Field(
        ...,
        description="Brief explanation of why this score was assigned",
    )


class Scorecard(BaseModel):
    """Structured 5-dimension scorecard for a candidate recommendation.

    Dimensions (per spec):
      1. skill_match — skills/proficiency alignment with role requirements
      2. availability — scheduling readiness and timeline fit
      3. proximity — geographic proximity to the project site
      4. reliability — track record: projects, hours, badges, career progression, docs verified
      5. cost — hourly rate competitiveness relative to role budget and per diem
    """

    skill_match: DimensionScore = Field(
        ...,
        description="How well the technician's skills and proficiency levels match the role requirements",
    )
    availability: DimensionScore = Field(
        ...,
        description="How soon the technician can start and whether they have scheduling conflicts",
    )
    proximity: DimensionScore = Field(
        ...,
        description="Geographic proximity to the project site and willingness to travel",
    )
    reliability: DimensionScore = Field(
        ...,
        description="Track record based on completed projects, approved hours, experience, badges, and docs verified",
    )
    cost: DimensionScore = Field(
        ...,
        description="Hourly rate competitiveness relative to the role budget and per diem costs",
    )

    @property
    def weighted_total(self) -> float:
        """Compute weighted total score using default weights."""
        weights = {
            "skill_match": 0.30,
            "availability": 0.20,
            "proximity": 0.15,
            "reliability": 0.20,
            "cost": 0.15,
        }
        return round(sum(
            getattr(self, dim).score * w
            for dim, w in weights.items()
        ), 2)

    def to_dict(self) -> dict:
        """Serialize scorecard to a flat dictionary for storage."""
        return {
            "skill_match": {"score": self.skill_match.score, "reasoning": self.skill_match.reasoning},
            "availability": {"score": self.availability.score, "reasoning": self.availability.reasoning},
            "proximity": {"score": self.proximity.score, "reasoning": self.proximity.reasoning},
            "reliability": {"score": self.reliability.score, "reasoning": self.reliability.reasoning},
            "cost": {"score": self.cost.score, "reasoning": self.cost.reasoning},
            "weighted_total": self.weighted_total,
        }


# ---------------------------------------------------------------------------
# Candidate Input (from SQL pre-filter shortlist)
# ---------------------------------------------------------------------------

class CandidateSkill(BaseModel):
    """A skill held by a candidate technician."""
    skill_name: str
    proficiency_level: str  # Beginner, Intermediate, Advanced
    training_hours: float = 0.0


class CandidateCert(BaseModel):
    """A certification held by a candidate technician."""
    cert_name: str
    status: str  # Active, Expired, Pending
    expiry_date: Optional[date] = None


class CandidateProfile(BaseModel):
    """Pre-filtered candidate profile sent to the re-ranking chain."""
    technician_id: str = Field(..., description="UUID of the technician")
    full_name: str
    home_base_city: str
    home_base_state: str
    approved_regions: list[str] = Field(default_factory=list)
    willing_to_travel: bool = True
    max_travel_radius_miles: Optional[int] = None
    career_stage: str = "Sourced"
    deployability_status: str = "In Training"
    available_from: Optional[date] = None
    archetype: Optional[str] = None
    years_experience: float = 0.0
    total_project_count: int = 0
    total_approved_hours: float = 0.0
    hourly_rate_min: Optional[float] = None
    hourly_rate_max: Optional[float] = None
    docs_verified: bool = False
    skills: list[CandidateSkill] = Field(default_factory=list)
    certifications: list[CandidateCert] = Field(default_factory=list)
    badge_count: int = 0

    # Pre-computed SQL score (from the pre-filter stage)
    sql_score: float = Field(default=0.0, description="Pre-computed SQL-based composite score")


# ---------------------------------------------------------------------------
# Role Requirements (the position being staffed)
# ---------------------------------------------------------------------------

class SkillRequirement(BaseModel):
    """A required skill for a project role."""
    skill_name: str
    min_proficiency: str  # Beginner, Intermediate, Advanced


class RoleRequirements(BaseModel):
    """The project role requirements that candidates are being matched against."""
    role_id: str
    role_name: str
    project_id: str
    project_name: str
    project_location_city: str
    project_location_region: str  # State abbreviation
    required_skills: list[SkillRequirement] = Field(default_factory=list)
    required_certs: list[str] = Field(default_factory=list)
    quantity_needed: int = 1
    hourly_rate_budget: Optional[float] = None
    per_diem_budget: Optional[float] = None
    start_date: Optional[date] = None


# ---------------------------------------------------------------------------
# Re-ranking Chain Input
# ---------------------------------------------------------------------------

class RerankingInput(BaseModel):
    """Complete input to the re-ranking chain."""
    role: RoleRequirements
    candidates: list[CandidateProfile] = Field(
        ...,
        max_length=25,
        description="Top-20 shortlist from SQL pre-filter (max 25 for safety margin)",
    )
    preference_rules: list[dict] = Field(
        default_factory=list,
        description="Active preference rules that modify scoring (e.g., experience threshold, skill minimum)",
    )
    dimension_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "skill_match": 0.30,
            "proximity": 0.15,
            "availability": 0.20,
            "cost_efficiency": 0.15,
            "past_performance": 0.20,
        },
    )


# ---------------------------------------------------------------------------
# Re-ranking Chain Output
# ---------------------------------------------------------------------------

class RankedCandidate(BaseModel):
    """A single re-ranked candidate with scorecard and explanation."""
    rank: int = Field(default=1, ge=1, description="Final rank position (1 = best)")
    technician_id: str
    full_name: str
    scorecard: Scorecard
    weighted_score: float = Field(..., ge=0.0, le=10.0, description="Weighted composite score")
    explanation: str = Field(
        ...,
        description="Natural-language explanation of why this candidate is ranked here, "
        "highlighting strengths and any concerns",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Any flags or warnings (e.g., 'cert expiring soon', 'requires travel')",
    )


class RerankingOutput(BaseModel):
    """Complete output from the re-ranking chain."""
    role_id: str
    role_name: str
    project_name: str
    ranked_candidates: list[RankedCandidate]
    total_evaluated: int
    agent_model: str = Field(default="", description="Model used for re-ranking")
    processing_time_ms: Optional[float] = None
    summary: str = Field(
        default="",
        description="High-level summary of the candidate pool quality and top picks",
    )
