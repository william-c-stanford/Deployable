"""Add ManualBadge and MilestoneBadge tables.

Revision ID: 002_badges
Revises: 001_training
Create Date: 2026-03-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002_badges"
down_revision: Union[str, None] = "001_training"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enums
    manual_badge_category_enum = postgresql.ENUM(
        "site", "client", "safety", "recognition",
        name="manual_badge_category_enum",
        create_type=True,
    )
    milestone_type_enum = postgresql.ENUM(
        "hours_threshold", "projects_completed", "certs_earned",
        "training_completed", "perfect_attendance", "tenure",
        name="milestone_type_enum",
        create_type=True,
    )

    manual_badge_category_enum.create(op.get_bind(), checkfirst=True)
    milestone_type_enum.create(op.get_bind(), checkfirst=True)

    # ManualBadge table
    op.create_table(
        "manual_badges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "technician_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("technicians.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("category", manual_badge_category_enum, nullable=False, server_default="site"),
        sa.Column("badge_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("site_name", sa.String(200), nullable=True),
        sa.Column("client_name", sa.String(200), nullable=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("granted_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("metadata_json", postgresql.JSON, server_default="{}"),
    )

    # MilestoneBadge table
    op.create_table(
        "milestone_badges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "technician_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("technicians.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("milestone_type", milestone_type_enum, nullable=False),
        sa.Column("badge_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("threshold_value", sa.Float, nullable=False),
        sa.Column("actual_value", sa.Float, nullable=False),
        sa.Column("reference_entity_type", sa.String(100), nullable=True),
        sa.Column("reference_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("icon", sa.String(100), nullable=True, server_default="award"),
        sa.Column("tier", sa.Integer, nullable=False, server_default="1"),
        sa.Column("granted_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint(
            "technician_id",
            "milestone_type",
            "threshold_value",
            "reference_entity_type",
            "reference_entity_id",
            name="uq_milestone_badge_tech_type_threshold_ref",
        ),
    )


def downgrade() -> None:
    op.drop_table("milestone_badges")
    op.drop_table("manual_badges")
    op.execute("DROP TYPE IF EXISTS milestone_type_enum")
    op.execute("DROP TYPE IF EXISTS manual_badge_category_enum")
