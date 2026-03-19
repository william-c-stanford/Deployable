"""Pydantic schemas for career passport token endpoints."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CareerPassportTokenCreate(BaseModel):
    """Request body to generate a new career passport share token."""
    technician_id: UUID
    label: Optional[str] = Field(None, max_length=200, description="Optional label for the token")
    expiry_days: int = Field(30, ge=1, le=365, description="Token validity in days (default 30)")


class CareerPassportTokenResponse(BaseModel):
    """Response for a career passport token."""
    id: UUID
    technician_id: UUID
    token: str
    label: Optional[str] = None
    revoked: bool
    expires_at: datetime
    created_at: datetime
    created_by_role: str
    is_active: bool
    share_url: Optional[str] = None

    class Config:
        from_attributes = True


class CareerPassportTokenListResponse(BaseModel):
    """List of tokens for a technician."""
    tokens: list[CareerPassportTokenResponse]
    count: int


class CareerPassportTokenRevokeResponse(BaseModel):
    """Response after revoking a token."""
    id: UUID
    token: str
    revoked: bool = True
    revoked_at: datetime
    message: str = "Token revoked successfully"


# ---------------------------------------------------------------------------
# Public view schemas (unauthenticated endpoint)
# ---------------------------------------------------------------------------

class PublicSkillView(BaseModel):
    """Read-only skill for public passport."""
    skill_name: str
    proficiency_level: str

    class Config:
        from_attributes = True


class PublicCertView(BaseModel):
    """Read-only certification for public passport."""
    cert_name: str
    status: str
    expiry_date: Optional[datetime] = None

    class Config:
        from_attributes = True


class PublicBadgeView(BaseModel):
    """Read-only badge for public passport."""
    badge_name: str
    badge_type: str
    description: Optional[str] = None

    class Config:
        from_attributes = True


class PublicEnrollmentView(BaseModel):
    """Read-only training enrollment for public passport."""
    program_name: str
    advancement_level: str
    total_hours_logged: float
    total_hours_required: float
    status: str

    class Config:
        from_attributes = True


class PublicCareerPassportView(BaseModel):
    """Full read-only career passport JSON response for public access."""
    first_name: str
    last_name: str
    home_base_city: Optional[str] = None
    home_base_state: Optional[str] = None
    years_experience: float
    career_stage: str
    deployability_status: str
    archetype: Optional[str] = None
    total_project_count: int
    total_approved_hours: float
    docs_verified: bool
    bio: Optional[str] = None
    skills: list[PublicSkillView]
    certifications: list[PublicCertView]
    badges: list[PublicBadgeView]
    training_enrollments: list[PublicEnrollmentView]
    active_cert_count: int
    token_expires_at: datetime
