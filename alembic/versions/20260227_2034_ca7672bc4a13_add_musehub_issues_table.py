"""Add musehub_issues table for issue tracking per repo.

Revision ID: ca7672bc4a13
Revises: 20260227_0001
Create Date: 2026-02-27 20:34:25.445344+00:00

Adds issue tracking to Muse Hub so musicians can open, list, and close
production/creative issues against a repo (issue #42).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ca7672bc4a13"
down_revision = "20260227_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "musehub_issues",
        sa.Column("issue_id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="open"),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["musehub_repos.repo_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("issue_id"),
    )
    op.create_index("ix_musehub_issues_repo_id", "musehub_issues", ["repo_id"])
    op.create_index("ix_musehub_issues_number", "musehub_issues", ["number"])
    op.create_index("ix_musehub_issues_state", "musehub_issues", ["state"])


def downgrade() -> None:
    op.drop_index("ix_musehub_issues_state", table_name="musehub_issues")
    op.drop_index("ix_musehub_issues_number", table_name="musehub_issues")
    op.drop_index("ix_musehub_issues_repo_id", table_name="musehub_issues")
    op.drop_table("musehub_issues")
