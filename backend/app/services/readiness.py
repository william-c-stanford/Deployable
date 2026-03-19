"""Technician Readiness Re-Evaluation Service.

Computes updated readiness scores for technicians based on three dimensions:
  1. Certification Readiness — active certs vs required/expected certs, expiry proximity
  2. Training Progress — skill proficiency levels, hours accumulated, advancement potential
  3. Assignment History — past assignment count, completion rate, recency, diversity

Each dimension produces a 0-100 score. The composite readiness score is a weighted
combination that drives the technician's deployability_status and availability ranking.

This service is DETERMINISTIC — it reads current state and produces a readiness
assessment. The caller decides whether to apply status changes (human-approval gate).

Usage:
    from app.services.readiness import evaluate_technician_readiness, ReadinessResult

    result = evaluate_technician_readiness(session, technician_id)
    print(result.overall_score)       # 0-100
    print(result.suggested_status)    # DeployabilityStatus enum value
    print(result.certification_score) # CertificationReadiness dataclass
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    TechnicianDocument,
    DeployabilityStatus,
    ProficiencyLevel,
    CareerStage,
    CertStatus,
    VerificationStatus,
)
from app.models.assignment import Assignment, AssignmentStatus
from app.models.training import TrainingEnrollment, EnrollmentStatus
from app.models.skill import Skill

logger = logging.getLogger("deployable.services.readiness")

# ── Weights for composite readiness score ──────────────────────────────────
READINESS_WEIGHTS = {
    "certification": 0.35,
    "training": 0.35,
    "assignment_history": 0.20,
    "documentation": 0.10,
}


def _enum_val(v) -> str:
    """Safely extract .value from an enum, or return str."""
    return v.value if hasattr(v, "value") else str(v) if v else ""


# ── Data classes for structured results ────────────────────────────────────

@dataclass
class CertReadinessDetail:
    """Detail for a single certification check."""
    cert_name: str
    status: str
    is_active: bool
    days_until_expiry: Optional[int] = None
    score_contribution: float = 0.0
    note: str = ""


@dataclass
class CertificationReadiness:
    """Certification dimension of readiness scoring."""
    score: float = 0.0
    total_certs: int = 0
    active_certs: int = 0
    expired_certs: int = 0
    expiring_soon_certs: int = 0  # within 30 days
    pending_certs: int = 0
    details: list[CertReadinessDetail] = field(default_factory=list)
    summary: str = ""


@dataclass
class SkillReadinessDetail:
    """Detail for a single skill's training readiness."""
    skill_name: str
    proficiency_level: str
    hours_accumulated: float = 0.0
    hours_to_next_level: Optional[float] = None
    next_level: Optional[str] = None
    advancement_blocked_by: Optional[str] = None
    score_contribution: float = 0.0


@dataclass
class TrainingReadiness:
    """Training progress dimension of readiness scoring."""
    score: float = 0.0
    total_skills: int = 0
    advanced_skills: int = 0
    intermediate_skills: int = 0
    apprentice_skills: int = 0
    total_training_hours: float = 0.0
    active_enrollments: int = 0
    completed_enrollments: int = 0
    details: list[SkillReadinessDetail] = field(default_factory=list)
    summary: str = ""


@dataclass
class AssignmentHistoryDetail:
    """Detail for assignment history analysis."""
    total_assignments: int = 0
    completed_assignments: int = 0
    active_assignments: int = 0
    pre_booked_assignments: int = 0
    cancelled_assignments: int = 0
    completion_rate: float = 0.0
    avg_assignment_duration_days: Optional[float] = None
    most_recent_end_date: Optional[date] = None
    days_since_last_assignment: Optional[int] = None
    unique_projects: int = 0


@dataclass
class AssignmentHistoryReadiness:
    """Assignment history dimension of readiness scoring."""
    score: float = 0.0
    details: AssignmentHistoryDetail = field(default_factory=AssignmentHistoryDetail)
    summary: str = ""


@dataclass
class DocumentationReadiness:
    """Documentation verification dimension of readiness scoring."""
    score: float = 0.0
    total_docs: int = 0
    verified_docs: int = 0
    pending_docs: int = 0
    missing_docs: int = 0
    summary: str = ""


@dataclass
class ReadinessResult:
    """Complete readiness evaluation result for a technician."""
    technician_id: str
    technician_name: str
    overall_score: float = 0.0
    current_status: str = ""
    suggested_status: str = ""
    status_change_recommended: bool = False
    status_change_reason: Optional[str] = None
    certification: CertificationReadiness = field(default_factory=CertificationReadiness)
    training: TrainingReadiness = field(default_factory=TrainingReadiness)
    assignment_history: AssignmentHistoryReadiness = field(default_factory=AssignmentHistoryReadiness)
    documentation: DocumentationReadiness = field(default_factory=DocumentationReadiness)
    dimension_scores: dict[str, float] = field(default_factory=dict)
    evaluated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response / JSON transport."""
        return {
            "technician_id": self.technician_id,
            "technician_name": self.technician_name,
            "overall_score": round(self.overall_score, 1),
            "current_status": self.current_status,
            "suggested_status": self.suggested_status,
            "status_change_recommended": self.status_change_recommended,
            "status_change_reason": self.status_change_reason,
            "dimension_scores": {
                k: round(v, 1) for k, v in self.dimension_scores.items()
            },
            "certification": {
                "score": round(self.certification.score, 1),
                "total_certs": self.certification.total_certs,
                "active_certs": self.certification.active_certs,
                "expired_certs": self.certification.expired_certs,
                "expiring_soon_certs": self.certification.expiring_soon_certs,
                "summary": self.certification.summary,
                "details": [
                    {
                        "cert_name": d.cert_name,
                        "status": d.status,
                        "is_active": d.is_active,
                        "days_until_expiry": d.days_until_expiry,
                        "score_contribution": round(d.score_contribution, 1),
                        "note": d.note,
                    }
                    for d in self.certification.details
                ],
            },
            "training": {
                "score": round(self.training.score, 1),
                "total_skills": self.training.total_skills,
                "advanced_skills": self.training.advanced_skills,
                "intermediate_skills": self.training.intermediate_skills,
                "apprentice_skills": self.training.apprentice_skills,
                "total_training_hours": round(self.training.total_training_hours, 1),
                "active_enrollments": self.training.active_enrollments,
                "completed_enrollments": self.training.completed_enrollments,
                "summary": self.training.summary,
                "details": [
                    {
                        "skill_name": d.skill_name,
                        "proficiency_level": d.proficiency_level,
                        "hours_accumulated": round(d.hours_accumulated, 1),
                        "hours_to_next_level": round(d.hours_to_next_level, 1) if d.hours_to_next_level else None,
                        "next_level": d.next_level,
                        "advancement_blocked_by": d.advancement_blocked_by,
                        "score_contribution": round(d.score_contribution, 1),
                    }
                    for d in self.training.details
                ],
            },
            "assignment_history": {
                "score": round(self.assignment_history.score, 1),
                "summary": self.assignment_history.summary,
                "details": {
                    "total_assignments": self.assignment_history.details.total_assignments,
                    "completed_assignments": self.assignment_history.details.completed_assignments,
                    "active_assignments": self.assignment_history.details.active_assignments,
                    "pre_booked_assignments": self.assignment_history.details.pre_booked_assignments,
                    "completion_rate": round(self.assignment_history.details.completion_rate, 2),
                    "avg_duration_days": (
                        round(self.assignment_history.details.avg_assignment_duration_days, 1)
                        if self.assignment_history.details.avg_assignment_duration_days else None
                    ),
                    "days_since_last_assignment": self.assignment_history.details.days_since_last_assignment,
                    "unique_projects": self.assignment_history.details.unique_projects,
                },
            },
            "documentation": {
                "score": round(self.documentation.score, 1),
                "total_docs": self.documentation.total_docs,
                "verified_docs": self.documentation.verified_docs,
                "pending_docs": self.documentation.pending_docs,
                "missing_docs": self.documentation.missing_docs,
                "summary": self.documentation.summary,
            },
            "evaluated_at": self.evaluated_at,
        }


# ── Core evaluation functions ──────────────────────────────────────────────

def _evaluate_certification_readiness(
    session: Session,
    technician: Technician,
) -> CertificationReadiness:
    """Evaluate certification readiness for a technician.

    Scoring:
      - Each active cert contributes positively
      - Expired certs penalize heavily
      - Expiring-soon certs (<30 days) get a moderate penalty
      - Pending certs get partial credit
      - Base score of 50 if no certs exist (not penalized for optional certs)
    """
    result = CertificationReadiness()
    certs = technician.certifications or []
    today = date.today()

    if not certs:
        result.score = 50.0
        result.summary = "No certifications on record; baseline score"
        return result

    result.total_certs = len(certs)
    total_score = 0.0
    per_cert_max = 100.0 / max(len(certs), 1)

    for cert in certs:
        status_val = _enum_val(cert.status)
        days_until = None
        if cert.expiry_date:
            days_until = (cert.expiry_date - today).days

        detail = CertReadinessDetail(
            cert_name=cert.cert_name,
            status=status_val,
            is_active=False,
            days_until_expiry=days_until,
        )

        if status_val == CertStatus.ACTIVE.value:
            detail.is_active = True
            result.active_certs += 1

            if days_until is not None and days_until <= 30:
                # Expiring soon — partial penalty
                result.expiring_soon_certs += 1
                freshness = max(0.5, days_until / 30.0)
                score_contrib = per_cert_max * freshness
                detail.score_contribution = score_contrib
                detail.note = f"Expiring in {days_until} days"
            elif days_until is not None and days_until <= 60:
                # Nearing expiry — minor warning
                score_contrib = per_cert_max * 0.85
                detail.score_contribution = score_contrib
                detail.note = f"Expires in {days_until} days"
            else:
                score_contrib = per_cert_max
                detail.score_contribution = score_contrib
                detail.note = "Active and current"

        elif status_val == CertStatus.PENDING.value:
            result.pending_certs += 1
            score_contrib = per_cert_max * 0.3
            detail.score_contribution = score_contrib
            detail.note = "Pending — awaiting verification"

        elif status_val == CertStatus.EXPIRED.value:
            result.expired_certs += 1
            score_contrib = 0.0
            detail.score_contribution = score_contrib
            detail.note = f"Expired{f' {abs(days_until)} days ago' if days_until else ''}"

        elif status_val == CertStatus.REVOKED.value:
            result.expired_certs += 1
            score_contrib = 0.0
            detail.score_contribution = score_contrib
            detail.note = "Revoked"

        else:
            score_contrib = per_cert_max * 0.2
            detail.score_contribution = score_contrib
            detail.note = f"Status: {status_val}"

        total_score += score_contrib
        result.details.append(detail)

    result.score = min(100.0, total_score)

    # Generate summary
    parts = [f"{result.active_certs}/{result.total_certs} active"]
    if result.expired_certs > 0:
        parts.append(f"{result.expired_certs} expired")
    if result.expiring_soon_certs > 0:
        parts.append(f"{result.expiring_soon_certs} expiring within 30d")
    if result.pending_certs > 0:
        parts.append(f"{result.pending_certs} pending")
    result.summary = "; ".join(parts)

    return result


def _evaluate_training_readiness(
    session: Session,
    technician: Technician,
) -> TrainingReadiness:
    """Evaluate training progress and skill readiness.

    Scoring:
      - Advanced skills: 100 points each
      - Intermediate skills: 65 points each
      - Apprentice skills: 30 points each
      - Bonus for total training hours
      - Bonus for completed training enrollments
      - Overall normalized to 0-100
    """
    result = TrainingReadiness()
    skills = technician.skills or []

    if not skills:
        result.score = 20.0
        result.summary = "No skills on record"
        return result

    result.total_skills = len(skills)
    total_skill_score = 0.0
    proficiency_scores = {
        ProficiencyLevel.APPRENTICE.value: 30,
        ProficiencyLevel.INTERMEDIATE.value: 65,
        ProficiencyLevel.ADVANCED.value: 100,
    }

    # Default thresholds
    DEFAULT_INTERMEDIATE_HOURS = 100
    DEFAULT_ADVANCED_HOURS = 300

    for ts in skills:
        level = _enum_val(ts.proficiency_level)
        hours = ts.training_hours_accumulated or 0.0
        result.total_training_hours += hours

        if level == ProficiencyLevel.ADVANCED.value:
            result.advanced_skills += 1
        elif level == ProficiencyLevel.INTERMEDIATE.value:
            result.intermediate_skills += 1
        else:
            result.apprentice_skills += 1

        prof_score = proficiency_scores.get(level, 30)
        total_skill_score += prof_score

        # Determine next level & hours needed
        skill_def = session.query(Skill).filter(Skill.name == ts.skill_name).first()
        next_level = None
        hours_to_next = None
        blocked_by = None

        if level == ProficiencyLevel.APPRENTICE.value:
            next_level = ProficiencyLevel.INTERMEDIATE.value
            threshold = (
                skill_def.intermediate_hours_threshold
                if skill_def and skill_def.intermediate_hours_threshold
                else DEFAULT_INTERMEDIATE_HOURS
            )
            hours_to_next = max(0, threshold - hours)
            if skill_def and skill_def.cert_gate_intermediate:
                # Check if cert gate is satisfied
                has_cert = any(
                    c.cert_name == skill_def.cert_gate_intermediate
                    and _enum_val(c.status) == CertStatus.ACTIVE.value
                    for c in (technician.certifications or [])
                )
                if not has_cert and hours >= threshold:
                    blocked_by = f"Missing cert: {skill_def.cert_gate_intermediate}"

        elif level == ProficiencyLevel.INTERMEDIATE.value:
            next_level = ProficiencyLevel.ADVANCED.value
            threshold = (
                skill_def.advanced_hours_threshold
                if skill_def and skill_def.advanced_hours_threshold
                else DEFAULT_ADVANCED_HOURS
            )
            hours_to_next = max(0, threshold - hours)
            if skill_def and skill_def.cert_gate_advanced:
                has_cert = any(
                    c.cert_name == skill_def.cert_gate_advanced
                    and _enum_val(c.status) == CertStatus.ACTIVE.value
                    for c in (technician.certifications or [])
                )
                if not has_cert and hours >= threshold:
                    blocked_by = f"Missing cert: {skill_def.cert_gate_advanced}"

        detail = SkillReadinessDetail(
            skill_name=ts.skill_name,
            proficiency_level=level,
            hours_accumulated=hours,
            hours_to_next_level=hours_to_next,
            next_level=next_level,
            advancement_blocked_by=blocked_by,
            score_contribution=prof_score,
        )
        result.details.append(detail)

    # Normalize skill scores to 0-100
    max_possible = result.total_skills * 100
    base_score = (total_skill_score / max_possible * 70) if max_possible > 0 else 0

    # Bonus for training hours (up to 15 points)
    hours_bonus = min(15.0, result.total_training_hours / 50.0)

    # Training enrollment bonus (up to 15 points)
    enrollments = technician.training_enrollments or []
    result.active_enrollments = sum(
        1 for e in enrollments if _enum_val(e.status) == EnrollmentStatus.ACTIVE.value
    )
    result.completed_enrollments = sum(
        1 for e in enrollments if _enum_val(e.status) == EnrollmentStatus.COMPLETED.value
    )
    enrollment_bonus = min(15.0, result.completed_enrollments * 5.0 + result.active_enrollments * 2.0)

    result.score = min(100.0, base_score + hours_bonus + enrollment_bonus)

    # Summary
    parts = []
    if result.advanced_skills > 0:
        parts.append(f"{result.advanced_skills} advanced")
    if result.intermediate_skills > 0:
        parts.append(f"{result.intermediate_skills} intermediate")
    if result.apprentice_skills > 0:
        parts.append(f"{result.apprentice_skills} apprentice")
    parts.append(f"{result.total_training_hours:.0f} total hours")
    result.summary = "; ".join(parts)

    return result


def _evaluate_assignment_history(
    session: Session,
    technician: Technician,
) -> AssignmentHistoryReadiness:
    """Evaluate assignment history for readiness scoring.

    Scoring:
      - Base points from completed assignment count (up to 40 points)
      - Completion rate bonus (up to 20 points)
      - Recency bonus — more recent assignments score higher (up to 20 points)
      - Diversity bonus — assignments across different projects (up to 20 points)
    """
    result = AssignmentHistoryReadiness()
    detail = AssignmentHistoryDetail()

    assignments = (
        session.query(Assignment)
        .filter(Assignment.technician_id == technician.id)
        .all()
    )

    if not assignments:
        result.score = 25.0  # Baseline — no history isn't a hard penalty
        result.summary = "No assignment history"
        result.details = detail
        return result

    today = date.today()
    detail.total_assignments = len(assignments)
    completed_durations = []
    most_recent_end = None
    project_ids = set()

    for a in assignments:
        status = a.status if isinstance(a.status, str) else _enum_val(a.status)
        if status in ("Completed", AssignmentStatus.COMPLETED.value if hasattr(AssignmentStatus, 'COMPLETED') else "Completed"):
            detail.completed_assignments += 1
            if a.start_date and a.end_date:
                duration = (a.end_date - a.start_date).days
                completed_durations.append(duration)
                if most_recent_end is None or a.end_date > most_recent_end:
                    most_recent_end = a.end_date
        elif status in ("Active", "active"):
            detail.active_assignments += 1
        elif status in ("Pre-Booked", "Pending Confirmation"):
            detail.pre_booked_assignments += 1
        elif status in ("Cancelled",):
            detail.cancelled_assignments += 1

        if hasattr(a, 'role') and a.role:
            project_ids.add(str(a.role.project_id))
        elif hasattr(a, 'role_id'):
            project_ids.add(str(a.role_id))

    detail.unique_projects = len(project_ids)

    if completed_durations:
        detail.avg_assignment_duration_days = sum(completed_durations) / len(completed_durations)

    if most_recent_end:
        detail.most_recent_end_date = most_recent_end
        detail.days_since_last_assignment = (today - most_recent_end).days

    # Completion rate
    finishable = detail.completed_assignments + detail.cancelled_assignments
    if finishable > 0:
        detail.completion_rate = detail.completed_assignments / finishable
    elif detail.active_assignments > 0:
        detail.completion_rate = 1.0  # Actively assigned, no cancellations
    else:
        detail.completion_rate = 0.0

    # ── Score calculation ──
    # Count-based score (up to 40 points)
    count_score = min(40.0, detail.completed_assignments * 6.0 + detail.active_assignments * 4.0)

    # Completion rate bonus (up to 20 points)
    rate_score = detail.completion_rate * 20.0

    # Recency bonus (up to 20 points)
    recency_score = 0.0
    if detail.days_since_last_assignment is not None:
        if detail.days_since_last_assignment <= 30:
            recency_score = 20.0
        elif detail.days_since_last_assignment <= 90:
            recency_score = 15.0
        elif detail.days_since_last_assignment <= 180:
            recency_score = 10.0
        elif detail.days_since_last_assignment <= 365:
            recency_score = 5.0
    elif detail.active_assignments > 0:
        recency_score = 20.0  # Currently active

    # Diversity bonus (up to 20 points)
    diversity_score = min(20.0, detail.unique_projects * 4.0)

    result.score = min(100.0, count_score + rate_score + recency_score + diversity_score)
    result.details = detail

    # Summary
    parts = [f"{detail.total_assignments} total assignments"]
    if detail.completed_assignments > 0:
        parts.append(f"{detail.completed_assignments} completed")
    if detail.active_assignments > 0:
        parts.append(f"{detail.active_assignments} active")
    if detail.completion_rate > 0:
        parts.append(f"{detail.completion_rate:.0%} completion rate")
    if detail.days_since_last_assignment is not None:
        parts.append(f"last ended {detail.days_since_last_assignment}d ago")
    result.summary = "; ".join(parts)

    return result


def _evaluate_documentation_readiness(
    technician: Technician,
) -> DocumentationReadiness:
    """Evaluate documentation verification status.

    Scoring:
      - 100 if all docs verified (or no docs required)
      - Proportional score based on verified/total ratio
      - Penalty for explicitly missing docs
    """
    result = DocumentationReadiness()
    docs = technician.documents or []

    if not docs:
        # No docs tracked — rely on the docs_verified flag
        if technician.docs_verified:
            result.score = 100.0
            result.summary = "All documents verified"
        else:
            result.score = 50.0
            result.summary = "No documents tracked"
        return result

    result.total_docs = len(docs)

    for doc in docs:
        status = _enum_val(doc.verification_status)
        if status == VerificationStatus.VERIFIED.value:
            result.verified_docs += 1
        elif status == VerificationStatus.PENDING_REVIEW.value:
            result.pending_docs += 1
        else:
            result.missing_docs += 1

    if result.total_docs > 0:
        verified_ratio = result.verified_docs / result.total_docs
        pending_ratio = result.pending_docs / result.total_docs
        result.score = min(100.0, verified_ratio * 80 + pending_ratio * 20)
    else:
        result.score = 50.0

    # Summary
    parts = [f"{result.verified_docs}/{result.total_docs} verified"]
    if result.pending_docs > 0:
        parts.append(f"{result.pending_docs} pending")
    if result.missing_docs > 0:
        parts.append(f"{result.missing_docs} missing")
    result.summary = "; ".join(parts)

    return result


def _determine_suggested_status(
    technician: Technician,
    cert_readiness: CertificationReadiness,
    training_readiness: TrainingReadiness,
    assignment_history: AssignmentHistoryReadiness,
    doc_readiness: DocumentationReadiness,
    overall_score: float,
) -> tuple[str, bool, Optional[str]]:
    """Determine the suggested deployability status based on readiness scores.

    Returns:
        (suggested_status, should_change, reason)
    """
    current = _enum_val(technician.deployability_status)
    career = _enum_val(technician.career_stage)

    # Locked status — no changes
    if technician.deployability_locked:
        return current, False, None

    # ── Hard rules (deterministic status derivation) ──

    # Active assignments → Currently Assigned
    if assignment_history.details.active_assignments > 0:
        if current != DeployabilityStatus.CURRENTLY_ASSIGNED.value:
            return (
                DeployabilityStatus.CURRENTLY_ASSIGNED.value,
                True,
                f"Has {assignment_history.details.active_assignments} active assignment(s)",
            )
        return current, False, None

    # Expired/revoked certs → Missing Cert
    if cert_readiness.expired_certs > 0:
        if current != DeployabilityStatus.MISSING_CERT.value:
            return (
                DeployabilityStatus.MISSING_CERT.value,
                True,
                f"{cert_readiness.expired_certs} expired/revoked certification(s)",
            )
        return current, False, None

    # Missing docs → Missing Docs
    if doc_readiness.missing_docs > 0 or (doc_readiness.total_docs > 0 and doc_readiness.score < 30):
        if current != DeployabilityStatus.MISSING_DOCS.value:
            return (
                DeployabilityStatus.MISSING_DOCS.value,
                True,
                f"Documentation incomplete: {doc_readiness.summary}",
            )
        return current, False, None

    # In training career stage → In Training status
    if career in (CareerStage.SOURCED.value, CareerStage.SCREENED.value, CareerStage.IN_TRAINING.value):
        if current != DeployabilityStatus.IN_TRAINING.value:
            return (
                DeployabilityStatus.IN_TRAINING.value,
                True,
                f"Career stage is {career}",
            )
        return current, False, None

    # Pre-booked for upcoming assignment → Rolling Off Soon
    if assignment_history.details.pre_booked_assignments > 0:
        # They're ready but committed — keep as Ready Now or Rolling Off
        pass

    # High readiness + completed training → Ready Now
    if (
        career in (
            CareerStage.TRAINING_COMPLETED.value,
            CareerStage.AWAITING_ASSIGNMENT.value,
            CareerStage.DEPLOYED.value,
        )
        and overall_score >= 50
        and cert_readiness.expired_certs == 0
    ):
        if current != DeployabilityStatus.READY_NOW.value and current != DeployabilityStatus.ROLLING_OFF_SOON.value:
            return (
                DeployabilityStatus.READY_NOW.value,
                True,
                f"All requirements met (score: {overall_score:.0f}), career stage: {career}",
            )
        return current, False, None

    return current, False, None


# ── Main evaluation functions ──────────────────────────────────────────────

def evaluate_technician_readiness(
    session: Session,
    technician_id: str,
) -> ReadinessResult:
    """Evaluate comprehensive readiness for a single technician.

    This is the main entry point for readiness re-evaluation. It computes
    scores across all dimensions and suggests a deployability status.

    Args:
        session: Active SQLAlchemy session.
        technician_id: UUID string of the technician.

    Returns:
        ReadinessResult with full scoring breakdown.

    Raises:
        ValueError: If technician not found.
    """
    from datetime import datetime, timezone

    technician = session.get(Technician, technician_id)
    if not technician:
        raise ValueError(f"Technician {technician_id} not found")

    # Evaluate each dimension
    cert_readiness = _evaluate_certification_readiness(session, technician)
    training_readiness = _evaluate_training_readiness(session, technician)
    assignment_readiness = _evaluate_assignment_history(session, technician)
    doc_readiness = _evaluate_documentation_readiness(technician)

    # Compute weighted overall score
    dimension_scores = {
        "certification": cert_readiness.score,
        "training": training_readiness.score,
        "assignment_history": assignment_readiness.score,
        "documentation": doc_readiness.score,
    }

    overall = sum(
        dimension_scores[dim] * READINESS_WEIGHTS[dim]
        for dim in dimension_scores
    )

    # Determine suggested status
    suggested_status, should_change, change_reason = _determine_suggested_status(
        technician,
        cert_readiness,
        training_readiness,
        assignment_readiness,
        doc_readiness,
        overall,
    )

    result = ReadinessResult(
        technician_id=str(technician.id),
        technician_name=technician.full_name,
        overall_score=overall,
        current_status=_enum_val(technician.deployability_status),
        suggested_status=suggested_status,
        status_change_recommended=should_change,
        status_change_reason=change_reason,
        certification=cert_readiness,
        training=training_readiness,
        assignment_history=assignment_readiness,
        documentation=doc_readiness,
        dimension_scores=dimension_scores,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "Readiness evaluation for %s: overall=%.1f, cert=%.1f, training=%.1f, "
        "assignments=%.1f, docs=%.1f | current=%s suggested=%s change=%s",
        technician.full_name,
        overall,
        cert_readiness.score,
        training_readiness.score,
        assignment_readiness.score,
        doc_readiness.score,
        result.current_status,
        result.suggested_status,
        should_change,
    )

    return result


def evaluate_all_technicians_readiness(
    session: Session,
    only_active: bool = True,
) -> list[ReadinessResult]:
    """Evaluate readiness for all technicians (batch mode).

    Args:
        session: Active SQLAlchemy session.
        only_active: If True, skip inactive technicians.

    Returns:
        List of ReadinessResult for each technician evaluated.
    """
    query = session.query(Technician)
    if only_active:
        query = query.filter(
            Technician.deployability_status != DeployabilityStatus.INACTIVE
        )

    technicians = query.all()
    results = []

    for tech in technicians:
        try:
            result = evaluate_technician_readiness(session, str(tech.id))
            results.append(result)
        except Exception:
            logger.exception("Failed to evaluate readiness for technician %s", tech.id)

    logger.info("Batch readiness evaluation complete: %d technicians evaluated", len(results))
    return results


def apply_readiness_status_update(
    session: Session,
    technician_id: str,
    readiness_result: ReadinessResult,
) -> dict[str, Any]:
    """Apply the suggested status change from a readiness evaluation.

    This performs the actual state mutation. Should only be called
    after human approval (or from the deterministic training advancement path).

    Args:
        session: Active SQLAlchemy session.
        technician_id: UUID of the technician.
        readiness_result: The evaluation result to apply.

    Returns:
        Dict with old/new status and whether a change was made.
    """
    technician = session.get(Technician, technician_id)
    if not technician:
        raise ValueError(f"Technician {technician_id} not found")

    old_status = _enum_val(technician.deployability_status)
    new_status = readiness_result.suggested_status

    if not readiness_result.status_change_recommended or old_status == new_status:
        return {
            "technician_id": technician_id,
            "changed": False,
            "status": old_status,
            "reason": "No change recommended or already at suggested status",
        }

    # Map status string to enum
    status_map = {v.value: v for v in DeployabilityStatus}
    if new_status in status_map:
        technician.deployability_status = status_map[new_status]
        session.flush()

        logger.info(
            "Applied readiness status change for %s: %s -> %s (reason: %s)",
            technician.full_name,
            old_status,
            new_status,
            readiness_result.status_change_reason,
        )

        return {
            "technician_id": technician_id,
            "changed": True,
            "old_status": old_status,
            "new_status": new_status,
            "reason": readiness_result.status_change_reason,
        }

    return {
        "technician_id": technician_id,
        "changed": False,
        "status": old_status,
        "reason": f"Unknown suggested status: {new_status}",
    }
