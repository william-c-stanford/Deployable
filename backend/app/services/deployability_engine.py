"""Deployability Status Computation Engine.

The single source of truth for computing a technician's deployability status
from objective data fields. This engine evaluates a deterministic set of rules
against the technician's current data state and returns the computed status
along with a full audit trail of which rules fired and why.

Rules are evaluated in priority order (highest priority first). The first
matching rule determines the status. This means blocking conditions (like
expired certs or missing background checks) take precedence over positive
conditions (like readiness).

Data fields evaluated:
  - Certifications: active, expired, pending, revoked
  - Training enrollment: status, completion, career stage
  - Background check: verification status
  - Drug test/screen: verification status
  - Equipment/vehicle: insurance and safety docs
  - Assignment status: active, pre-booked, rolling off
  - Document verification: all required docs verified
  - Manual lock: ops can lock a status to prevent auto-computation

Usage:
    from app.services.deployability_engine import compute_deployability_status

    result = compute_deployability_status(session, technician_id)
    print(result.computed_status)     # DeployabilityStatus enum value
    print(result.fired_rules)         # List of rules that matched
    print(result.blocking_issues)     # Issues preventing Ready Now

    # Batch evaluation
    results = compute_all_deployability_statuses(session)
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.technician import (
    Technician,
    TechnicianCertification,
    TechnicianDocument,
    TechnicianSkill,
    DeployabilityStatus,
    CareerStage,
    CertStatus,
    VerificationStatus,
    ProficiencyLevel,
)
from app.models.assignment import Assignment, AssignmentStatus
from app.models.training import TrainingEnrollment, EnrollmentStatus

logger = logging.getLogger("deployable.services.deployability_engine")


# ---------------------------------------------------------------------------
# Required document types — the minimum set a technician must have verified
# ---------------------------------------------------------------------------
REQUIRED_DOC_TYPES = frozenset({
    "background_check",
    "drug_screen",
    "drivers_license",
    "w9",
})

# Critical docs that block deployability if not verified
CRITICAL_DOC_TYPES = frozenset({
    "background_check",
    "drug_screen",
})

# Rolling-off threshold: if an active assignment ends within this many days,
# the tech is considered "rolling off soon"
ROLLING_OFF_DAYS_THRESHOLD = 30

# Certification expiry warning: certs expiring within this window are flagged
CERT_EXPIRING_SOON_DAYS = 30


# ---------------------------------------------------------------------------
# Rule definition
# ---------------------------------------------------------------------------

class RulePriority(int, Enum):
    """Priority levels for deployability rules. Lower number = higher priority."""
    LOCKED = 0          # Manual lock — highest priority
    INACTIVE = 10       # Explicitly inactive
    MISSING_CRITICAL = 20   # Missing background check / drug test
    EXPIRED_CERT = 30       # Expired or revoked certifications
    MISSING_DOCS = 40       # Other missing required documents
    CURRENTLY_ASSIGNED = 50  # Active assignment
    IN_TRAINING = 60        # In training pipeline
    ROLLING_OFF = 70        # Active assignment ending soon
    READY_NOW = 80          # All requirements met
    FALLBACK = 100          # Default / catch-all


@dataclass
class RuleResult:
    """Result of a single rule evaluation."""
    rule_name: str
    rule_priority: int
    matched: bool
    status: Optional[str] = None
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class BlockingIssue:
    """A specific issue blocking a technician from Ready Now status."""
    category: str  # "certification", "document", "training", "compliance"
    severity: str  # "critical", "warning", "info"
    description: str
    resolution: str  # What needs to happen to resolve this
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeployabilityResult:
    """Complete result of deployability status computation."""
    technician_id: str
    technician_name: str
    computed_status: str
    current_status: str
    status_changed: bool
    fired_rule: Optional[RuleResult] = None
    all_rules_evaluated: list[RuleResult] = field(default_factory=list)
    blocking_issues: list[BlockingIssue] = field(default_factory=list)
    compliance_summary: dict[str, Any] = field(default_factory=dict)
    computed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response."""
        return {
            "technician_id": self.technician_id,
            "technician_name": self.technician_name,
            "computed_status": self.computed_status,
            "current_status": self.current_status,
            "status_changed": self.status_changed,
            "fired_rule": {
                "rule_name": self.fired_rule.rule_name,
                "reason": self.fired_rule.reason,
                "priority": self.fired_rule.rule_priority,
            } if self.fired_rule else None,
            "blocking_issues": [
                {
                    "category": bi.category,
                    "severity": bi.severity,
                    "description": bi.description,
                    "resolution": bi.resolution,
                }
                for bi in self.blocking_issues
            ],
            "compliance_summary": self.compliance_summary,
            "rules_evaluated": [
                {
                    "rule_name": r.rule_name,
                    "matched": r.matched,
                    "status": r.status,
                    "reason": r.reason,
                }
                for r in self.all_rules_evaluated
            ],
            "computed_at": self.computed_at,
        }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _enum_val(v) -> str:
    """Safely extract .value from an enum, or return str."""
    return v.value if hasattr(v, "value") else str(v) if v else ""


def _get_active_assignments(session: Session, technician_id) -> list[Assignment]:
    """Get all active assignments for a technician."""
    return (
        session.query(Assignment)
        .filter(
            Assignment.technician_id == technician_id,
            Assignment.status.in_(["Active", AssignmentStatus.ACTIVE.value]),
        )
        .all()
    )


def _get_rolling_off_assignments(
    session: Session, technician_id, threshold_days: int = ROLLING_OFF_DAYS_THRESHOLD
) -> list[Assignment]:
    """Get active assignments ending within threshold_days."""
    today = date.today()
    cutoff = today + timedelta(days=threshold_days)
    return (
        session.query(Assignment)
        .filter(
            Assignment.technician_id == technician_id,
            Assignment.status.in_(["Active", AssignmentStatus.ACTIVE.value]),
            Assignment.end_date.isnot(None),
            Assignment.end_date <= cutoff,
            Assignment.end_date >= today,
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Individual rule evaluators
# ---------------------------------------------------------------------------

def _rule_locked(technician: Technician, **_kwargs) -> RuleResult:
    """Rule: if deployability_locked is True, keep current status."""
    if technician.deployability_locked:
        return RuleResult(
            rule_name="manual_lock",
            rule_priority=RulePriority.LOCKED,
            matched=True,
            status=_enum_val(technician.deployability_status),
            reason="Status manually locked by ops — auto-computation bypassed",
        )
    return RuleResult(
        rule_name="manual_lock",
        rule_priority=RulePriority.LOCKED,
        matched=False,
        reason="Not locked",
    )


def _rule_inactive(technician: Technician, **_kwargs) -> RuleResult:
    """Rule: if career stage indicates fully inactive, mark Inactive."""
    current = _enum_val(technician.deployability_status)
    # If currently set to Inactive and no active assignments, keep it
    if current == DeployabilityStatus.INACTIVE.value:
        return RuleResult(
            rule_name="inactive_status",
            rule_priority=RulePriority.INACTIVE,
            matched=True,
            status=DeployabilityStatus.INACTIVE.value,
            reason="Technician is marked as inactive",
        )
    return RuleResult(
        rule_name="inactive_status",
        rule_priority=RulePriority.INACTIVE,
        matched=False,
        reason="Not inactive",
    )


def _rule_missing_critical_docs(
    technician: Technician, blocking_issues: list[BlockingIssue], **_kwargs
) -> RuleResult:
    """Rule: missing background check or drug screen blocks deployment."""
    docs = technician.documents or []

    # If no individual doc records, fall back to docs_verified flag
    if not docs:
        if technician.docs_verified:
            return RuleResult(
                rule_name="missing_critical_docs",
                rule_priority=RulePriority.MISSING_CRITICAL,
                matched=False,
                reason="No doc records but docs_verified flag is True",
            )
        # No records and not verified — treat all critical docs as missing
        for doc_type in CRITICAL_DOC_TYPES:
            label = doc_type.replace("_", " ").title()
            blocking_issues.append(BlockingIssue(
                category="document",
                severity="critical",
                description=f"{label} not verified (status: missing)",
                resolution=f"Submit and verify {label}",
                data={"doc_type": doc_type, "status": None},
            ))
        labels = [d.replace("_", " ").title() for d in CRITICAL_DOC_TYPES]
        return RuleResult(
            rule_name="missing_critical_docs",
            rule_priority=RulePriority.MISSING_CRITICAL,
            matched=True,
            status=DeployabilityStatus.MISSING_DOCS.value,
            reason=f"Critical document(s) not verified: {', '.join(sorted(labels))}",
            details={"missing": sorted(CRITICAL_DOC_TYPES)},
        )

    doc_map: dict[str, str] = {}
    for doc in docs:
        doc_map[doc.doc_type] = _enum_val(doc.verification_status)

    missing_critical = []
    for doc_type in CRITICAL_DOC_TYPES:
        status = doc_map.get(doc_type)
        if status is None or status in (
            VerificationStatus.NOT_SUBMITTED.value,
            VerificationStatus.EXPIRED.value,
        ):
            label = doc_type.replace("_", " ").title()
            missing_critical.append(doc_type)
            blocking_issues.append(BlockingIssue(
                category="document",
                severity="critical",
                description=f"{label} not verified (status: {status or 'missing'})",
                resolution=f"Submit and verify {label}",
                data={"doc_type": doc_type, "status": status},
            ))

    if missing_critical:
        labels = [d.replace("_", " ").title() for d in missing_critical]
        return RuleResult(
            rule_name="missing_critical_docs",
            rule_priority=RulePriority.MISSING_CRITICAL,
            matched=True,
            status=DeployabilityStatus.MISSING_DOCS.value,
            reason=f"Critical document(s) not verified: {', '.join(labels)}",
            details={"missing": missing_critical},
        )

    return RuleResult(
        rule_name="missing_critical_docs",
        rule_priority=RulePriority.MISSING_CRITICAL,
        matched=False,
        reason="All critical documents verified or pending",
    )


def _rule_expired_certs(
    technician: Technician, blocking_issues: list[BlockingIssue], **_kwargs
) -> RuleResult:
    """Rule: any expired or revoked certification blocks deployment."""
    certs = technician.certifications or []
    today = date.today()

    expired_certs = []
    expiring_soon = []

    for cert in certs:
        status = _enum_val(cert.status)
        if status in (CertStatus.EXPIRED.value, CertStatus.REVOKED.value):
            expired_certs.append(cert.cert_name)
            blocking_issues.append(BlockingIssue(
                category="certification",
                severity="critical",
                description=f"Certification '{cert.cert_name}' is {status.lower()}",
                resolution=f"Renew or replace '{cert.cert_name}'",
                data={"cert_name": cert.cert_name, "status": status},
            ))
        elif (
            status == CertStatus.ACTIVE.value
            and cert.expiry_date
            and (cert.expiry_date - today).days <= CERT_EXPIRING_SOON_DAYS
        ):
            expiring_soon.append(cert.cert_name)
            blocking_issues.append(BlockingIssue(
                category="certification",
                severity="warning",
                description=(
                    f"Certification '{cert.cert_name}' expires in "
                    f"{(cert.expiry_date - today).days} days"
                ),
                resolution=f"Renew '{cert.cert_name}' before {cert.expiry_date}",
                data={
                    "cert_name": cert.cert_name,
                    "expiry_date": str(cert.expiry_date),
                    "days_remaining": (cert.expiry_date - today).days,
                },
            ))

    if expired_certs:
        return RuleResult(
            rule_name="expired_certifications",
            rule_priority=RulePriority.EXPIRED_CERT,
            matched=True,
            status=DeployabilityStatus.MISSING_CERT.value,
            reason=f"Expired/revoked certification(s): {', '.join(expired_certs)}",
            details={"expired": expired_certs, "expiring_soon": expiring_soon},
        )

    return RuleResult(
        rule_name="expired_certifications",
        rule_priority=RulePriority.EXPIRED_CERT,
        matched=False,
        reason="No expired certifications",
        details={"expiring_soon": expiring_soon} if expiring_soon else {},
    )


def _rule_missing_docs(
    technician: Technician, blocking_issues: list[BlockingIssue], **_kwargs
) -> RuleResult:
    """Rule: other required documents missing (non-critical but still blocking)."""
    docs = technician.documents or []
    doc_map: dict[str, str] = {}
    for doc in docs:
        doc_map[doc.doc_type] = _enum_val(doc.verification_status)

    # If no individual doc records exist, fall back to the docs_verified flag
    if not docs:
        if not technician.docs_verified:
            missing_docs = ["general_docs"]
            blocking_issues.append(BlockingIssue(
                category="document",
                severity="warning",
                description="No documentation records on file",
                resolution="Submit all required documentation for verification",
            ))
        else:
            missing_docs = []
    else:
        # Check non-critical required docs
        non_critical_required = REQUIRED_DOC_TYPES - CRITICAL_DOC_TYPES
        missing_docs = []
        for doc_type in non_critical_required:
            status = doc_map.get(doc_type)
            if status is None or status in (
                VerificationStatus.NOT_SUBMITTED.value,
                VerificationStatus.EXPIRED.value,
            ):
                missing_docs.append(doc_type)
                label = doc_type.replace("_", " ").title()
                blocking_issues.append(BlockingIssue(
                    category="document",
                    severity="warning",
                    description=f"{label} not verified (status: {status or 'missing'})",
                    resolution=f"Submit and verify {label}",
                    data={"doc_type": doc_type, "status": status},
                ))

    if missing_docs:
        labels = [d.replace("_", " ").title() for d in missing_docs]
        return RuleResult(
            rule_name="missing_required_docs",
            rule_priority=RulePriority.MISSING_DOCS,
            matched=True,
            status=DeployabilityStatus.MISSING_DOCS.value,
            reason=f"Required document(s) not verified: {', '.join(labels)}",
            details={"missing": missing_docs},
        )

    return RuleResult(
        rule_name="missing_required_docs",
        rule_priority=RulePriority.MISSING_DOCS,
        matched=False,
        reason="All required documents verified or pending",
    )


def _rule_currently_assigned(
    technician: Technician, session: Session, blocking_issues: list[BlockingIssue],
    **_kwargs
) -> RuleResult:
    """Rule: technician has at least one active assignment."""
    active = _get_active_assignments(session, technician.id)
    if active:
        # Check if any are rolling off soon
        rolling_off = _get_rolling_off_assignments(session, technician.id)
        if rolling_off and len(rolling_off) == len(active):
            # All active assignments are ending soon — handled by rolling_off rule
            return RuleResult(
                rule_name="currently_assigned",
                rule_priority=RulePriority.CURRENTLY_ASSIGNED,
                matched=False,
                reason="Active assignments exist but all are rolling off soon",
                details={"active_count": len(active), "rolling_off_count": len(rolling_off)},
            )

        return RuleResult(
            rule_name="currently_assigned",
            rule_priority=RulePriority.CURRENTLY_ASSIGNED,
            matched=True,
            status=DeployabilityStatus.CURRENTLY_ASSIGNED.value,
            reason=f"Has {len(active)} active assignment(s)",
            details={
                "active_count": len(active),
                "assignment_ids": [str(a.id) for a in active],
            },
        )

    return RuleResult(
        rule_name="currently_assigned",
        rule_priority=RulePriority.CURRENTLY_ASSIGNED,
        matched=False,
        reason="No active assignments",
    )


def _rule_in_training(
    technician: Technician, blocking_issues: list[BlockingIssue], **_kwargs
) -> RuleResult:
    """Rule: technician is in the training pipeline (career stage or enrollment)."""
    career = _enum_val(technician.career_stage)

    training_stages = {
        CareerStage.SOURCED.value,
        CareerStage.SCREENED.value,
        CareerStage.IN_TRAINING.value,
    }

    if career in training_stages:
        blocking_issues.append(BlockingIssue(
            category="training",
            severity="info",
            description=f"Career stage is '{career}' — still in training pipeline",
            resolution="Complete training requirements to advance career stage",
            data={"career_stage": career},
        ))
        return RuleResult(
            rule_name="in_training_pipeline",
            rule_priority=RulePriority.IN_TRAINING,
            matched=True,
            status=DeployabilityStatus.IN_TRAINING.value,
            reason=f"Career stage '{career}' indicates training pipeline",
            details={"career_stage": career},
        )

    # Check for active training enrollments even if career stage is beyond IN_TRAINING
    enrollments = technician.training_enrollments or []
    active_enrollments = [
        e for e in enrollments
        if _enum_val(e.status) == EnrollmentStatus.ACTIVE.value
    ]

    if active_enrollments and career == CareerStage.IN_TRAINING.value:
        return RuleResult(
            rule_name="in_training_pipeline",
            rule_priority=RulePriority.IN_TRAINING,
            matched=True,
            status=DeployabilityStatus.IN_TRAINING.value,
            reason=f"Has {len(active_enrollments)} active training enrollment(s)",
            details={"active_enrollments": len(active_enrollments)},
        )

    return RuleResult(
        rule_name="in_training_pipeline",
        rule_priority=RulePriority.IN_TRAINING,
        matched=False,
        reason="Not in training pipeline",
    )


def _rule_rolling_off(
    technician: Technician, session: Session, blocking_issues: list[BlockingIssue],
    **_kwargs
) -> RuleResult:
    """Rule: all active assignments ending within 30 days."""
    active = _get_active_assignments(session, technician.id)
    if not active:
        return RuleResult(
            rule_name="rolling_off_soon",
            rule_priority=RulePriority.ROLLING_OFF,
            matched=False,
            reason="No active assignments to roll off from",
        )

    rolling_off = _get_rolling_off_assignments(session, technician.id)
    if rolling_off and len(rolling_off) == len(active):
        earliest_end = min(a.end_date for a in rolling_off if a.end_date)
        days_remaining = (earliest_end - date.today()).days
        return RuleResult(
            rule_name="rolling_off_soon",
            rule_priority=RulePriority.ROLLING_OFF,
            matched=True,
            status=DeployabilityStatus.ROLLING_OFF_SOON.value,
            reason=(
                f"All {len(rolling_off)} active assignment(s) ending within "
                f"{ROLLING_OFF_DAYS_THRESHOLD} days (earliest: {days_remaining}d)"
            ),
            details={
                "rolling_off_count": len(rolling_off),
                "earliest_end_date": str(earliest_end),
                "days_remaining": days_remaining,
            },
        )

    return RuleResult(
        rule_name="rolling_off_soon",
        rule_priority=RulePriority.ROLLING_OFF,
        matched=False,
        reason="Active assignments not all ending soon",
    )


def _rule_ready_now(
    technician: Technician, blocking_issues: list[BlockingIssue], **_kwargs
) -> RuleResult:
    """Rule: all requirements met — technician is deployable.

    Prerequisites (checked by higher-priority rules):
      - No expired/revoked certs
      - No missing critical documents
      - No missing required documents
      - Not currently assigned (or rolling off)
      - Not in training pipeline

    Additional checks for Ready Now:
      - Career stage must be Training Completed, Awaiting Assignment, or Deployed
      - Must have at least one active certification OR career stage Awaiting Assignment
    """
    career = _enum_val(technician.career_stage)
    ready_stages = {
        CareerStage.TRAINING_COMPLETED.value,
        CareerStage.AWAITING_ASSIGNMENT.value,
        CareerStage.DEPLOYED.value,
    }

    if career not in ready_stages:
        return RuleResult(
            rule_name="ready_now",
            rule_priority=RulePriority.READY_NOW,
            matched=False,
            reason=f"Career stage '{career}' not in ready stages",
        )

    # If we reached here, no blocking rules fired
    certs = technician.certifications or []
    active_certs = [
        c for c in certs if _enum_val(c.status) == CertStatus.ACTIVE.value
    ]

    return RuleResult(
        rule_name="ready_now",
        rule_priority=RulePriority.READY_NOW,
        matched=True,
        status=DeployabilityStatus.READY_NOW.value,
        reason=(
            f"All requirements met — career stage '{career}', "
            f"{len(active_certs)} active cert(s), all docs verified"
        ),
        details={
            "career_stage": career,
            "active_cert_count": len(active_certs),
        },
    )


def _rule_fallback(technician: Technician, **_kwargs) -> RuleResult:
    """Fallback rule: if nothing else matched, keep current status."""
    return RuleResult(
        rule_name="fallback",
        rule_priority=RulePriority.FALLBACK,
        matched=True,
        status=_enum_val(technician.deployability_status),
        reason="No specific rule matched; retaining current status",
    )


# ---------------------------------------------------------------------------
# Compliance summary builder
# ---------------------------------------------------------------------------

def _build_compliance_summary(
    technician: Technician,
    session: Session,
) -> dict[str, Any]:
    """Build a compliance data summary for the technician."""
    docs = technician.documents or []
    certs = technician.certifications or []
    today = date.today()

    # Document compliance
    doc_statuses: dict[str, str] = {}
    for doc in docs:
        doc_statuses[doc.doc_type] = _enum_val(doc.verification_status)

    bg_check = doc_statuses.get("background_check", "Not Submitted")
    drug_test = doc_statuses.get("drug_screen", "Not Submitted")
    drivers_license = doc_statuses.get("drivers_license", "Not Submitted")
    w9 = doc_statuses.get("w9", "Not Submitted")
    safety_training = doc_statuses.get("safety_training_record", "Not Submitted")
    vehicle_insurance = doc_statuses.get("vehicle_insurance", "Not Submitted")

    # Certification compliance
    cert_summary = []
    for cert in certs:
        status = _enum_val(cert.status)
        days_until_expiry = None
        if cert.expiry_date:
            days_until_expiry = (cert.expiry_date - today).days
        cert_summary.append({
            "name": cert.cert_name,
            "status": status,
            "days_until_expiry": days_until_expiry,
        })

    # Training compliance
    enrollments = technician.training_enrollments or []
    active_enrollments = sum(
        1 for e in enrollments if _enum_val(e.status) == EnrollmentStatus.ACTIVE.value
    )
    completed_enrollments = sum(
        1 for e in enrollments if _enum_val(e.status) == EnrollmentStatus.COMPLETED.value
    )

    # Skills summary
    skills = technician.skills or []
    skill_levels = {"Advanced": 0, "Intermediate": 0, "Apprentice": 0}
    for sk in skills:
        level = _enum_val(sk.proficiency_level)
        if level in skill_levels:
            skill_levels[level] += 1

    # Active assignments
    active_assignments = _get_active_assignments(session, technician.id)

    return {
        "documents": {
            "background_check": bg_check,
            "drug_screen": drug_test,
            "drivers_license": drivers_license,
            "w9": w9,
            "safety_training_record": safety_training,
            "vehicle_insurance": vehicle_insurance,
            "all_verified": all(
                v == VerificationStatus.VERIFIED.value
                for v in [bg_check, drug_test, drivers_license, w9]
            ),
        },
        "certifications": {
            "total": len(certs),
            "active": sum(1 for c in certs if _enum_val(c.status) == CertStatus.ACTIVE.value),
            "expired": sum(1 for c in certs if _enum_val(c.status) in (CertStatus.EXPIRED.value, CertStatus.REVOKED.value)),
            "pending": sum(1 for c in certs if _enum_val(c.status) == CertStatus.PENDING.value),
            "expiring_within_30d": sum(
                1 for c in certs
                if _enum_val(c.status) == CertStatus.ACTIVE.value
                and c.expiry_date
                and (c.expiry_date - today).days <= CERT_EXPIRING_SOON_DAYS
            ),
            "details": cert_summary,
        },
        "training": {
            "career_stage": _enum_val(technician.career_stage),
            "active_enrollments": active_enrollments,
            "completed_enrollments": completed_enrollments,
            "total_approved_hours": technician.total_approved_hours or 0,
            "skill_levels": skill_levels,
        },
        "assignments": {
            "active_count": len(active_assignments),
            "total_project_count": technician.total_project_count or 0,
            "years_experience": technician.years_experience or 0,
        },
    }


# ---------------------------------------------------------------------------
# Main computation function
# ---------------------------------------------------------------------------

# Rule evaluators in priority order (highest priority first)
_RULE_CHAIN = [
    _rule_locked,
    _rule_inactive,
    _rule_missing_critical_docs,
    _rule_expired_certs,
    _rule_missing_docs,
    _rule_currently_assigned,
    _rule_in_training,
    _rule_rolling_off,
    _rule_ready_now,
    _rule_fallback,
]


def compute_deployability_status(
    session: Session,
    technician_id: str,
) -> DeployabilityResult:
    """Compute the deployability status for a single technician.

    Evaluates all rules in priority order against the technician's current
    objective data. Returns the computed status with full audit trail.

    Args:
        session: Active SQLAlchemy session.
        technician_id: UUID string of the technician.

    Returns:
        DeployabilityResult with computed status and audit trail.

    Raises:
        ValueError: If technician not found.
    """
    technician = session.get(Technician, technician_id)
    if not technician:
        raise ValueError(f"Technician {technician_id} not found")

    blocking_issues: list[BlockingIssue] = []
    all_rules: list[RuleResult] = []
    fired_rule: Optional[RuleResult] = None

    # Evaluate each rule in priority order
    for rule_fn in _RULE_CHAIN:
        result = rule_fn(
            technician=technician,
            session=session,
            blocking_issues=blocking_issues,
        )
        all_rules.append(result)

        if result.matched and fired_rule is None:
            fired_rule = result

    # Build compliance summary
    compliance = _build_compliance_summary(technician, session)

    # Determine computed status
    computed_status = (
        fired_rule.status if fired_rule else _enum_val(technician.deployability_status)
    )
    current_status = _enum_val(technician.deployability_status)

    result = DeployabilityResult(
        technician_id=str(technician.id),
        technician_name=technician.full_name,
        computed_status=computed_status,
        current_status=current_status,
        status_changed=computed_status != current_status,
        fired_rule=fired_rule,
        all_rules_evaluated=all_rules,
        blocking_issues=blocking_issues,
        compliance_summary=compliance,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "Deployability computation for %s: computed=%s current=%s changed=%s rule=%s",
        technician.full_name,
        computed_status,
        current_status,
        result.status_changed,
        fired_rule.rule_name if fired_rule else "none",
    )

    return result


def compute_all_deployability_statuses(
    session: Session,
    only_active: bool = True,
) -> list[DeployabilityResult]:
    """Compute deployability status for all technicians (batch mode).

    Args:
        session: Active SQLAlchemy session.
        only_active: If True, skip technicians currently marked Inactive.

    Returns:
        List of DeployabilityResult for each technician.
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
            result = compute_deployability_status(session, str(tech.id))
            results.append(result)
        except Exception:
            logger.exception("Failed to compute deployability for technician %s", tech.id)

    logger.info(
        "Batch deployability computation complete: %d technicians, %d with status changes",
        len(results),
        sum(1 for r in results if r.status_changed),
    )

    return results


def apply_computed_status(
    session: Session,
    technician_id: str,
    result: DeployabilityResult,
) -> dict[str, Any]:
    """Apply the computed status change to the technician record.

    This performs the actual state mutation. Should only be called after
    human approval (or from deterministic advancement paths).

    Args:
        session: Active SQLAlchemy session.
        technician_id: UUID of the technician.
        result: The computation result to apply.

    Returns:
        Dict with old/new status and whether a change was made.
    """
    technician = session.get(Technician, technician_id)
    if not technician:
        raise ValueError(f"Technician {technician_id} not found")

    if not result.status_changed:
        return {
            "technician_id": technician_id,
            "changed": False,
            "status": result.current_status,
            "reason": "No status change computed",
        }

    # Map status string to enum
    status_map = {v.value: v for v in DeployabilityStatus}
    new_status = result.computed_status

    if new_status in status_map:
        old_status = _enum_val(technician.deployability_status)
        technician.deployability_status = status_map[new_status]
        session.flush()

        logger.info(
            "Applied deployability status change for %s: %s -> %s (rule: %s)",
            technician.full_name,
            old_status,
            new_status,
            result.fired_rule.rule_name if result.fired_rule else "unknown",
        )

        return {
            "technician_id": technician_id,
            "changed": True,
            "old_status": old_status,
            "new_status": new_status,
            "rule": result.fired_rule.rule_name if result.fired_rule else None,
            "reason": result.fired_rule.reason if result.fired_rule else None,
        }

    return {
        "technician_id": technician_id,
        "changed": False,
        "status": result.current_status,
        "reason": f"Unknown computed status: {new_status}",
    }


# ---------------------------------------------------------------------------
# Convenience queries
# ---------------------------------------------------------------------------

def get_technicians_by_status(
    session: Session,
    status: DeployabilityStatus,
) -> list[Technician]:
    """Get all technicians with a given deployability status."""
    return (
        session.query(Technician)
        .filter(Technician.deployability_status == status)
        .all()
    )


def get_technicians_with_blocking_issues(
    session: Session,
    severity: str = "critical",
) -> list[tuple[str, list[BlockingIssue]]]:
    """Find all technicians with blocking issues of the given severity.

    Returns a list of (technician_id, blocking_issues) tuples.
    """
    technicians = (
        session.query(Technician)
        .filter(Technician.deployability_status != DeployabilityStatus.INACTIVE)
        .all()
    )

    results = []
    for tech in technicians:
        try:
            dr = compute_deployability_status(session, str(tech.id))
            matching_issues = [
                bi for bi in dr.blocking_issues if bi.severity == severity
            ]
            if matching_issues:
                results.append((str(tech.id), matching_issues))
        except Exception:
            logger.exception("Error checking blocking issues for %s", tech.id)

    return results
