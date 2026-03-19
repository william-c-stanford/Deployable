"""Pydantic schemas for the technician domain."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.technician import (
    CareerStage,
    DeployabilityStatus,
    ProficiencyLevel,
    CertStatus,
    VerificationStatus,
    BadgeType,
)


# ---------------------------------------------------------------------------
# Skill schemas
# ---------------------------------------------------------------------------

class SkillBase(BaseModel):
    skill_name: str
    proficiency_level: ProficiencyLevel = ProficiencyLevel.APPRENTICE
    training_hours_accumulated: float = 0.0


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    skill_name: Optional[str] = None
    proficiency_level: Optional[ProficiencyLevel] = None
    training_hours_accumulated: Optional[float] = None


class SkillResponse(SkillBase):
    id: uuid.UUID
    technician_id: uuid.UUID

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Certification schemas
# ---------------------------------------------------------------------------

class CertBase(BaseModel):
    cert_name: str
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: CertStatus = CertStatus.PENDING


class CertCreate(CertBase):
    pass


class CertUpdate(BaseModel):
    cert_name: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: Optional[CertStatus] = None


class CertResponse(CertBase):
    id: uuid.UUID
    technician_id: uuid.UUID

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Document schemas
# ---------------------------------------------------------------------------

class DocumentBase(BaseModel):
    doc_type: str
    verification_status: VerificationStatus = VerificationStatus.NOT_SUBMITTED


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(BaseModel):
    doc_type: Optional[str] = None
    verification_status: Optional[VerificationStatus] = None


class DocumentResponse(DocumentBase):
    id: uuid.UUID
    technician_id: uuid.UUID

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Badge schemas
# ---------------------------------------------------------------------------

class BadgeBase(BaseModel):
    badge_type: BadgeType
    badge_name: str
    description: Optional[str] = None


class BadgeCreate(BadgeBase):
    pass


class BadgeUpdate(BaseModel):
    badge_type: Optional[BadgeType] = None
    badge_name: Optional[str] = None
    description: Optional[str] = None


class BadgeResponse(BadgeBase):
    id: uuid.UUID
    technician_id: uuid.UUID
    granted_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Technician schemas
# ---------------------------------------------------------------------------

class TechnicianBase(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: Optional[str] = None
    home_base_city: Optional[str] = None
    approved_regions: List[str] = Field(default_factory=list)
    career_stage: CareerStage = CareerStage.SOURCED
    deployability_status: DeployabilityStatus = DeployabilityStatus.IN_TRAINING
    deployability_locked: bool = False
    inactive_locked_at: Optional[datetime] = None
    inactive_locked_by: Optional[str] = None
    inactive_lock_reason: Optional[str] = None
    available_from: Optional[date] = None
    bio: Optional[str] = None
    ops_notes: Optional[str] = None
    avatar_url: Optional[str] = None
    years_experience: Optional[int] = None
    total_project_count: Optional[int] = None
    total_approved_hours: Optional[float] = None
    hire_date: Optional[date] = None


class TechnicianCreate(TechnicianBase):
    skills: List[SkillCreate] = Field(default_factory=list)
    certifications: List[CertCreate] = Field(default_factory=list)
    documents: List[DocumentCreate] = Field(default_factory=list)
    badges: List[BadgeCreate] = Field(default_factory=list)


class TechnicianUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    home_base_city: Optional[str] = None
    approved_regions: Optional[List[str]] = None
    career_stage: Optional[CareerStage] = None
    deployability_status: Optional[DeployabilityStatus] = None
    deployability_locked: Optional[bool] = None
    available_from: Optional[date] = None
    bio: Optional[str] = None
    ops_notes: Optional[str] = None
    avatar_url: Optional[str] = None
    years_experience: Optional[int] = None
    total_project_count: Optional[int] = None
    total_approved_hours: Optional[float] = None
    hire_date: Optional[date] = None
    skills: Optional[List[SkillCreate]] = None
    certifications: Optional[List[CertCreate]] = None
    documents: Optional[List[DocumentCreate]] = None
    badges: Optional[List[BadgeCreate]] = None


class TechnicianResponse(TechnicianBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    skills: List[SkillResponse] = Field(default_factory=list)
    certifications: List[CertResponse] = Field(default_factory=list)
    documents: List[DocumentResponse] = Field(default_factory=list)
    badges: List[BadgeResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


class TechnicianListResponse(BaseModel):
    items: List[TechnicianResponse]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Filter params (for documentation / programmatic use)
# ---------------------------------------------------------------------------

class TechnicianFilterParams(BaseModel):
    search: Optional[str] = None
    career_stage: Optional[CareerStage] = None
    deployability_status: Optional[DeployabilityStatus] = None
    skills: Optional[List[str]] = None
    available_from: Optional[date] = None
    region: Optional[str] = None


# ---------------------------------------------------------------------------
# Manual Inactive Override schemas
# ---------------------------------------------------------------------------

class InactiveOverrideRequest(BaseModel):
    """Request to manually set a technician to Inactive with a locked override."""
    reason: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Reason for manually deactivating this technician",
    )


class InactiveOverrideResponse(BaseModel):
    """Response after applying or removing a manual Inactive override."""
    technician_id: str
    technician_name: str
    previous_status: str
    new_status: str
    deployability_locked: bool
    inactive_locked_at: Optional[datetime] = None
    inactive_locked_by: Optional[str] = None
    inactive_lock_reason: Optional[str] = None
    action: str  # "locked_inactive" | "unlocked_reactivated"

    class Config:
        from_attributes = True


class ReactivateRequest(BaseModel):
    """Request to unlock a manually-locked Inactive technician and reactivate them."""
    target_status: Optional[DeployabilityStatus] = Field(
        None,
        description="Status to set after unlocking. If omitted, auto-computation will determine it.",
    )
    reason: Optional[str] = Field(
        None,
        max_length=1000,
        description="Optional reason for reactivation",
    )
