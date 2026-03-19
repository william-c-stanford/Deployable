"""Add inactive override metadata fields to technicians table.

Revision ID: 005_inactive_override
Revises: 004_add_merge_history
Create Date: 2026-03-19

Adds columns for tracking manual Inactive override lock metadata:
- inactive_locked_at: When the lock was applied
- inactive_locked_by: User ID who applied the lock
- inactive_lock_reason: Reason for manual deactivation
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "005_inactive_override"
down_revision = "004_add_merge_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "technicians",
        sa.Column(
            "inactive_locked_at",
            sa.DateTime(),
            nullable=True,
            comment="When the manual Inactive override was applied",
        ),
    )
    op.add_column(
        "technicians",
        sa.Column(
            "inactive_locked_by",
            sa.String(200),
            nullable=True,
            comment="User ID who set the manual Inactive override",
        ),
    )
    op.add_column(
        "technicians",
        sa.Column(
            "inactive_lock_reason",
            sa.Text(),
            nullable=True,
            comment="Reason for manually setting Inactive status",
        ),
    )


def downgrade() -> None:
    op.drop_column("technicians", "inactive_lock_reason")
    op.drop_column("technicians", "inactive_locked_by")
    op.drop_column("technicians", "inactive_locked_at")
