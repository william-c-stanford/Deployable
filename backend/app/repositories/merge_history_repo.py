"""Repository methods for recommendation merge history and batch job execution.

All methods accept a SQLAlchemy Session and perform reads/writes through
the ORM — no direct DB access outside the session.
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import desc, and_, func
from sqlalchemy.orm import Session

from app.models.merge_history import (
    RecommendationMergeHistory,
    BatchJobExecution,
    MergeAction,
    BatchJobStatus,
    BatchJobType,
)

logger = logging.getLogger("deployable.repo.merge_history")


# ===========================================================================
# BatchJobExecution repository
# ===========================================================================

def create_batch_job(
    db: Session,
    job_type: str,
    *,
    job_name: Optional[str] = None,
    trigger: str = "celery_beat",
    correlation_id: Optional[str] = None,
    project_id: Optional[str] = None,
    role_id: Optional[str] = None,
    initiated_by: str = "system",
    metadata: Optional[dict] = None,
) -> BatchJobExecution:
    """Create a new batch job execution record in PENDING status.

    Call this at the start of a batch job to establish the audit trail.
    Returns the job so the caller can update it as the job progresses.
    """
    job = BatchJobExecution(
        id=uuid.uuid4(),
        job_type=job_type,
        job_name=job_name or f"{job_type} batch",
        trigger=trigger,
        correlation_id=correlation_id or str(uuid.uuid4()),
        project_id=project_id,
        role_id=role_id,
        status=BatchJobStatus.PENDING.value,
        initiated_by=initiated_by,
        metadata_=metadata or {},
    )
    db.add(job)
    db.flush()  # Get the ID without committing
    return job


def start_batch_job(db: Session, job_id: uuid.UUID) -> BatchJobExecution:
    """Mark a batch job as RUNNING with a start timestamp."""
    job = db.query(BatchJobExecution).filter(BatchJobExecution.id == job_id).first()
    if not job:
        raise ValueError(f"Batch job {job_id} not found")
    job.status = BatchJobStatus.RUNNING.value
    job.started_at = datetime.now(timezone.utc)
    db.flush()
    return job


def complete_batch_job(
    db: Session,
    job_id: uuid.UUID,
    *,
    roles_processed: int = 0,
    recommendations_added: int = 0,
    recommendations_removed: int = 0,
    recommendations_retained: int = 0,
    recommendations_superseded: int = 0,
    scores_updated: int = 0,
    total_candidates_evaluated: int = 0,
    results_summary: Optional[dict] = None,
    warnings: Optional[list] = None,
) -> BatchJobExecution:
    """Mark a batch job as COMPLETED with summary statistics."""
    job = db.query(BatchJobExecution).filter(BatchJobExecution.id == job_id).first()
    if not job:
        raise ValueError(f"Batch job {job_id} not found")

    now = datetime.now(timezone.utc)
    job.status = BatchJobStatus.COMPLETED.value
    job.completed_at = now
    if job.started_at:
        job.duration_seconds = (now - job.started_at).total_seconds()

    job.roles_processed = roles_processed
    job.recommendations_added = recommendations_added
    job.recommendations_removed = recommendations_removed
    job.recommendations_retained = recommendations_retained
    job.recommendations_superseded = recommendations_superseded
    job.scores_updated = scores_updated
    job.total_candidates_evaluated = total_candidates_evaluated
    job.results_summary = results_summary
    job.warnings = warnings

    db.flush()
    return job


def fail_batch_job(
    db: Session,
    job_id: uuid.UUID,
    error_message: str,
    *,
    error_details: Optional[dict] = None,
    partial: bool = False,
) -> BatchJobExecution:
    """Mark a batch job as FAILED or PARTIAL with error info."""
    job = db.query(BatchJobExecution).filter(BatchJobExecution.id == job_id).first()
    if not job:
        raise ValueError(f"Batch job {job_id} not found")

    now = datetime.now(timezone.utc)
    job.status = BatchJobStatus.PARTIAL.value if partial else BatchJobStatus.FAILED.value
    job.completed_at = now
    if job.started_at:
        job.duration_seconds = (now - job.started_at).total_seconds()
    job.error_message = error_message
    job.error_details = error_details

    db.flush()
    return job


def get_batch_job(db: Session, job_id: uuid.UUID) -> Optional[BatchJobExecution]:
    """Get a batch job by ID."""
    return db.query(BatchJobExecution).filter(BatchJobExecution.id == job_id).first()


def list_batch_jobs(
    db: Session,
    *,
    job_type: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[BatchJobExecution], int]:
    """List batch job executions with filtering.

    Returns (jobs, total_count).
    """
    query = db.query(BatchJobExecution)

    if job_type:
        query = query.filter(BatchJobExecution.job_type == job_type)
    if status:
        query = query.filter(BatchJobExecution.status == status)
    if since:
        query = query.filter(BatchJobExecution.created_at >= since)

    total = query.count()
    jobs = (
        query.order_by(desc(BatchJobExecution.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return jobs, total


def get_last_successful_job(
    db: Session,
    job_type: str,
    *,
    role_id: Optional[str] = None,
) -> Optional[BatchJobExecution]:
    """Get the most recent successfully completed job of a given type."""
    query = db.query(BatchJobExecution).filter(
        and_(
            BatchJobExecution.job_type == job_type,
            BatchJobExecution.status == BatchJobStatus.COMPLETED.value,
        )
    )
    if role_id:
        query = query.filter(BatchJobExecution.role_id == role_id)

    return query.order_by(desc(BatchJobExecution.completed_at)).first()


# ===========================================================================
# RecommendationMergeHistory repository
# ===========================================================================

def record_merge_action(
    db: Session,
    *,
    batch_job_id: uuid.UUID,
    role_id: str,
    technician_id: str,
    action: str,
    batch_id: Optional[str] = None,
    project_id: Optional[str] = None,
    recommendation_id: Optional[uuid.UUID] = None,
    reason: Optional[str] = None,
    previous_score: Optional[float] = None,
    new_score: Optional[float] = None,
    previous_rank: Optional[int] = None,
    new_rank: Optional[int] = None,
    scorecard_snapshot: Optional[dict] = None,
    disqualification_reasons: Optional[list] = None,
    metadata: Optional[dict] = None,
) -> RecommendationMergeHistory:
    """Record a single merge action for audit trail."""
    entry = RecommendationMergeHistory(
        batch_job_id=batch_job_id,
        batch_id=batch_id,
        role_id=role_id,
        project_id=project_id,
        technician_id=technician_id,
        recommendation_id=recommendation_id,
        action=action,
        reason=reason,
        previous_score=previous_score,
        new_score=new_score,
        previous_rank=previous_rank,
        new_rank=new_rank,
        scorecard_snapshot=scorecard_snapshot,
        disqualification_reasons=disqualification_reasons,
        metadata_=metadata or {},
    )
    db.add(entry)
    return entry


def record_merge_actions_bulk(
    db: Session,
    entries: list[dict],
) -> list[RecommendationMergeHistory]:
    """Record multiple merge actions in bulk for efficiency.

    Each dict in entries should contain the kwargs for record_merge_action.
    """
    records = []
    for entry_data in entries:
        meta = entry_data.pop("metadata", None)
        entry = RecommendationMergeHistory(
            **entry_data,
            metadata_=meta or {},
        )
        db.add(entry)
        records.append(entry)
    db.flush()
    return records


def get_merge_history_for_role(
    db: Session,
    role_id: str,
    *,
    batch_job_id: Optional[uuid.UUID] = None,
    action: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[RecommendationMergeHistory], int]:
    """Get merge history entries for a specific role.

    Returns (entries, total_count).
    """
    query = db.query(RecommendationMergeHistory).filter(
        RecommendationMergeHistory.role_id == role_id
    )
    if batch_job_id:
        query = query.filter(RecommendationMergeHistory.batch_job_id == batch_job_id)
    if action:
        query = query.filter(RecommendationMergeHistory.action == action)

    total = query.count()
    entries = (
        query.order_by(desc(RecommendationMergeHistory.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return entries, total


def get_merge_history_for_technician(
    db: Session,
    technician_id: str,
    *,
    since: Optional[datetime] = None,
    limit: int = 50,
) -> list[RecommendationMergeHistory]:
    """Get merge history entries for a specific technician."""
    query = db.query(RecommendationMergeHistory).filter(
        RecommendationMergeHistory.technician_id == technician_id
    )
    if since:
        query = query.filter(RecommendationMergeHistory.created_at >= since)

    return (
        query.order_by(desc(RecommendationMergeHistory.created_at))
        .limit(limit)
        .all()
    )


def get_merge_history_for_batch_job(
    db: Session,
    batch_job_id: uuid.UUID,
) -> list[RecommendationMergeHistory]:
    """Get all merge history entries for a specific batch job execution."""
    return (
        db.query(RecommendationMergeHistory)
        .filter(RecommendationMergeHistory.batch_job_id == batch_job_id)
        .order_by(
            RecommendationMergeHistory.role_id,
            RecommendationMergeHistory.new_rank,
        )
        .all()
    )


def get_merge_summary_for_batch(
    db: Session,
    batch_job_id: uuid.UUID,
) -> dict:
    """Get aggregated summary of merge actions for a batch job.

    Returns dict with counts per action type and per role.
    """
    rows = (
        db.query(
            RecommendationMergeHistory.action,
            func.count(RecommendationMergeHistory.id).label("count"),
        )
        .filter(RecommendationMergeHistory.batch_job_id == batch_job_id)
        .group_by(RecommendationMergeHistory.action)
        .all()
    )

    action_counts = {row.action: row.count for row in rows}

    role_rows = (
        db.query(
            RecommendationMergeHistory.role_id,
            RecommendationMergeHistory.action,
            func.count(RecommendationMergeHistory.id).label("count"),
        )
        .filter(RecommendationMergeHistory.batch_job_id == batch_job_id)
        .group_by(
            RecommendationMergeHistory.role_id,
            RecommendationMergeHistory.action,
        )
        .all()
    )

    per_role: dict[str, dict[str, int]] = {}
    for row in role_rows:
        if row.role_id not in per_role:
            per_role[row.role_id] = {}
        per_role[row.role_id][row.action] = row.count

    return {
        "batch_job_id": str(batch_job_id),
        "action_counts": action_counts,
        "per_role": per_role,
        "total_entries": sum(action_counts.values()),
    }


def was_technician_previously_dismissed(
    db: Session,
    technician_id: str,
    role_id: str,
    *,
    lookback_days: int = 30,
) -> bool:
    """Check if a technician was dismissed or rejected for a role recently.

    Used by nightly batch to avoid resurfacing dismissed/acted-on recommendations.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    count = (
        db.query(func.count(RecommendationMergeHistory.id))
        .filter(
            and_(
                RecommendationMergeHistory.technician_id == technician_id,
                RecommendationMergeHistory.role_id == role_id,
                RecommendationMergeHistory.action.in_([
                    MergeAction.REMOVED.value,
                ]),
                RecommendationMergeHistory.created_at >= cutoff,
            )
        )
        .scalar()
    )
    return (count or 0) > 0


def get_dismissed_technician_ids_for_role(
    db: Session,
    role_id: str,
    *,
    lookback_days: int = 30,
) -> list[str]:
    """Get list of technician IDs that were previously removed for a role.

    Used to exclude them from new recommendation batches (never resurface).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = (
        db.query(RecommendationMergeHistory.technician_id)
        .filter(
            and_(
                RecommendationMergeHistory.role_id == role_id,
                RecommendationMergeHistory.action == MergeAction.REMOVED.value,
                RecommendationMergeHistory.created_at >= cutoff,
            )
        )
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


def cleanup_old_merge_history(
    db: Session,
    *,
    older_than_days: int = 90,
) -> int:
    """Delete merge history entries older than the specified number of days.

    Returns the number of deleted rows.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    count = (
        db.query(RecommendationMergeHistory)
        .filter(RecommendationMergeHistory.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.flush()
    return count
