"""Project lifecycle service — validation logic for project closure.

Before a project can transition to 'Closed' status, we must verify:
1. No active assignments remain on any of the project's roles (HARD)
2. No open (Submitted/Flagged) timesheets exist for the project's assignments (HARD)
3. No unresolved escalations exist for the project's assignments (HARD)
4. No pending partner confirmations exist (HARD)
5. No unfilled roles exist (SOFT — warning only)
6. No pending staffing recommendations exist (SOFT — warning only)
7. No completed assignments missing skill breakdowns (SOFT — warning only)

Hard blockers must be resolved; soft blockers generate warnings that ops
can acknowledge and override.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.assignment import Assignment
from app.models.timesheet import Timesheet, TimesheetStatus
from app.models.assignment_confirmation import (
    AssignmentConfirmation,
    ConfirmationStatus,
    EscalationStatus,
)
from app.models.recommendation import Recommendation, RecommendationStatus
from app.models.skill_breakdown import SkillBreakdown


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ProjectClosureBlockedError(Exception):
    """Raised when a project cannot be closed due to blocking conditions."""

    def __init__(
        self,
        message: str,
        active_assignments: List[dict] | None = None,
        open_timesheets: List[dict] | None = None,
        unresolved_escalations: List[dict] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.active_assignments = active_assignments or []
        self.open_timesheets = open_timesheets or []
        self.unresolved_escalations = unresolved_escalations or []


class ProjectNotFoundError(Exception):
    """Raised when a project ID does not exist."""
    pass


class InvalidProjectStateError(Exception):
    """Raised when a project state transition is not valid."""
    pass


# ---------------------------------------------------------------------------
# Dataclass for closure check result
# ---------------------------------------------------------------------------

@dataclass
class ClosureCheckResult:
    """Result of a project closure pre-check.

    Hard blockers (can_close = False if any present):
    - active_assignments, open_timesheets, unresolved_escalations, pending_confirmations

    Soft blockers (warnings — can be overridden):
    - unfilled_roles, pending_recommendations, missing_skill_breakdowns
    """
    can_close: bool = True
    has_warnings: bool = False

    # Hard blockers
    active_assignments: List[dict] = field(default_factory=list)
    open_timesheets: List[dict] = field(default_factory=list)
    unresolved_escalations: List[dict] = field(default_factory=list)
    pending_confirmations: List[dict] = field(default_factory=list)

    # Soft blockers (warnings)
    unfilled_roles: List[dict] = field(default_factory=list)
    pending_recommendations: List[dict] = field(default_factory=list)
    missing_skill_breakdowns: List[dict] = field(default_factory=list)

    @property
    def hard_blocker_count(self) -> int:
        return (
            len(self.active_assignments)
            + len(self.open_timesheets)
            + len(self.unresolved_escalations)
            + len(self.pending_confirmations)
        )

    @property
    def soft_blocker_count(self) -> int:
        return (
            len(self.unfilled_roles)
            + len(self.pending_recommendations)
            + len(self.missing_skill_breakdowns)
        )

    @property
    def blocking_reasons(self) -> List[str]:
        reasons = []
        if self.active_assignments:
            reasons.append(
                f"{len(self.active_assignments)} active assignment(s) still on this project"
            )
        if self.open_timesheets:
            reasons.append(
                f"{len(self.open_timesheets)} open timesheet(s) pending review"
            )
        if self.unresolved_escalations:
            reasons.append(
                f"{len(self.unresolved_escalations)} unresolved escalation(s) require attention"
            )
        if self.pending_confirmations:
            reasons.append(
                f"{len(self.pending_confirmations)} pending partner confirmation(s)"
            )
        return reasons

    @property
    def warning_reasons(self) -> List[str]:
        warnings = []
        if self.unfilled_roles:
            total_open = sum(r.get("open_slots", 0) for r in self.unfilled_roles)
            warnings.append(
                f"{len(self.unfilled_roles)} role(s) with {total_open} total unfilled slot(s)"
            )
        if self.pending_recommendations:
            warnings.append(
                f"{len(self.pending_recommendations)} pending staffing recommendation(s) will be auto-dismissed"
            )
        if self.missing_skill_breakdowns:
            warnings.append(
                f"{len(self.missing_skill_breakdowns)} completed assignment(s) missing skill breakdown(s)"
            )
        return warnings


# ---------------------------------------------------------------------------
# Core validation functions
# ---------------------------------------------------------------------------

def get_project_role_ids(db: Session, project_id: uuid.UUID) -> List[uuid.UUID]:
    """Return all role IDs for a project."""
    roles = (
        db.query(ProjectRole.id)
        .filter(ProjectRole.project_id == project_id)
        .all()
    )
    return [r[0] for r in roles]


def check_active_assignments(
    db: Session, role_ids: List[uuid.UUID]
) -> List[dict]:
    """Find assignments in Active or Pre-Booked status for the given roles."""
    if not role_ids:
        return []

    active_statuses = {"Active", "Pre-Booked", "Pending Confirmation"}
    assignments = (
        db.query(Assignment)
        .filter(
            Assignment.role_id.in_(role_ids),
            Assignment.status.in_(active_statuses),
        )
        .all()
    )
    return [
        {
            "assignment_id": str(a.id),
            "technician_id": str(a.technician_id),
            "role_id": str(a.role_id),
            "status": a.status,
            "start_date": str(a.start_date) if a.start_date else None,
            "end_date": str(a.end_date) if a.end_date else None,
        }
        for a in assignments
    ]


def check_open_timesheets(
    db: Session, role_ids: List[uuid.UUID]
) -> List[dict]:
    """Find timesheets in Submitted or Flagged status for the given roles' assignments."""
    if not role_ids:
        return []

    # Get assignment IDs for these roles
    assignment_ids = (
        db.query(Assignment.id)
        .filter(Assignment.role_id.in_(role_ids))
        .all()
    )
    assignment_id_list = [a[0] for a in assignment_ids]
    if not assignment_id_list:
        return []

    open_statuses = [TimesheetStatus.SUBMITTED, TimesheetStatus.FLAGGED]
    timesheets = (
        db.query(Timesheet)
        .filter(
            Timesheet.assignment_id.in_(assignment_id_list),
            Timesheet.status.in_(open_statuses),
        )
        .all()
    )
    return [
        {
            "timesheet_id": str(t.id),
            "assignment_id": str(t.assignment_id),
            "technician_id": str(t.technician_id),
            "week_start": str(t.week_start) if t.week_start else None,
            "hours": t.hours,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
        }
        for t in timesheets
    ]


def check_unresolved_escalations(
    db: Session, role_ids: List[uuid.UUID]
) -> List[dict]:
    """Find unresolved escalations linked to assignments on the given roles."""
    if not role_ids:
        return []

    # Get assignment IDs for these roles
    assignment_ids = (
        db.query(Assignment.id)
        .filter(Assignment.role_id.in_(role_ids))
        .all()
    )
    assignment_id_list = [a[0] for a in assignment_ids]
    if not assignment_id_list:
        return []

    unresolved_statuses = [EscalationStatus.ESCALATED, EscalationStatus.OPS_REVIEWING]
    escalations = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.assignment_id.in_(assignment_id_list),
            AssignmentConfirmation.escalated.is_(True),
            AssignmentConfirmation.escalation_status.in_(unresolved_statuses),
        )
        .all()
    )
    return [
        {
            "escalation_id": str(e.id),
            "assignment_id": str(e.assignment_id),
            "partner_id": str(e.partner_id),
            "escalation_status": (
                e.escalation_status.value
                if hasattr(e.escalation_status, "value")
                else str(e.escalation_status)
            ),
            "escalated_at": str(e.escalated_at) if e.escalated_at else None,
            "hours_waiting": e.hours_waiting,
        }
        for e in escalations
    ]


# ---------------------------------------------------------------------------
# Additional validation checks (pending confirmations, soft blockers)
# ---------------------------------------------------------------------------

def check_pending_confirmations(
    db: Session, role_ids: List[uuid.UUID]
) -> List[dict]:
    """Find pending partner confirmations for the given roles' assignments."""
    if not role_ids:
        return []

    assignment_ids = (
        db.query(Assignment.id)
        .filter(Assignment.role_id.in_(role_ids))
        .all()
    )
    assignment_id_list = [a[0] for a in assignment_ids]
    if not assignment_id_list:
        return []

    pending = (
        db.query(AssignmentConfirmation)
        .filter(
            AssignmentConfirmation.assignment_id.in_(assignment_id_list),
            AssignmentConfirmation.status == ConfirmationStatus.PENDING,
        )
        .all()
    )
    return [
        {
            "confirmation_id": str(c.id),
            "assignment_id": str(c.assignment_id),
            "partner_id": str(c.partner_id),
            "confirmation_type": (
                c.confirmation_type.value
                if hasattr(c.confirmation_type, "value")
                else str(c.confirmation_type)
            ),
            "requested_date": str(c.requested_date) if c.requested_date else None,
            "hours_waiting": c.hours_waiting,
        }
        for c in pending
    ]


def check_unfilled_roles(
    db: Session, project_id: uuid.UUID
) -> List[dict]:
    """Find project roles with unfilled slots (soft blocker)."""
    roles = (
        db.query(ProjectRole)
        .filter(ProjectRole.project_id == project_id)
        .all()
    )
    unfilled = []
    for r in roles:
        if r.open_slots > 0:
            unfilled.append({
                "role_id": str(r.id),
                "role_name": r.role_name,
                "quantity": r.quantity,
                "filled": r.filled,
                "open_slots": r.open_slots,
            })
    return unfilled


def check_pending_recommendations(
    db: Session, role_ids: List[uuid.UUID]
) -> List[dict]:
    """Find pending staffing recommendations for the given roles (soft blocker)."""
    if not role_ids:
        return []

    role_id_strs = [str(rid) for rid in role_ids]
    pending = (
        db.query(Recommendation)
        .filter(
            Recommendation.role_id.in_(role_id_strs),
            Recommendation.status == RecommendationStatus.PENDING.value,
        )
        .all()
    )
    return [
        {
            "recommendation_id": str(r.id),
            "role_id": str(r.role_id),
            "technician_id": str(r.technician_id) if r.technician_id else None,
            "overall_score": r.overall_score,
            "status": r.status,
        }
        for r in pending
    ]


def check_missing_skill_breakdowns(
    db: Session, role_ids: List[uuid.UUID]
) -> List[dict]:
    """Find completed assignments missing skill breakdowns (soft blocker)."""
    if not role_ids:
        return []

    completed = (
        db.query(Assignment)
        .filter(
            Assignment.role_id.in_(role_ids),
            Assignment.status == "Completed",
        )
        .all()
    )

    missing = []
    for a in completed:
        breakdown = (
            db.query(SkillBreakdown)
            .filter(SkillBreakdown.assignment_id == a.id)
            .first()
        )
        if not breakdown:
            missing.append({
                "assignment_id": str(a.id),
                "technician_id": str(a.technician_id),
                "role_id": str(a.role_id),
                "start_date": str(a.start_date) if a.start_date else None,
                "end_date": str(a.end_date) if a.end_date else None,
            })
    return missing


def auto_dismiss_pending_recommendations(
    db: Session, role_ids: List[uuid.UUID]
) -> int:
    """Auto-dismiss pending recommendations when a project closes.

    This is deterministic cleanup triggered by explicit ops action (project close).
    Returns the count of dismissed recommendations.
    """
    if not role_ids:
        return 0

    role_id_strs = [str(rid) for rid in role_ids]
    pending = (
        db.query(Recommendation)
        .filter(
            Recommendation.role_id.in_(role_id_strs),
            Recommendation.status == RecommendationStatus.PENDING.value,
        )
        .all()
    )
    for rec in pending:
        rec.status = RecommendationStatus.DISMISSED.value
    db.flush()
    return len(pending)


# ---------------------------------------------------------------------------
# Resolution hints generator
# ---------------------------------------------------------------------------

def get_resolution_hints(result: ClosureCheckResult) -> List[str]:
    """Generate actionable resolution hints from a ClosureCheckResult.

    Returns deduplicated, ordered hints that guide ops through resolving
    all blocking issues.
    """
    hints: List[str] = []

    if result.active_assignments:
        count = len(result.active_assignments)
        hints.append(
            f"Complete or cancel {count} active assignment(s) on the Project Staffing page"
        )

    if result.open_timesheets:
        submitted = sum(1 for t in result.open_timesheets if t["status"] == "Submitted")
        flagged = sum(1 for t in result.open_timesheets if t["status"] == "Flagged")
        parts = []
        if submitted:
            parts.append(f"approve {submitted} pending")
        if flagged:
            parts.append(f"resolve {flagged} flagged")
        hints.append(
            f"Review timesheets: {' and '.join(parts)} timesheet(s) on the Hours & Timesheets page"
        )

    if result.unresolved_escalations:
        count = len(result.unresolved_escalations)
        hints.append(
            f"Resolve {count} escalated confirmation(s) on the Project Staffing page"
        )

    if result.pending_confirmations:
        count = len(result.pending_confirmations)
        hints.append(
            f"Await or escalate {count} pending partner confirmation(s)"
        )

    if result.unfilled_roles:
        total_open = sum(r.get("open_slots", 0) for r in result.unfilled_roles)
        hints.append(
            f"Consider filling or reducing quantity on {total_open} unfilled role slot(s) "
            f"(soft blocker — can override)"
        )

    if result.pending_recommendations:
        count = len(result.pending_recommendations)
        hints.append(
            f"Review or dismiss {count} pending staffing recommendation(s) "
            f"(soft blocker — auto-dismissed on close)"
        )

    if result.missing_skill_breakdowns:
        count = len(result.missing_skill_breakdowns)
        hints.append(
            f"Submit skill breakdowns for {count} completed assignment(s) "
            f"to preserve career passport data (soft blocker)"
        )

    return hints


# ---------------------------------------------------------------------------
# Composite closure check
# ---------------------------------------------------------------------------

def check_project_closure(db: Session, project_id: uuid.UUID) -> ClosureCheckResult:
    """Run all closure pre-checks and return a composite result.

    This is the main entry point for validation — used by both the API
    endpoint (dry-run check) and the actual close operation.

    Checks (HARD — must resolve):
    1. Active assignments
    2. Open timesheets (Submitted/Flagged)
    3. Unresolved escalations
    4. Pending partner confirmations

    Checks (SOFT — warnings):
    5. Unfilled role slots
    6. Pending staffing recommendations
    7. Missing skill breakdowns for completed assignments
    """
    role_ids = get_project_role_ids(db, project_id)

    # Hard blockers
    active = check_active_assignments(db, role_ids)
    timesheets = check_open_timesheets(db, role_ids)
    escalations = check_unresolved_escalations(db, role_ids)
    confirmations = check_pending_confirmations(db, role_ids)

    # Soft blockers
    unfilled = check_unfilled_roles(db, project_id)
    recommendations = check_pending_recommendations(db, role_ids)
    missing_breakdowns = check_missing_skill_breakdowns(db, role_ids)

    has_hard_blockers = bool(active or timesheets or escalations or confirmations)
    has_warnings = bool(unfilled or recommendations or missing_breakdowns)
    can_close = not has_hard_blockers

    return ClosureCheckResult(
        can_close=can_close,
        has_warnings=has_warnings,
        active_assignments=active,
        open_timesheets=timesheets,
        unresolved_escalations=escalations,
        pending_confirmations=confirmations,
        unfilled_roles=unfilled,
        pending_recommendations=recommendations,
        missing_skill_breakdowns=missing_breakdowns,
    )


# ---------------------------------------------------------------------------
# Project closure operation
# ---------------------------------------------------------------------------

def close_project(db: Session, project_id: uuid.UUID) -> Project:
    """Attempt to close a project after validating all blocking conditions.

    Args:
        db: SQLAlchemy session.
        project_id: UUID of the project to close.

    Returns:
        The updated Project instance with status = Closed.

    Raises:
        ProjectNotFoundError: If the project doesn't exist.
        InvalidProjectStateError: If the project is already closed or in draft.
        ProjectClosureBlockedError: If any blocking conditions exist.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ProjectNotFoundError(f"Project {project_id} not found")

    # Validate current state allows closure
    if project.status == ProjectStatus.CLOSED:
        raise InvalidProjectStateError("Project is already closed")
    if project.status == ProjectStatus.DRAFT:
        raise InvalidProjectStateError(
            "Cannot close a project in Draft status. It must be active first."
        )

    # Run all closure checks
    result = check_project_closure(db, project_id)

    if not result.can_close:
        reasons = "; ".join(result.blocking_reasons)
        raise ProjectClosureBlockedError(
            message=f"Cannot close project: {reasons}",
            active_assignments=result.active_assignments,
            open_timesheets=result.open_timesheets,
            unresolved_escalations=result.unresolved_escalations,
        )

    # All checks passed — close the project
    project.status = ProjectStatus.CLOSED
    db.flush()

    return project
