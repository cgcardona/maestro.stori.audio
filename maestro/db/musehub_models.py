"""SQLAlchemy ORM models for Muse Hub — the remote collaboration backend.

Tables:
- musehub_repos: Remote repos (one per project/musician)
- musehub_branches: Named branch pointers inside a repo
- musehub_commits: Remote commit records pushed from CLI clients
- musehub_issues: Issue tracker entries per repo
- musehub_issue_comments: Threaded comments on issues
- musehub_milestones: Milestone groupings for issues
- musehub_issue_milestones: Many-to-many join between issues and milestones
- musehub_pull_requests: Pull requests proposing branch merges
- musehub_pr_reviews: Formal reviews (approval / changes requested / dismissed) on PRs
- musehub_pr_comments: Inline review comments on musical diffs within PRs
- musehub_objects: Content-addressed binary artifact storage
- musehub_releases: Tagged releases
- musehub_stars: Per-user repo starring (one row per user×repo pair)
- musehub_profiles: Public user profiles (bio, avatar, pinned repos)
- musehub_releases: Published version releases with download packages
- musehub_webhooks: Registered webhook subscriptions per repo
- musehub_webhook_deliveries: Delivery log for each webhook dispatch attempt
- musehub_render_jobs: Render status tracking for auto-generated MP3/piano-roll artifacts
- musehub_events: Repo-level activity event stream (commits, PRs, issues, branches, tags, sessions)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from maestro.db.database import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)



class MusehubRepo(Base):
    """A remote Muse repository — the hub-side equivalent of a Git remote.

    ``owner`` is the URL-visible username (e.g. "gabriel") and ``slug`` is the
    URL-safe repo name auto-generated from ``name`` (e.g. "neo-soul-experiment").
    Together they form the canonical /{owner}/{slug} URL scheme.  The internal
    ``repo_id`` UUID remains the primary key — external URLs never expose it.

    Music-semantic fields (key_signature, tempo_bpm, tags) are optional metadata
    that musicians set to make their repos discoverable on the explore page.
    Tags are free-form strings that encode genre, instrumentation, and mood.
    """

    __tablename__ = "musehub_repos"
    __table_args__ = (UniqueConstraint("owner", "slug", name="uq_musehub_repos_owner_slug"),)

    repo_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # URL-visible owner username, e.g. "gabriel" — forms the /{owner}/{slug} path
    owner: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # URL-safe slug auto-generated from name, e.g. "neo-soul-experiment"
    slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON list of free-form tag strings: genre ("jazz"), key ("F# minor"), instrument ("bass")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Music-semantic metadata for filter-based discovery
    key_signature: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tempo_bpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Feature-flag settings not covered by dedicated columns (JSON blob).
    # Keys: has_issues, has_projects, has_wiki, license, homepage_url,
    #       allow_merge_commit, allow_squash_merge, allow_rebase_merge,
    #       delete_branch_on_merge, default_branch.
    settings: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    # Soft-delete timestamp; non-null means the repo is logically deleted
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
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
    milestones: Mapped[list[MusehubMilestone]] = relationship(
        "MusehubMilestone", back_populates="repo", cascade="all, delete-orphan",
        foreign_keys="[MusehubMilestone.repo_id]",
    )
    pull_requests: Mapped[list[MusehubPullRequest]] = relationship(
        "MusehubPullRequest", back_populates="repo", cascade="all, delete-orphan"
    )
    releases: Mapped[list[MusehubRelease]] = relationship(
        "MusehubRelease", back_populates="repo", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[MusehubSession]] = relationship(
        "MusehubSession", back_populates="repo", cascade="all, delete-orphan"
    )
    webhooks: Mapped[list[MusehubWebhook]] = relationship(
        "MusehubWebhook", back_populates="repo", cascade="all, delete-orphan"
    )
    stars: Mapped[list[MusehubStar]] = relationship(
        "MusehubStar", back_populates="repo", cascade="all, delete-orphan"
    )
    events: Mapped[list[MusehubEvent]] = relationship(
        "MusehubEvent", back_populates="repo", cascade="all, delete-orphan"
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

class MusehubMilestone(Base):
    """A milestone that groups issues within a repo.

    Milestones give musicians a way to track progress toward goals like
    "Album v1.0" or "Mix Session 3". Issues are linked via milestone_id.

    ``number`` is sequential per repo (1-based), mirroring the issue numbering
    convention so users can reference milestones as "Milestone #1".
    """

    __tablename__ = "musehub_milestones"

    milestone_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Sequential per-repo milestone number (1, 2, 3…)
    number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Lifecycle: open → closed
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    # Display name of the user who created the milestone
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Optional due date for the milestone
    due_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship(
        "MusehubRepo", back_populates="milestones", foreign_keys=[repo_id]
    )
    issues: Mapped[list[MusehubIssue]] = relationship(
        "MusehubIssue", back_populates="milestone", foreign_keys="[MusehubIssue.milestone_id]"
    )
    issue_milestones: Mapped[list[MusehubIssueMilestone]] = relationship(
        "MusehubIssueMilestone",
        back_populates="milestone",
        cascade="all, delete-orphan",
    )


class MusehubIssueMilestone(Base):
    """Join table linking issues to milestones (many-to-many).

    A single issue can belong to multiple milestones and a milestone can
    contain many issues. The composite primary key on ``(issue_id,
    milestone_id)`` enforces uniqueness without a surrogate key.

    Cascade deletes ensure rows are removed when either the issue or the
    milestone is deleted.
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


class MusehubIssue(Base):
    """An issue opened against a Muse Hub repo.

    ``number`` is auto-incremented per repo starting at 1 so musicians can
    reference issues as ``#1``, ``#2``, etc., independently of the global PK.
    ``labels`` is a JSON list of free-form strings (no validation at MVP).
    ``assignee`` is the display name or identifier of the user assigned to this issue.
    ``milestone_id`` links the issue to a MusehubMilestone for progress tracking.
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
    # Display name or identifier of the user who opened this issue
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Display name or user ID of the collaborator assigned to resolve this issue
    assignee: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # FK to musehub_milestones — null when the issue is not part of a milestone
    milestone_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("musehub_milestones.milestone_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="issues")
    milestone: Mapped[MusehubMilestone | None] = relationship(
        "MusehubMilestone", back_populates="issues", foreign_keys=[milestone_id]
    )
    comments: Mapped[list[MusehubIssueComment]] = relationship(
        "MusehubIssueComment", back_populates="issue", cascade="all, delete-orphan",
        order_by="MusehubIssueComment.created_at",
    )


class MusehubIssueComment(Base):
    """A comment in a threaded discussion on a Muse Hub issue.

    Comments support threaded replies via ``parent_id``. Top-level comments
    have ``parent_id=None``. Markdown body is stored verbatim; rendering
    happens client-side.

    ``musical_refs`` is a JSON list of parsed musical context references
    extracted from the body (e.g. ``track:bass``, ``section:chorus``,
    ``beats:16-24``). Parsing is done at write time so reads are fast.
    """

    __tablename__ = "musehub_issue_comments"

    comment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    issue_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_issues.issue_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Display name or user ID of the comment author
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Markdown comment body
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Parent comment ID for threaded replies; null for top-level comments
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # JSON list of parsed musical context dicts: {"type": "track"|"section"|"beats", "value": "..."}
    musical_refs: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    issue: Mapped[MusehubIssue] = relationship("MusehubIssue", back_populates="comments")

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
    # Populated with the exact UTC timestamp when the PR is merged; None while open/closed
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Display name or identifier of the user who opened this PR
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="pull_requests")
    reviews: Mapped[list[MusehubPRReview]] = relationship(
        "MusehubPRReview", back_populates="pull_request", cascade="all, delete-orphan"
    )
    review_comments: Mapped[list[MusehubPRComment]] = relationship(
        "MusehubPRComment", back_populates="pull_request", cascade="all, delete-orphan"
    )

class MusehubPRReview(Base):
    """A formal review submission on a pull request.

    Tracks both reviewer assignment (``pending`` state) and submitted reviews
    (``approved``, ``changes_requested``, ``dismissed``).  One row per
    (pr_id, reviewer_username) pair — a reviewer can only hold one active state
    at a time.  Re-submitting replaces the previous state.

    State lifecycle:
      requested (by PR author) → pending
      reviewer submits         → approved | changes_requested | dismissed

    A PR is merge-ready when every pending/changes_requested review has been
    resolved to ``approved``, or the owner forces a merge.
    """

    __tablename__ = "musehub_pr_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    pr_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_pull_requests.pr_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Display name / JWT sub of the requested reviewer
    reviewer_username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # pending | approved | changes_requested | dismissed
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    # Markdown body of the review; null for bare reviewer assignments
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated when reviewer submits; null for pending assignments
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    pull_request: Mapped[MusehubPullRequest] = relationship(
        "MusehubPullRequest", back_populates="reviews"
    )


class MusehubPRComment(Base):
    """Inline review comment on a musical diff within a pull request.

    Supports four targeting granularities to let reviewers pinpoint exactly
    what they're commenting on:
      - ``general``  — whole PR (no positional target)
      - ``track``    — a named instrument track (e.g. "bass", "keys")
      - ``region``   — a beat range within a track (beat_start..beat_end)
      - ``note``     — a single note event (track + beat + pitch)

    ``parent_comment_id`` enables threaded replies.  None means a top-level
    review comment.  Replies carry the same ``pr_id`` so threads can be
    assembled in a single query.
    """

    __tablename__ = "musehub_pr_comments"

    comment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    pr_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_pull_requests.pr_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    repo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    # Markdown-formatted review body
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Targeting: "general" | "track" | "region" | "note"
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="general")
    # Instrument track name when target_type is "track", "region", or "note"
    target_track: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Beat range for "region" targets (inclusive start, exclusive end)
    target_beat_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_beat_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    # MIDI pitch (0-127) for "note" targets
    target_note_pitch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Parent comment ID for threaded replies; None for top-level comments
    parent_comment_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, index=True
    )

    pull_request: Mapped[MusehubPullRequest] = relationship(
        "MusehubPullRequest", back_populates="review_comments"
    )


class MusehubRelease(Base):
    """A published version release for a Muse Hub repo.

    Releases tie a human-readable ``tag`` (e.g. "v1.0") to a specific commit
    and carry markdown release notes plus a JSON map of download package URLs.
    The ``download_urls`` field is a JSON object keyed by package type:
    "midi_bundle", "stems", "mp3", "musicxml", "metadata".

    ``tag`` is unique per repo — enforced by the DB constraint
    ``uq_musehub_releases_repo_tag`` and guarded at the service layer to
    return a clean 409 before the constraint fires.
    """

    __tablename__ = "musehub_releases"
    __table_args__ = (UniqueConstraint("repo_id", "tag", name="uq_musehub_releases_repo_tag"),)

    release_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Semantic version tag, e.g. "v1.0", "v2.3.1" — unique per repo.
    tag: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # Markdown release notes authored by the musician.
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Optional commit this release is pinned to.
    commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # JSON map of download package URLs, keyed by package type.
    download_urls: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    # Display name or identifier of the user who published this release
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # True when this release is not yet stable (e.g. "v2.0-beta").
    is_prerelease: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True when the release is saved but not yet publicly visible.
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Optional ASCII-armoured GPG signature for the tag object.
    gpg_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="releases")
    assets: Mapped[list[MusehubReleaseAsset]] = relationship(
        "MusehubReleaseAsset", back_populates="release", cascade="all, delete-orphan"
    )


class MusehubReleaseAsset(Base):
    """An asset (file attachment) associated with a Muse Hub release.

    Assets represent downloadable artifacts attached to a release — for example
    a MIDI bundle, a stems archive, or a rendered MP3. ``download_count``
    tracks how many times the asset has been downloaded so the release page
    can surface popularity metrics without querying the analytics pipeline.

    ``release_id`` is the FK to the owning release and participates in cascade
    deletes: removing the release removes all its assets.
    """

    __tablename__ = "musehub_release_assets"

    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    release_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_releases.release_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised so we can query assets by repo without joining releases.
    repo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # Filename shown in the UI, e.g. "summer-sessions-v1.0.mid"
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    # Optional human-readable label, e.g. "MIDI Bundle", "Stems Archive"
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # MIME type, e.g. "audio/midi", "application/zip"
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # File size in bytes; 0 when unknown
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Direct download URL for the artifact (pre-signed or CDN URL)
    download_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Incrementing counter updated each time the asset is downloaded
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    release: Mapped[MusehubRelease] = relationship("MusehubRelease", back_populates="assets")


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
    )


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
    # JSON-encoded payload bytes that were (or will be) sent to the subscriber URL.
    # Stored so that failed deliveries can be retried with the original payload.
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    webhook: Mapped[MusehubWebhook] = relationship(
        "MusehubWebhook", back_populates="deliveries"
    )

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


class MusehubSession(Base):
    """A recording session record pushed to Muse Hub from the CLI.

    Sessions capture the creative context of a recording period: who was
    present, where they recorded, what they intended to create, which commits
    were made, and any closing notes. Maps to ``muse session show`` locally.

    ``commits`` is a JSON list of Muse commit IDs associated with the session.
    ``participants`` is a JSON list of participant name strings.
    """

    __tablename__ = "musehub_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(10), nullable=False, default="1")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    participants: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    location: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    intent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON list of Muse commit IDs made during this session
    commits: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # True if session is currently active; False after stop
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="sessions")


# ---------------------------------------------------------------------------
# Social layer — Phase 4
# ---------------------------------------------------------------------------


class MusehubComment(Base):
    """Threaded comment on a repo object (commit, PR, issue, or repo itself).

    ``target_type`` distinguishes what the comment is attached to:
      "commit"  | "pull_request" | "issue" | "repo"
    ``target_id`` is the primary key of the target object.
    ``parent_id`` enables threaded replies; None means a top-level comment.
    """

    __tablename__ = "musehub_comments"

    comment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MusehubReaction(Base):
    """Emoji reaction on a comment or target object.

    One reaction per (user, target_type, target_id, emoji) combination.
    """

    __tablename__ = "musehub_reactions"
    __table_args__ = (
        UniqueConstraint("user_id", "target_type", "target_id", "emoji", name="uq_musehub_reactions"),
    )

    reaction_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    emoji: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class MusehubFollow(Base):
    """User follows another user.

    Enables a social graph where followers see followed users' activity
    in their feed.
    """

    __tablename__ = "musehub_follows"
    __table_args__ = (
        UniqueConstraint("follower_id", "followee_id", name="uq_musehub_follows"),
    )

    follow_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    follower_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    followee_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class MusehubWatch(Base):
    """User watches a repo — subscribes to activity notifications."""

    __tablename__ = "musehub_watches"
    __table_args__ = (
        UniqueConstraint("user_id", "repo_id", name="uq_musehub_watches"),
    )

    watch_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class MusehubNotification(Base):
    """A notification delivered to a user.

    ``event_type`` classifies the triggering event:
      "comment" | "mention" | "pr_opened" | "pr_merged" | "issue_opened" |
      "issue_closed" | "new_commit" | "new_follower"
    """

    __tablename__ = "musehub_notifications"

    notif_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    recipient_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    repo_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, index=True
    )


class MusehubFork(Base):
    """Records a fork relationship between two repos.

    When user B forks user A's repo, a new repo is created for B and a
    MusehubFork row records the lineage.
    """

    __tablename__ = "musehub_forks"
    __table_args__ = (
        UniqueConstraint("source_repo_id", "fork_repo_id", name="uq_musehub_forks"),
    )

    fork_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source_repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fork_repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    forked_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class MusehubViewEvent(Base):
    """Debounced repo view event for view-count tracking.

    One row per (repo_id, viewer_fingerprint, date) to avoid counting
    the same visitor multiple times per day.
    """

    __tablename__ = "musehub_view_events"
    __table_args__ = (
        UniqueConstraint("repo_id", "viewer_fingerprint", "event_date", name="uq_musehub_view_events"),
    )

    view_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    viewer_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    event_date: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class MusehubDownloadEvent(Base):
    """Records each artifact export download for analytics."""

    __tablename__ = "musehub_download_events"

    dl_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ref: Mapped[str] = mapped_column(String(255), nullable=False)
    downloader_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, index=True
    )


class MusehubRenderJob(Base):
    """Render job record tracking the async generation of MP3 and piano-roll artifacts.

    One row is created per commit push. The render pipeline sets ``status`` as
    work progresses: ``pending`` → ``rendering`` → ``complete`` | ``failed``.

    ``mp3_object_ids`` and ``image_object_ids`` are JSON lists of
    ``musehub_objects.object_id`` values written by the render pipeline.
    They are empty until the job reaches ``complete`` or ``failed``.

    Design: idempotent by ``(repo_id, commit_id)`` — re-pushing the same
    commit does not create a second render job. The pipeline checks for an
    existing row before creating one.
    """

    __tablename__ = "musehub_render_jobs"
    __table_args__ = (
        UniqueConstraint("repo_id", "commit_id", name="uq_musehub_render_jobs_repo_commit"),
    )

    render_job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The commit SHA this render covers — matches musehub_commits.commit_id.
    commit_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Lifecycle: pending → rendering → complete | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    # Human-readable error from the last failure; null when status != "failed".
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Number of MIDI objects found in the commit snapshot.
    midi_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # JSON list of object_ids for generated MP3 artifacts.
    mp3_object_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # JSON list of object_ids for generated piano-roll PNG artifacts.
    image_object_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )


class MusehubEvent(Base):
    """A repo-level activity event — the chronological event stream for a repo.

    One row per action that mutates repo state: commits pushed, PRs opened/merged,
    issues opened/closed, branches created/deleted, tags pushed, and sessions started/ended.

    ``event_type`` vocabulary (enforced by service layer, not by DB constraint):
      "commit_pushed" | "pr_opened" | "pr_merged" | "pr_closed" |
      "issue_opened" | "issue_closed" | "branch_created" | "branch_deleted" |
      "tag_pushed" | "session_started" | "session_ended"

    ``actor`` is the human-readable identifier of the user who triggered the event
    (typically the JWT ``sub`` claim or the pusher username).
    ``description`` is a one-line human-readable summary rendered in the feed.
    ``metadata`` carries event-specific structured data (e.g. commit SHA, PR number)
    for deep-link rendering without additional DB joins.

    Design: append-only.  Events are never updated or deleted (cascade only on
    repo delete).  The feed is read newest-first, paginated by ``(repo_id, created_at)``.
    """

    __tablename__ = "musehub_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("musehub_repos.repo_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Vocabulary: commit_pushed | pr_opened | pr_merged | pr_closed |
    #             issue_opened | issue_closed | branch_created | branch_deleted |
    #             tag_pushed | session_started | session_ended
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Human-readable actor — JWT sub or pusher username
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    # One-line summary rendered in the feed UI
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Event-specific payload (commit SHA, PR/issue number, branch name, …)
    # Named event_metadata to avoid conflict with SQLAlchemy DeclarativeBase.metadata
    event_metadata: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, index=True
    )

    repo: Mapped[MusehubRepo] = relationship("MusehubRepo", back_populates="events")
