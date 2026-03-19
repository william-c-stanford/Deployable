"""Badge service — milestone computation and badge management.

Auto-generated milestone badges are computed from technician data
(hours, projects, certs, training, tenure) and persisted on demand.
Manual badges (site/client) are managed through explicit grant/revoke.
"""

from __future__ import annotations

import uuid
from datetime import datetime, date
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.technician import (
    Technician,
    TechnicianBadge,
    TechnicianCertification,
    TechnicianSkill,
    BadgeType,
    CertStatus,
)
from app.models.assignment import Assignment
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.training import TrainingEnrollment, EnrollmentStatus


# ---------------------------------------------------------------------------
# Milestone definitions
# ---------------------------------------------------------------------------

class MilestoneDefinition:
    """A milestone badge definition with evaluation logic."""

    def __init__(
        self,
        name: str,
        description: str,
        category: str,
        threshold_desc: str,
        evaluator,
    ):
        self.name = name
        self.description = description
        self.category = category
        self.threshold_desc = threshold_desc
        self.evaluator = evaluator  # callable(db, technician) -> bool


def _check_hours(threshold: float):
    """Return evaluator for total approved hours milestone."""
    def _eval(db: Session, tech: Technician) -> bool:
        total = db.query(func.coalesce(func.sum(Timesheet.hours), 0.0)).filter(
            Timesheet.technician_id == tech.id,
            Timesheet.status == TimesheetStatus.APPROVED,
        ).scalar()
        return float(total) >= threshold
    return _eval


def _check_projects(threshold: int):
    """Return evaluator for completed projects milestone."""
    def _eval(db: Session, tech: Technician) -> bool:
        count = db.query(func.count(Assignment.id.distinct())).filter(
            Assignment.technician_id == tech.id,
            Assignment.status == "Completed",
        ).scalar()
        return int(count) >= threshold
    return _eval


def _check_active_certs(threshold: int):
    """Return evaluator for active certifications milestone."""
    def _eval(db: Session, tech: Technician) -> bool:
        count = db.query(func.count(TechnicianCertification.id)).filter(
            TechnicianCertification.technician_id == tech.id,
            TechnicianCertification.status == CertStatus.ACTIVE,
        ).scalar()
        return int(count) >= threshold
    return _eval


def _check_training_completed(threshold: int):
    """Return evaluator for completed training programs milestone."""
    def _eval(db: Session, tech: Technician) -> bool:
        count = db.query(func.count(TrainingEnrollment.id)).filter(
            TrainingEnrollment.technician_id == tech.id,
            TrainingEnrollment.status == EnrollmentStatus.COMPLETED,
        ).scalar()
        return int(count) >= threshold
    return _eval


def _check_tenure_years(threshold: float):
    """Return evaluator for tenure milestone."""
    def _eval(db: Session, tech: Technician) -> bool:
        if not tech.hire_date:
            return False
        tenure_days = (date.today() - tech.hire_date).days
        return (tenure_days / 365.25) >= threshold
    return _eval


def _check_skills_advanced(threshold: int):
    """Return evaluator for advanced skills milestone."""
    def _eval(db: Session, tech: Technician) -> bool:
        from app.models.technician import ProficiencyLevel
        count = db.query(func.count(TechnicianSkill.id)).filter(
            TechnicianSkill.technician_id == tech.id,
            TechnicianSkill.proficiency_level == ProficiencyLevel.ADVANCED,
        ).scalar()
        return int(count) >= threshold
    return _eval


# The complete catalog of milestone badges
MILESTONE_CATALOG: List[MilestoneDefinition] = [
    # Hours milestones
    MilestoneDefinition(
        name="First 100 Hours",
        description="Logged 100+ approved field hours",
        category="hours",
        threshold_desc="100 approved hours",
        evaluator=_check_hours(100),
    ),
    MilestoneDefinition(
        name="500 Hour Veteran",
        description="Logged 500+ approved field hours",
        category="hours",
        threshold_desc="500 approved hours",
        evaluator=_check_hours(500),
    ),
    MilestoneDefinition(
        name="1000 Hour Club",
        description="Logged 1,000+ approved field hours — elite status",
        category="hours",
        threshold_desc="1,000 approved hours",
        evaluator=_check_hours(1000),
    ),
    MilestoneDefinition(
        name="5000 Hour Legend",
        description="Logged 5,000+ approved field hours — legendary contributor",
        category="hours",
        threshold_desc="5,000 approved hours",
        evaluator=_check_hours(5000),
    ),

    # Project milestones
    MilestoneDefinition(
        name="First Project Complete",
        description="Successfully completed first project assignment",
        category="projects",
        threshold_desc="1 completed project",
        evaluator=_check_projects(1),
    ),
    MilestoneDefinition(
        name="5 Projects Strong",
        description="Completed 5 project assignments",
        category="projects",
        threshold_desc="5 completed projects",
        evaluator=_check_projects(5),
    ),
    MilestoneDefinition(
        name="10 Project Pro",
        description="Completed 10 project assignments — seasoned professional",
        category="projects",
        threshold_desc="10 completed projects",
        evaluator=_check_projects(10),
    ),
    MilestoneDefinition(
        name="25 Project Master",
        description="Completed 25 project assignments — master deployer",
        category="projects",
        threshold_desc="25 completed projects",
        evaluator=_check_projects(25),
    ),

    # Certification milestones
    MilestoneDefinition(
        name="First Cert Earned",
        description="Earned first active industry certification",
        category="certifications",
        threshold_desc="1 active certification",
        evaluator=_check_active_certs(1),
    ),
    MilestoneDefinition(
        name="Triple Certified",
        description="Holds 3+ active industry certifications",
        category="certifications",
        threshold_desc="3 active certifications",
        evaluator=_check_active_certs(3),
    ),
    MilestoneDefinition(
        name="Certification Master",
        description="Holds 5+ active certifications — deeply credentialed",
        category="certifications",
        threshold_desc="5 active certifications",
        evaluator=_check_active_certs(5),
    ),

    # Training milestones
    MilestoneDefinition(
        name="Training Graduate",
        description="Completed first training program",
        category="training",
        threshold_desc="1 completed training program",
        evaluator=_check_training_completed(1),
    ),
    MilestoneDefinition(
        name="Lifelong Learner",
        description="Completed 3+ training programs",
        category="training",
        threshold_desc="3 completed training programs",
        evaluator=_check_training_completed(3),
    ),

    # Tenure milestones
    MilestoneDefinition(
        name="1 Year Anniversary",
        description="Has been with the organization for 1+ years",
        category="tenure",
        threshold_desc="1 year tenure",
        evaluator=_check_tenure_years(1),
    ),
    MilestoneDefinition(
        name="3 Year Veteran",
        description="3+ years of dedicated service",
        category="tenure",
        threshold_desc="3 years tenure",
        evaluator=_check_tenure_years(3),
    ),
    MilestoneDefinition(
        name="5 Year Stalwart",
        description="5+ years — cornerstone of the workforce",
        category="tenure",
        threshold_desc="5 years tenure",
        evaluator=_check_tenure_years(5),
    ),

    # Skill mastery milestones
    MilestoneDefinition(
        name="Advanced Specialist",
        description="Achieved Advanced proficiency in 1+ skills",
        category="skills",
        threshold_desc="1 skill at Advanced level",
        evaluator=_check_skills_advanced(1),
    ),
    MilestoneDefinition(
        name="Multi-Discipline Expert",
        description="Achieved Advanced proficiency in 3+ skills",
        category="skills",
        threshold_desc="3 skills at Advanced level",
        evaluator=_check_skills_advanced(3),
    ),
]


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

def list_badges(
    db: Session,
    technician_id: uuid.UUID,
    badge_type: Optional[BadgeType] = None,
) -> List[TechnicianBadge]:
    """List all badges for a technician with optional type filter."""
    query = db.query(TechnicianBadge).filter(
        TechnicianBadge.technician_id == technician_id,
    )
    if badge_type is not None:
        query = query.filter(TechnicianBadge.badge_type == badge_type)
    return query.order_by(TechnicianBadge.granted_at.desc()).all()


def grant_manual_badge(
    db: Session,
    technician_id: uuid.UUID,
    badge_type: BadgeType,
    badge_name: str,
    description: Optional[str] = None,
) -> TechnicianBadge:
    """Grant a manual (site/client) badge to a technician.

    Raises ValueError if badge_type is 'milestone' (milestones are auto-generated).
    Raises ValueError if an identical badge already exists.
    """
    if badge_type == BadgeType.MILESTONE:
        raise ValueError("Milestone badges are auto-generated and cannot be manually granted")

    # Check for duplicate
    existing = db.query(TechnicianBadge).filter(
        TechnicianBadge.technician_id == technician_id,
        TechnicianBadge.badge_type == badge_type,
        TechnicianBadge.badge_name == badge_name,
    ).first()
    if existing:
        raise ValueError(f"Badge '{badge_name}' of type '{badge_type.value}' already exists for this technician")

    badge = TechnicianBadge(
        technician_id=technician_id,
        badge_type=badge_type,
        badge_name=badge_name,
        description=description,
    )
    db.add(badge)
    db.flush()
    return badge


def revoke_badge(
    db: Session,
    technician_id: uuid.UUID,
    badge_id: uuid.UUID,
) -> TechnicianBadge:
    """Revoke (delete) a badge from a technician. Returns the badge before deletion.

    Raises ValueError if the badge is not found.
    """
    badge = db.query(TechnicianBadge).filter(
        TechnicianBadge.id == badge_id,
        TechnicianBadge.technician_id == technician_id,
    ).first()
    if not badge:
        raise ValueError("Badge not found")
    db.delete(badge)
    db.flush()
    return badge


def compute_milestone_badges(
    db: Session,
    technician: Technician,
) -> Tuple[List[dict], List[dict]]:
    """Compute milestone badges for a technician.

    Returns (earned, available) where each item has milestone definition info
    plus earned status and optional persisted badge_id.
    """
    # Get existing milestone badges for this technician
    existing_milestones = {
        b.badge_name: b
        for b in db.query(TechnicianBadge).filter(
            TechnicianBadge.technician_id == technician.id,
            TechnicianBadge.badge_type == BadgeType.MILESTONE,
        ).all()
    }

    earned = []
    available = []

    for milestone in MILESTONE_CATALOG:
        is_earned = milestone.evaluator(db, technician)
        existing_badge = existing_milestones.get(milestone.name)

        entry = {
            "badge_name": milestone.name,
            "description": milestone.description,
            "category": milestone.category,
            "threshold": milestone.threshold_desc,
            "earned": is_earned,
            "earned_at": existing_badge.granted_at if existing_badge else None,
            "badge_id": existing_badge.id if existing_badge else None,
        }

        if is_earned:
            earned.append(entry)
        else:
            available.append(entry)

    return earned, available


def sync_milestone_badges(
    db: Session,
    technician: Technician,
) -> List[TechnicianBadge]:
    """Evaluate and persist any newly-earned milestone badges.

    Called after events that may trigger new milestones (hours logged,
    projects completed, certs earned, etc.)

    Returns list of newly created badge records.
    """
    existing_names = {
        b.badge_name
        for b in db.query(TechnicianBadge).filter(
            TechnicianBadge.technician_id == technician.id,
            TechnicianBadge.badge_type == BadgeType.MILESTONE,
        ).all()
    }

    new_badges = []
    for milestone in MILESTONE_CATALOG:
        if milestone.name in existing_names:
            continue  # Already granted
        if milestone.evaluator(db, technician):
            badge = TechnicianBadge(
                technician_id=technician.id,
                badge_type=BadgeType.MILESTONE,
                badge_name=milestone.name,
                description=milestone.description,
            )
            db.add(badge)
            new_badges.append(badge)

    if new_badges:
        db.flush()

    return new_badges
