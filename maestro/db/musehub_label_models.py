"""SQLAlchemy ORM models for Muse Hub label tables.

Tables:
- musehub_labels: Coloured label definitions scoped to a repo
- musehub_issue_labels: Many-to-many join between issues and labels
- musehub_pr_labels: Many-to-many join between pull requests and labels
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maestro.db.database import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class MusehubLabel(Base):
    """A coloured label tag that can be applied to issues and pull requests.

    Labels are scoped to a repo — the same name may exist across repos with
    different colours. The UNIQUE(repo_id, name) constraint enforces uniqueness
    within a repo. ``color`` stores a hex string like ``#d73a4a``.
    """

    __tablename__ = "musehub_labels"
    __table_args__ = (
        UniqueConstraint("repo_id", "name", name="uq_musehub_labels_repo_name"),
        Index("ix_musehub_labels_repo_id", "repo_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    # Hex colour string, e.g. "#d73a4a"
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    issue_labels: Mapped[list[MusehubIssueLabel]] = relationship(
        "MusehubIssueLabel", back_populates="label", cascade="all, delete-orphan"
    )
    pr_labels: Mapped[list[MusehubPRLabel]] = relationship(
        "MusehubPRLabel", back_populates="label", cascade="all, delete-orphan"
    )


class MusehubIssueLabel(Base):
    """Join table linking issues to labels.

    Composite primary key on (issue_id, label_id). Both sides cascade-delete
    so removing an issue or a label automatically cleans up the association.
    """

    __tablename__ = "musehub_issue_labels"
    __table_args__ = (
        Index("ix_musehub_issue_labels_label_id", "label_id"),
    )

    issue_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_issues.issue_id", ondelete="CASCADE"),
        primary_key=True,
    )
    label_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_labels.id", ondelete="CASCADE"),
        primary_key=True,
    )

    label: Mapped[MusehubLabel] = relationship(
        "MusehubLabel", back_populates="issue_labels"
    )


class MusehubPRLabel(Base):
    """Join table linking pull requests to labels.

    Composite primary key on (pr_id, label_id). Both sides cascade-delete
    so removing a PR or a label automatically cleans up the association.
    """

    __tablename__ = "musehub_pr_labels"
    __table_args__ = (
        Index("ix_musehub_pr_labels_label_id", "label_id"),
    )

    pr_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_pull_requests.pr_id", ondelete="CASCADE"),
        primary_key=True,
    )
    label_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_labels.id", ondelete="CASCADE"),
        primary_key=True,
    )

    label: Mapped[MusehubLabel] = relationship(
        "MusehubLabel", back_populates="pr_labels"
    )
