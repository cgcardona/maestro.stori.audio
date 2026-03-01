"""Add musehub_labels, musehub_issue_labels, and musehub_pr_labels tables.

Revision ID: 0003_labels
Revises: 0001
Create Date: 2026-02-28 00:00:00.000000

Adds coloured label tags that can be applied to issues and pull requests
for categorisation. Three tables:

  musehub_labels           — label definitions per repo (name, hex colour)
  musehub_issue_labels     — many-to-many join: issues ↔ labels
  musehub_pr_labels        — many-to-many join: pull requests ↔ labels

Note: 0002_milestones was folded into 0001_consolidated_schema (commit 82d7a8b).
This migration chains directly from 0001.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_labels"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "musehub_labels",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["musehub_repos.repo_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "name", name="uq_musehub_labels_repo_name"),
    )
    op.create_index("ix_musehub_labels_repo_id", "musehub_labels", ["repo_id"])

    op.create_table(
        "musehub_issue_labels",
        sa.Column("issue_id", sa.String(36), nullable=False),
        sa.Column("label_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["musehub_issues.issue_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["musehub_labels.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("issue_id", "label_id"),
    )
    op.create_index(
        "ix_musehub_issue_labels_label_id", "musehub_issue_labels", ["label_id"]
    )

    op.create_table(
        "musehub_pr_labels",
        sa.Column("pr_id", sa.String(36), nullable=False),
        sa.Column("label_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["pr_id"],
            ["musehub_pull_requests.pr_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["musehub_labels.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("pr_id", "label_id"),
    )
    op.create_index(
        "ix_musehub_pr_labels_label_id", "musehub_pr_labels", ["label_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_musehub_pr_labels_label_id", table_name="musehub_pr_labels")
    op.drop_table("musehub_pr_labels")

    op.drop_index(
        "ix_musehub_issue_labels_label_id", table_name="musehub_issue_labels"
    )
    op.drop_table("musehub_issue_labels")

    op.drop_index("ix_musehub_labels_repo_id", table_name="musehub_labels")
    op.drop_table("musehub_labels")
