"""Add musehub_collaborators table for repo access control.

Revision ID: 0004_collaborators
Revises: 0003_labels
Create Date: 2026-02-28 00:00:00.000000

Creates the musehub_collaborators table which tracks users granted explicit
push/admin access to a repo beyond the owner.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_collaborators"
down_revision = "0003_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "musehub_collaborators",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("permission", sa.String(20), nullable=False, server_default="write"),
        sa.Column("invited_by", sa.String(36), nullable=True),
        sa.Column(
            "invited_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["maestro_users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["maestro_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "user_id", name="uq_musehub_collaborators_repo_user"),
    )
    op.create_index("ix_musehub_collaborators_repo_id", "musehub_collaborators", ["repo_id"])
    op.create_index("ix_musehub_collaborators_user_id", "musehub_collaborators", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_musehub_collaborators_user_id", table_name="musehub_collaborators")
    op.drop_index("ix_musehub_collaborators_repo_id", table_name="musehub_collaborators")
    op.drop_table("musehub_collaborators")
