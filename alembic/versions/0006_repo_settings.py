"""Add settings JSON column to musehub_repos.

Revision ID: 0006_repo_settings
Revises: 0005_stash
Create Date: 2026-02-28 00:00:00.000000

Adds a nullable JSON ``settings`` column to ``musehub_repos`` that stores
mutable feature-flag settings not covered by dedicated columns:
  has_issues, has_projects, has_wiki, license, homepage_url,
  allow_merge_commit, allow_squash_merge, allow_rebase_merge,
  delete_branch_on_merge, default_branch.

Existing rows receive NULL (service layer defaults to an empty dict and
fills in canonical defaults on first GET).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_repo_settings"
down_revision = "0005_stash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "musehub_repos",
        sa.Column("settings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("musehub_repos", "settings")
