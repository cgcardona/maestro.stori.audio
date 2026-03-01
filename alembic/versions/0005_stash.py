"""Add musehub_stash and musehub_stash_entries tables.

Revision ID: 0005_stash
Revises: 0004_collaborators
Create Date: 2026-02-28 00:00:00.000000

Stash is a temporary shelf for uncommitted Muse changes — analogous to git stash.
Two tables:
  - musehub_stash: one stash record per (repo, user, branch) save point
  - musehub_stash_entries: individual MIDI file snapshots within a stash
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_stash"
down_revision = "0004_collaborators"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "musehub_stash",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("is_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["maestro_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_musehub_stash_repo_id", "musehub_stash", ["repo_id"])
    op.create_index("ix_musehub_stash_user_id", "musehub_stash", ["user_id"])

    op.create_table(
        "musehub_stash_entries",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("stash_id", sa.String(36), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["stash_id"], ["musehub_stash.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_musehub_stash_entries_stash_id", "musehub_stash_entries", ["stash_id"])


def downgrade() -> None:
    op.drop_index("ix_musehub_stash_entries_stash_id", table_name="musehub_stash_entries")
    op.drop_table("musehub_stash_entries")

    op.drop_index("ix_musehub_stash_user_id", table_name="musehub_stash")
    op.drop_index("ix_musehub_stash_repo_id", table_name="musehub_stash")
    op.drop_table("musehub_stash")
