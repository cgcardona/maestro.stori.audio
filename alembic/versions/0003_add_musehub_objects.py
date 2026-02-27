"""Add musehub_objects table for binary artifact storage.

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-27 00:00:00.000000

Adds the musehub_objects table introduced by the push/pull sync protocol
(PR #64 / issue #40).  Objects are content-addressed (sha256:…) and stored
with disk_path pointing to the on-disk file written by ingest_push().
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "musehub_objects",
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disk_path", sa.String(2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("object_id"),
    )
    op.create_index("ix_musehub_objects_repo_id", "musehub_objects", ["repo_id"])


def downgrade() -> None:
    op.drop_index("ix_musehub_objects_repo_id", table_name="musehub_objects")
    op.drop_table("musehub_objects")
