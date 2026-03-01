"""SQLAlchemy ORM models for Muse Hub stash — a temporary shelf for uncommitted changes.

Analogous to git stash: musicians can save in-progress work, switch context,
and pop the stash later to resume. Each stash record captures the branch it
was created on plus zero or more MIDI file snapshots (entries).

Tables:
- musehub_stash: one stash record per save point
- musehub_stash_entries: individual MIDI file snapshots within a stash
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from maestro.db.database import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class MusehubStash(Base):
    """A stash record — a named save point for uncommitted Muse changes.

    ``branch`` records which branch the stash was created on so the user
    can be warned if they try to pop it on a different branch.
    ``message`` is an optional free-text description (up to 500 chars).
    ``is_applied`` flips to True when the stash has been popped back into
    the working tree; ``applied_at`` records the exact timestamp.
    """

    __tablename__ = "musehub_stash"
    __table_args__ = (
        Index("ix_musehub_stash_repo_id", "repo_id"),
        Index("ix_musehub_stash_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("maestro_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    entries: Mapped[list[MusehubStashEntry]] = relationship(
        "MusehubStashEntry",
        back_populates="stash",
        cascade="all, delete-orphan",
        order_by="MusehubStashEntry.position",
    )


class MusehubStashEntry(Base):
    """A single MIDI file snapshot within a stash.

    ``path`` is the MIDI file's path relative to the repo root.
    ``object_id`` is the content-addressed hash of the file at stash time,
    matching the format used in ``musehub_objects`` (e.g. ``sha256:<hex>``).
    ``position`` preserves the order of entries within the stash so pop
    restores files in a deterministic sequence.
    """

    __tablename__ = "musehub_stash_entries"
    __table_args__ = (Index("ix_musehub_stash_entries_stash_id", "stash_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    stash_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_stash.id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    stash: Mapped[MusehubStash] = relationship("MusehubStash", back_populates="entries")
