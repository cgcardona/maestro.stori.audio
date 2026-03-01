"""Add musehub_pr_reviews table for PR reviewer assignment and approval tracking.

Revision ID: 0006_pr_reviews
Revises: 0005_stash
Create Date: 2026-02-28 00:00:00.000000

Each row represents one reviewer's disposition on a PR:
  pending           — assigned but not yet submitted
  approved          — reviewer approved the changes
  changes_requested — reviewer blocked the merge pending fixes
  dismissed         — a previous review was dismissed by the PR author

A unique index on (pr_id, reviewer_username) enforces one active review per
reviewer per PR.  Service-layer upserts replace state on re-submission.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_pr_reviews"
down_revision = "0005_stash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "musehub_pr_reviews",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("pr_id", sa.String(36), nullable=False),
        sa.Column("reviewer_username", sa.String(255), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["pr_id"],
            ["musehub_pull_requests.pr_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_musehub_pr_reviews_pr_id", "musehub_pr_reviews", ["pr_id"])
    op.create_index(
        "ix_musehub_pr_reviews_reviewer_username",
        "musehub_pr_reviews",
        ["reviewer_username"],
    )
    op.create_index("ix_musehub_pr_reviews_state", "musehub_pr_reviews", ["state"])


def downgrade() -> None:
    op.drop_index("ix_musehub_pr_reviews_state", table_name="musehub_pr_reviews")
    op.drop_index(
        "ix_musehub_pr_reviews_reviewer_username", table_name="musehub_pr_reviews"
    )
    op.drop_index("ix_musehub_pr_reviews_pr_id", table_name="musehub_pr_reviews")
    op.drop_table("musehub_pr_reviews")
