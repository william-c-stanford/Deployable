"""Smart Merge Algorithm for Recommendation Lifecycle.

Compares new readiness evaluations against existing recommendations and
merges/updates/expires them without duplicating or losing prior context.

Core invariants:
  1. Never resurface dismissed or acted-on (Approved/Rejected) recommendations
  2. Update existing pending recs in-place when tech+role pair already exists
  3. Expire (supersede) recs where the technician is now disqualified
  4. Add new qualifying candidates that weren't previously recommended
  5. Preserve metadata, rejection reasons, and prior context across merges
  6. Track score deltas for ops visibility into recommendation drift

Usage:
  Called by:
    - Nightly batch job (full refresh across all unfilled roles)
    - Forward staffing scan (gap-based proactive recommendations)
    - On-demand refresh (single role or single technician)
    - Preference rule change (re-evaluate all pending recs)

Architecture contract:
  New evaluations (list of scorecards) → SmartMerge → Updated DB state
    - Existing pending recs updated in-place (score, scorecard, explanation)
    - Disqualified pending recs superseded with reason
    - New candidates added as fresh Pending recommendations
    - Dismissed/Approved/Rejected recs are NEVER touched
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Any, Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.technician import Technician, DeployabilityStatus
from app.models.project import Project, ProjectRole, ProjectStatus
from app.models.assignment import Assignment
from app.models.recommendation import (
    Recommendation,
    RecommendationStatus,
    PreferenceRule,
)
from app.services.scoring import score_technician_for_role, rank_technicians_for_role

logger = logging.getLogger("deployable.smart_merge")

# Terminal statuses that should never be touched by smart merge
TERMINAL_STATUSES = frozenset({
    RecommendationStatus.APPROVED.value,
    RecommendationStatus.REJECTED.value,
    RecommendationStatus.DISMISSED.value,
})

# Score change threshold to trigger explanation refresh (percentage points)
SCORE_DRIFT_THRESHOLD = 5.0


@dataclass
class MergeAction:
    """Describes a single action taken during a smart merge."""
    action: str  # "updated", "superseded", "added", "unchanged", "skipped"
    recommendation_id: Optional[str] = None
    technician_id: Optional[str] = None
    role_id: Optional[str] = None
    reason: str = ""
    old_score: Optional[float] = None
    new_score: Optional[float] = None
    score_delta: Optional[float] = None


@dataclass
class SmartMergeResult:
    """Result summary of a smart merge operation."""
    role_id: str
    project_id: Optional[str] = None
    batch_id: str = ""
    updated: int = 0
    superseded: int = 0
    added: int = 0
    unchanged: int = 0
    skipped_terminal: int = 0
    actions: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.updated + self.superseded + self.added + self.unchanged

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "project_id": self.project_id,
            "batch_id": self.batch_id,
            "updated": self.updated,
            "superseded": self.superseded,
            "added": self.added,
            "unchanged": self.unchanged,
            "skipped_terminal": self.skipped_terminal,
            "total_processed": self.total_processed,
            "actions": [
                {
                    "action": a.action,
                    "recommendation_id": a.recommendation_id,
                    "technician_id": a.technician_id,
                    "reason": a.reason,
                    "old_score": a.old_score,
                    "new_score": a.new_score,
                    "score_delta": a.score_delta,
                }
                for a in self.actions
            ],
            "errors": self.errors,
        }


def _get_terminal_tech_ids(session: Session, role_id: str) -> set[str]:
    """Get technician IDs with terminal-status recommendations for this role.

    These technicians must NEVER be resurfaced as new recommendations.
    """
    terminal_recs = (
        session.query(Recommendation.target_entity_id)
        .filter(
            Recommendation.role_id == role_id,
            Recommendation.recommendation_type == "staffing",
            Recommendation.status.in_(list(TERMINAL_STATUSES)),
        )
        .all()
    )
    return {r[0] for r in terminal_recs if r[0]}


def _get_assigned_tech_ids(session: Session, role_id: str) -> set[str]:
    """Get technician IDs currently assigned to this role."""
    assignments = (
        session.query(Assignment.technician_id)
        .filter(
            Assignment.role_id == role_id,
            Assignment.status.in_(["Active", "Pre-Booked"]),
        )
        .all()
    )
    return {str(a[0]) for a in assignments if a[0]}


def _build_existing_pending_map(
    session: Session,
    role_id: str,
    recommendation_type: str = "staffing",
) -> dict[str, Recommendation]:
    """Build a map of technician_id -> existing Pending recommendation for this role.

    This is the key data structure for smart merge: we look up by tech+role
    to decide whether to update in-place vs. create new.
    """
    pending_recs = (
        session.query(Recommendation)
        .filter(
            Recommendation.role_id == role_id,
            Recommendation.recommendation_type == recommendation_type,
            Recommendation.status == RecommendationStatus.PENDING.value,
        )
        .all()
    )
    result = {}
    for rec in pending_recs:
        tech_id = rec.target_entity_id or rec.technician_id
        if tech_id:
            result[str(tech_id)] = rec
    return result


def _compute_score_delta(old_score: Optional[float], new_score: float) -> float:
    """Compute the absolute score change."""
    if old_score is None:
        return new_score
    return round(new_score - old_score, 1)


def _should_refresh_explanation(old_score: Optional[float], new_score: float) -> bool:
    """Determine if the explanation should be regenerated based on score drift."""
    if old_score is None:
        return True
    return abs(new_score - old_score) >= SCORE_DRIFT_THRESHOLD


def _preserve_prior_context(existing_rec: Recommendation, new_scorecard: dict) -> dict:
    """Merge prior context into the new scorecard metadata.

    Preserves:
    - Score history (last N scores)
    - Prior rejection reasons if any
    - Merge count
    - Original creation date
    """
    metadata = dict(existing_rec.metadata_ or {})

    # Track score history (keep last 10)
    score_history = metadata.get("score_history", [])
    if existing_rec.overall_score is not None:
        score_history.append({
            "score": existing_rec.overall_score,
            "timestamp": existing_rec.updated_at.isoformat() if existing_rec.updated_at else None,
        })
    score_history = score_history[-10:]  # Keep last 10 entries
    metadata["score_history"] = score_history

    # Track merge count
    metadata["merge_count"] = metadata.get("merge_count", 0) + 1

    # Preserve original creation date
    if "original_created_at" not in metadata and existing_rec.created_at:
        metadata["original_created_at"] = existing_rec.created_at.isoformat()

    # Preserve any prior rejection context from sibling recs
    if existing_rec.rejection_reason:
        metadata["prior_rejection_reason"] = existing_rec.rejection_reason

    return metadata


def smart_merge_for_role(
    session: Session,
    role_id: str,
    new_evaluations: list[dict[str, Any]],
    batch_id: Optional[str] = None,
    recommendation_type: str = "staffing",
    agent_name: str = "staffing_agent",
    generate_explanation_fn=None,
    max_recommendations: int = 10,
) -> SmartMergeResult:
    """Execute the smart merge algorithm for a single role.

    Compares new readiness evaluations against existing recommendations
    and performs in-place updates, expirations, and additions.

    Args:
        session: SQLAlchemy session
        role_id: The project role ID
        new_evaluations: List of scorecard dicts from the scoring engine,
            each containing at minimum: technician_id, overall_score, dimensions
        batch_id: Optional batch identifier for traceability
        recommendation_type: "staffing" or "forward_staffing"
        agent_name: Name of the originating agent
        generate_explanation_fn: Optional callable(scorecard, tech_name, role_name, project_name) -> str
        max_recommendations: Maximum number of recommendations to maintain

    Returns:
        SmartMergeResult with detailed action log
    """
    if batch_id is None:
        batch_id = f"merge_{date.today().isoformat()}_{uuid.uuid4().hex[:8]}"

    role = session.get(ProjectRole, role_id)
    project = session.get(Project, role.project_id) if role else None
    project_id = str(project.id) if project else None
    project_name = project.name if project else ""
    role_name = role.role_name if role else ""

    result = SmartMergeResult(
        role_id=role_id,
        project_id=project_id,
        batch_id=batch_id,
    )

    if not role:
        result.errors.append(f"ProjectRole {role_id} not found")
        return result

    now = datetime.now(timezone.utc)

    # Phase 1: Build lookup structures
    terminal_tech_ids = _get_terminal_tech_ids(session, role_id)
    assigned_tech_ids = _get_assigned_tech_ids(session, role_id)
    existing_pending = _build_existing_pending_map(session, role_id, recommendation_type)

    # Track which existing pending recs were matched to new evaluations
    matched_tech_ids: set[str] = set()

    # Phase 2: Process each new evaluation
    new_eval_map: dict[str, dict] = {}
    for evaluation in new_evaluations:
        tech_id = str(evaluation.get("technician_id", ""))
        if not tech_id:
            continue
        new_eval_map[tech_id] = evaluation

    # Sort evaluations by score descending for rank assignment
    sorted_evals = sorted(
        new_eval_map.items(),
        key=lambda x: x[1].get("overall_score", 0),
        reverse=True,
    )

    rank_counter = 0
    for tech_id, evaluation in sorted_evals:
        overall_score = evaluation.get("overall_score", 0)
        disqualified = evaluation.get("disqualified", False)
        disqualification_reason = evaluation.get("disqualification_reason")

        # Skip terminal-status technicians (never resurface)
        if tech_id in terminal_tech_ids:
            result.skipped_terminal += 1
            result.actions.append(MergeAction(
                action="skipped",
                technician_id=tech_id,
                role_id=role_id,
                reason=f"Terminal status recommendation exists (approved/rejected/dismissed)",
            ))
            continue

        # Skip currently assigned technicians
        if tech_id in assigned_tech_ids:
            result.actions.append(MergeAction(
                action="skipped",
                technician_id=tech_id,
                role_id=role_id,
                reason="Currently assigned to this role",
            ))
            continue

        existing_rec = existing_pending.get(tech_id)

        if disqualified:
            # Case A: Technician is now disqualified
            if existing_rec:
                # Supersede the existing pending recommendation
                old_score = existing_rec.overall_score
                existing_rec.status = RecommendationStatus.SUPERSEDED.value
                existing_rec.explanation = (
                    f"Smart merge: disqualified — {disqualification_reason or 'no longer eligible'}"
                )
                existing_rec.updated_at = now
                existing_rec.batch_id = batch_id

                # Preserve context in metadata
                meta = _preserve_prior_context(existing_rec, evaluation)
                meta["superseded_reason"] = disqualification_reason
                meta["superseded_by_batch"] = batch_id
                existing_rec.metadata_ = meta

                matched_tech_ids.add(tech_id)
                result.superseded += 1
                result.actions.append(MergeAction(
                    action="superseded",
                    recommendation_id=str(existing_rec.id),
                    technician_id=tech_id,
                    role_id=role_id,
                    reason=f"Disqualified: {disqualification_reason}",
                    old_score=old_score,
                    new_score=overall_score,
                    score_delta=_compute_score_delta(old_score, overall_score),
                ))
            # If disqualified and no existing rec, just skip (don't add)
            continue

        # Case B: Technician qualifies — check if we should update or add
        rank_counter += 1

        if rank_counter > max_recommendations:
            # Already at max capacity; only update if existing, don't add new
            if existing_rec:
                # Update in-place but note it's beyond the top N
                pass
            else:
                continue

        if existing_rec:
            # Case B.1: Update existing pending recommendation in-place
            old_score = existing_rec.overall_score
            score_delta = _compute_score_delta(old_score, overall_score)

            # Preserve prior context
            meta = _preserve_prior_context(existing_rec, evaluation)
            meta["last_merge_batch"] = batch_id
            meta["score_delta"] = score_delta

            # Update scorecard and score
            existing_rec.scorecard = evaluation
            existing_rec.overall_score = overall_score
            existing_rec.rank = str(rank_counter)
            existing_rec.batch_id = batch_id
            existing_rec.updated_at = now
            existing_rec.metadata_ = meta

            # Regenerate explanation if score drifted significantly
            if _should_refresh_explanation(old_score, overall_score):
                if generate_explanation_fn:
                    tech = session.get(Technician, tech_id)
                    tech_name = tech.full_name if tech else "Unknown"
                    try:
                        existing_rec.explanation = generate_explanation_fn(
                            scorecard=evaluation,
                            technician_name=tech_name,
                            role_name=role_name,
                            project_name=project_name,
                        )
                    except Exception as e:
                        logger.warning("Explanation gen failed for %s: %s", tech_id, e)
                        # Keep existing explanation
                else:
                    # Update with score delta annotation
                    direction = "↑" if score_delta > 0 else "↓" if score_delta < 0 else "→"
                    existing_rec.explanation = (
                        f"{existing_rec.explanation or ''} "
                        f"[Score {direction} {abs(score_delta):.1f} pts in latest evaluation]"
                    ).strip()

            matched_tech_ids.add(tech_id)

            if abs(score_delta) < 0.1:
                result.unchanged += 1
                result.actions.append(MergeAction(
                    action="unchanged",
                    recommendation_id=str(existing_rec.id),
                    technician_id=tech_id,
                    role_id=role_id,
                    reason="Score unchanged",
                    old_score=old_score,
                    new_score=overall_score,
                    score_delta=score_delta,
                ))
            else:
                result.updated += 1
                result.actions.append(MergeAction(
                    action="updated",
                    recommendation_id=str(existing_rec.id),
                    technician_id=tech_id,
                    role_id=role_id,
                    reason=f"Score changed {score_delta:+.1f} pts",
                    old_score=old_score,
                    new_score=overall_score,
                    score_delta=score_delta,
                ))

        else:
            # Case B.2: New candidate not previously recommended — add
            tech = session.get(Technician, tech_id)
            tech_name = tech.full_name if tech else "Unknown"

            explanation = None
            if generate_explanation_fn:
                try:
                    explanation = generate_explanation_fn(
                        scorecard=evaluation,
                        technician_name=tech_name,
                        role_name=role_name,
                        project_name=project_name,
                    )
                except Exception as e:
                    logger.warning("Explanation gen failed for new rec %s: %s", tech_id, e)

            if not explanation:
                explanation = (
                    f"{tech_name} scored {overall_score:.1f}/100 for {role_name}"
                    f"{' on ' + project_name if project_name else ''}. "
                    f"Added via smart merge (batch: {batch_id})."
                )

            new_rec = Recommendation(
                recommendation_type=recommendation_type,
                target_entity_type="technician",
                target_entity_id=tech_id,
                technician_id=tech_id,
                role_id=role_id,
                project_id=project_id,
                rank=str(rank_counter),
                overall_score=overall_score,
                scorecard=evaluation,
                explanation=explanation,
                status=RecommendationStatus.PENDING.value,
                agent_name=agent_name,
                batch_id=batch_id,
                metadata_={
                    "project_name": project_name,
                    "role_name": role_name,
                    "source": "smart_merge",
                    "merge_count": 0,
                },
            )
            session.add(new_rec)
            result.added += 1
            result.actions.append(MergeAction(
                action="added",
                technician_id=tech_id,
                role_id=role_id,
                reason=f"New qualifying candidate (score {overall_score:.1f})",
                new_score=overall_score,
            ))

    # Phase 3: Supersede stale pending recs that weren't in the new evaluation set
    for tech_id, existing_rec in existing_pending.items():
        if tech_id in matched_tech_ids:
            continue  # Already handled

        # This technician was previously recommended but is no longer in the
        # new evaluation results — they may have become disqualified or simply
        # dropped below the cutoff.
        old_score = existing_rec.overall_score
        existing_rec.status = RecommendationStatus.SUPERSEDED.value
        existing_rec.explanation = (
            f"Smart merge: no longer in top candidates after re-evaluation "
            f"(was score {old_score:.1f}). Batch: {batch_id}"
        )
        existing_rec.updated_at = now

        meta = _preserve_prior_context(existing_rec, {})
        meta["superseded_reason"] = "dropped_from_evaluation"
        meta["superseded_by_batch"] = batch_id
        existing_rec.metadata_ = meta

        result.superseded += 1
        result.actions.append(MergeAction(
            action="superseded",
            recommendation_id=str(existing_rec.id),
            technician_id=tech_id,
            role_id=role_id,
            reason="No longer in top candidates after re-evaluation",
            old_score=old_score,
        ))

    logger.info(
        "Smart merge for role %s: %d updated, %d superseded, %d added, "
        "%d unchanged, %d terminal-skipped (batch: %s)",
        role_id, result.updated, result.superseded, result.added,
        result.unchanged, result.skipped_terminal, batch_id,
    )

    return result


def smart_merge_nightly_batch(
    session: Session,
    generate_explanation_fn=None,
) -> dict[str, Any]:
    """Execute smart merge across all unfilled roles.

    Called by the nightly batch Celery task. For each unfilled role on
    active/staffing projects:
    1. Re-score all eligible technicians
    2. Smart-merge against existing recommendations
    3. Never resurface dismissed or acted-on recs

    Returns aggregate stats.
    """
    batch_id = f"nightly_{date.today().isoformat()}_{uuid.uuid4().hex[:6]}"
    aggregate = {
        "batch_id": batch_id,
        "roles_processed": 0,
        "total_updated": 0,
        "total_superseded": 0,
        "total_added": 0,
        "total_unchanged": 0,
        "total_terminal_skipped": 0,
        "role_results": [],
        "errors": [],
    }

    # Load preference rules once
    preference_rules = (
        session.query(PreferenceRule).filter(PreferenceRule.active == True).all()
    )

    # Find all active/staffing projects with unfilled roles
    active_projects = (
        session.query(Project)
        .filter(Project.status.in_([ProjectStatus.STAFFING, ProjectStatus.ACTIVE]))
        .all()
    )

    for project in active_projects:
        roles = (
            session.query(ProjectRole)
            .filter(ProjectRole.project_id == project.id)
            .all()
        )

        for role in roles:
            unfilled = role.quantity - (role.filled or 0)
            if unfilled <= 0:
                continue

            aggregate["roles_processed"] += 1

            try:
                # Get terminal tech IDs to build exclusion set for scoring
                terminal_ids = _get_terminal_tech_ids(session, str(role.id))
                assigned_ids = _get_assigned_tech_ids(session, str(role.id))
                exclude_ids = terminal_ids | assigned_ids

                # Score all eligible technicians (the scoring engine handles
                # disqualification and preference rules internally)
                evaluations = rank_technicians_for_role(
                    session, role, project,
                    limit=15,  # Get extra to account for smart merge filtering
                    exclude_ids=exclude_ids,
                )

                # Run smart merge
                merge_result = smart_merge_for_role(
                    session=session,
                    role_id=str(role.id),
                    new_evaluations=evaluations,
                    batch_id=batch_id,
                    recommendation_type="staffing",
                    agent_name="nightly_batch_agent",
                    generate_explanation_fn=generate_explanation_fn,
                    max_recommendations=10,
                )

                aggregate["total_updated"] += merge_result.updated
                aggregate["total_superseded"] += merge_result.superseded
                aggregate["total_added"] += merge_result.added
                aggregate["total_unchanged"] += merge_result.unchanged
                aggregate["total_terminal_skipped"] += merge_result.skipped_terminal

                aggregate["role_results"].append(merge_result.to_dict())

            except Exception as e:
                logger.error("Smart merge failed for role %s: %s", role.id, e)
                aggregate["errors"].append({
                    "role_id": str(role.id),
                    "error": str(e),
                })

    # Commit all changes in a single transaction
    try:
        session.commit()
    except Exception as e:
        session.rollback()
        aggregate["errors"].append({"error": f"Commit failed: {str(e)}"})
        raise

    logger.info(
        "Nightly smart merge complete: %d roles, %d updated, %d superseded, "
        "%d added, %d unchanged (batch: %s)",
        aggregate["roles_processed"],
        aggregate["total_updated"],
        aggregate["total_superseded"],
        aggregate["total_added"],
        aggregate["total_unchanged"],
        batch_id,
    )

    return aggregate


def smart_merge_for_technician(
    session: Session,
    technician_id: str,
    batch_id: Optional[str] = None,
    generate_explanation_fn=None,
) -> dict[str, Any]:
    """Re-evaluate all pending recommendations involving a specific technician.

    Called when a technician's status, skills, or certifications change.
    Updates existing pending recs in-place with new scores.

    Returns aggregate stats across all affected roles.
    """
    if batch_id is None:
        batch_id = f"tech_refresh_{uuid.uuid4().hex[:8]}"

    technician = session.get(Technician, technician_id)
    if not technician:
        return {"error": f"Technician {technician_id} not found"}

    # Find all pending recommendations for this technician
    pending_recs = (
        session.query(Recommendation)
        .filter(
            Recommendation.target_entity_id == str(technician_id),
            Recommendation.target_entity_type == "technician",
            Recommendation.status == RecommendationStatus.PENDING.value,
            Recommendation.recommendation_type.in_(["staffing", "forward_staffing"]),
        )
        .all()
    )

    preference_rules = (
        session.query(PreferenceRule).filter(PreferenceRule.active == True).all()
    )

    now = datetime.now(timezone.utc)
    stats = {"updated": 0, "superseded": 0, "unchanged": 0, "errors": []}

    for rec in pending_recs:
        try:
            role = session.get(ProjectRole, rec.role_id) if rec.role_id else None
            project = session.get(Project, role.project_id) if role else None

            if not role:
                rec.status = RecommendationStatus.SUPERSEDED.value
                rec.explanation = "Smart merge: role no longer exists"
                rec.updated_at = now
                stats["superseded"] += 1
                continue

            new_scorecard = score_technician_for_role(
                session, technician, role, project, preference_rules
            )

            old_score = rec.overall_score
            new_score = new_scorecard["overall_score"]
            score_delta = _compute_score_delta(old_score, new_score)

            if new_scorecard["disqualified"]:
                # Supersede with reason
                meta = _preserve_prior_context(rec, new_scorecard)
                meta["superseded_reason"] = new_scorecard["disqualification_reason"]
                meta["superseded_by_batch"] = batch_id

                rec.status = RecommendationStatus.SUPERSEDED.value
                rec.explanation = (
                    f"Smart merge: disqualified — {new_scorecard['disqualification_reason']}"
                )
                rec.updated_at = now
                rec.metadata_ = meta
                stats["superseded"] += 1
            elif abs(score_delta) < 0.1:
                stats["unchanged"] += 1
            else:
                # Update in-place
                meta = _preserve_prior_context(rec, new_scorecard)
                meta["last_merge_batch"] = batch_id
                meta["score_delta"] = score_delta

                rec.scorecard = new_scorecard
                rec.overall_score = new_score
                rec.updated_at = now
                rec.batch_id = batch_id
                rec.metadata_ = meta

                if _should_refresh_explanation(old_score, new_score) and generate_explanation_fn:
                    try:
                        rec.explanation = generate_explanation_fn(
                            scorecard=new_scorecard,
                            technician_name=technician.full_name,
                            role_name=role.role_name,
                            project_name=project.name if project else "",
                        )
                    except Exception:
                        pass  # Keep existing explanation

                stats["updated"] += 1

        except Exception as e:
            logger.error("Failed to refresh rec %s: %s", rec.id, e)
            stats["errors"].append({"recommendation_id": str(rec.id), "error": str(e)})

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        stats["errors"].append({"error": f"Commit failed: {str(e)}"})
        raise

    logger.info(
        "Smart merge for technician %s: %d updated, %d superseded, %d unchanged",
        technician_id, stats["updated"], stats["superseded"], stats["unchanged"],
    )

    return stats


def smart_merge_on_preference_rule_change(
    session: Session,
    generate_explanation_fn=None,
) -> dict[str, Any]:
    """Re-evaluate all pending recommendations after a preference rule change.

    Preference rule changes can affect scoring across all roles, so this
    performs a full sweep of all pending staffing recommendations.

    Returns aggregate stats.
    """
    batch_id = f"rule_change_{uuid.uuid4().hex[:8]}"

    preference_rules = (
        session.query(PreferenceRule).filter(PreferenceRule.active == True).all()
    )

    pending_recs = (
        session.query(Recommendation)
        .filter(
            Recommendation.status == RecommendationStatus.PENDING.value,
            Recommendation.recommendation_type.in_(["staffing", "forward_staffing"]),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    stats = {"reevaluated": 0, "updated": 0, "superseded": 0, "unchanged": 0}

    for rec in pending_recs:
        if not rec.target_entity_id or not rec.role_id:
            continue

        tech = session.get(Technician, rec.target_entity_id)
        role = session.get(ProjectRole, rec.role_id)
        if not tech or not role:
            rec.status = RecommendationStatus.SUPERSEDED.value
            rec.updated_at = now
            stats["superseded"] += 1
            continue

        project = session.get(Project, role.project_id)
        new_scorecard = score_technician_for_role(
            session, tech, role, project, preference_rules
        )

        old_score = rec.overall_score
        new_score = new_scorecard["overall_score"]
        score_delta = _compute_score_delta(old_score, new_score)

        if new_scorecard["disqualified"]:
            meta = _preserve_prior_context(rec, new_scorecard)
            meta["superseded_reason"] = f"Rule change: {new_scorecard['disqualification_reason']}"
            meta["superseded_by_batch"] = batch_id

            rec.status = RecommendationStatus.SUPERSEDED.value
            rec.explanation = (
                f"Superseded by preference rule change: "
                f"{new_scorecard['disqualification_reason']}"
            )
            rec.updated_at = now
            rec.metadata_ = meta
            stats["superseded"] += 1
        elif abs(score_delta) < 0.1:
            stats["unchanged"] += 1
        else:
            meta = _preserve_prior_context(rec, new_scorecard)
            meta["last_merge_batch"] = batch_id
            meta["score_delta"] = score_delta

            rec.scorecard = new_scorecard
            rec.overall_score = new_score
            rec.updated_at = now
            rec.batch_id = batch_id
            rec.metadata_ = meta

            if _should_refresh_explanation(old_score, new_score) and generate_explanation_fn:
                try:
                    rec.explanation = generate_explanation_fn(
                        scorecard=new_scorecard,
                        technician_name=tech.full_name,
                        role_name=role.role_name,
                        project_name=project.name if project else "",
                    )
                except Exception:
                    pass

            stats["updated"] += 1

        stats["reevaluated"] += 1

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        raise

    logger.info(
        "Preference rule change smart merge: %d evaluated, %d updated, %d superseded",
        stats["reevaluated"], stats["updated"], stats["superseded"],
    )

    return stats
