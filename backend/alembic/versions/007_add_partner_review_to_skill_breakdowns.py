"""Add partner review fields to skill_breakdowns table.

Revision ID: 007_partner_review
Revises: 006_enhance_preference_rules
Create Date: 2026-03-19

Partners can review skill breakdowns alongside hours as part of the
joint timesheet + skill review flow. This adds partner review status,
note, timestamp, and reviewer ID fields.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "007_partner_review"
down_revision = "006_enhance_preference_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the enum type first
    partner_review_status_enum = sa.Enum(
        "Pending", "Approved", "Rejected", "Revision Requested",
        name="partner_review_status_enum",
    )
    partner_review_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "skill_breakdowns",
        sa.Column(
            "partner_review_status",
            partner_review_status_enum,
            nullable=True,
            comment="Partner's review status of the skill breakdown",
        ),
    )
    op.add_column(
        "skill_breakdowns",
        sa.Column(
            "partner_review_note",
            sa.Text(),
            nullable=True,
            comment="Partner's notes on the skill breakdown",
        ),
    )
    op.add_column(
        "skill_breakdowns",
        sa.Column(
            "partner_reviewed_at",
            sa.DateTime(),
            nullable=True,
            comment="When the partner reviewed",
        ),
    )
    op.add_column(
        "skill_breakdowns",
        sa.Column(
            "partner_reviewed_by",
            sa.String(200),
            nullable=True,
            comment="Partner user ID who reviewed",
        ),
    )


def downgrade() -> None:
    op.drop_column("skill_breakdowns", "partner_reviewed_by")
    op.drop_column("skill_breakdowns", "partner_reviewed_at")
    op.drop_column("skill_breakdowns", "partner_review_note")
    op.drop_column("skill_breakdowns", "partner_review_status")

    # Drop the enum type
    sa.Enum(name="partner_review_status_enum").drop(op.get_bind(), checkfirst=True)
