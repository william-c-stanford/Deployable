"""Project management endpoints — including closure validation.

Ops users can:
- List/get projects
- Run a dry-run closure check to see blocking conditions
- Close a project (after all checks pass)

Close-validation guards enforce that hard blockers (active assignments,
open timesheets, unresolved escalations, pending confirmations) must be
resolved before a project can close. Soft blockers (unfilled roles,
pending recommendations, missing skill breakdowns) generate warnings
but allow closure when acknowledged.
"""

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_current_user, require_role, CurrentUser
from app.models.project import Project, ProjectRole, ProjectStatus
from app.schemas.project import (
    BlockingItem,
    BlockingItemType,
    BlockingSeverity,
    CloseValidationResponse,
    ProjectCloseError,
    ProjectResponse,
    ProjectRoleResponse,
    ProjectRoleDetailResponse,
    ProjectListResponse,
    ProjectStatusUpdate,
    CloseProjectRequest,
    CloseProjectResponse,
)
from app.services.project_service import (
    check_project_closure,
    close_project,
    auto_dismiss_pending_recommendations,
    get_resolution_hints,
    get_project_role_ids,
    ProjectClosureBlockedError,
    ProjectNotFoundError,
    InvalidProjectStateError,
)
from app.workers.dispatcher import dispatch_event_safe
from app.workers.events import EventPayload, EventType

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_to_response(project: Project) -> ProjectResponse:
    """Convert a Project model to a response schema."""
    roles = []
    if project.roles:
        for r in project.roles:
            # Map required_skills from DB format to frontend skill_bundle format
            skill_bundle = []
            for s in (r.required_skills or []):
                skill_bundle.append({
                    "skill_name": s.get("skill", s.get("skill_name", "")),
                    "min_proficiency": s.get("min_level", s.get("min_proficiency", "Beginner")),
                })
            roles.append(ProjectRoleDetailResponse(
                id=str(r.id),
                project_id=str(r.project_id),
                role_name=r.role_name,
                quantity=r.quantity,
                filled=r.filled,
                open_slots=r.open_slots,
                hourly_rate=r.hourly_rate,
                per_diem=r.per_diem,
                required_skills=skill_bundle,
                required_certs=r.required_certs or [],
                skill_weights=r.skill_weights or {},
            ))

    # Resolve partner name
    partner_name = ""
    if project.partner:
        partner_name = project.partner.name

    return ProjectResponse(
        id=str(project.id),
        name=project.name,
        partner_id=str(project.partner_id),
        partner_name=partner_name,
        status=project.status.value if hasattr(project.status, "value") else str(project.status),
        location_region=project.location_region,
        location_city=project.location_city,
        start_date=project.start_date,
        end_date=project.end_date,
        budget_hours=project.budget_hours,
        description=project.description,
        created_at=project.created_at,
        roles=roles,
    )


def _build_blocking_items(result) -> List[BlockingItem]:
    """Convert a ClosureCheckResult into the schema's BlockingItem list.

    Returns all items — both hard and soft blockers — categorized by severity
    so the UI can display them in appropriate sections.
    """
    items: List[BlockingItem] = []

    # ── Hard blockers ────────────────────────────────────────────────────

    for a in result.active_assignments:
        items.append(BlockingItem(
            type=BlockingItemType.ACTIVE_ASSIGNMENT,
            severity=BlockingSeverity.HARD,
            entity_id=a["assignment_id"],
            entity_type="assignment",
            summary=f"Active assignment (status: {a['status']}) for technician {a['technician_id'][:8]}…",
            detail=f"Dates: {a.get('start_date', '?')} → {a.get('end_date', 'ongoing')}",
        ))

    for t in result.open_timesheets:
        items.append(BlockingItem(
            type=(
                BlockingItemType.FLAGGED_TIMESHEET
                if t["status"] == "Flagged"
                else BlockingItemType.PENDING_TIMESHEET
            ),
            severity=BlockingSeverity.HARD,
            entity_id=t["timesheet_id"],
            entity_type="timesheet",
            summary=f"{t['status']} timesheet ({t['hours']}h) for week of {t.get('week_start', '?')}",
            detail=f"Technician: {t['technician_id'][:8]}…, Assignment: {t['assignment_id'][:8]}…",
        ))

    for e in result.unresolved_escalations:
        items.append(BlockingItem(
            type=BlockingItemType.ESCALATED_CONFIRMATION,
            severity=BlockingSeverity.HARD,
            entity_id=e["escalation_id"],
            entity_type="assignment_confirmation",
            summary=f"Unresolved escalation ({e['escalation_status']}) waiting {e['hours_waiting']}h",
            detail=f"Partner: {e['partner_id'][:8]}…, Assignment: {e['assignment_id'][:8]}…",
        ))

    for c in result.pending_confirmations:
        items.append(BlockingItem(
            type=BlockingItemType.PENDING_CONFIRMATION,
            severity=BlockingSeverity.HARD,
            entity_id=c["confirmation_id"],
            entity_type="assignment_confirmation",
            summary=(
                f"Pending {c['confirmation_type']} confirmation "
                f"(date: {c.get('requested_date', '?')}) waiting {c['hours_waiting']}h"
            ),
            detail=f"Partner: {c['partner_id'][:8]}…, Assignment: {c['assignment_id'][:8]}…",
        ))

    # ── Soft blockers (warnings) ─────────────────────────────────────────

    for r in result.unfilled_roles:
        items.append(BlockingItem(
            type=BlockingItemType.UNFILLED_ROLE,
            severity=BlockingSeverity.SOFT,
            entity_id=r["role_id"],
            entity_type="project_role",
            summary=(
                f"Role '{r['role_name']}' has {r['open_slots']} unfilled "
                f"slot(s) ({r['filled']}/{r['quantity']})"
            ),
            detail="Unfilled roles will be recorded as incomplete in the project history.",
        ))

    for rec in result.pending_recommendations:
        items.append(BlockingItem(
            type=BlockingItemType.PENDING_RECOMMENDATION,
            severity=BlockingSeverity.SOFT,
            entity_id=rec["recommendation_id"],
            entity_type="recommendation",
            summary=f"Pending staffing recommendation (score: {rec.get('overall_score', 'N/A')})",
            detail=(
                f"Recommendation for role {rec['role_id']} has not been acted on. "
                f"It will be automatically dismissed on project close."
            ),
        ))

    for m in result.missing_skill_breakdowns:
        items.append(BlockingItem(
            type=BlockingItemType.PENDING_SKILL_BREAKDOWN,
            severity=BlockingSeverity.SOFT,
            entity_id=m["assignment_id"],
            entity_type="assignment",
            summary=(
                f"Completed assignment for technician {m['technician_id'][:8]}… "
                f"has no skill breakdown submitted"
            ),
            detail=(
                "Skill breakdowns capture performance data for career passports. "
                "Consider submitting one before closing the project."
            ),
        ))

    return items


# ---------------------------------------------------------------------------
# List projects
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List all projects",
)
def list_projects(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by project status"),
    partner_id: Optional[str] = Query(None, description="Filter by partner ID"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List projects visible to the current user."""
    query = db.query(Project).options(
        joinedload(Project.partner),
        joinedload(Project.roles),
    )

    # Partner scoping — partners only see their own projects
    if current_user.role == "partner" and current_user.account_id:
        query = query.filter(Project.partner_id == current_user.account_id)
    elif partner_id:
        query = query.filter(Project.partner_id == partner_id)

    if status_filter:
        query = query.filter(Project.status == status_filter)

    projects = query.order_by(Project.created_at.desc()).all()
    return ProjectListResponse(
        items=[_project_to_response(p) for p in projects],
        total=len(projects),
    )


# ---------------------------------------------------------------------------
# Get single project
# ---------------------------------------------------------------------------

@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get project details",
)
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get details of a single project."""
    project = db.query(Project).options(
        joinedload(Project.partner),
        joinedload(Project.roles),
    ).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Partner scoping
    if current_user.role == "partner" and current_user.account_id:
        if str(project.partner_id) != current_user.account_id:
            raise HTTPException(status_code=403, detail="Not authorized to view this project")

    return _project_to_response(project)


# ---------------------------------------------------------------------------
# Closure pre-check (dry run)
# ---------------------------------------------------------------------------

@router.get(
    "/{project_id}/close-check",
    response_model=CloseValidationResponse,
    summary="Check if a project can be closed",
    description=(
        "Runs all closure validation checks without actually closing the project. "
        "Returns detailed blocking items organized by severity (hard vs soft). "
        "Hard blockers must be resolved; soft blockers are warnings that can be overridden."
    ),
)
def close_check(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Dry-run closure validation — returns all blocking conditions.

    Checks performed:
    - HARD: Active assignments, open timesheets, unresolved escalations, pending confirmations
    - SOFT: Unfilled roles, pending recommendations, missing skill breakdowns
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Already closed — no blockers
    if project.status == ProjectStatus.CLOSED:
        return CloseValidationResponse(
            project_id=str(project.id),
            project_name=project.name,
            can_close=True,
            has_warnings=False,
            hard_blockers=[],
            soft_blockers=[],
            total_blocking_items=0,
            checked_at=datetime.utcnow(),
        )

    result = check_project_closure(db, project.id)
    blocking_items = _build_blocking_items(result)

    hard_blockers = [b for b in blocking_items if b.severity == BlockingSeverity.HARD]
    soft_blockers = [b for b in blocking_items if b.severity == BlockingSeverity.SOFT]

    return CloseValidationResponse(
        project_id=str(project.id),
        project_name=project.name,
        can_close=result.can_close,
        has_warnings=result.has_warnings,
        hard_blockers=hard_blockers,
        soft_blockers=soft_blockers,
        total_blocking_items=len(blocking_items),
        checked_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Close a project
# ---------------------------------------------------------------------------

@router.post(
    "/{project_id}/close",
    response_model=CloseProjectResponse,
    responses={
        409: {
            "model": ProjectCloseError,
            "description": (
                "Closure blocked by unresolved items. Response body contains "
                "structured list of all blocking items with resolution hints."
            ),
        },
        400: {"description": "Invalid state transition or missing confirmation"},
    },
    summary="Close a project",
    description=(
        "Attempts to close a project. Enforces the close-validation guard:\n\n"
        "**Hard blockers** (must be resolved):\n"
        "- Active assignments (Active, Pre-Booked, Pending Confirmation)\n"
        "- Open timesheets (Submitted or Flagged)\n"
        "- Unresolved escalations\n"
        "- Pending partner confirmations\n\n"
        "**Soft blockers** (warnings — acknowledged on close):\n"
        "- Unfilled role slots\n"
        "- Pending staffing recommendations (auto-dismissed)\n"
        "- Missing skill breakdowns for completed assignments\n\n"
        "Returns a 409 with detailed blocking items and resolution hints if blocked."
    ),
)
def close_project_endpoint(
    project_id: str,
    body: CloseProjectRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Close a project — human approval gate.

    This is the primary endpoint for project closure. It enforces
    the full close-validation gate with structured error responses.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must confirm closure intent (set confirm=true)",
        )

    try:
        project = close_project(db, project_id)

        # Auto-dismiss pending recommendations on successful close
        role_ids = get_project_role_ids(db, project_id)
        dismissed_count = auto_dismiss_pending_recommendations(db, role_ids)

        db.commit()

        # Dispatch project closed event for reactive agents
        dispatch_event_safe(EventPayload(
            event_type=EventType.PROJECT_STATUS_CHANGED,
            entity_type="project",
            entity_id=str(project.id),
            actor_id=current_user.user_id,
            data={
                "new_status": "Closed",
                "project_name": project.name,
                "close_note": body.close_note,
                "recommendations_dismissed": dismissed_count,
            },
        ))

        return CloseProjectResponse(
            project_id=str(project.id),
            project_name=project.name,
            status="Closed",
            message=(
                f"Project '{project.name}' successfully closed."
                + (f" {dismissed_count} pending recommendation(s) auto-dismissed."
                   if dismissed_count > 0 else "")
            ),
        )

    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")

    except InvalidProjectStateError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except ProjectClosureBlockedError as e:
        # Build the structured error with all blocking items
        result = check_project_closure(db, project_id)
        blocking_items = _build_blocking_items(result)
        hard_blockers = [b for b in blocking_items if b.severity == BlockingSeverity.HARD]
        soft_blockers = [b for b in blocking_items if b.severity == BlockingSeverity.SOFT]
        hints = get_resolution_hints(result)

        error_response = ProjectCloseError(
            message=e.message,
            project_id=project_id,
            blocking_items=blocking_items,
            hard_blocker_count=len(hard_blockers),
            soft_blocker_count=len(soft_blockers),
            resolution_hints=hints,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_response.model_dump(),
        )


# ---------------------------------------------------------------------------
# Update project status (general transitions)
# ---------------------------------------------------------------------------

@router.patch(
    "/{project_id}/status",
    response_model=ProjectResponse,
    responses={
        409: {
            "model": ProjectCloseError,
            "description": "Close blocked — use POST /close endpoint instead",
        },
    },
    summary="Update project status",
    description=(
        "Transition a project to a new status. When the target status is 'Closed', "
        "this endpoint enforces the close-validation guard and returns a 409 with "
        "structured blocking items if any hard blockers exist. For a dedicated "
        "close flow, prefer POST /{project_id}/close."
    ),
)
def update_project_status(
    project_id: str,
    body: ProjectStatusUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("ops")),
):
    """Update project status with close-validation enforcement on closure attempts.

    When the target status is 'Closed':
    1. Runs all close-validation checks
    2. If hard blockers exist → 409 Conflict with structured error
    3. If only soft blockers exist and force=false → 409 with warning
    4. If no blockers (or force=true with only soft blockers) → closes
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate the target status is a valid ProjectStatus value
    valid_statuses = {s.value for s in ProjectStatus}
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {body.status}. Valid: {sorted(valid_statuses)}",
        )

    # Already closed
    if project.status == ProjectStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project is already closed",
        )

    # ── CLOSE-VALIDATION GUARD ──────────────────────────────────────────
    if body.status == "Closed":
        result = check_project_closure(db, project.id)
        blocking_items = _build_blocking_items(result)
        hard_blockers = [b for b in blocking_items if b.severity == BlockingSeverity.HARD]
        soft_blockers = [b for b in blocking_items if b.severity == BlockingSeverity.SOFT]
        hints = get_resolution_hints(result)

        # Hard blockers: always reject
        if not result.can_close:
            error_response = ProjectCloseError(
                error="project_close_blocked",
                message=(
                    f"Cannot close project '{project.name}': "
                    f"{len(hard_blockers)} hard blocker(s) must be resolved"
                ),
                project_id=str(project.id),
                blocking_items=blocking_items,
                hard_blocker_count=len(hard_blockers),
                soft_blocker_count=len(soft_blockers),
                resolution_hints=hints,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_response.model_dump(),
            )

        # Soft blockers without force: reject with warning
        if result.has_warnings and not body.force:
            error_response = ProjectCloseError(
                error="project_close_warnings",
                message=(
                    f"Project '{project.name}' has {len(soft_blockers)} "
                    f"warning(s). Use force=true to override or resolve them first."
                ),
                project_id=str(project.id),
                blocking_items=soft_blockers,
                hard_blocker_count=0,
                soft_blocker_count=len(soft_blockers),
                resolution_hints=hints,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_response.model_dump(),
            )

        # Auto-dismiss pending recommendations on close
        role_ids = get_project_role_ids(db, project.id)
        auto_dismiss_pending_recommendations(db, role_ids)

    # ── Apply status change ──────────────────────────────────────────────
    old_status = project.status.value if hasattr(project.status, "value") else str(project.status)
    project.status = body.status
    db.commit()
    db.refresh(project)

    # Dispatch event
    dispatch_event_safe(EventPayload(
        event_type=EventType.PROJECT_STATUS_CHANGED,
        entity_type="project",
        entity_id=str(project.id),
        actor_id=current_user.user_id,
        data={
            "old_status": old_status,
            "new_status": body.status,
            "project_name": project.name,
            "force": body.force,
            "close_note": body.close_note,
        },
    ))

    return _project_to_response(project)
