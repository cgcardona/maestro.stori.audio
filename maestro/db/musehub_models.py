"""SQLAlchemy ORM models for Muse Hub — the remote collaboration backend.

Tables:
- musehub_repos: Remote repos (one per project/musician)
- musehub_branches: Named branch pointers inside a repo
- musehub_commits: Remote commit records pushed from CLI clients
- musehub_issues: Issue tracker entries per repo
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from maestro.db.database import Base


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class MusehubRepo(Base):
    """A remote Muse repository — the hub-side equivalent of a Git remote."""

    __tablename__ = "musehub_repos"

    repo_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    branches: Mapped[list[MusehubBranch]] = relationship(
        "MusehubBranch", back_populates="repo", cascade="all, delete-orphan"
    )
    commits: Mapped[list[MusehubCommit]] = relationship(
        "MusehubCommit", back_populates="repo", cascade="all, delete-orphan"
    )
    objects: Mapped[list[MusehubObject]] = relationship(
        "MusehubObject", back_populates="repo", cascade="all, delete-orphan"
    )
    issues: Mapped[list[MusehubIssue]] = relationship(
        "MusehubIssue", back_populates="repo", cascade="all, delete-orphan"
    )


class MusehubBranch(Base):
    """A named branch pointer inside a Muse Hub repo."""

    __tablename__ = "musehub_branches"

    branch_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Null until the first push sets the head.
    head_commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="branches")


class MusehubCommit(Base):
    """A commit record pushed to the Muse Hub.

    ``parent_ids`` is a JSON list so merge commits can carry two parents,
    matching the local CLI ``muse_cli_commits`` contract.
    """

    __tablename__ = "musehub_commits"

    commit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # JSON list of parent commit IDs; two entries for merge commits.
    parent_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="commits")


class MusehubObject(Base):
    """A binary artifact (MIDI, MP3, WebP piano roll) stored in Muse Hub.

    Object content is written to disk at ``disk_path``; only metadata lives in
    Postgres.  ``object_id`` is the canonical content-addressed identifier in
    the form ``sha256:<hex>`` and doubles as the primary key — upserts are safe
    by design because the same content always maps to the same ID.
    """

    __tablename__ = "musehub_objects"

    # Content-addressed ID, e.g. "sha256:abc123..."
    object_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Relative path hint from the client, e.g. "tracks/jazz_4b.mid"
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Absolute path on the Hub server's filesystem where the bytes are stored
    disk_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="objects")


class MusehubIssue(Base):
    """An issue opened against a Muse Hub repo.

    ``number`` is auto-incremented per repo starting at 1 so musicians can
    reference issues as ``#1``, ``#2``, etc., independently of the global PK.
    ``labels`` is a JSON list of free-form strings (no validation at MVP).
    """

    __tablename__ = "musehub_issues"

    issue_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Sequential per-repo issue number (1, 2, 3…)
    number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    # JSON list of free-form label strings
    labels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="issues")
