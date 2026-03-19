"""Pydantic schemas for readiness evaluation API responses."""

from typing import Any, Optional
from pydantic import BaseModel, Field


class CertDetailSchema(BaseModel):
    cert_name: str
    status: str
    is_active: bool
    days_until_expiry: Optional[int] = None
    score_contribution: float = 0.0
    note: str = ""


class CertificationReadinessSchema(BaseModel):
    score: float
    total_certs: int = 0
    active_certs: int = 0
    expired_certs: int = 0
    expiring_soon_certs: int = 0
    summary: str = ""
    details: list[CertDetailSchema] = []


class SkillDetailSchema(BaseModel):
    skill_name: str
    proficiency_level: str
    hours_accumulated: float = 0.0
    hours_to_next_level: Optional[float] = None
    next_level: Optional[str] = None
    advancement_blocked_by: Optional[str] = None
    score_contribution: float = 0.0


class TrainingReadinessSchema(BaseModel):
    score: float
    total_skills: int = 0
    advanced_skills: int = 0
    intermediate_skills: int = 0
    apprentice_skills: int = 0
    total_training_hours: float = 0.0
    active_enrollments: int = 0
    completed_enrollments: int = 0
    summary: str = ""
    details: list[SkillDetailSchema] = []


class AssignmentHistoryDetailsSchema(BaseModel):
    total_assignments: int = 0
    completed_assignments: int = 0
    active_assignments: int = 0
    pre_booked_assignments: int = 0
    completion_rate: float = 0.0
    avg_duration_days: Optional[float] = None
    days_since_last_assignment: Optional[int] = None
    unique_projects: int = 0


class AssignmentHistoryReadinessSchema(BaseModel):
    score: float
    summary: str = ""
    details: AssignmentHistoryDetailsSchema = AssignmentHistoryDetailsSchema()


class DocumentationReadinessSchema(BaseModel):
    score: float
    total_docs: int = 0
    verified_docs: int = 0
    pending_docs: int = 0
    missing_docs: int = 0
    summary: str = ""


class ReadinessResultSchema(BaseModel):
    technician_id: str
    technician_name: str
    overall_score: float
    current_status: str
    suggested_status: str
    status_change_recommended: bool = False
    status_change_reason: Optional[str] = None
    dimension_scores: dict[str, float] = {}
    certification: CertificationReadinessSchema
    training: TrainingReadinessSchema
    assignment_history: AssignmentHistoryReadinessSchema
    documentation: DocumentationReadinessSchema
    evaluated_at: str = ""


class ReadinessSummaryTechnicianSchema(BaseModel):
    technician_id: str
    technician_name: str
    overall_score: float
    current_status: str
    suggested_status: str
    status_change_recommended: bool = False
    dimension_scores: dict[str, float] = {}


class ScoreDistributionSchema(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0


class ReadinessSummarySchema(BaseModel):
    total_evaluated: int
    status_changes_recommended: int = 0
    average_score: float = 0.0
    score_distribution: ScoreDistributionSchema = ScoreDistributionSchema()
    technicians: list[ReadinessSummaryTechnicianSchema] = []


class StatusApplyResponseSchema(BaseModel):
    technician_id: str
    changed: bool
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None


class BatchTriggerResponseSchema(BaseModel):
    status: str
    message: str
