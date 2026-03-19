"""Pydantic schemas for technician advancement and cert gate configuration."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.technician import ProficiencyLevel


# ---------------------------------------------------------------------------
# Skill advancement status (per skill)
# ---------------------------------------------------------------------------

class SkillAdvancementStatus(BaseModel):
    """Current advancement status for a single technician skill."""
    skill_name: str
    current_level: ProficiencyLevel
    training_hours_accumulated: float
    hours_to_next_level: Optional[float] = None
    next_level: Optional[ProficiencyLevel] = None
    cert_gate_required: Optional[str] = None
    cert_gate_met: bool = False
    eligible_for_advancement: bool = False
    blocked_reason: Optional[str] = None


class TechnicianAdvancementStatus(BaseModel):
    """Full advancement status for a technician across all skills."""
    technician_id: uuid.UUID
    technician_name: str
    career_stage: str
    deployability_status: str
    total_skills: int
    skills_at_apprentice: int
    skills_at_intermediate: int
    skills_at_advanced: int
    skills: List[SkillAdvancementStatus]
    overall_training_complete: bool = False
    last_evaluated: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Cert gate configuration
# ---------------------------------------------------------------------------

class CertGateConfig(BaseModel):
    """Configuration for certification gates on a skill."""
    skill_id: uuid.UUID
    skill_name: str
    intermediate_hours_threshold: int
    advanced_hours_threshold: int
    cert_gate_intermediate: Optional[str] = None
    cert_gate_advanced: Optional[str] = None

    class Config:
        from_attributes = True


class CertGateUpdate(BaseModel):
    """Request body for updating cert gates on a skill."""
    intermediate_hours_threshold: Optional[int] = Field(None, ge=0)
    advanced_hours_threshold: Optional[int] = Field(None, ge=0)
    cert_gate_intermediate: Optional[str] = None
    cert_gate_advanced: Optional[str] = None


class CertGateListResponse(BaseModel):
    """List of cert gate configurations."""
    items: List[CertGateConfig]
    total: int


# ---------------------------------------------------------------------------
# Re-evaluation trigger
# ---------------------------------------------------------------------------

class ReEvaluationRequest(BaseModel):
    """Request body for manually triggering advancement re-evaluation."""
    technician_ids: Optional[List[uuid.UUID]] = Field(
        None, description="Specific technician IDs to re-evaluate. If empty, re-evaluates all."
    )
    dry_run: bool = Field(
        False, description="If true, compute results but do not apply changes."
    )


class SkillAdvancementResult(BaseModel):
    """Result for a single skill advancement during re-evaluation."""
    skill_name: str
    old_level: str
    new_level: str
    hours: float
    cert_gate_met: bool


class TechnicianReEvaluationResult(BaseModel):
    """Result of re-evaluation for a single technician."""
    technician_id: uuid.UUID
    technician_name: str
    advancements: List[SkillAdvancementResult]
    career_stage_changed: bool = False
    old_career_stage: Optional[str] = None
    new_career_stage: Optional[str] = None


class ReEvaluationResponse(BaseModel):
    """Response from a manual advancement re-evaluation."""
    dry_run: bool
    technicians_evaluated: int
    technicians_with_changes: int
    total_advancements: int
    results: List[TechnicianReEvaluationResult]
