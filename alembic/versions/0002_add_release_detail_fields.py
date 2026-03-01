"""Add is_prerelease, is_draft, gpg_signature to musehub_releases.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-01

Adds three columns needed for the enhanced release detail page:
- is_prerelease: marks beta/rc builds so the UI shows a Pre-release badge
- is_draft:      marks unpublished drafts so they are hidden from public feeds
- gpg_signature: stores the ASCII-armoured GPG tag signature for the verified badge

All columns use safe server-side defaults so existing rows are valid immediately.
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
        "musehub_releases",
        sa.Column("is_prerelease", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "musehub_releases",
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "musehub_releases",
        sa.Column("gpg_signature", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("musehub_releases", "gpg_signature")
    op.drop_column("musehub_releases", "is_draft")
    op.drop_column("musehub_releases", "is_prerelease")
