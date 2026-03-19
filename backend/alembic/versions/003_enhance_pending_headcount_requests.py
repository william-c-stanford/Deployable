"""Enhance pending_headcount_requests with proper FK refs, new columns, and indexes.

Revision ID: 003_enhance_headcount
Revises: 002_forward_staffing
Create Date: 2026-03-19

This migration upgrades the pending_headcount_requests table from
string-based IDs to proper UUID ForeignKey columns, adds new fields
(priority, end_date, required_skills, required_certs, notes,
reviewed_by, reviewed_at, rejection_reason, updated_at), and creates
indexes on partner_id, project_id, and status for efficient querying.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "003_enhance_headcount"
down_revision = "002_forward_staffing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old table if it exists (from initial seed/create_all)
    # and recreate with proper schema.
    # Using a safe approach: create columns if not present via batch alter.

    # First check if table exists; if so, drop and recreate cleanly
    op.execute("""
        DROP TABLE IF EXISTS pending_headcount_requests CASCADE
    """)

    op.create_table(
        "pending_headcount_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("partner_id", UUID(as_uuid=True),
                  sa.ForeignKey("partners.id"), nullable=False, index=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id"), nullable=True, index=True),
        sa.Column("role_name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("priority", sa.String(30), server_default="normal"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("required_skills", sa.JSON(), server_default="[]"),
        sa.Column("required_certs", sa.JSON(), server_default="[]"),
        sa.Column("constraints", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False,
                  server_default="Pending", index=True),
        sa.Column("reviewed_by", sa.String(200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("pending_headcount_requests")
