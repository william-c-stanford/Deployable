"""Models for recommendation merge history and batch job execution audit logs.

RecommendationMergeHistory tracks every merge operation performed during
nightly batch refreshes — recording which recommendations were added,
removed (disqualified), retained, or superseded, along with the reason.

BatchJobExecution records each batch job run (nightly refresh, score
refresh, cert expiry scan, etc.) with timing, status, and summary stats
for auditing and debugging.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    ForeignKey,
    String,
    Text,
    Boolean,
    Integer,
    Float,
    DateTime,
    JSON,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MergeAction(str, enum.Enum):
    """What happened to a recommendation during a merge cycle."""
    ADDED = "added"              # New qualifier entered the list
    REMOVED = "removed"          # Disqualified and removed
    RETAINED = "retained"        # Kept from previous batch
    SUPERSEDED = "superseded"    # Replaced by a higher-scored rec
    SCORE_UPDATED = "score_updated"  # Score changed but still on list
    RANK_CHANGED = "rank_changed"    # Rank position shifted


class BatchJobStatus(str, enum.Enum):
    """Lifecycle status of a batch job execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"          # Some roles succeeded, some failed


class BatchJobType(str, enum.Enum):
    """Types of batch jobs that can be executed."""
    NIGHTLY_REFRESH = "nightly_refresh"
    SCORE_REFRESH = "score_refresh"
    CERT_EXPIRY_SCAN = "cert_expiry_scan"
    FORWARD_STAFFING_SCAN = "forward_staffing_scan"
    PREFERENCE_RULE_REFRESH = "preference_rule_refresh"
    ESCALATION_SCAN = "escalation_scan"


# ---------------------------------------------------------------------------
# RecommendationMergeHistory
# ---------------------------------------------------------------------------

class RecommendationMergeHistory(Base):
    """Immutable audit log of every recommendation change during a merge cycle.

    Each row represents one recommendation that was touched (added, removed,
    retained, etc.) during a single batch merge operation for a specific role.
    """

    __tablename__ = "recommendation_merge_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Link to the batch job that produced this merge
    batch_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batch_job_executions.id"),
        nullable=False,
        index=True,
    )
    batch_id = Column(String(200), nullable=True, index=True)  # Recommendation batch_id

    # What was merged
    role_id = Column(String(200), nullable=False, index=True)
    project_id = Column(String(200), nullable=True, index=True)
    technician_id = Column(String(200), nullable=False, index=True)
    recommendation_id = Column(UUID(as_uuid=True), nullable=True)  # May be null for removals

    # Merge details
    action = Column(String(30), nullable=False)  # MergeAction value
    reason = Column(Text, nullable=True)          # Human-readable reason

    # Score snapshot at merge time
    previous_score = Column(Float, nullable=True)
    new_score = Column(Float, nullable=True)
    previous_rank = Column(Integer, nullable=True)
    new_rank = Column(Integer, nullable=True)

    # Scorecard snapshot (5-dimension scores at merge time)
    scorecard_snapshot = Column(JSON, nullable=True)

    # Disqualification details (for removals)
    disqualification_reasons = Column(JSON, nullable=True)  # List of reasons

    # Metadata
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_merge_history_batch_role", "batch_job_id", "role_id"),
        Index("ix_merge_history_tech_action", "technician_id", "action"),
    )

    def __repr__(self):
        return (
            f"<MergeHistory {self.action} tech={self.technician_id} "
            f"role={self.role_id}>"
        )


# ---------------------------------------------------------------------------
# BatchJobExecution
# ---------------------------------------------------------------------------

class BatchJobExecution(Base):
    """Audit log for each batch job execution.

    Records timing, status, error details, and summary statistics
    for nightly refreshes, score recalculations, cert scans, etc.
    """

    __tablename__ = "batch_job_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Job identification
    job_type = Column(String(60), nullable=False, index=True)  # BatchJobType value
    job_name = Column(String(200), nullable=True)              # Human-readable name
    trigger = Column(String(100), nullable=True)               # What triggered it: "celery_beat", "manual", "event"
    correlation_id = Column(String(100), nullable=True, index=True)

    # Scope
    project_id = Column(String(200), nullable=True)
    role_id = Column(String(200), nullable=True)

    # Status and timing
    status = Column(String(30), nullable=False, default=BatchJobStatus.PENDING.value)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Summary statistics
    roles_processed = Column(Integer, default=0)
    recommendations_added = Column(Integer, default=0)
    recommendations_removed = Column(Integer, default=0)
    recommendations_retained = Column(Integer, default=0)
    recommendations_superseded = Column(Integer, default=0)
    scores_updated = Column(Integer, default=0)
    total_candidates_evaluated = Column(Integer, default=0)

    # Error handling
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)   # Stack trace, partial failures
    warnings = Column(JSON, nullable=True)         # Non-fatal issues

    # Full results summary
    results_summary = Column(JSON, nullable=True)  # Per-role breakdown

    # Metadata
    initiated_by = Column(String(200), nullable=True)  # User or "system"
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    merge_history_entries = relationship(
        "RecommendationMergeHistory",
        backref="batch_job",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_batch_job_type_status", "job_type", "status"),
        Index("ix_batch_job_created", "created_at"),
    )

    def __repr__(self):
        return (
            f"<BatchJobExecution {self.job_type} status={self.status} "
            f"started={self.started_at}>"
        )
