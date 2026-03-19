"""Add skill_breakdowns and skill_breakdown_items tables.

Revision ID: 005_add_skill_breakdowns
Revises: 004_add_merge_history
Create Date: 2026-03-19

Supports skill breakdown submissions at assignment completion,
capturing which skills a technician performed, proficiency ratings,
and supervisor notes.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "005_add_skill_breakdowns"
down_revision = "004_add_merge_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the enum type first
    skill_proficiency_rating_enum = sa.Enum(
        "Below Expectations", "Meets Expectations", "Exceeds Expectations", "Expert",
        name="skill_proficiency_rating_enum",
    )
    skill_proficiency_rating_enum.create(op.get_bind(), checkfirst=True)

    # -- skill_breakdowns --
    op.create_table(
        "skill_breakdowns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "assignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assignments.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "technician_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("technicians.id"),
            nullable=False,
        ),
        sa.Column("submitted_by", sa.String(200), nullable=False),
        sa.Column("overall_notes", sa.Text, nullable=True),
        sa.Column(
            "overall_rating",
            skill_proficiency_rating_enum,
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        # Partner review fields
        sa.Column("partner_review_status", sa.Enum(
            "Pending", "Approved", "Rejected", "Revision Requested",
            name="partner_review_status_enum",
        ), nullable=True),
        sa.Column("partner_review_note", sa.Text, nullable=True),
        sa.Column("partner_reviewed_at", sa.DateTime, nullable=True),
        sa.Column("partner_reviewed_by", sa.String(200), nullable=True),
    )
    op.create_index("ix_skill_breakdowns_assignment_id", "skill_breakdowns", ["assignment_id"])
    op.create_index("ix_skill_breakdowns_technician_id", "skill_breakdowns", ["technician_id"])

    # -- skill_breakdown_items --
    op.create_table(
        "skill_breakdown_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "skill_breakdown_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skill_breakdowns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skill_name", sa.String(200), nullable=False),
        sa.Column(
            "skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id"),
            nullable=True,
        ),
        sa.Column("hours_applied", sa.Float, nullable=True),
        sa.Column(
            "proficiency_rating",
            skill_proficiency_rating_enum,
            nullable=False,
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_skill_breakdown_items_breakdown_id", "skill_breakdown_items", ["skill_breakdown_id"])
    op.create_index(
        "ix_skill_breakdown_items_breakdown_skill",
        "skill_breakdown_items",
        ["skill_breakdown_id", "skill_name"],
    )


def downgrade() -> None:
    op.drop_table("skill_breakdown_items")
    op.drop_table("skill_breakdowns")
    sa.Enum(name="skill_proficiency_rating_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="partner_review_status_enum").drop(op.get_bind(), checkfirst=True)
