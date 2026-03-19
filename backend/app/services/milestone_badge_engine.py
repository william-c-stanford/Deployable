"""Milestone Badge Auto-Generation Engine.

Calculates milestone badges from coarse denormalized technician data:
  - total_approved_hours  (Technician.total_approved_hours)
  - total_project_count   (Technician.total_project_count)
  - distinct role types held (from Assignment → ProjectRole)

This uses the MilestoneBadge model (app.models.badge) which has:
  - milestone_type enum (hours_threshold, projects_completed, certs_earned, etc.)
  - threshold_value / actual_value for audit
  - tier (1=bronze, 2=silver, 3=gold)
  - icon

The engine is DETERMINISTIC — it reads coarse counters on the Technician
model and existing badge records, then returns a diff.  Actual DB writes
happen only via the `sync_milestone_badges` entry point.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.technician import (
    Technician,
    TechnicianCertification,
    TechnicianSkill,
    CertStatus,
    ProficiencyLevel,
)
from app.models.badge import MilestoneBadge, MilestoneType
from app.models.assignment import Assignment
from app.models.project import ProjectRole

logger = logging.getLogger("deployable.services.milestone_badge_engine")


# ---------------------------------------------------------------------------
# Threshold definitions — declarative milestone catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MilestoneThreshold:
    """A single milestone threshold that triggers badge auto-generation."""

    badge_name: str
    description: str
    milestone_type: MilestoneType
    threshold_value: float
    tier: int  # 1=bronze, 2=silver, 3=gold
    icon: str = "award"


# Hours-based milestones (read from Technician.total_approved_hours)
HOURS_MILESTONES: List[MilestoneThreshold] = [
    MilestoneThreshold(
        badge_name="First 100 Hours",
        description="Logged 100+ approved field hours",
        milestone_type=MilestoneType.HOURS_THRESHOLD,
        threshold_value=100,
        tier=1,
        icon="clock",
    ),
    MilestoneThreshold(
        badge_name="500 Hour Veteran",
        description="Logged 500+ approved field hours",
        milestone_type=MilestoneType.HOURS_THRESHOLD,
        threshold_value=500,
        tier=1,
        icon="clock",
    ),
    MilestoneThreshold(
        badge_name="1000 Hour Club",
        description="Logged 1,000+ approved field hours — elite status",
        milestone_type=MilestoneType.HOURS_THRESHOLD,
        threshold_value=1000,
        tier=2,
        icon="star",
    ),
    MilestoneThreshold(
        badge_name="5000 Hour Legend",
        description="Logged 5,000+ approved field hours — legendary contributor",
        milestone_type=MilestoneType.HOURS_THRESHOLD,
        threshold_value=5000,
        tier=3,
        icon="trophy",
    ),
    MilestoneThreshold(
        badge_name="10000 Hour Master",
        description="Logged 10,000+ approved field hours — master of the craft",
        milestone_type=MilestoneType.HOURS_THRESHOLD,
        threshold_value=10000,
        tier=3,
        icon="crown",
    ),
]

# Project-count milestones (read from Technician.total_project_count)
PROJECT_MILESTONES: List[MilestoneThreshold] = [
    MilestoneThreshold(
        badge_name="First Project Complete",
        description="Successfully completed first project assignment",
        milestone_type=MilestoneType.PROJECTS_COMPLETED,
        threshold_value=1,
        tier=1,
        icon="briefcase",
    ),
    MilestoneThreshold(
        badge_name="5 Projects Strong",
        description="Completed 5 project assignments",
        milestone_type=MilestoneType.PROJECTS_COMPLETED,
        threshold_value=5,
        tier=1,
        icon="briefcase",
    ),
    MilestoneThreshold(
        badge_name="10 Project Pro",
        description="Completed 10 project assignments — seasoned professional",
        milestone_type=MilestoneType.PROJECTS_COMPLETED,
        threshold_value=10,
        tier=2,
        icon="star",
    ),
    MilestoneThreshold(
        badge_name="25 Project Master",
        description="Completed 25 project assignments — master deployer",
        milestone_type=MilestoneType.PROJECTS_COMPLETED,
        threshold_value=25,
        tier=2,
        icon="trophy",
    ),
    MilestoneThreshold(
        badge_name="50 Project Legend",
        description="Completed 50 project assignments — legendary field tech",
        milestone_type=MilestoneType.PROJECTS_COMPLETED,
        threshold_value=50,
        tier=3,
        icon="crown",
    ),
    MilestoneThreshold(
        badge_name="100+ Projects",
        description="Completed 100+ project assignments — the century mark",
        milestone_type=MilestoneType.PROJECTS_COMPLETED,
        threshold_value=100,
        tier=3,
        icon="crown",
    ),
]

# Role-diversity milestones (count of distinct role_name values from assignments)
ROLE_DIVERSITY_MILESTONES: List[MilestoneThreshold] = [
    MilestoneThreshold(
        badge_name="Role Explorer",
        description="Held 2+ distinct role types across assignments",
        milestone_type=MilestoneType.TRAINING_COMPLETED,  # repurposed for role diversity
        threshold_value=2,
        tier=1,
        icon="compass",
    ),
    MilestoneThreshold(
        badge_name="Multi-Role Specialist",
        description="Held 4+ distinct role types — versatile field tech",
        milestone_type=MilestoneType.TRAINING_COMPLETED,
        threshold_value=4,
        tier=2,
        icon="layers",
    ),
    MilestoneThreshold(
        badge_name="Swiss Army Tech",
        description="Held 6+ distinct role types — can fill any position",
        milestone_type=MilestoneType.TRAINING_COMPLETED,
        threshold_value=6,
        tier=3,
        icon="shield",
    ),
]

# Certification-count milestones (from active certs on technician)
CERT_MILESTONES: List[MilestoneThreshold] = [
    MilestoneThreshold(
        badge_name="First Cert Earned",
        description="Earned first active industry certification",
        milestone_type=MilestoneType.CERTS_EARNED,
        threshold_value=1,
        tier=1,
        icon="file-check",
    ),
    MilestoneThreshold(
        badge_name="Triple Certified",
        description="Holds 3+ active industry certifications",
        milestone_type=MilestoneType.CERTS_EARNED,
        threshold_value=3,
        tier=2,
        icon="badge-check",
    ),
    MilestoneThreshold(
        badge_name="Certification Master",
        description="Holds 5+ active certifications — deeply credentialed",
        milestone_type=MilestoneType.CERTS_EARNED,
        threshold_value=5,
        tier=3,
        icon="medal",
    ),
]

# Full catalog combining all categories
MILESTONE_CATALOG: List[MilestoneThreshold] = (
    HOURS_MILESTONES
    + PROJECT_MILESTONES
    + ROLE_DIVERSITY_MILESTONES
    + CERT_MILESTONES
)


# ---------------------------------------------------------------------------
# Data classes for evaluation results
# ---------------------------------------------------------------------------

@dataclass
class BadgeEvaluation:
    """Result of evaluating one milestone threshold for a technician."""

    threshold: MilestoneThreshold
    actual_value: float
    is_earned: bool
    already_persisted: bool = False
    existing_badge_id: Optional[uuid.UUID] = None


@dataclass
class TechnicianMilestoneReport:
    """Full milestone evaluation report for one technician."""

    technician_id: uuid.UUID
    technician_name: str
    total_approved_hours: float
    total_project_count: int
    distinct_role_count: int
    active_cert_count: int
    evaluations: List[BadgeEvaluation] = field(default_factory=list)

    @property
    def newly_earned(self) -> List[BadgeEvaluation]:
        """Badges earned but not yet persisted."""
        return [e for e in self.evaluations if e.is_earned and not e.already_persisted]

    @property
    def all_earned(self) -> List[BadgeEvaluation]:
        """All earned badges (persisted or not)."""
        return [e for e in self.evaluations if e.is_earned]

    @property
    def not_yet_earned(self) -> List[BadgeEvaluation]:
        """Thresholds not yet met."""
        return [e for e in self.evaluations if not e.is_earned]


# ---------------------------------------------------------------------------
# Coarse data extraction helpers
# ---------------------------------------------------------------------------

def _get_coarse_hours(technician: Technician) -> float:
    """Read the denormalized total_approved_hours from the Technician model."""
    return float(technician.total_approved_hours or 0.0)


def _get_coarse_project_count(technician: Technician) -> int:
    """Read the denormalized total_project_count from the Technician model."""
    return int(technician.total_project_count or 0)


def _get_distinct_role_count(db: Session, technician_id: uuid.UUID) -> int:
    """Count distinct role_name values across all assignments for a technician.

    This is the only query we issue — role diversity can't be denormalized
    cheaply on the Technician model.
    """
    result = (
        db.query(func.count(func.distinct(ProjectRole.role_name)))
        .join(Assignment, Assignment.role_id == ProjectRole.id)
        .filter(Assignment.technician_id == technician_id)
        .scalar()
    )
    return int(result or 0)


def _get_active_cert_count(technician: Technician) -> int:
    """Count active certifications from loaded relationship data."""
    if not technician.certifications:
        return 0
    count = 0
    for cert in technician.certifications:
        status_val = cert.status.value if hasattr(cert.status, "value") else str(cert.status)
        if status_val == CertStatus.ACTIVE.value:
            count += 1
    return count


def _get_existing_milestone_badges(
    db: Session,
    technician_id: uuid.UUID,
) -> dict[str, MilestoneBadge]:
    """Load existing milestone badges keyed by badge_name."""
    badges = (
        db.query(MilestoneBadge)
        .filter(MilestoneBadge.technician_id == technician_id)
        .all()
    )
    return {b.badge_name: b for b in badges}


# ---------------------------------------------------------------------------
# Core evaluation logic
# ---------------------------------------------------------------------------

def _get_actual_value_for_threshold(
    threshold: MilestoneThreshold,
    hours: float,
    projects: int,
    roles: int,
    certs: int,
) -> float:
    """Map a milestone type to the actual technician value."""
    if threshold.milestone_type == MilestoneType.HOURS_THRESHOLD:
        return hours
    elif threshold.milestone_type == MilestoneType.PROJECTS_COMPLETED:
        return float(projects)
    elif threshold.milestone_type == MilestoneType.TRAINING_COMPLETED:
        # Repurposed for role diversity
        return float(roles)
    elif threshold.milestone_type == MilestoneType.CERTS_EARNED:
        return float(certs)
    return 0.0


def evaluate_milestones(
    db: Session,
    technician: Technician,
) -> TechnicianMilestoneReport:
    """Evaluate all milestone thresholds for a technician using coarse data.

    This is a pure READ operation — no state is mutated.

    Uses denormalized fields on Technician for hours and projects,
    counts distinct roles from assignments, and counts active certs.
    """
    tech_id = technician.id
    hours = _get_coarse_hours(technician)
    projects = _get_coarse_project_count(technician)
    roles = _get_distinct_role_count(db, tech_id)
    certs = _get_active_cert_count(technician)

    existing = _get_existing_milestone_badges(db, tech_id)

    report = TechnicianMilestoneReport(
        technician_id=tech_id,
        technician_name=technician.full_name,
        total_approved_hours=hours,
        total_project_count=projects,
        distinct_role_count=roles,
        active_cert_count=certs,
    )

    for threshold in MILESTONE_CATALOG:
        actual = _get_actual_value_for_threshold(
            threshold, hours, projects, roles, certs,
        )
        is_earned = actual >= threshold.threshold_value
        existing_badge = existing.get(threshold.badge_name)

        report.evaluations.append(
            BadgeEvaluation(
                threshold=threshold,
                actual_value=actual,
                is_earned=is_earned,
                already_persisted=existing_badge is not None,
                existing_badge_id=existing_badge.id if existing_badge else None,
            )
        )

    return report


def sync_milestone_badges(
    db: Session,
    technician: Technician,
) -> List[MilestoneBadge]:
    """Evaluate milestones and persist any newly earned badges.

    This is the main entry point for milestone badge auto-generation.
    It is DETERMINISTIC — badges are granted based on threshold comparison
    against coarse counters. No LLM or subjective logic involved.

    Returns the list of newly created MilestoneBadge records.
    """
    report = evaluate_milestones(db, technician)
    new_badges: List[MilestoneBadge] = []

    for eval_result in report.newly_earned:
        threshold = eval_result.threshold
        badge = MilestoneBadge(
            technician_id=technician.id,
            milestone_type=threshold.milestone_type,
            badge_name=threshold.badge_name,
            description=threshold.description,
            threshold_value=threshold.threshold_value,
            actual_value=eval_result.actual_value,
            icon=threshold.icon,
            tier=threshold.tier,
            granted_at=datetime.utcnow(),
        )
        db.add(badge)
        new_badges.append(badge)
        logger.info(
            "Milestone badge granted: %s -> %s (actual=%.1f, threshold=%.1f, tier=%d)",
            technician.full_name,
            threshold.badge_name,
            eval_result.actual_value,
            threshold.threshold_value,
            threshold.tier,
        )

    if new_badges:
        db.flush()

    return new_badges


def sync_all_technicians(
    db: Session,
    batch_size: int = 50,
) -> dict[str, List[MilestoneBadge]]:
    """Run milestone badge sync for all technicians (nightly batch).

    Returns a dict mapping technician names to their newly earned badges.
    """
    technicians = db.query(Technician).all()
    results: dict[str, List[MilestoneBadge]] = {}

    for tech in technicians:
        new_badges = sync_milestone_badges(db, tech)
        if new_badges:
            results[tech.full_name] = new_badges

    return results


def get_milestone_progress(
    db: Session,
    technician: Technician,
) -> List[dict]:
    """Get milestone progress for career passport / technician profile display.

    Returns a list of milestone info dicts with progress percentages,
    suitable for rendering progress bars and badge displays.
    """
    report = evaluate_milestones(db, technician)
    progress_items = []

    for eval_result in report.evaluations:
        threshold = eval_result.threshold
        actual = eval_result.actual_value
        target = threshold.threshold_value
        pct = min(100.0, (actual / target * 100.0)) if target > 0 else 0.0

        progress_items.append({
            "badge_name": threshold.badge_name,
            "description": threshold.description,
            "category": threshold.milestone_type.value,
            "tier": threshold.tier,
            "icon": threshold.icon,
            "threshold_value": target,
            "actual_value": actual,
            "progress_pct": round(pct, 1),
            "earned": eval_result.is_earned,
            "earned_at": (
                None  # filled below if persisted
            ),
            "badge_id": str(eval_result.existing_badge_id) if eval_result.existing_badge_id else None,
        })

    # Fill in earned_at from persisted badges
    existing = _get_existing_milestone_badges(db, technician.id)
    for item in progress_items:
        if item["badge_name"] in existing:
            item["earned_at"] = existing[item["badge_name"]].granted_at.isoformat()

    return progress_items
