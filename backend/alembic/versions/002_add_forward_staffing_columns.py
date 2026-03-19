"""Add forward staffing and assignment chaining columns to assignments table.

Revision ID: 002_forward_staffing
Revises: 001_training
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "002_forward_staffing"
down_revision = "001_training"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Forward staffing / pre-booking fields
    op.add_column("assignments", sa.Column("is_forward_booked", sa.Boolean(), server_default="false", nullable=True))
    op.add_column("assignments", sa.Column("booking_confidence", sa.Float(), nullable=True))
    op.add_column("assignments", sa.Column("confirmed_at", sa.DateTime(), nullable=True))
    op.add_column("assignments", sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True))

    # Assignment chaining fields
    op.add_column("assignments", sa.Column(
        "previous_assignment_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("assignments.id"), nullable=True,
    ))
    op.add_column("assignments", sa.Column(
        "next_assignment_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("assignments.id"), nullable=True,
    ))
    op.add_column("assignments", sa.Column("chain_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("assignments", sa.Column("chain_position", sa.Integer(), nullable=True))
    op.add_column("assignments", sa.Column(
        "chain_priority",
        sa.Enum("High", "Medium", "Low", name="chain_priority_enum"),
        nullable=True,
    ))
    op.add_column("assignments", sa.Column("gap_days", sa.Integer(), nullable=True))
    op.add_column("assignments", sa.Column("chain_notes", sa.Text(), nullable=True))

    # Add new status values to assignment_status (Pre-Booked, Pending Confirmation)
    # Note: PostgreSQL enums need ALTER TYPE for new values
    # These are stored as String(30) in the model, so no enum alter needed for status column

    # Indexes for forward staffing queries
    op.create_index("ix_assignments_tech_dates", "assignments", ["technician_id", "start_date", "end_date"])
    op.create_index("ix_assignments_forward", "assignments", ["is_forward_booked", "start_date"])
    op.create_index("ix_assignments_chain", "assignments", ["chain_id", "chain_position"])
    op.create_index("ix_assignments_prev", "assignments", ["previous_assignment_id"])


def downgrade() -> None:
    op.drop_index("ix_assignments_prev", table_name="assignments")
    op.drop_index("ix_assignments_chain", table_name="assignments")
    op.drop_index("ix_assignments_forward", table_name="assignments")
    op.drop_index("ix_assignments_tech_dates", table_name="assignments")

    op.drop_column("assignments", "chain_notes")
    op.drop_column("assignments", "gap_days")
    op.drop_column("assignments", "chain_priority")
    op.drop_column("assignments", "chain_position")
    op.drop_column("assignments", "chain_id")
    op.drop_column("assignments", "next_assignment_id")
    op.drop_column("assignments", "previous_assignment_id")
    op.drop_column("assignments", "confirmed_by")
    op.drop_column("assignments", "confirmed_at")
    op.drop_column("assignments", "booking_confidence")
    op.drop_column("assignments", "is_forward_booked")

    sa.Enum(name="chain_priority_enum").drop(op.get_bind(), checkfirst=True)
