from __future__ import annotations

"""Add musehub_issue_milestones join table.

musehub_milestones was created in 0001_consolidated_schema; this migration
adds the many-to-many join table that links issues to milestones.

Revision ID: 0002_milestones
Revises: 0001
Create Date: 2026-02-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_milestones"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # musehub_milestones already exists from revision 0001; only the join
    # table is new in this migration.
    op.create_table(
        "musehub_issue_milestones",
        sa.Column("issue_id", sa.String(36), nullable=False),
        sa.Column("milestone_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["musehub_issues.issue_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"],
            ["musehub_milestones.milestone_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("issue_id", "milestone_id"),
    )
    op.create_index(
        "ix_musehub_issue_milestones_milestone_id",
        "musehub_issue_milestones",
        ["milestone_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_musehub_issue_milestones_milestone_id",
        table_name="musehub_issue_milestones",
    )
    op.drop_table("musehub_issue_milestones")
