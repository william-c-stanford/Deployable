"""Pydantic schemas for badge API endpoints — ManualBadge and MilestoneBadge CRUD."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.technician import BadgeType
from app.models.badge import ManualBadgeCategory, MilestoneType


# ---------------------------------------------------------------------------
# Legacy badge schemas (TechnicianBadge — kept for backwards compat)
# ---------------------------------------------------------------------------

class BadgeGrantRequest(BaseModel):
    """Grant a manual badge (site or client) to a technician."""
    badge_type: BadgeType = Field(
        ...,
        description="Type of badge: 'site' or 'client'. Milestone badges are auto-generated.",
    )
    badge_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)


class BadgeRevokeRequest(BaseModel):
    """Revoke a manual badge from a technician."""
    reason: Optional[str] = Field(None, max_length=500, description="Reason for revocation")


class BadgeResponse(BaseModel):
    """A badge on a technician's profile (legacy TechnicianBadge)."""
    id: uuid.UUID
    technician_id: uuid.UUID
    badge_type: BadgeType
    badge_name: str
    description: Optional[str] = None
    granted_at: datetime

    class Config:
        from_attributes = True


class BadgeListResponse(BaseModel):
    """List of badges with optional type filtering."""
    items: List[BadgeResponse]
    total: int
    badge_type_filter: Optional[str] = None


# ---------------------------------------------------------------------------
# ManualBadge schemas (site/client badges)
# ---------------------------------------------------------------------------

class ManualBadgeBase(BaseModel):
    category: ManualBadgeCategory = ManualBadgeCategory.SITE
    badge_name: str = Field(..., max_length=200)
    description: Optional[str] = None
    site_name: Optional[str] = Field(None, max_length=200)
    client_name: Optional[str] = Field(None, max_length=200)
    project_id: Optional[uuid.UUID] = None
    expires_at: Optional[datetime] = None
    is_active: bool = True
    metadata_json: dict = Field(default_factory=dict)


class ManualBadgeCreate(ManualBadgeBase):
    """Create payload — technician_id comes from the URL path."""
    pass


class ManualBadgeUpdate(BaseModel):
    category: Optional[ManualBadgeCategory] = None
    badge_name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    site_name: Optional[str] = None
    client_name: Optional[str] = None
    project_id: Optional[uuid.UUID] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None
    metadata_json: Optional[dict] = None


class ManualBadgeResponse(ManualBadgeBase):
    id: uuid.UUID
    technician_id: uuid.UUID
    granted_by: Optional[uuid.UUID] = None
    granted_at: datetime

    class Config:
        from_attributes = True


class ManualBadgeListResponse(BaseModel):
    items: List[ManualBadgeResponse]
    total: int


# ---------------------------------------------------------------------------
# MilestoneBadge schemas (auto-generated)
# ---------------------------------------------------------------------------

class MilestoneBadgeBase(BaseModel):
    milestone_type: MilestoneType
    badge_name: str = Field(..., max_length=200)
    description: Optional[str] = None
    threshold_value: float
    actual_value: float
    reference_entity_type: Optional[str] = None
    reference_entity_id: Optional[uuid.UUID] = None
    icon: Optional[str] = "award"
    tier: int = Field(1, ge=1, le=3)


class MilestoneBadgeCreate(MilestoneBadgeBase):
    """Create payload — technician_id comes from URL path."""
    pass


class MilestoneBadgeUpdate(BaseModel):
    badge_name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    icon: Optional[str] = None
    tier: Optional[int] = Field(None, ge=1, le=3)


class MilestoneBadgeResponse(MilestoneBadgeBase):
    id: uuid.UUID
    technician_id: uuid.UUID
    granted_at: datetime

    class Config:
        from_attributes = True


class MilestoneBadgeListResponse(BaseModel):
    items: List[MilestoneBadgeResponse]
    total: int


# ---------------------------------------------------------------------------
# Milestone definitions (for showing earned/unearned status)
# ---------------------------------------------------------------------------

class MilestoneBadgeDefinition(BaseModel):
    """Definition of an auto-generated milestone badge and whether it's earned."""
    badge_name: str
    description: str
    category: str = Field(
        ...,
        description="Category: hours, projects, certifications, training, tenure",
    )
    threshold: str = Field(..., description="Human-readable threshold description")
    earned: bool
    earned_at: Optional[datetime] = None
    badge_id: Optional[uuid.UUID] = Field(
        None, description="ID if this milestone badge has been persisted",
    )


class MilestoneBadgesResponse(BaseModel):
    """All milestone badges with earned/unearned status for a technician."""
    technician_id: uuid.UUID
    earned: List[MilestoneBadgeDefinition]
    available: List[MilestoneBadgeDefinition]
    total_earned: int
    total_available: int
