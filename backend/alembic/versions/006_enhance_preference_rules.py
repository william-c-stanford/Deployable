"""Enhance preference_rules with template types, status lifecycle, rejection linkage, and created_by fields.

Revision ID: 006_enhance_preference_rules
Revises: 005_add_skill_breakdowns
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "006_enhance_preference_rules"
down_revision = "005_add_skill_breakdowns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add template_type column with default for existing rows
    op.add_column(
        "preference_rules",
        sa.Column(
            "template_type",
            sa.String(60),
            nullable=False,
            server_default="custom",
            comment="Template type enum: skill_minimum, cert_required, region_preference, etc.",
        ),
    )
    op.create_index("ix_preference_rules_template_type", "preference_rules", ["template_type"])

    # Add description column
    op.add_column(
        "preference_rules",
        sa.Column("description", sa.Text(), nullable=True),
    )

    # Add scope_target_id
    op.add_column(
        "preference_rules",
        sa.Column("scope_target_id", sa.String(200), nullable=True),
    )

    # Add score_modifier
    op.add_column(
        "preference_rules",
        sa.Column(
            "score_modifier",
            sa.Float(),
            nullable=True,
            server_default="0.0",
            comment="Scoring modifier value: negative for demote, positive for boost",
        ),
    )

    # Add priority
    op.add_column(
        "preference_rules",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=True,
            server_default="0",
            comment="Higher priority rules take precedence",
        ),
    )

    # Add/update status column (may already exist from prior migration)
    # Use a try/except pattern since status may already be present
    try:
        op.add_column(
            "preference_rules",
            sa.Column(
                "status",
                sa.String(30),
                nullable=False,
                server_default="active",
                comment="Lifecycle status: proposed, active, disabled, archived",
            ),
        )
    except Exception:
        pass  # Column already exists from prior work

    op.create_index(
        "ix_preference_rules_status",
        "preference_rules",
        ["status"],
        if_not_exists=True,
    )

    # Add rejection_id FK to recommendations
    op.add_column(
        "preference_rules",
        sa.Column(
            "rejection_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Recommendation ID whose rejection triggered this rule proposal",
        ),
    )
    op.create_foreign_key(
        "fk_preference_rules_rejection_id",
        "preference_rules",
        "recommendations",
        ["rejection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_preference_rules_rejection_id", "preference_rules", ["rejection_id"])

    # Add created_by_type
    op.add_column(
        "preference_rules",
        sa.Column(
            "created_by_type",
            sa.String(30),
            nullable=False,
            server_default="ops",
            comment="Who created: agent or ops",
        ),
    )

    # Add created_by_id
    op.add_column(
        "preference_rules",
        sa.Column(
            "created_by_id",
            sa.String(200),
            nullable=True,
            comment="User ID (for ops) or agent name (for agent-created rules)",
        ),
    )

    # Add approved_by_id
    op.add_column(
        "preference_rules",
        sa.Column(
            "approved_by_id",
            sa.String(200),
            nullable=True,
            comment="Ops user ID who approved a proposed rule",
        ),
    )

    # Add approved_at
    op.add_column(
        "preference_rules",
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the rule was approved",
        ),
    )

    # Add updated_at
    op.add_column(
        "preference_rules",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Add spawned_preference_rules relationship support on recommendations
    # (no schema change needed, relationship is ORM-only)


def downgrade() -> None:
    op.drop_column("preference_rules", "updated_at")
    op.drop_column("preference_rules", "approved_at")
    op.drop_column("preference_rules", "approved_by_id")
    op.drop_column("preference_rules", "created_by_id")
    op.drop_column("preference_rules", "created_by_type")
    op.drop_index("ix_preference_rules_rejection_id", table_name="preference_rules")
    op.drop_constraint("fk_preference_rules_rejection_id", "preference_rules", type_="foreignkey")
    op.drop_column("preference_rules", "rejection_id")
    op.drop_index("ix_preference_rules_status", table_name="preference_rules")
    op.drop_column("preference_rules", "priority")
    op.drop_column("preference_rules", "score_modifier")
    op.drop_column("preference_rules", "scope_target_id")
    op.drop_column("preference_rules", "description")
    op.drop_index("ix_preference_rules_template_type", table_name="preference_rules")
    op.drop_column("preference_rules", "template_type")
