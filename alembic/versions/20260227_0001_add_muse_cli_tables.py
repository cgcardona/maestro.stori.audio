"""Add muse_cli tables: objects, snapshots, commits.

Revision ID: 20260227_0001
Revises: 20260202_0000
Create Date: 2026-02-27 00:00:00.000000

Adds three content-addressed tables for the ``muse commit`` CLI command:

- ``muse_cli_objects`` — sha256-keyed file blobs (deduplicated across commits)
- ``muse_cli_snapshots`` — snapshot manifests mapping relative paths to object IDs
- ``muse_cli_commits`` — commit history with parent linkage and branch tracking

These tables are managed by the Muse CLI (``muse commit``) and are distinct
from the DAW-level Muse VCS tables (variations, phrases, note_changes).

Fresh install: ``alembic upgrade head`` from the maestro container.
Rollback: ``alembic downgrade 20260202_0000``.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260227_0001"
down_revision = "20260202_0000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "muse_cli_objects",
        sa.Column("object_id", sa.String(64), primary_key=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "muse_cli_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "muse_cli_commits",
        sa.Column("commit_id", sa.String(64), primary_key=True),
        sa.Column("repo_id", sa.String(36), nullable=False, index=True),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("parent_commit_id", sa.String(64), nullable=True, index=True),
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("muse_cli_snapshots.snapshot_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("muse_cli_commits")
    op.drop_table("muse_cli_snapshots")
    op.drop_table("muse_cli_objects")
