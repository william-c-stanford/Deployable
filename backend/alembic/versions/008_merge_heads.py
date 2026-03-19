"""Merge all branch heads into a single head.

Revision ID: 008_merge_heads
Revises: 002_badges, 005_inactive_override, 007_partner_review
Create Date: 2026-03-19
"""

from alembic import op

revision = "008_merge_heads"
down_revision = ("002_badges", "005_inactive_override", "007_partner_review")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
