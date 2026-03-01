"""Add payload column to musehub_webhook_deliveries.

Stores the JSON-encoded payload sent during each delivery attempt so that
failed deliveries can be retried via the POST /deliveries/{id}/redeliver
endpoint without losing the original event data.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-28
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
        "musehub_webhook_deliveries",
        sa.Column("payload", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("musehub_webhook_deliveries", "payload")
