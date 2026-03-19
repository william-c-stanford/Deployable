"""90-Day Assignment Chaining Engine.

Core business logic for calculating assignment chains within a rolling
90-day window.  Provides:

  • Chain construction from active → pre-booked assignments per technician
  • Gap detection between consecutive assignments
  • Overlap resolution (flag / auto-split overlapping date ranges)
  • Continuity validation (chain integrity checks)
  • Forward-schedule assembly (full 90-day timeline for ops dashboard)

Design contract:
    All reads go through SQLAlchemy queries on the Assignment model.
    All writes go through FastAPI endpoints (human-approved).
    This module never mutates state autonomously — it returns analysis
    results that callers persist via the REST API layer.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.models.assignment import (
    Assignment,
    AssignmentStatus,
    AssignmentType,
    ChainPriority,
)
from app.models.project import Project, ProjectRole
from app.models.technician import Technician

logger = logging.getLogger("deployable.chaining")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROLLING_WINDOW_DAYS = 90
MAX_GAP_SEAMLESS_DAYS = 0       # 0 = back-to-back with no gap
MAX_GAP_ACCEPTABLE_DAYS = 7     # ≤ 7-day gap considered acceptable
MAX_GAP_WARNING_DAYS = 14       # > 14-day gap triggers a warning
DEFAULT_CHAIN_PRIORITY = ChainPriority.MEDIUM


# ---------------------------------------------------------------------------
# Enums & data classes for engine results (framework-free)
# ---------------------------------------------------------------------------

class GapSeverity(str, Enum):
    """Severity classification for gaps between assignments."""
    SEAMLESS = "seamless"       # 0 days
    ACCEPTABLE = "acceptable"   # 1–7 days
    WARNING = "warning"         # 8–14 days
    CRITICAL = "critical"       # >14 days


class OverlapType(str, Enum):
    """Classification for overlapping assignments."""
    FULL_OVERLAP = "full_overlap"       # One fully inside the other
    PARTIAL_OVERLAP = "partial_overlap" # Dates partially overlap
    SAME_DATES = "same_dates"           # Identical start+end


class ContinuityIssueType(str, Enum):
    """Types of continuity problems in a chain."""
    GAP = "gap"
    OVERLAP = "overlap"
    MISSING_END_DATE = "missing_end_date"
    BROKEN_LINK = "broken_link"         # prev/next pointers mismatch
    OUT_OF_ORDER = "out_of_order"       # chain_position vs dates mismatch
    CANCELLED_IN_CHAIN = "cancelled_in_chain"


@dataclass
class GapInfo:
    """Details about a gap between two consecutive assignments."""
    technician_id: str
    technician_name: str
    gap_start: date
    gap_end: date
    gap_days: int
    severity: GapSeverity
    previous_assignment_id: Optional[str] = None
    next_assignment_id: Optional[str] = None
    previous_project_name: Optional[str] = None
    next_project_name: Optional[str] = None


@dataclass
class OverlapInfo:
    """Details about an overlap between two assignments."""
    technician_id: str
    technician_name: str
    assignment_a_id: str
    assignment_b_id: str
    overlap_start: date
    overlap_end: date
    overlap_days: int
    overlap_type: OverlapType
    project_a_name: Optional[str] = None
    project_b_name: Optional[str] = None


@dataclass
class ContinuityIssue:
    """A continuity problem detected in a chain."""
    issue_type: ContinuityIssueType
    assignment_id: str
    chain_id: Optional[str] = None
    description: str = ""
    severity: str = "warning"  # "warning" | "error"


@dataclass
class ChainLink:
    """Resolved link in an assignment chain for output."""
    assignment_id: str
    technician_id: str
    technician_name: str
    role_id: str
    role_name: str
    project_id: str
    project_name: str
    start_date: date
    end_date: Optional[date]
    status: str
    assignment_type: str
    chain_position: int
    gap_days_to_next: Optional[int] = None
    gap_severity: Optional[GapSeverity] = None
    booking_confidence: Optional[float] = None
    is_forward_booked: bool = False
    confirmed_at: Optional[str] = None
    chain_notes: Optional[str] = None
    duration_days: Optional[int] = None


@dataclass
class ResolvedChain:
    """A fully resolved assignment chain for a technician."""
    chain_id: str
    technician_id: str
    technician_name: str
    chain_priority: str
    links: List[ChainLink]
    total_duration_days: int = 0
    total_gap_days: int = 0
    total_booked_days: int = 0
    is_valid: bool = True
    continuity_issues: List[ContinuityIssue] = field(default_factory=list)


@dataclass
class TechnicianTimeline:
    """A technician's full 90-day assignment timeline."""
    technician_id: str
    technician_name: str
    current_assignment: Optional[ChainLink] = None
    upcoming_assignments: List[ChainLink] = field(default_factory=list)
    chains: List[ResolvedChain] = field(default_factory=list)
    gaps: List[GapInfo] = field(default_factory=list)
    overlaps: List[OverlapInfo] = field(default_factory=list)
    available_from: Optional[date] = None
    total_booked_days: int = 0
    utilization_pct: float = 0.0


@dataclass
class ForwardSchedule:
    """Aggregate 90-day forward schedule across all technicians."""
    schedule_start: date
    schedule_end: date
    timelines: Dict[str, TechnicianTimeline]  # keyed by technician_id
    total_assignments: int = 0
    active_count: int = 0
    pre_booked_count: int = 0
    chained_count: int = 0
    all_gaps: List[GapInfo] = field(default_factory=list)
    all_overlaps: List[OverlapInfo] = field(default_factory=list)
    all_issues: List[ContinuityIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_gap(gap_days: int) -> GapSeverity:
    """Classify a gap by its duration."""
    if gap_days <= MAX_GAP_SEAMLESS_DAYS:
        return GapSeverity.SEAMLESS
    if gap_days <= MAX_GAP_ACCEPTABLE_DAYS:
        return GapSeverity.ACCEPTABLE
    if gap_days <= MAX_GAP_WARNING_DAYS:
        return GapSeverity.WARNING
    return GapSeverity.CRITICAL


def _assignment_to_chain_link(
    assignment: Assignment,
    position: int,
) -> ChainLink:
    """Convert an Assignment ORM object to a ChainLink data class."""
    role = assignment.role
    project = role.project if role else None

    duration = None
    if assignment.start_date and assignment.end_date:
        duration = (assignment.end_date - assignment.start_date).days

    status_val = assignment.status
    if hasattr(status_val, "value"):
        status_val = status_val.value

    type_val = assignment.assignment_type
    if hasattr(type_val, "value"):
        type_val = type_val.value

    return ChainLink(
        assignment_id=str(assignment.id),
        technician_id=str(assignment.technician_id),
        technician_name=assignment.technician.full_name if assignment.technician else "Unknown",
        role_id=str(assignment.role_id),
        role_name=role.role_name if role else "Unknown",
        project_id=str(project.id) if project else "",
        project_name=project.name if project else "Unknown",
        start_date=assignment.start_date,
        end_date=assignment.end_date,
        status=status_val,
        assignment_type=type_val,
        chain_position=position,
        booking_confidence=assignment.booking_confidence,
        is_forward_booked=bool(assignment.is_forward_booked),
        confirmed_at=assignment.confirmed_at.isoformat() if assignment.confirmed_at else None,
        chain_notes=assignment.chain_notes,
        duration_days=duration,
    )


def _get_project_name_for_assignment(assignment: Assignment) -> str:
    """Safely extract project name from an assignment."""
    try:
        if assignment.role and assignment.role.project:
            return assignment.role.project.name
    except Exception:
        pass
    return "Unknown"


# ---------------------------------------------------------------------------
# Core queries
# ---------------------------------------------------------------------------

def _fetch_assignments_in_window(
    db: Session,
    technician_id: Optional[str] = None,
    window_start: Optional[date] = None,
    window_end: Optional[date] = None,
    include_cancelled: bool = False,
) -> List[Assignment]:
    """Fetch assignments within the 90-day rolling window.

    Includes:
      • Active assignments overlapping the window
      • Pre-booked assignments starting within the window
      • Completed assignments that ended within the window (for gap context)

    Excludes cancelled unless include_cancelled=True.
    """
    if window_start is None:
        window_start = date.today()
    if window_end is None:
        window_end = window_start + timedelta(days=ROLLING_WINDOW_DAYS)

    query = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
    )

    if technician_id:
        query = query.filter(Assignment.technician_id == technician_id)

    # Assignments that overlap with [window_start, window_end]:
    #   start_date <= window_end AND (end_date >= window_start OR end_date IS NULL)
    query = query.filter(
        Assignment.start_date <= window_end,
        or_(
            Assignment.end_date >= window_start,
            Assignment.end_date.is_(None),
        ),
    )

    if not include_cancelled:
        cancelled_statuses = [
            AssignmentStatus.CANCELLED.value,
            "Cancelled",
        ]
        query = query.filter(~Assignment.status.in_(cancelled_statuses))

    return query.order_by(Assignment.start_date.asc()).all()


def _fetch_chain_assignments(
    db: Session,
    chain_id: str,
) -> List[Assignment]:
    """Fetch all assignments belonging to a specific chain."""
    return (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(Assignment.chain_id == chain_id)
        .order_by(Assignment.chain_position.asc(), Assignment.start_date.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Gap Detection
# ---------------------------------------------------------------------------

def detect_gaps(
    assignments: List[Assignment],
    technician_name: str = "Unknown",
) -> List[GapInfo]:
    """Detect gaps between consecutive assignments for a single technician.

    Assignments must be pre-sorted by start_date ascending.
    Only considers non-cancelled assignments with defined end dates.
    """
    gaps: List[GapInfo] = []
    # Filter to assignments with end dates, sorted by start
    valid = [
        a for a in assignments
        if a.end_date is not None
        and a.status not in (AssignmentStatus.CANCELLED.value, "Cancelled")
    ]
    valid.sort(key=lambda a: a.start_date)

    for i in range(len(valid) - 1):
        current = valid[i]
        next_a = valid[i + 1]

        # Gap = next start - current end (in days)
        # If next starts the day after current ends, gap = 0 (seamless)
        gap_days = (next_a.start_date - current.end_date).days - 1
        # A gap_days < 0 means overlap (handled separately)
        if gap_days < 0:
            continue

        gap_start = current.end_date + timedelta(days=1)
        gap_end = next_a.start_date - timedelta(days=1)

        severity = _classify_gap(gap_days)

        gaps.append(GapInfo(
            technician_id=str(current.technician_id),
            technician_name=technician_name,
            gap_start=gap_start,
            gap_end=gap_end if gap_days > 0 else gap_start,
            gap_days=gap_days,
            severity=severity,
            previous_assignment_id=str(current.id),
            next_assignment_id=str(next_a.id),
            previous_project_name=_get_project_name_for_assignment(current),
            next_project_name=_get_project_name_for_assignment(next_a),
        ))

    return gaps


# ---------------------------------------------------------------------------
# Overlap Detection & Resolution
# ---------------------------------------------------------------------------

def detect_overlaps(
    assignments: List[Assignment],
    technician_name: str = "Unknown",
) -> List[OverlapInfo]:
    """Detect date-range overlaps between assignments for a single technician.

    Compares every pair of non-cancelled assignments.
    """
    overlaps: List[OverlapInfo] = []

    active = [
        a for a in assignments
        if a.status not in (AssignmentStatus.CANCELLED.value, "Cancelled")
    ]
    active.sort(key=lambda a: a.start_date)

    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a = active[i]
            b = active[j]

            a_end = a.end_date or (a.start_date + timedelta(days=ROLLING_WINDOW_DAYS))
            b_end = b.end_date or (b.start_date + timedelta(days=ROLLING_WINDOW_DAYS))

            overlap_start = max(a.start_date, b.start_date)
            overlap_end = min(a_end, b_end)

            if overlap_start <= overlap_end:
                overlap_days = (overlap_end - overlap_start).days + 1

                # Classify overlap type
                if a.start_date == b.start_date and a_end == b_end:
                    otype = OverlapType.SAME_DATES
                elif (a.start_date <= b.start_date and a_end >= b_end):
                    otype = OverlapType.FULL_OVERLAP
                elif (b.start_date <= a.start_date and b_end >= a_end):
                    otype = OverlapType.FULL_OVERLAP
                else:
                    otype = OverlapType.PARTIAL_OVERLAP

                overlaps.append(OverlapInfo(
                    technician_id=str(a.technician_id),
                    technician_name=technician_name,
                    assignment_a_id=str(a.id),
                    assignment_b_id=str(b.id),
                    overlap_start=overlap_start,
                    overlap_end=overlap_end,
                    overlap_days=overlap_days,
                    overlap_type=otype,
                    project_a_name=_get_project_name_for_assignment(a),
                    project_b_name=_get_project_name_for_assignment(b),
                ))

    return overlaps


def propose_overlap_resolution(
    overlap: OverlapInfo,
    assignments: List[Assignment],
) -> Dict:
    """Propose a resolution for an overlap — returns a suggestion dict.

    Does NOT mutate state. The caller (API endpoint) presents the
    suggestion to ops for human approval.
    """
    a = next((x for x in assignments if str(x.id) == overlap.assignment_a_id), None)
    b = next((x for x in assignments if str(x.id) == overlap.assignment_b_id), None)

    if not a or not b:
        return {"action": "manual_review", "reason": "Could not find overlapping assignments"}

    # Heuristic resolution strategies:
    # 1. If one is pre-booked and one is active → suggest adjusting pre-booked start
    # 2. If both pre-booked → suggest adjusting the later one
    # 3. If both active → flag for manual resolution

    a_type = a.assignment_type
    b_type = b.assignment_type
    if hasattr(a_type, "value"):
        a_type = a_type.value
    if hasattr(b_type, "value"):
        b_type = b_type.value

    if a_type == AssignmentType.ACTIVE.value and b_type == AssignmentType.PRE_BOOKED.value:
        # Suggest pushing pre-booked start to day after active ends
        new_start = a.end_date + timedelta(days=1) if a.end_date else None
        return {
            "action": "adjust_start",
            "target_assignment_id": str(b.id),
            "proposed_start_date": new_start.isoformat() if new_start else None,
            "reason": f"Push pre-booked assignment start to {new_start} (day after active ends)",
        }
    elif a_type == AssignmentType.PRE_BOOKED.value and b_type == AssignmentType.ACTIVE.value:
        new_end = b.start_date - timedelta(days=1)
        return {
            "action": "adjust_end",
            "target_assignment_id": str(a.id),
            "proposed_end_date": new_end.isoformat(),
            "reason": f"Shorten pre-booked assignment to end {new_end} (day before active starts)",
        }
    elif a_type == AssignmentType.PRE_BOOKED.value and b_type == AssignmentType.PRE_BOOKED.value:
        # Adjust the later one
        new_start = a.end_date + timedelta(days=1) if a.end_date else None
        return {
            "action": "adjust_start",
            "target_assignment_id": str(b.id),
            "proposed_start_date": new_start.isoformat() if new_start else None,
            "reason": f"Push later pre-booked assignment start to {new_start}",
        }
    else:
        return {
            "action": "manual_review",
            "reason": (
                f"Both assignments are active — overlap of {overlap.overlap_days} days "
                f"between {overlap.project_a_name} and {overlap.project_b_name} "
                f"requires manual resolution"
            ),
        }


# ---------------------------------------------------------------------------
# Continuity Validation
# ---------------------------------------------------------------------------

def validate_chain_continuity(
    chain_assignments: List[Assignment],
    chain_id: str,
) -> List[ContinuityIssue]:
    """Validate the integrity of an assignment chain.

    Checks:
      1. chain_position ordering matches chronological date ordering
      2. prev/next assignment pointers are consistent
      3. No cancelled assignments in an otherwise active chain
      4. End dates exist for all links except possibly the last
      5. No gaps > warning threshold within the chain
    """
    issues: List[ContinuityIssue] = []

    if not chain_assignments:
        return issues

    sorted_by_pos = sorted(
        chain_assignments,
        key=lambda a: (a.chain_position or 0, a.start_date),
    )

    for i, assignment in enumerate(sorted_by_pos):
        aid = str(assignment.id)

        # Check 1: Position vs date ordering
        if i > 0:
            prev = sorted_by_pos[i - 1]
            if assignment.start_date < prev.start_date:
                issues.append(ContinuityIssue(
                    issue_type=ContinuityIssueType.OUT_OF_ORDER,
                    assignment_id=aid,
                    chain_id=chain_id,
                    description=(
                        f"Position {assignment.chain_position} starts "
                        f"{assignment.start_date} before position "
                        f"{prev.chain_position} starts {prev.start_date}"
                    ),
                    severity="error",
                ))

        # Check 2: Previous/next pointer consistency
        if i > 0:
            prev = sorted_by_pos[i - 1]
            if assignment.previous_assignment_id and str(assignment.previous_assignment_id) != str(prev.id):
                issues.append(ContinuityIssue(
                    issue_type=ContinuityIssueType.BROKEN_LINK,
                    assignment_id=aid,
                    chain_id=chain_id,
                    description=(
                        f"previous_assignment_id points to "
                        f"{assignment.previous_assignment_id} but expected {prev.id}"
                    ),
                    severity="warning",
                ))

        if i < len(sorted_by_pos) - 1:
            next_a = sorted_by_pos[i + 1]
            if assignment.next_assignment_id and str(assignment.next_assignment_id) != str(next_a.id):
                issues.append(ContinuityIssue(
                    issue_type=ContinuityIssueType.BROKEN_LINK,
                    assignment_id=aid,
                    chain_id=chain_id,
                    description=(
                        f"next_assignment_id points to "
                        f"{assignment.next_assignment_id} but expected {next_a.id}"
                    ),
                    severity="warning",
                ))

        # Check 3: Cancelled in chain
        status = assignment.status
        if hasattr(status, "value"):
            status = status.value
        if status in (AssignmentStatus.CANCELLED.value, "Cancelled"):
            issues.append(ContinuityIssue(
                issue_type=ContinuityIssueType.CANCELLED_IN_CHAIN,
                assignment_id=aid,
                chain_id=chain_id,
                description=f"Assignment at position {assignment.chain_position} is cancelled",
                severity="error",
            ))

        # Check 4: Missing end date (except last in chain)
        if i < len(sorted_by_pos) - 1 and assignment.end_date is None:
            issues.append(ContinuityIssue(
                issue_type=ContinuityIssueType.MISSING_END_DATE,
                assignment_id=aid,
                chain_id=chain_id,
                description=(
                    f"Assignment at position {assignment.chain_position} has no "
                    f"end_date but is not the last link in the chain"
                ),
                severity="error",
            ))

        # Check 5: Large gaps within chain
        if i > 0 and sorted_by_pos[i - 1].end_date:
            prev = sorted_by_pos[i - 1]
            gap_days = (assignment.start_date - prev.end_date).days - 1
            if gap_days > MAX_GAP_WARNING_DAYS:
                issues.append(ContinuityIssue(
                    issue_type=ContinuityIssueType.GAP,
                    assignment_id=aid,
                    chain_id=chain_id,
                    description=(
                        f"{gap_days}-day gap between position "
                        f"{prev.chain_position} and {assignment.chain_position}"
                    ),
                    severity="warning" if gap_days <= 21 else "error",
                ))
            elif gap_days < 0:
                issues.append(ContinuityIssue(
                    issue_type=ContinuityIssueType.OVERLAP,
                    assignment_id=aid,
                    chain_id=chain_id,
                    description=(
                        f"{abs(gap_days)}-day overlap between position "
                        f"{prev.chain_position} and {assignment.chain_position}"
                    ),
                    severity="error",
                ))

    return issues


# ---------------------------------------------------------------------------
# Chain Construction
# ---------------------------------------------------------------------------

def build_chain(
    db: Session,
    chain_id: str,
) -> ResolvedChain:
    """Build a fully resolved chain from the database by chain_id."""
    assignments = _fetch_chain_assignments(db, chain_id)
    if not assignments:
        return ResolvedChain(
            chain_id=chain_id,
            technician_id="",
            technician_name="Unknown",
            chain_priority=DEFAULT_CHAIN_PRIORITY.value,
            links=[],
            is_valid=False,
            continuity_issues=[ContinuityIssue(
                issue_type=ContinuityIssueType.BROKEN_LINK,
                assignment_id="",
                chain_id=chain_id,
                description="No assignments found for this chain",
                severity="error",
            )],
        )

    first = assignments[0]
    tech_id = str(first.technician_id)
    tech_name = first.technician.full_name if first.technician else "Unknown"

    priority = first.chain_priority
    if hasattr(priority, "value"):
        priority = priority.value
    priority = priority or DEFAULT_CHAIN_PRIORITY.value

    # Build links
    links: List[ChainLink] = []
    for i, a in enumerate(assignments):
        link = _assignment_to_chain_link(a, i + 1)
        links.append(link)

    # Compute gap info between consecutive links
    for i in range(len(links) - 1):
        current = links[i]
        next_link = links[i + 1]
        if current.end_date:
            gap = (next_link.start_date - current.end_date).days - 1
            current.gap_days_to_next = gap
            current.gap_severity = _classify_gap(gap) if gap >= 0 else None

    # Compute totals
    total_booked = sum(
        (link.duration_days or 0) for link in links
    )
    total_gap = sum(
        max(0, link.gap_days_to_next or 0) for link in links
    )
    total_duration = 0
    if links:
        first_start = links[0].start_date
        last_end = links[-1].end_date
        if last_end:
            total_duration = (last_end - first_start).days

    # Validate continuity
    issues = validate_chain_continuity(assignments, chain_id)

    return ResolvedChain(
        chain_id=chain_id,
        technician_id=tech_id,
        technician_name=tech_name,
        chain_priority=priority,
        links=links,
        total_duration_days=total_duration,
        total_gap_days=total_gap,
        total_booked_days=total_booked,
        is_valid=len([i for i in issues if i.severity == "error"]) == 0,
        continuity_issues=issues,
    )


def build_implicit_chain(
    assignments: List[Assignment],
    technician_name: str = "Unknown",
) -> Optional[ResolvedChain]:
    """Build an implicit chain from a list of assignments that are NOT
    explicitly chained but belong to the same technician and are sequential.

    Used for technicians who have back-to-back assignments without formal
    chain_id linkage.
    """
    if not assignments:
        return None

    # Filter to non-cancelled with dates, sort by start
    valid = [
        a for a in assignments
        if a.status not in (AssignmentStatus.CANCELLED.value, "Cancelled")
    ]
    valid.sort(key=lambda a: a.start_date)

    if len(valid) < 2:
        return None

    # Find sequential segments where gap ≤ acceptable threshold
    chain_segments: List[List[Assignment]] = []
    current_segment = [valid[0]]

    for i in range(1, len(valid)):
        prev = current_segment[-1]
        curr = valid[i]

        if prev.end_date:
            gap = (curr.start_date - prev.end_date).days - 1
            if gap <= MAX_GAP_ACCEPTABLE_DAYS:
                current_segment.append(curr)
                continue

        # Gap too large or no end date: break the segment
        if len(current_segment) >= 2:
            chain_segments.append(current_segment)
        current_segment = [curr]

    if len(current_segment) >= 2:
        chain_segments.append(current_segment)

    if not chain_segments:
        return None

    # Return the longest implicit chain
    longest = max(chain_segments, key=len)
    implicit_chain_id = str(uuid.uuid4())
    tech_id = str(longest[0].technician_id)

    links = []
    for i, a in enumerate(longest):
        link = _assignment_to_chain_link(a, i + 1)
        links.append(link)

    for i in range(len(links) - 1):
        if links[i].end_date:
            gap = (links[i + 1].start_date - links[i].end_date).days - 1
            links[i].gap_days_to_next = gap
            links[i].gap_severity = _classify_gap(gap) if gap >= 0 else None

    total_booked = sum(link.duration_days or 0 for link in links)
    total_gap = sum(max(0, link.gap_days_to_next or 0) for link in links)
    total_duration = 0
    if links and links[-1].end_date:
        total_duration = (links[-1].end_date - links[0].start_date).days

    return ResolvedChain(
        chain_id=implicit_chain_id,
        technician_id=tech_id,
        technician_name=technician_name,
        chain_priority=DEFAULT_CHAIN_PRIORITY.value,
        links=links,
        total_duration_days=total_duration,
        total_gap_days=total_gap,
        total_booked_days=total_booked,
        is_valid=True,
    )


# ---------------------------------------------------------------------------
# Technician Timeline Builder
# ---------------------------------------------------------------------------

def build_technician_timeline(
    db: Session,
    technician_id: str,
    as_of: Optional[date] = None,
) -> TechnicianTimeline:
    """Build a full 90-day timeline for a single technician.

    Includes:
      • Current active assignment (if any)
      • All upcoming assignments within the window
      • Explicit chains (from chain_id)
      • Implicit chains (detected from sequential assignments)
      • Gaps and overlaps
      • Utilization percentage
    """
    today = as_of or date.today()
    window_end = today + timedelta(days=ROLLING_WINDOW_DAYS)

    # Fetch assignments
    assignments = _fetch_assignments_in_window(
        db, technician_id=technician_id,
        window_start=today, window_end=window_end,
    )

    # Get technician name
    tech = db.query(Technician).filter(Technician.id == technician_id).first()
    tech_name = tech.full_name if tech else "Unknown"

    # Identify current vs upcoming
    current_assignment = None
    upcoming = []

    for a in assignments:
        link = _assignment_to_chain_link(a, 0)
        status = a.status
        if hasattr(status, "value"):
            status = status.value

        if (
            a.start_date <= today
            and (a.end_date is None or a.end_date >= today)
            and status in (AssignmentStatus.ACTIVE.value, "Active")
        ):
            if current_assignment is None:
                current_assignment = link
            else:
                upcoming.append(link)
        elif a.start_date > today:
            upcoming.append(link)
        else:
            upcoming.append(link)

    upcoming.sort(key=lambda l: l.start_date)

    # Build explicit chains
    chain_ids = set()
    for a in assignments:
        if a.chain_id:
            chain_ids.add(str(a.chain_id))

    chains: List[ResolvedChain] = []
    for cid in chain_ids:
        chain = build_chain(db, cid)
        chains.append(chain)

    # Try implicit chain for unchained assignments
    unchained = [a for a in assignments if a.chain_id is None]
    if len(unchained) >= 2:
        implicit = build_implicit_chain(unchained, tech_name)
        if implicit:
            chains.append(implicit)

    # Detect gaps and overlaps
    gaps = detect_gaps(assignments, tech_name)
    overlaps = detect_overlaps(assignments, tech_name)

    # Calculate utilization
    total_booked = 0
    for a in assignments:
        if a.status in (AssignmentStatus.CANCELLED.value, "Cancelled"):
            continue
        a_start = max(a.start_date, today)
        a_end = min(a.end_date, window_end) if a.end_date else window_end
        if a_end >= a_start:
            total_booked += (a_end - a_start).days + 1

    utilization = round((total_booked / ROLLING_WINDOW_DAYS) * 100, 1)

    # Determine available_from
    available_from = None
    if not current_assignment:
        available_from = today
    elif current_assignment.end_date:
        available_from = current_assignment.end_date + timedelta(days=1)

    return TechnicianTimeline(
        technician_id=technician_id,
        technician_name=tech_name,
        current_assignment=current_assignment,
        upcoming_assignments=upcoming,
        chains=chains,
        gaps=gaps,
        overlaps=overlaps,
        available_from=available_from,
        total_booked_days=total_booked,
        utilization_pct=utilization,
    )


# ---------------------------------------------------------------------------
# Full 90-day Forward Schedule
# ---------------------------------------------------------------------------

def build_forward_schedule(
    db: Session,
    as_of: Optional[date] = None,
    technician_ids: Optional[List[str]] = None,
) -> ForwardSchedule:
    """Build the aggregate 90-day forward schedule for all (or selected) technicians.

    This is the primary entry point for the ops dashboard forward-staffing view.
    """
    today = as_of or date.today()
    window_end = today + timedelta(days=ROLLING_WINDOW_DAYS)

    # Fetch all assignments in window
    all_assignments = _fetch_assignments_in_window(
        db, window_start=today, window_end=window_end,
    )

    # Filter to requested technicians if specified
    if technician_ids:
        tid_set = set(technician_ids)
        all_assignments = [a for a in all_assignments if str(a.technician_id) in tid_set]

    # Group by technician
    by_tech: Dict[str, List[Assignment]] = {}
    for a in all_assignments:
        tid = str(a.technician_id)
        by_tech.setdefault(tid, []).append(a)

    # Build per-technician timelines
    timelines: Dict[str, TechnicianTimeline] = {}
    all_gaps: List[GapInfo] = []
    all_overlaps: List[OverlapInfo] = []
    all_issues: List[ContinuityIssue] = []

    active_count = 0
    pre_booked_count = 0
    chained_count = 0

    for tid, tech_assignments in by_tech.items():
        timeline = build_technician_timeline(db, tid, as_of=today)
        timelines[tid] = timeline
        all_gaps.extend(timeline.gaps)
        all_overlaps.extend(timeline.overlaps)
        for chain in timeline.chains:
            all_issues.extend(chain.continuity_issues)

        for a in tech_assignments:
            atype = a.assignment_type
            if hasattr(atype, "value"):
                atype = atype.value
            if atype == AssignmentType.ACTIVE.value:
                active_count += 1
            elif atype == AssignmentType.PRE_BOOKED.value:
                pre_booked_count += 1
            if a.chain_id:
                chained_count += 1

    return ForwardSchedule(
        schedule_start=today,
        schedule_end=window_end,
        timelines=timelines,
        total_assignments=len(all_assignments),
        active_count=active_count,
        pre_booked_count=pre_booked_count,
        chained_count=chained_count,
        all_gaps=all_gaps,
        all_overlaps=all_overlaps,
        all_issues=all_issues,
    )


# ---------------------------------------------------------------------------
# Chain Management Helpers (propose-only, no mutations)
# ---------------------------------------------------------------------------

def propose_chain_creation(
    db: Session,
    technician_id: str,
    assignment_ids: List[str],
) -> Dict:
    """Propose linking a set of assignments into a new chain.

    Returns a proposal dict with the proposed chain_id, ordering,
    gap analysis, and any issues. Does NOT write to DB.
    """
    assignments = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(Assignment.id.in_(assignment_ids))
        .all()
    )

    if len(assignments) != len(assignment_ids):
        found = {str(a.id) for a in assignments}
        missing = [aid for aid in assignment_ids if aid not in found]
        return {"valid": False, "error": f"Assignments not found: {missing}"}

    # Verify all belong to same technician
    tech_ids = {str(a.technician_id) for a in assignments}
    if len(tech_ids) > 1:
        return {"valid": False, "error": "All assignments must belong to the same technician"}
    if technician_id not in tech_ids:
        return {"valid": False, "error": f"Assignments do not belong to technician {technician_id}"}

    # Sort by start date
    assignments.sort(key=lambda a: a.start_date)
    proposed_chain_id = str(uuid.uuid4())

    links = []
    issues = []
    total_gap = 0

    for i, a in enumerate(assignments):
        link = _assignment_to_chain_link(a, i + 1)

        if i > 0:
            prev = assignments[i - 1]
            if prev.end_date:
                gap = (a.start_date - prev.end_date).days - 1
                link.gap_days_to_next = None  # set on prev
                links[i - 1].gap_days_to_next = gap
                links[i - 1].gap_severity = _classify_gap(gap) if gap >= 0 else None
                if gap >= 0:
                    total_gap += gap
                else:
                    issues.append({
                        "type": "overlap",
                        "positions": [i, i + 1],
                        "days": abs(gap),
                    })
            else:
                issues.append({
                    "type": "missing_end_date",
                    "position": i,
                    "assignment_id": str(prev.id),
                })

        links.append(link)

    tech_name = assignments[0].technician.full_name if assignments[0].technician else "Unknown"

    return {
        "valid": len([iss for iss in issues if iss.get("type") == "overlap"]) == 0,
        "proposed_chain_id": proposed_chain_id,
        "technician_id": technician_id,
        "technician_name": tech_name,
        "links": [
            {
                "assignment_id": l.assignment_id,
                "role_name": l.role_name,
                "project_name": l.project_name,
                "start_date": l.start_date.isoformat(),
                "end_date": l.end_date.isoformat() if l.end_date else None,
                "chain_position": l.chain_position,
                "gap_days_to_next": l.gap_days_to_next,
                "gap_severity": l.gap_severity.value if l.gap_severity else None,
            }
            for l in links
        ],
        "total_gap_days": total_gap,
        "issues": issues,
    }


def calculate_rolling_off_soon(
    db: Session,
    within_days: int = 14,
    as_of: Optional[date] = None,
) -> List[Dict]:
    """Identify technicians whose current assignment ends within N days
    and who do NOT have a chained or pre-booked next assignment.

    Used by the staffing agent to proactively suggest forward placements.
    """
    today = as_of or date.today()
    cutoff = today + timedelta(days=within_days)

    # Active assignments ending soon
    ending_soon = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.technician),
            joinedload(Assignment.role).joinedload(ProjectRole.project),
        )
        .filter(
            Assignment.start_date <= today,
            Assignment.end_date.isnot(None),
            Assignment.end_date >= today,
            Assignment.end_date <= cutoff,
            ~Assignment.status.in_([
                AssignmentStatus.CANCELLED.value,
                AssignmentStatus.COMPLETED.value,
                "Cancelled", "Completed",
            ]),
        )
        .all()
    )

    results = []
    for a in ending_soon:
        tech_id = str(a.technician_id)
        # Check for chained or upcoming assignment
        has_next = (
            db.query(Assignment)
            .filter(
                Assignment.technician_id == a.technician_id,
                Assignment.start_date > a.end_date,
                ~Assignment.status.in_([
                    AssignmentStatus.CANCELLED.value,
                    "Cancelled",
                ]),
            )
            .first()
        ) is not None

        if not has_next:
            results.append({
                "technician_id": tech_id,
                "technician_name": a.technician.full_name if a.technician else "Unknown",
                "current_assignment_id": str(a.id),
                "current_project": _get_project_name_for_assignment(a),
                "current_role": a.role.role_name if a.role else "Unknown",
                "end_date": a.end_date.isoformat(),
                "days_remaining": (a.end_date - today).days,
                "has_next_assignment": False,
            })

    results.sort(key=lambda r: r["days_remaining"])
    return results
