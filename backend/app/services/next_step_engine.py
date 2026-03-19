"""Next-step and suggested-action recommendation engine.

Analyzes each technician's current state (career stage, certifications,
training progress, assignments, documents) and generates personalized
'next step' recommendations and actionable suggested actions.

This engine is deterministic — no LLM calls. It evaluates rules-based
logic to identify the highest-priority actions for each technician.

Used by:
  - Nightly Celery batch task (generate all)
  - Event-triggered updates (refresh single technician)
"""

import logging
from datetime import date, timedelta, datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.technician import (
    Technician,
    TechnicianSkill,
    TechnicianCertification,
    TechnicianDocument,
    CareerStage,
    DeployabilityStatus,
    CertStatus,
    ProficiencyLevel,
    VerificationStatus,
)
from app.models.training import TrainingEnrollment, EnrollmentStatus
from app.models.assignment import Assignment
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.recommendation import Recommendation, RecommendationStatus, RecommendationType
from app.models.audit import SuggestedAction
from app.models.certification import Certification

logger = logging.getLogger("deployable.next_step_engine")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Priority levels for next-step recommendations (lower = more urgent)
PRIORITY_CRITICAL = 1
PRIORITY_HIGH = 2
PRIORITY_MEDIUM = 3
PRIORITY_LOW = 4
PRIORITY_INFO = 5


# ---------------------------------------------------------------------------
# Next-step rule evaluators
# ---------------------------------------------------------------------------

def _check_expiring_certs(technician: Technician, today: date) -> list[dict[str, Any]]:
    """Check for certifications expiring within 60 days."""
    results = []
    for cert in technician.certifications:
        cert_status = cert.status.value if hasattr(cert.status, "value") else str(cert.status or "")
        if cert_status != CertStatus.ACTIVE.value:
            continue
        if cert.expiry_date:
            days_until = (cert.expiry_date - today).days
            if 0 < days_until <= 60:
                urgency = "critical" if days_until <= 14 else "high" if days_until <= 30 else "medium"
                priority = PRIORITY_CRITICAL if days_until <= 14 else PRIORITY_HIGH if days_until <= 30 else PRIORITY_MEDIUM
                results.append({
                    "action_key": f"renew_cert_{cert.cert_name}",
                    "category": "certification",
                    "title": f"Renew {cert.cert_name} certification",
                    "description": (
                        f"{cert.cert_name} expires in {days_until} days "
                        f"(on {cert.expiry_date.isoformat()}). Schedule renewal to maintain deployability."
                    ),
                    "priority": priority,
                    "urgency": urgency,
                    "link": f"/technicians/{technician.id}/certifications",
                    "metadata": {
                        "cert_name": cert.cert_name,
                        "expiry_date": cert.expiry_date.isoformat(),
                        "days_until_expiry": days_until,
                    },
                })
    return results


def _check_expired_certs(technician: Technician, today: date) -> list[dict[str, Any]]:
    """Check for expired certifications that need renewal."""
    results = []
    for cert in technician.certifications:
        cert_status = cert.status.value if hasattr(cert.status, "value") else str(cert.status or "")
        if cert_status == CertStatus.EXPIRED.value:
            results.append({
                "action_key": f"renew_expired_cert_{cert.cert_name}",
                "category": "certification",
                "title": f"Renew expired {cert.cert_name}",
                "description": (
                    f"{cert.cert_name} has expired"
                    f"{' on ' + cert.expiry_date.isoformat() if cert.expiry_date else ''}. "
                    f"Renewal required to regain full deployability."
                ),
                "priority": PRIORITY_CRITICAL,
                "urgency": "critical",
                "link": f"/technicians/{technician.id}/certifications",
                "metadata": {
                    "cert_name": cert.cert_name,
                    "expiry_date": cert.expiry_date.isoformat() if cert.expiry_date else None,
                },
            })
    return results


def _check_missing_documents(technician: Technician) -> list[dict[str, Any]]:
    """Check for unverified or missing documents."""
    results = []
    for doc in technician.documents:
        doc_status = doc.verification_status.value if hasattr(doc.verification_status, "value") else str(doc.verification_status or "")
        if doc_status in (VerificationStatus.NOT_SUBMITTED.value, VerificationStatus.EXPIRED.value):
            results.append({
                "action_key": f"submit_doc_{doc.doc_type}",
                "category": "documentation",
                "title": f"Submit {doc.doc_type} document",
                "description": (
                    f"{doc.doc_type} document is {doc_status.lower().replace('_', ' ')}. "
                    f"Submit to maintain readiness for deployment."
                ),
                "priority": PRIORITY_HIGH,
                "urgency": "high",
                "link": f"/technicians/{technician.id}/documents",
                "metadata": {
                    "doc_type": doc.doc_type,
                    "current_status": doc_status,
                },
            })
        elif doc_status == VerificationStatus.PENDING_REVIEW.value:
            results.append({
                "action_key": f"pending_doc_{doc.doc_type}",
                "category": "documentation",
                "title": f"{doc.doc_type} awaiting verification",
                "description": f"{doc.doc_type} is currently under review. No action needed.",
                "priority": PRIORITY_INFO,
                "urgency": "info",
                "link": f"/technicians/{technician.id}/documents",
                "metadata": {
                    "doc_type": doc.doc_type,
                    "current_status": doc_status,
                },
            })
    return results


def _check_training_progress(session: Session, technician: Technician) -> list[dict[str, Any]]:
    """Check active training enrollments and suggest next steps."""
    results = []
    cs_val = technician.career_stage.value if hasattr(technician.career_stage, "value") else str(technician.career_stage or "")

    # Active training enrollments
    enrollments = (
        session.query(TrainingEnrollment)
        .filter(
            TrainingEnrollment.technician_id == technician.id,
            TrainingEnrollment.status == EnrollmentStatus.ACTIVE.value,
        )
        .all()
    )

    for enrollment in enrollments:
        # Calculate progress using actual model fields
        hours_logged = enrollment.total_hours_logged or 0
        # Get required hours from the training program
        hours_required = (
            enrollment.program.total_hours_required
            if enrollment.program and enrollment.program.total_hours_required
            else 100
        )
        program_name = enrollment.program.name if enrollment.program else "Current program"
        progress_pct = min(100, int((hours_logged / max(hours_required, 1)) * 100))

        if progress_pct < 100:
            remaining = hours_required - hours_logged
            results.append({
                "action_key": f"continue_training_{enrollment.id}",
                "category": "training",
                "title": f"Continue training: {program_name}",
                "description": (
                    f"Training is {progress_pct}% complete ({hours_logged:.0f}/{hours_required:.0f} hours). "
                    f"{remaining:.0f} hours remaining to reach the next proficiency level."
                ),
                "priority": PRIORITY_MEDIUM,
                "urgency": "medium",
                "link": f"/technicians/{technician.id}/training",
                "metadata": {
                    "enrollment_id": str(enrollment.id),
                    "hours_completed": hours_logged,
                    "hours_required": hours_required,
                    "progress_pct": progress_pct,
                    "program_name": program_name,
                },
            })

    # If in training career stage but no active enrollments
    if cs_val in (CareerStage.SCREENED.value, CareerStage.IN_TRAINING.value) and not enrollments:
        results.append({
            "action_key": "enroll_training",
            "category": "training",
            "title": "Enroll in a training program",
            "description": (
                "No active training enrollments found. Enroll in a training program "
                "to advance toward deployment readiness."
            ),
            "priority": PRIORITY_HIGH,
            "urgency": "high",
            "link": f"/technicians/{technician.id}/training",
            "metadata": {"career_stage": cs_val},
        })

    return results


def _check_skill_advancement(technician: Technician) -> list[dict[str, Any]]:
    """Suggest skill advancement opportunities based on current proficiency."""
    results = []
    for skill in technician.skills:
        prof_level = skill.proficiency_level.value if hasattr(skill.proficiency_level, "value") else str(skill.proficiency_level or "")

        if prof_level == ProficiencyLevel.APPRENTICE.value:
            results.append({
                "action_key": f"advance_skill_{skill.skill_name}",
                "category": "skills",
                "title": f"Advance {skill.skill_name} to Intermediate",
                "description": (
                    f"Currently at Apprentice level in {skill.skill_name}. "
                    f"Log more hours and complete training to advance to Intermediate."
                ),
                "priority": PRIORITY_LOW,
                "urgency": "low",
                "link": f"/technicians/{technician.id}/skills",
                "metadata": {
                    "skill_name": skill.skill_name,
                    "current_level": prof_level,
                    "target_level": ProficiencyLevel.INTERMEDIATE.value,
                    "hours_accumulated": skill.training_hours_accumulated or 0,
                },
            })
        elif prof_level == ProficiencyLevel.INTERMEDIATE.value:
            results.append({
                "action_key": f"advance_skill_{skill.skill_name}",
                "category": "skills",
                "title": f"Advance {skill.skill_name} to Advanced",
                "description": (
                    f"Currently at Intermediate level in {skill.skill_name}. "
                    f"Continue building expertise to reach Advanced proficiency."
                ),
                "priority": PRIORITY_LOW,
                "urgency": "low",
                "link": f"/technicians/{technician.id}/skills",
                "metadata": {
                    "skill_name": skill.skill_name,
                    "current_level": prof_level,
                    "target_level": ProficiencyLevel.ADVANCED.value,
                    "hours_accumulated": skill.training_hours_accumulated or 0,
                },
            })

    return results


def _check_assignment_status(session: Session, technician: Technician, today: date) -> list[dict[str, Any]]:
    """Check current assignments and suggest next steps based on status."""
    results = []
    cs_val = technician.career_stage.value if hasattr(technician.career_stage, "value") else str(technician.career_stage or "")
    ds_val = technician.deployability_status.value if hasattr(technician.deployability_status, "value") else str(technician.deployability_status or "")

    # Check for rolling-off assignments
    active_assignments = (
        session.query(Assignment)
        .filter(
            Assignment.technician_id == technician.id,
            Assignment.status == "Active",
        )
        .all()
    )

    for assignment in active_assignments:
        if assignment.end_date:
            days_remaining = (assignment.end_date - today).days
            if 0 < days_remaining <= 30:
                results.append({
                    "action_key": f"rolling_off_{assignment.id}",
                    "category": "assignment",
                    "title": f"Assignment ending in {days_remaining} days",
                    "description": (
                        f"Current assignment ends on {assignment.end_date.isoformat()}. "
                        f"Prepare for transition and update availability."
                    ),
                    "priority": PRIORITY_HIGH,
                    "urgency": "high",
                    "link": f"/technicians/{technician.id}/assignments",
                    "metadata": {
                        "assignment_id": str(assignment.id),
                        "end_date": assignment.end_date.isoformat(),
                        "days_remaining": days_remaining,
                    },
                })

    # Ready but unassigned
    if ds_val == DeployabilityStatus.READY_NOW.value and not active_assignments:
        if cs_val in (
            CareerStage.TRAINING_COMPLETED.value,
            CareerStage.AWAITING_ASSIGNMENT.value,
            CareerStage.DEPLOYED.value,
        ):
            results.append({
                "action_key": "awaiting_assignment",
                "category": "assignment",
                "title": "Available for assignment",
                "description": (
                    "All requirements met — ready for deployment. "
                    "The staffing agent is looking for matching opportunities."
                ),
                "priority": PRIORITY_INFO,
                "urgency": "info",
                "link": f"/technicians/{technician.id}",
                "metadata": {
                    "deployability_status": ds_val,
                    "career_stage": cs_val,
                },
            })

    return results


def _check_availability_update(technician: Technician, today: date) -> list[dict[str, Any]]:
    """Check if availability date is in the past or needs updating."""
    results = []
    if technician.available_from and technician.available_from < today:
        # Availability date is in the past — may need updating
        days_past = (today - technician.available_from).days
        if days_past > 30:
            results.append({
                "action_key": "update_availability",
                "category": "profile",
                "title": "Update availability date",
                "description": (
                    f"Availability date ({technician.available_from.isoformat()}) "
                    f"is {days_past} days in the past. Update to reflect current availability."
                ),
                "priority": PRIORITY_LOW,
                "urgency": "low",
                "link": f"/technicians/{technician.id}/edit",
                "metadata": {
                    "available_from": technician.available_from.isoformat(),
                    "days_past": days_past,
                },
            })
    return results


# ---------------------------------------------------------------------------
# Ops suggested action generators
# ---------------------------------------------------------------------------

def _generate_ops_actions(session: Session, today: date) -> list[dict[str, Any]]:
    """Generate suggested actions for ops users based on system state."""
    results = []

    # 1. Technicians with expired certs
    expired_cert_techs = (
        session.query(Technician)
        .join(TechnicianCertification)
        .filter(TechnicianCertification.status == CertStatus.EXPIRED.value)
        .distinct()
        .count()
    )
    if expired_cert_techs > 0:
        results.append({
            "action_key": "review_expired_certs",
            "category": "certification",
            "title": f"Review {expired_cert_techs} technician(s) with expired certs",
            "description": (
                f"{expired_cert_techs} technician(s) have at least one expired certification. "
                f"Review and schedule renewals."
            ),
            "priority": PRIORITY_HIGH,
            "urgency": "high",
            "link": "/technicians?deployability_status=Missing+Cert",
            "target_role": "ops",
        })

    # 2. Pending headcount requests
    from app.models.audit import PendingHeadcountRequest, HeadcountRequestStatus
    pending_headcounts = (
        session.query(PendingHeadcountRequest)
        .filter(PendingHeadcountRequest.status == HeadcountRequestStatus.PENDING.value)
        .count()
    )
    if pending_headcounts > 0:
        results.append({
            "action_key": "review_headcount_requests",
            "category": "staffing",
            "title": f"Review {pending_headcounts} pending headcount request(s)",
            "description": (
                f"{pending_headcounts} headcount request(s) from partners await review."
            ),
            "priority": PRIORITY_HIGH,
            "urgency": "high",
            "link": "/headcount-requests?status=Pending",
            "target_role": "ops",
        })

    # 3. Pending recommendations
    pending_recs = (
        session.query(Recommendation)
        .filter(Recommendation.status == RecommendationStatus.PENDING.value)
        .count()
    )
    if pending_recs > 0:
        results.append({
            "action_key": "review_recommendations",
            "category": "staffing",
            "title": f"Review {pending_recs} pending recommendation(s)",
            "description": (
                f"The staffing agent has {pending_recs} recommendation(s) "
                f"waiting for approval or rejection."
            ),
            "priority": PRIORITY_MEDIUM,
            "urgency": "medium",
            "link": "/agent-inbox?status=Pending",
            "target_role": "ops",
        })

    # 4. Unfilled roles on active projects
    unfilled_roles = (
        session.query(ProjectRole)
        .join(Project)
        .filter(
            Project.status == ProjectStatus.ACTIVE.value,
            ProjectRole.filled < ProjectRole.quantity,
        )
        .count()
    )
    if unfilled_roles > 0:
        results.append({
            "action_key": "review_unfilled_roles",
            "category": "staffing",
            "title": f"{unfilled_roles} role(s) need staffing",
            "description": (
                f"{unfilled_roles} role(s) on active projects still have unfilled positions."
            ),
            "priority": PRIORITY_MEDIUM,
            "urgency": "medium",
            "link": "/projects?filter=unfilled",
            "target_role": "ops",
        })

    # 5. Technicians rolling off soon (within 30 days)
    rolling_off = (
        session.query(Technician)
        .filter(
            Technician.deployability_status == DeployabilityStatus.ROLLING_OFF_SOON.value,
        )
        .count()
    )
    if rolling_off > 0:
        results.append({
            "action_key": "review_rolling_off",
            "category": "assignment",
            "title": f"{rolling_off} technician(s) rolling off soon",
            "description": (
                f"{rolling_off} technician(s) have assignments ending within 30 days. "
                f"Plan next assignments or transitions."
            ),
            "priority": PRIORITY_MEDIUM,
            "urgency": "medium",
            "link": "/technicians?deployability_status=Rolling+Off+Soon",
            "target_role": "ops",
        })

    # 6. Technicians in training
    in_training = (
        session.query(Technician)
        .filter(
            Technician.deployability_status == DeployabilityStatus.IN_TRAINING.value,
        )
        .count()
    )
    if in_training > 0:
        results.append({
            "action_key": "monitor_training",
            "category": "training",
            "title": f"{in_training} technician(s) in training",
            "description": f"Monitor training progress for {in_training} technician(s).",
            "priority": PRIORITY_LOW,
            "urgency": "low",
            "link": "/technicians?deployability_status=In+Training",
            "target_role": "ops",
        })

    # 7. Proposed preference rules awaiting review
    from app.models.recommendation import PreferenceRule, PreferenceRuleStatus
    proposed_rules = (
        session.query(PreferenceRule)
        .filter(PreferenceRule.status == PreferenceRuleStatus.PROPOSED.value)
        .count()
    )
    if proposed_rules > 0:
        results.append({
            "action_key": "review_proposed_rules",
            "category": "preference",
            "title": f"Review {proposed_rules} proposed preference rule(s)",
            "description": (
                f"The rejection-learning agent has proposed {proposed_rules} new scoring rule(s) "
                f"based on recent rejections."
            ),
            "priority": PRIORITY_MEDIUM,
            "urgency": "medium",
            "link": "/settings/preference-rules?status=proposed",
            "target_role": "ops",
        })

    return results


# ---------------------------------------------------------------------------
# Main engine functions
# ---------------------------------------------------------------------------

def generate_next_steps_for_technician(
    session: Session,
    technician: Technician,
    today: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Generate all next-step recommendations for a single technician.

    Evaluates certification status, training progress, document completeness,
    assignment status, and career trajectory to produce a prioritized list
    of recommended next actions.

    Args:
        session: SQLAlchemy session
        technician: Technician ORM instance (with relationships loaded)
        today: Optional date override for testing

    Returns:
        List of recommendation dicts sorted by priority (most urgent first)
    """
    if today is None:
        today = date.today()

    all_steps = []

    # Run all rule evaluators
    all_steps.extend(_check_expired_certs(technician, today))
    all_steps.extend(_check_expiring_certs(technician, today))
    all_steps.extend(_check_missing_documents(technician))
    all_steps.extend(_check_training_progress(session, technician))
    all_steps.extend(_check_assignment_status(session, technician, today))
    all_steps.extend(_check_skill_advancement(technician))
    all_steps.extend(_check_availability_update(technician, today))

    # Add technician context to each step
    for step in all_steps:
        step["technician_id"] = str(technician.id)
        step["technician_name"] = technician.full_name
        step["generated_at"] = datetime.now(timezone.utc).isoformat()

    # Sort by priority (lower number = higher priority)
    all_steps.sort(key=lambda s: s.get("priority", 99))

    return all_steps


def generate_all_next_steps(
    session: Session,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Generate next-step recommendations for ALL technicians.

    Returns:
        Dict with stats and per-technician results
    """
    if today is None:
        today = date.today()

    technicians = session.query(Technician).all()

    stats = {
        "total_technicians": len(technicians),
        "technicians_with_steps": 0,
        "total_steps_generated": 0,
        "by_category": {},
        "by_urgency": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
    }
    results = {}

    for technician in technicians:
        steps = generate_next_steps_for_technician(session, technician, today)
        if steps:
            stats["technicians_with_steps"] += 1
            stats["total_steps_generated"] += len(steps)

            for step in steps:
                cat = step.get("category", "other")
                stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
                urg = step.get("urgency", "info")
                if urg in stats["by_urgency"]:
                    stats["by_urgency"][urg] += 1

        results[str(technician.id)] = steps

    return {"stats": stats, "results": results}


def generate_ops_suggested_actions(
    session: Session,
    today: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Generate suggested actions for ops dashboard.

    Returns:
        Sorted list of suggested action dicts
    """
    if today is None:
        today = date.today()

    actions = _generate_ops_actions(session, today)
    actions.sort(key=lambda a: a.get("priority", 99))
    return actions


def persist_next_step_recommendations(
    session: Session,
    technician_id: str,
    steps: list[dict[str, Any]],
    batch_id: Optional[str] = None,
) -> list[Recommendation]:
    """Persist next-step recommendations to the database.

    Supersedes any existing pending NEXT_STEP recommendations for this
    technician before creating new ones.

    Returns:
        List of created Recommendation ORM instances
    """
    # Supersede existing pending next-step recs for this technician
    existing = (
        session.query(Recommendation)
        .filter(
            Recommendation.technician_id == technician_id,
            Recommendation.recommendation_type == RecommendationType.NEXT_STEP.value,
            Recommendation.status == RecommendationStatus.PENDING.value,
        )
        .all()
    )
    for rec in existing:
        rec.status = RecommendationStatus.SUPERSEDED.value
        rec.updated_at = datetime.now(timezone.utc)

    # Create new recommendations
    created = []
    for rank_idx, step in enumerate(steps):
        rec = Recommendation(
            recommendation_type=RecommendationType.NEXT_STEP.value,
            target_entity_type="technician",
            target_entity_id=technician_id,
            technician_id=technician_id,
            rank=str(rank_idx + 1),
            overall_score=float(100 - step.get("priority", 5) * 15),  # Convert priority to score
            scorecard={
                "category": step.get("category"),
                "urgency": step.get("urgency"),
                "action_key": step.get("action_key"),
            },
            explanation=step.get("description", ""),
            status=RecommendationStatus.PENDING.value,
            agent_name="next_step_agent",
            batch_id=batch_id,
            metadata_={
                "title": step.get("title"),
                "category": step.get("category"),
                "urgency": step.get("urgency"),
                "link": step.get("link"),
                "action_key": step.get("action_key"),
                "step_metadata": step.get("metadata", {}),
            },
        )
        session.add(rec)
        created.append(rec)

    return created


def persist_ops_suggested_actions(
    session: Session,
    actions: list[dict[str, Any]],
) -> list[SuggestedAction]:
    """Persist ops suggested actions to the database.

    Clears existing agent-generated suggested actions before creating new ones.
    Preserves user-initiated actions (those with non-null target_user_id).
    """
    # Remove old agent-generated ops actions (keep user-specific ones)
    old_actions = (
        session.query(SuggestedAction)
        .filter(
            SuggestedAction.target_role == "ops",
            SuggestedAction.target_user_id.is_(None),
            SuggestedAction.action_type.in_([
                "review_expired_certs",
                "review_headcount_requests",
                "review_recommendations",
                "review_unfilled_roles",
                "review_rolling_off",
                "monitor_training",
                "review_proposed_rules",
            ]),
        )
        .all()
    )
    for action in old_actions:
        session.delete(action)

    # Create new suggested actions
    created = []
    for action_data in actions:
        sa = SuggestedAction(
            target_role=action_data.get("target_role", "ops"),
            action_type=action_data.get("action_key", "unknown"),
            title=action_data.get("title", ""),
            description=action_data.get("description", ""),
            link=action_data.get("link"),
            priority=action_data.get("priority", 5),
            metadata_={
                "category": action_data.get("category"),
                "urgency": action_data.get("urgency"),
                "generated_by": "next_step_engine",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        session.add(sa)
        created.append(sa)

    return created
