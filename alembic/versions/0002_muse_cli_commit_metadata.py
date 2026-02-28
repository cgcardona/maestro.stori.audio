"""Add metadata JSON column to muse_cli_commits.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-27 00:00:00.000000

Adds an extensible ``metadata`` JSON blob to ``muse_cli_commits`` so that
commit-level annotations (e.g. ``tempo_bpm`` set via ``muse tempo --set``)
can be stored without schema changes.  The column is nullable — existing
commits have no metadata.
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
        sa.Column("metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("muse_cli_commits", "metadata")
