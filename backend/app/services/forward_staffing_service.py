"""Forward Staffing Service — 90-day window gap analysis and proactive recommendations.

Analyzes upcoming assignment gaps within a 90-day lookahead window and identifies:
  - Roles that will become unfilled as technicians roll off
  - Projects entering staffing phase with unfilled roles
  - Technicians becoming available who match upcoming project needs
  - Assignment chain gaps where back-to-back scheduling breaks down

This service is called by both:
  - The Celery beat scheduled task (periodic scan)
  - On-demand API endpoint (manual trigger)

All data access goes through the service layer — never direct DB access from agents.
"""

import logging
from datetime import date, timedelta
from typing import Any, Optional
from dataclasses import dataclass, field

from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session, joinedload

from app.models.technician import (
    Technician,
    TechnicianSkill,
    DeployabilityStatus,
    CareerStage,
)
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.assignment import Assignment, AssignmentStatus
from app.models.recommendation import Recommendation, PreferenceRule, RecommendationStatus

logger = logging.getLogger("deployable.forward_staffing")

# 90-day forward staffing lookahead window
FORWARD_WINDOW_DAYS = 90


@dataclass
class GapAnalysisRole:
    """A role that will have staffing gaps in the forward window."""
    role_id: str
    role_name: str
    project_id: str
    project_name: str
    project_region: str
    project_city: str
    required_skills: list
    required_certs: list
    total_slots: int
    currently_filled: int
    rolling_off_count: int
    rolling_off_techs: list  # [{tech_id, tech_name, end_date}]
    gap_start_date: date
    gap_slots: int  # How many slots will be open
    urgency: str  # "critical" (<14d), "high" (<30d), "medium" (<60d), "low" (60-90d)
    hourly_rate: Optional[float] = None
    per_diem: Optional[float] = None


@dataclass
class AvailableTechnician:
    """A technician becoming available in the forward window."""
    technician_id: str
    full_name: str
    available_from: date
    home_base_state: str
    home_base_city: str
    approved_regions: list
    career_stage: str
    deployability_status: str
    skills: list  # [{skill_name, proficiency}]
    certifications: list  # [{cert_name, status}]
    current_assignment_end: Optional[date] = None


@dataclass
class ForwardStaffingGap:
    """A specific gap that needs to be filled."""
    gap_id: str
    role: GapAnalysisRole
    available_candidates: list  # List of AvailableTechnician
    recommended_candidate_ids: list  # Top candidates from scoring
    urgency_score: float  # 0-100, higher = more urgent
    gap_type: str  # "rolloff", "unfilled", "chain_break"
    notes: str


@dataclass
class ForwardStaffingScanResult:
    """Complete result of a forward staffing scan."""
    scan_date: date
    window_end: date
    total_gaps_found: int
    gaps_by_urgency: dict  # {"critical": N, "high": N, "medium": N, "low": N}
    gaps: list  # List of ForwardStaffingGap
    available_technicians: list  # List of AvailableTechnician
    summary: str  # NL summary of the scan


def _urgency_from_days(days_until_gap: int) -> str:
    """Classify urgency based on days until the gap opens."""
    if days_until_gap <= 14:
        return "critical"
    elif days_until_gap <= 30:
        return "high"
    elif days_until_gap <= 60:
        return "medium"
    return "low"


def _urgency_score(days_until_gap: int, gap_slots: int) -> float:
    """Compute urgency score (0-100). Higher = more urgent."""
    # Time pressure (exponentially more urgent as deadline approaches)
    if days_until_gap <= 0:
        time_score = 100.0
    elif days_until_gap <= 7:
        time_score = 95.0
    elif days_until_gap <= 14:
        time_score = 85.0
    elif days_until_gap <= 30:
        time_score = 70.0
    elif days_until_gap <= 60:
        time_score = 45.0
    else:
        time_score = 25.0

    # Scale factor: more open slots = higher urgency
    slot_multiplier = min(1.0 + (gap_slots - 1) * 0.1, 1.5)

    return round(min(100.0, time_score * slot_multiplier), 1)


def analyze_rolloff_gaps(session: Session, today: date, window_end: date) -> list[GapAnalysisRole]:
    """Find roles where technicians are rolling off within the window.

    Identifies active assignments ending within the 90-day window where the
    role will have unfilled slots after the rolloff.
    """
    gaps = []

    # Find assignments ending within the window
    ending_assignments = (
        session.query(Assignment)
        .filter(
            Assignment.status.in_(["Active", "Pre-Booked"]),
            Assignment.end_date != None,
            Assignment.end_date >= today,
            Assignment.end_date <= window_end,
        )
        .options(joinedload(Assignment.technician), joinedload(Assignment.role))
        .all()
    )

    # Group by role_id
    role_rolloffs: dict[str, list[Assignment]] = {}
    for assignment in ending_assignments:
        role_key = str(assignment.role_id)
        if role_key not in role_rolloffs:
            role_rolloffs[role_key] = []
        role_rolloffs[role_key].append(assignment)

    for role_id_str, assignments in role_rolloffs.items():
        role = assignments[0].role if assignments else None
        if not role:
            continue

        project = session.get(Project, role.project_id)
        if not project or project.status in (ProjectStatus.CLOSED, ProjectStatus.ON_HOLD):
            continue

        # Check if replacement assignments exist
        rolling_off_techs = []
        earliest_gap_date = window_end

        for a in assignments:
            # Check for chained/replacement assignment
            next_assignment = None
            if a.next_assignment_id:
                next_assignment = session.get(Assignment, a.next_assignment_id)

            if not next_assignment or next_assignment.status == "Cancelled":
                tech = a.technician
                tech_name = tech.full_name if tech else "Unknown"
                rolling_off_techs.append({
                    "technician_id": str(a.technician_id),
                    "technician_name": tech_name,
                    "end_date": a.end_date.isoformat() if a.end_date else None,
                })
                if a.end_date and a.end_date < earliest_gap_date:
                    earliest_gap_date = a.end_date

        if not rolling_off_techs:
            continue

        days_until = (earliest_gap_date - today).days
        gap_slots = len(rolling_off_techs)

        gaps.append(GapAnalysisRole(
            role_id=str(role.id),
            role_name=role.role_name,
            project_id=str(project.id),
            project_name=project.name,
            project_region=project.location_region or "",
            project_city=project.location_city or "",
            required_skills=role.required_skills or [],
            required_certs=role.required_certs or [],
            total_slots=role.quantity,
            currently_filled=role.filled or 0,
            rolling_off_count=len(rolling_off_techs),
            rolling_off_techs=rolling_off_techs,
            gap_start_date=earliest_gap_date,
            gap_slots=gap_slots,
            urgency=_urgency_from_days(days_until),
            hourly_rate=role.hourly_rate,
            per_diem=role.per_diem,
        ))

    return gaps


def analyze_unfilled_roles(session: Session, today: date, window_end: date) -> list[GapAnalysisRole]:
    """Find roles on active/staffing projects that have unfilled slots.

    These are existing gaps that still need staffing attention.
    """
    gaps = []

    active_projects = (
        session.query(Project)
        .filter(
            Project.status.in_([ProjectStatus.STAFFING, ProjectStatus.ACTIVE]),
            or_(
                Project.start_date <= window_end,
                Project.start_date == None,
            ),
        )
        .all()
    )

    for project in active_projects:
        roles = session.query(ProjectRole).filter(
            ProjectRole.project_id == project.id
        ).all()

        for role in roles:
            open_slots = role.quantity - (role.filled or 0)
            if open_slots <= 0:
                continue

            # Check if there are already pending recommendations
            existing_pending = session.query(Recommendation).filter(
                Recommendation.role_id == str(role.id),
                Recommendation.recommendation_type == "staffing",
                Recommendation.status == RecommendationStatus.PENDING.value,
            ).count()

            # Only flag if under-recommended (fewer pending recs than open slots)
            if existing_pending >= open_slots:
                continue

            gap_date = project.start_date if project.start_date and project.start_date > today else today
            days_until = max(0, (gap_date - today).days)

            gaps.append(GapAnalysisRole(
                role_id=str(role.id),
                role_name=role.role_name,
                project_id=str(project.id),
                project_name=project.name,
                project_region=project.location_region or "",
                project_city=project.location_city or "",
                required_skills=role.required_skills or [],
                required_certs=role.required_certs or [],
                total_slots=role.quantity,
                currently_filled=role.filled or 0,
                rolling_off_count=0,
                rolling_off_techs=[],
                gap_start_date=gap_date,
                gap_slots=open_slots - existing_pending,
                urgency=_urgency_from_days(days_until),
                hourly_rate=role.hourly_rate,
                per_diem=role.per_diem,
            ))

    return gaps


def find_available_technicians(
    session: Session,
    today: date,
    window_end: date,
) -> list[AvailableTechnician]:
    """Find technicians who will be available within the forward window.

    Includes:
    - Currently ready ("Ready Now" deployability)
    - Rolling off soon (assignment ending within window)
    - Completing training soon
    """
    available = []

    # 1. Ready Now technicians
    ready_techs = (
        session.query(Technician)
        .filter(
            Technician.deployability_status.in_([
                DeployabilityStatus.READY_NOW,
                DeployabilityStatus.ROLLING_OFF_SOON,
            ]),
        )
        .options(
            joinedload(Technician.skills),
            joinedload(Technician.certifications),
        )
        .all()
    )

    for tech in ready_techs:
        # Find their current assignment end date if any
        current_end = None
        current_assignment = (
            session.query(Assignment)
            .filter(
                Assignment.technician_id == tech.id,
                Assignment.status == "Active",
            )
            .order_by(Assignment.end_date.desc())
            .first()
        )
        if current_assignment and current_assignment.end_date:
            current_end = current_assignment.end_date
            if current_end > window_end:
                continue  # Not available within our window

        cs = tech.career_stage
        career_stage = cs.value if hasattr(cs, "value") else str(cs) if cs else ""
        ds = tech.deployability_status
        deploy_status = ds.value if hasattr(ds, "value") else str(ds) if ds else ""

        skills = []
        for ts in tech.skills:
            prof = ts.proficiency_level
            prof_val = prof.value if hasattr(prof, "value") else str(prof) if prof else "Apprentice"
            skills.append({
                "skill_name": ts.skill_name,
                "proficiency": prof_val,
            })

        certs = []
        for tc in tech.certifications:
            status = tc.status
            status_val = status.value if hasattr(status, "value") else str(status) if status else "Active"
            certs.append({
                "cert_name": tc.cert_name,
                "status": status_val,
            })

        avail_date = tech.available_from or today
        if current_end and current_end > avail_date:
            avail_date = current_end

        available.append(AvailableTechnician(
            technician_id=str(tech.id),
            full_name=tech.full_name,
            available_from=avail_date,
            home_base_state=tech.home_base_state or "",
            home_base_city=tech.home_base_city or "",
            approved_regions=tech.approved_regions or [],
            career_stage=career_stage,
            deployability_status=deploy_status,
            skills=skills,
            certifications=certs,
            current_assignment_end=current_end,
        ))

    return available


def match_candidates_to_gap(
    session: Session,
    gap: GapAnalysisRole,
    available_techs: list[AvailableTechnician],
) -> list[str]:
    """Quick pre-match of available technicians to a gap based on basic criteria.

    Returns list of technician IDs that are potential matches, ordered by fit.
    Used to narrow the candidate pool before full scoring.
    """
    from app.services.scoring import score_technician_for_role

    role = session.get(ProjectRole, gap.role_id)
    project = session.get(Project, gap.project_id) if gap.project_id else None
    if not role:
        return []

    # Load active preference rules
    preference_rules = (
        session.query(PreferenceRule).filter(PreferenceRule.active == True).all()
    )

    # Exclude already-assigned, dismissed, or pending techs for this role
    exclude_recs = session.query(Recommendation).filter(
        Recommendation.role_id == gap.role_id,
        Recommendation.recommendation_type == "staffing",
        Recommendation.status.in_([
            RecommendationStatus.PENDING.value,
            RecommendationStatus.APPROVED.value,
            RecommendationStatus.DISMISSED.value,
        ]),
    ).all()
    exclude_ids = {r.target_entity_id for r in exclude_recs if r.target_entity_id}

    # Also exclude rolling-off techs for this specific role
    for rt in gap.rolling_off_techs:
        exclude_ids.add(rt["technician_id"])

    # Score available techs
    scored = []
    for avail_tech in available_techs:
        if avail_tech.technician_id in exclude_ids:
            continue

        # Check if available before gap opens (with 7-day buffer)
        if avail_tech.available_from > gap.gap_start_date + timedelta(days=7):
            continue

        # Basic region check
        if gap.project_region and avail_tech.approved_regions:
            if gap.project_region not in avail_tech.approved_regions:
                continue

        # Get the full technician for scoring
        tech = session.get(Technician, avail_tech.technician_id)
        if not tech:
            continue

        scorecard = score_technician_for_role(
            session, tech, role, project, preference_rules
        )

        if not scorecard["disqualified"] and scorecard["overall_score"] >= 30:
            scored.append((avail_tech.technician_id, scorecard["overall_score"]))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Return top 10 candidate IDs
    return [tech_id for tech_id, _ in scored[:10]]


def run_forward_staffing_scan(session: Session) -> ForwardStaffingScanResult:
    """Execute a complete forward staffing scan.

    This is the main entry point called by the Celery task.
    Analyzes the 90-day window and produces structured gap analysis.
    """
    today = date.today()
    window_end = today + timedelta(days=FORWARD_WINDOW_DAYS)

    logger.info("Starting forward staffing scan: %s to %s", today, window_end)

    # Phase 1: Find gaps
    rolloff_gaps = analyze_rolloff_gaps(session, today, window_end)
    unfilled_gaps = analyze_unfilled_roles(session, today, window_end)

    # Phase 2: Find available technicians
    available_techs = find_available_technicians(session, today, window_end)

    # Phase 3: Match candidates to gaps
    all_gaps: list[ForwardStaffingGap] = []

    for role_gap in rolloff_gaps:
        candidates = match_candidates_to_gap(session, role_gap, available_techs)
        days_until = (role_gap.gap_start_date - today).days

        all_gaps.append(ForwardStaffingGap(
            gap_id=f"rolloff_{role_gap.role_id}_{role_gap.gap_start_date.isoformat()}",
            role=role_gap,
            available_candidates=[
                t for t in available_techs if t.technician_id in candidates
            ],
            recommended_candidate_ids=candidates,
            urgency_score=_urgency_score(days_until, role_gap.gap_slots),
            gap_type="rolloff",
            notes=f"{role_gap.rolling_off_count} tech(s) rolling off by {role_gap.gap_start_date.isoformat()}",
        ))

    for role_gap in unfilled_gaps:
        candidates = match_candidates_to_gap(session, role_gap, available_techs)
        days_until = (role_gap.gap_start_date - today).days

        all_gaps.append(ForwardStaffingGap(
            gap_id=f"unfilled_{role_gap.role_id}",
            role=role_gap,
            available_candidates=[
                t for t in available_techs if t.technician_id in candidates
            ],
            recommended_candidate_ids=candidates,
            urgency_score=_urgency_score(days_until, role_gap.gap_slots),
            gap_type="unfilled",
            notes=f"{role_gap.gap_slots} unfilled slot(s) on {role_gap.project_name}",
        ))

    # Sort by urgency score descending
    all_gaps.sort(key=lambda g: g.urgency_score, reverse=True)

    # Compute urgency breakdown
    gaps_by_urgency = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for gap in all_gaps:
        gaps_by_urgency[gap.role.urgency] += 1

    # Generate summary
    summary = _generate_scan_summary(all_gaps, available_techs, today, window_end)

    result = ForwardStaffingScanResult(
        scan_date=today,
        window_end=window_end,
        total_gaps_found=len(all_gaps),
        gaps_by_urgency=gaps_by_urgency,
        gaps=all_gaps,
        available_technicians=available_techs,
        summary=summary,
    )

    logger.info(
        "Forward staffing scan complete: %d gaps found (%d critical, %d high)",
        result.total_gaps_found,
        gaps_by_urgency["critical"],
        gaps_by_urgency["high"],
    )

    return result


def _generate_scan_summary(
    gaps: list[ForwardStaffingGap],
    available_techs: list[AvailableTechnician],
    today: date,
    window_end: date,
) -> str:
    """Generate a deterministic summary of the scan results."""
    if not gaps:
        return (
            f"Forward staffing scan ({today} to {window_end}): "
            f"No staffing gaps identified. {len(available_techs)} technicians available."
        )

    critical = sum(1 for g in gaps if g.role.urgency == "critical")
    high = sum(1 for g in gaps if g.role.urgency == "high")
    total_slots = sum(g.role.gap_slots for g in gaps)

    parts = [
        f"Forward staffing scan ({today} to {window_end}): "
        f"{len(gaps)} gaps identified requiring {total_slots} total slot(s). "
    ]

    if critical:
        parts.append(f"{critical} critical gap(s) within 14 days. ")
    if high:
        parts.append(f"{high} high-priority gap(s) within 30 days. ")

    parts.append(f"{len(available_techs)} technicians available in the window.")

    # Top gap detail
    if gaps:
        top = gaps[0]
        parts.append(
            f" Most urgent: {top.role.role_name} on {top.role.project_name} "
            f"({top.role.urgency}, {top.role.gap_slots} slot(s), "
            f"{len(top.recommended_candidate_ids)} candidates matched)."
        )

    return "".join(parts)


def serialize_scan_result(result: ForwardStaffingScanResult) -> dict[str, Any]:
    """Serialize scan result for API response and WebSocket push."""
    return {
        "scan_date": result.scan_date.isoformat(),
        "window_end": result.window_end.isoformat(),
        "total_gaps_found": result.total_gaps_found,
        "gaps_by_urgency": result.gaps_by_urgency,
        "summary": result.summary,
        "gaps": [
            {
                "gap_id": gap.gap_id,
                "gap_type": gap.gap_type,
                "urgency_score": gap.urgency_score,
                "notes": gap.notes,
                "role": {
                    "role_id": gap.role.role_id,
                    "role_name": gap.role.role_name,
                    "project_id": gap.role.project_id,
                    "project_name": gap.role.project_name,
                    "project_region": gap.role.project_region,
                    "total_slots": gap.role.total_slots,
                    "currently_filled": gap.role.currently_filled,
                    "gap_slots": gap.role.gap_slots,
                    "gap_start_date": gap.role.gap_start_date.isoformat(),
                    "urgency": gap.role.urgency,
                    "rolling_off_techs": gap.role.rolling_off_techs,
                    "required_skills": gap.role.required_skills,
                    "required_certs": gap.role.required_certs,
                },
                "recommended_candidate_count": len(gap.recommended_candidate_ids),
                "recommended_candidate_ids": gap.recommended_candidate_ids[:5],  # Top 5
            }
            for gap in result.gaps
        ],
        "available_technician_count": len(result.available_technicians),
    }
