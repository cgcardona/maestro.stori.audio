"""Add extra_metadata JSON column to muse_cli_commits.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-27 00:00:00.000000

Adds a nullable JSON blob ``extra_metadata`` to ``muse_cli_commits`` that
stores commit-level compositional annotations such as:

  - ``meter``   — time signature string (e.g. ``"4/4"``, ``"7/8"``)
  - ``tempo``   — beats-per-minute (reserved, not yet written)
  - ``key``     — musical key (reserved, not yet written)

The column is nullable so that existing commits remain valid; unset
annotations are represented as ``NULL`` (ORM: ``None``).

Apply:
  docker compose exec maestro alembic upgrade head
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
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("muse_cli_commits", "extra_metadata")
