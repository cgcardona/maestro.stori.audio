"""Add parent2_commit_id to muse_cli_commits for merge commits.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "muse_cli_commits",
        sa.Column("parent2_commit_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_muse_cli_commits_parent2_commit_id",
        "muse_cli_commits",
        ["parent2_commit_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_muse_cli_commits_parent2_commit_id",
        table_name="muse_cli_commits",
    )
    op.drop_column("muse_cli_commits", "parent2_commit_id")
