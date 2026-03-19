"""Add recommendation_merge_history and batch_job_executions tables.

Revision ID: 004_add_merge_history
Revises: 003_enhance_headcount
Create Date: 2026-03-19

These tables support the Smart Merge Recommendation Lifecycle:
- recommendation_merge_history tracks every add/remove/retain/supersede
  action during nightly batch refreshes for full audit trail
- batch_job_executions records each batch job run with timing, status,
  and summary statistics
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "004_add_merge_history"
down_revision = "003_enhance_headcount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- batch_job_executions --
    op.create_table(
        "batch_job_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(60), nullable=False),
        sa.Column("job_name", sa.String(200), nullable=True),
        sa.Column("trigger", sa.String(100), nullable=True),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        sa.Column("project_id", sa.String(200), nullable=True),
        sa.Column("role_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("roles_processed", sa.Integer, server_default="0"),
        sa.Column("recommendations_added", sa.Integer, server_default="0"),
        sa.Column("recommendations_removed", sa.Integer, server_default="0"),
        sa.Column("recommendations_retained", sa.Integer, server_default="0"),
        sa.Column("recommendations_superseded", sa.Integer, server_default="0"),
        sa.Column("scores_updated", sa.Integer, server_default="0"),
        sa.Column("total_candidates_evaluated", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("error_details", postgresql.JSON, nullable=True),
        sa.Column("warnings", postgresql.JSON, nullable=True),
        sa.Column("results_summary", postgresql.JSON, nullable=True),
        sa.Column("initiated_by", sa.String(200), nullable=True),
        sa.Column("metadata", postgresql.JSON, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_batch_job_type", "batch_job_executions", ["job_type"])
    op.create_index("ix_batch_job_correlation", "batch_job_executions", ["correlation_id"])
    op.create_index("ix_batch_job_type_status", "batch_job_executions", ["job_type", "status"])
    op.create_index("ix_batch_job_created", "batch_job_executions", ["created_at"])

    # -- recommendation_merge_history --
    op.create_table(
        "recommendation_merge_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", sa.String(200), nullable=True),
        sa.Column("role_id", sa.String(200), nullable=False),
        sa.Column("project_id", sa.String(200), nullable=True),
        sa.Column("technician_id", sa.String(200), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("previous_score", sa.Float, nullable=True),
        sa.Column("new_score", sa.Float, nullable=True),
        sa.Column("previous_rank", sa.Integer, nullable=True),
        sa.Column("new_rank", sa.Integer, nullable=True),
        sa.Column("scorecard_snapshot", postgresql.JSON, nullable=True),
        sa.Column("disqualification_reasons", postgresql.JSON, nullable=True),
        sa.Column("metadata", postgresql.JSON, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_job_id"],
            ["batch_job_executions.id"],
            name="fk_merge_history_batch_job",
        ),
    )
    op.create_index("ix_merge_history_batch_job", "recommendation_merge_history", ["batch_job_id"])
    op.create_index("ix_merge_history_batch_id", "recommendation_merge_history", ["batch_id"])
    op.create_index("ix_merge_history_role", "recommendation_merge_history", ["role_id"])
    op.create_index("ix_merge_history_project", "recommendation_merge_history", ["project_id"])
    op.create_index("ix_merge_history_tech", "recommendation_merge_history", ["technician_id"])
    op.create_index(
        "ix_merge_history_batch_role",
        "recommendation_merge_history",
        ["batch_job_id", "role_id"],
    )
    op.create_index(
        "ix_merge_history_tech_action",
        "recommendation_merge_history",
        ["technician_id", "action"],
    )


def downgrade() -> None:
    op.drop_table("recommendation_merge_history")
    op.drop_table("batch_job_executions")
