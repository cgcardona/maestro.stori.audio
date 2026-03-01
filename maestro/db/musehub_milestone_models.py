from __future__ import annotations

"""SQLAlchemy ORM models for Muse Hub milestones.

Tables:
- musehub_milestones: Named goals that group issues toward a ship target.
- musehub_issue_milestones: Many-to-many join between issues and milestones.

``musehub_milestones`` was created in migration 0001_consolidated_schema.
``musehub_issue_milestones`` was added in migration 0002_milestones.

Import this module alongside ``musehub_models`` only after removing
``MusehubMilestone`` from that file to avoid SQLAlchemy metadata conflicts.
"""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maestro.db.database import Base

if TYPE_CHECKING:
    from maestro.db.musehub_models import MusehubIssue, MusehubRepo


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class MusehubMilestone(Base):
    """A milestone that groups issues within a repo toward a named goal.

    Milestones give musicians a way to track progress toward ship targets such
    as "Album v1.0" or "Mix Session 3".  Issues are linked via the
    ``musehub_issue_milestones`` join table (many-to-many) or by a direct
    ``milestone_id`` FK on ``musehub_issues`` (one-to-many, nullable).

    ``state`` progresses: ``open`` → ``closed``.
    ``due_on`` is an optional tz-aware deadline visible in the UI.
    ``created_by`` is the ``maestro_users.id`` of the user who created the
    milestone; stored as a plain string so the milestone survives user
    soft-deletes without a hard FK constraint.
    """

    __tablename__ = "musehub_milestones"
    __table_args__ = (
        Index("ix_musehub_milestones_repo_id", "repo_id"),
        Index("ix_musehub_milestones_state", "state"),
        {"extend_existing": True},
    )

    milestone_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid
    )
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_on: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # open | closed
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    # User ID of the creator; intentionally not a FK constraint to survive user deletes.
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship(
        "MusehubRepo",
        back_populates="milestones",
        foreign_keys="[MusehubMilestone.repo_id]",
    )
    issue_milestones: Mapped[list[MusehubIssueMilestone]] = relationship(
        "MusehubIssueMilestone",
        back_populates="milestone",
        cascade="all, delete-orphan",
    )


class MusehubIssueMilestone(Base):
    """Join table linking issues to milestones (many-to-many).

    A single issue can belong to multiple milestones and a milestone can
    contain many issues.  The composite primary key on ``(issue_id,
    milestone_id)`` enforces uniqueness without a surrogate key.
    """

    __tablename__ = "musehub_issue_milestones"
    __table_args__ = (
        Index("ix_musehub_issue_milestones_milestone_id", "milestone_id"),
    )

    issue_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_issues.issue_id", ondelete="CASCADE"),
        primary_key=True,
    )
    milestone_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_milestones.milestone_id", ondelete="CASCADE"),
        primary_key=True,
    )

    issue: Mapped[MusehubIssue] = relationship("MusehubIssue")
    milestone: Mapped[MusehubMilestone] = relationship(
        "MusehubMilestone",
        back_populates="issue_milestones",
    )
