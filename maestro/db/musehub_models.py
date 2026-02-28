"""SQLAlchemy ORM models for Muse Hub — the remote collaboration backend.

Tables:
- musehub_repos: Remote repos (one per project/musician)
- musehub_branches: Named branch pointers inside a repo
- musehub_commits: Remote commit records pushed from CLI clients
- musehub_issues: Issue tracker entries per repo
- musehub_pull_requests: Pull requests proposing branch merges
- musehub_objects: Content-addressed binary artifact storage
- musehub_stars: Per-user repo starring (one row per user×repo pair)
- musehub_profiles: Public user profiles (bio, avatar, pinned repos)
- musehub_webhooks: Registered webhook subscriptions per repo
- musehub_webhook_deliveries: Delivery log for each webhook dispatch attempt"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Textfrom sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from maestro.db.database import Base


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class MusehubRepo(Base):
    """A remote Muse repository — the hub-side equivalent of a Git remote.

    Music-semantic fields (key_signature, tempo_bpm, tags) are optional metadata
    that musicians set to make their repos discoverable on the explore page.
    Tags are free-form strings that encode genre, instrumentation, and mood.
    """

    __tablename__ = "musehub_repos"

    repo_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON list of free-form tag strings: genre ("jazz"), key ("F# minor"), instrument ("bass")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Music-semantic metadata for filter-based discovery
    key_signature: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tempo_bpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    pull_requests: Mapped[list[MusehubPullRequest]] = relationship(
        "MusehubPullRequest", back_populates="repo", cascade="all, delete-orphan"
    )
    stars: Mapped[list[MusehubStar]] = relationship(
        "MusehubStar", back_populates="repo", cascade="all, delete-orphan"
    )
    webhooks: Mapped[list[MusehubWebhook]] = relationship(
        "MusehubWebhook", back_populates="repo", cascade="all, delete-orphan"
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


class MusehubPullRequest(Base):
    """A pull request proposing to merge one branch into another.

    ``state`` progresses: ``open`` → ``merged`` | ``closed``.
    ``merge_commit_id`` is populated only when state becomes ``merged``.
    """

    __tablename__ = "musehub_pull_requests"

    pr_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    from_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    to_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    # Populated when state transitions to 'merged'
    merge_commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="pull_requests")


class MusehubStar(Base):
    """A single user's star on a public repo.

    Stars are the primary signal for the explore page's "trending" sort.
    The unique constraint on (repo_id, user_id) makes starring idempotent —
    a user can only star a repo once, and duplicate requests are safe.
    """

    __tablename__ = "musehub_stars"
    __table_args__ = (UniqueConstraint("repo_id", "user_id", name="uq_musehub_stars_repo_user"),)

    star_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="stars")


class MusehubProfile(Base):
    """Public user profile for Muse Hub — a musical portfolio page.

    One profile per user, keyed by ``user_id`` (the JWT ``sub`` claim).
    ``username`` is a unique, URL-friendly display handle chosen by the user.
    When no profile exists for a user, their repos are still accessible by
    ``owner_user_id`` but they have no public profile page.

    ``pinned_repo_ids`` is a JSON list of up to 6 repo_ids the user has
    chosen to highlight on their profile. Order is preserved.
    """

    __tablename__ = "musehub_profiles"
    __table_args__ = (UniqueConstraint("username", name="uq_musehub_profiles_username"),)

    # PK is the JWT sub — same value used in musehub_repos.owner_user_id
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # URL-friendly handle, e.g. "gabriel" → /musehub/ui/users/gabriel
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # JSON list of repo_ids (up to 6) pinned by the user
    pinned_repo_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
class MusehubWebhook(Base):
    """A registered webhook subscription for a Muse Hub repo.

    When an event matching one of the subscribed ``events`` types fires, the
    dispatcher POSTs a signed JSON payload to ``url``.  The ``secret`` is used
    to compute an HMAC-SHA256 signature sent in the ``X-MuseHub-Signature``
    header so receivers can verify authenticity without trusting the network.

    ``events`` is a JSON list of event-type strings (e.g. ``["push", "issue"]``).
    An empty list means the webhook receives no events and is effectively paused.
    """

    __tablename__ = "musehub_webhooks"

    webhook_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # JSON list of event-type strings the subscriber wants notifications for.
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # HMAC-SHA256 secret for payload signature; empty string means unsigned.
    secret: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="webhooks")
    deliveries: Mapped[list[MusehubWebhookDelivery]] = relationship(
        "MusehubWebhookDelivery", back_populates="webhook", cascade="all, delete-orphan"
    )


class MusehubWebhookDelivery(Base):
    """One delivery attempt for a webhook event.

    Each row records the outcome of a single HTTP POST to a ``MusehubWebhook``
    URL.  The dispatcher creates one row per attempt (including retries), so a
    delivery that required 3 attempts produces 3 rows with the same
    ``event_type`` and incrementing ``attempt`` counters.

    ``success`` is True only when the receiver responded with a 2xx status.
    ``response_status`` is 0 when the request did not reach the server
    (network error, DNS failure, timeout).
    """

    __tablename__ = "musehub_webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    webhook_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_webhooks.webhook_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    webhook: Mapped[MusehubWebhook] = relationship(
        "MusehubWebhook", back_populates="deliveries"    )
