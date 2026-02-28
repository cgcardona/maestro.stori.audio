"""add_merged_at_to_musehub_pull_requests

Revision ID: a32799094fd2
Revises: 23c0aad0f741
Create Date: 2026-02-28 23:48:00.000000+00:00

Adds merged_at (nullable timestamp with timezone) to musehub_pull_requests so
the timeline overlay can position PR merge markers at the actual merge time
rather than the PR creation date.  Existing merged PRs receive NULL for
merged_at; the JS timeline falls back to createdAt in that case.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a32799094fd2"
down_revision = "23c0aad0f741"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "musehub_pull_requests",
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("musehub_pull_requests", "merged_at")
