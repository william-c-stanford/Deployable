"""Pydantic schemas for training domain — programs, enrollment, hours logging."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.training import AdvancementLevel, EnrollmentStatus, HoursLogSource


# ---------------------------------------------------------------------------
# Training Program schemas
# ---------------------------------------------------------------------------

class TrainingProgramBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    total_hours_required: float = 0.0
    apprentice_hours_min: float = 0.0
    intermediate_hours_threshold: float = 100.0
    advanced_hours_threshold: float = 300.0
    skill_category_id: Optional[uuid.UUID] = None
    is_active: bool = True
    display_order: int = 0


class TrainingProgramCreate(TrainingProgramBase):
    pass


class TrainingProgramUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    total_hours_required: Optional[float] = None
    intermediate_hours_threshold: Optional[float] = None
    advanced_hours_threshold: Optional[float] = None
    is_active: Optional[bool] = None


class TrainingProgramResponse(TrainingProgramBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Training Enrollment schemas
# ---------------------------------------------------------------------------

class TrainingEnrollmentBase(BaseModel):
    technician_id: uuid.UUID
    program_id: uuid.UUID
    advancement_level: AdvancementLevel = AdvancementLevel.APPRENTICE
    status: EnrollmentStatus = EnrollmentStatus.ACTIVE


class TrainingEnrollmentCreate(TrainingEnrollmentBase):
    pass


class TrainingEnrollmentUpdate(BaseModel):
    advancement_level: Optional[AdvancementLevel] = None
    status: Optional[EnrollmentStatus] = None
    total_hours_logged: Optional[float] = None


class TrainingEnrollmentResponse(TrainingEnrollmentBase):
    id: uuid.UUID
    total_hours_logged: float
    enrolled_at: datetime
    completed_at: Optional[datetime] = None
    last_advancement_check: Optional[datetime] = None
    last_advanced_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TrainingEnrollmentDetail(TrainingEnrollmentResponse):
    """Extended enrollment response with program details."""
    program_name: Optional[str] = None
    program_slug: Optional[str] = None
    hours_to_next_level: Optional[float] = None
    next_level: Optional[AdvancementLevel] = None


# ---------------------------------------------------------------------------
# Training Hours Log schemas
# ---------------------------------------------------------------------------

class TrainingHoursLogBase(BaseModel):
    technician_id: uuid.UUID
    enrollment_id: Optional[uuid.UUID] = None
    skill_id: Optional[uuid.UUID] = None
    timesheet_id: Optional[uuid.UUID] = None
    hours: float = Field(..., gt=0)
    logged_date: date
    source: HoursLogSource = HoursLogSource.MANUAL
    description: Optional[str] = None


class TrainingHoursLogCreate(TrainingHoursLogBase):
    pass


class TrainingHoursLogUpdate(BaseModel):
    hours: Optional[float] = Field(None, gt=0)
    description: Optional[str] = None
    approved: Optional[bool] = None


class TrainingHoursLogResponse(TrainingHoursLogBase):
    id: uuid.UUID
    approved: bool
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Advancement Gate Config schemas
# ---------------------------------------------------------------------------

class AdvancementGateConfigBase(BaseModel):
    program_id: Optional[uuid.UUID] = None
    skill_id: Optional[uuid.UUID] = None
    target_level: AdvancementLevel
    certification_id: uuid.UUID
    min_hours_override: Optional[float] = None
    is_mandatory: bool = True
    gate_description: Optional[str] = None
    is_active: bool = True


class AdvancementGateConfigCreate(AdvancementGateConfigBase):
    pass


class AdvancementGateConfigUpdate(BaseModel):
    min_hours_override: Optional[float] = None
    is_mandatory: Optional[bool] = None
    gate_description: Optional[str] = None
    is_active: Optional[bool] = None


class AdvancementGateConfigResponse(AdvancementGateConfigBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Technician Training Summary (for dashboard / detail views)
# ---------------------------------------------------------------------------

class TechnicianTrainingSummary(BaseModel):
    """Aggregated training status for a technician."""
    technician_id: uuid.UUID
    technician_name: str
    total_training_hours: float
    active_enrollments: int
    completed_enrollments: int
    highest_advancement_level: Optional[AdvancementLevel] = None
    enrollments: List[TrainingEnrollmentDetail] = Field(default_factory=list)
    recent_hours: List[TrainingHoursLogResponse] = Field(default_factory=list)
