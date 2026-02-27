"""Add Muse Hub tables: musehub_repos, musehub_branches, musehub_commits.

Revision ID: 20260227_0001
Revises: 20260202_0000
Create Date: 2026-02-27 00:00:00.000000

Adds the remote collaboration backend tables for the Muse Hub (issue #39).
These tables are the server-side counterpart to the local muse_cli_commits
table: they store repos and commits as pushed by CLI clients.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260227_0001"
down_revision = "20260202_0000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Muse Hub repos ────────────────────────────────────────────────────
    op.create_table(
        "musehub_repos",
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("repo_id"),
    )
    op.create_index("ix_musehub_repos_owner_user_id", "musehub_repos", ["owner_user_id"])

    # ── Muse Hub branches ─────────────────────────────────────────────────
    op.create_table(
        "musehub_branches",
        sa.Column("branch_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("head_commit_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("branch_id"),
    )
    op.create_index("ix_musehub_branches_repo_id", "musehub_branches", ["repo_id"])

    # ── Muse Hub commits ──────────────────────────────────────────────────
    op.create_table(
        "musehub_commits",
        sa.Column("commit_id", sa.String(64), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        # JSON list of parent commit IDs; two entries for merge commits.
        sa.Column("parent_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("commit_id"),
    )
    op.create_index("ix_musehub_commits_repo_id", "musehub_commits", ["repo_id"])
    op.create_index("ix_musehub_commits_branch", "musehub_commits", ["branch"])
    op.create_index("ix_musehub_commits_timestamp", "musehub_commits", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_musehub_commits_timestamp", table_name="musehub_commits")
    op.drop_index("ix_musehub_commits_branch", table_name="musehub_commits")
    op.drop_index("ix_musehub_commits_repo_id", table_name="musehub_commits")
    op.drop_table("musehub_commits")

    op.drop_index("ix_musehub_branches_repo_id", table_name="musehub_branches")
    op.drop_table("musehub_branches")

    op.drop_index("ix_musehub_repos_owner_user_id", table_name="musehub_repos")
    op.drop_table("musehub_repos")
