"""SQLAlchemy ORM models for Muse CLI commit history.

Tables:
- muse_cli_objects: content-addressed file blobs (sha256 keyed)
- muse_cli_snapshots: snapshot manifests mapping paths to object IDs
- muse_cli_commits: commit history with parent linkage, branch tracking,
  and an extensible ``extra_metadata`` JSON blob for annotations such as
  meter (time signature), tempo, key, and other compositional metadata.

These tables are owned by the Muse CLI (``muse commit``) and are
distinct from the Muse VCS variation tables (``variations``, ``phrases``,
``note_changes``) which track DAW-level note editing history.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from maestro.db.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MuseCliObject(Base):
    """A content-addressed blob: sha256(file_bytes) → bytes on disk.

    Objects are deduplicated across commits — the same file committed on
    two different branches is stored exactly once.
    """

    __tablename__ = "muse_cli_objects"

    object_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    def __repr__(self) -> str:
        return f"<MuseCliObject {self.object_id[:8]} size={self.size_bytes}>"


class MuseCliSnapshot(Base):
    """An immutable snapshot manifest: sha256(sorted(path:object_id pairs)).

    The manifest JSON maps relative file paths to their object IDs.
    Content-addressed: two identical working trees produce the same snapshot_id.
    """

    __tablename__ = "muse_cli_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    def __repr__(self) -> str:
        files = len(self.manifest) if self.manifest else 0
        return f"<MuseCliSnapshot {self.snapshot_id[:8]} files={files}>"


class MuseCliCommit(Base):
    """A versioned commit record pointing to a snapshot and its parent.

    commit_id = sha256(sorted(parent_ids) | snapshot_id | message | committed_at_iso)

    This derivation is deterministic: given the same working tree state,
    message, and timestamp two machines produce identical commit IDs.
    The ``committed_at`` field is the timestamp used in the hash; ``created_at``
    is the wall-clock DB write time and is non-deterministic.
    """

    __tablename__ = "muse_cli_commits"

    commit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_commit_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    parent2_commit_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("muse_cli_snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )

    def __repr__(self) -> str:
        return (
            f"<MuseCliCommit {self.commit_id[:8]} branch={self.branch!r}"
            f" msg={self.message[:30]!r}>"
        )
