"""Pydantic v2 request/response models for the Muse Hub API.

All wire-format fields use camelCase via CamelModel.  Python code uses
snake_case throughout; only serialisation to JSON uses camelCase.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from maestro.models.base import CamelModel


# ── Sync protocol models ──────────────────────────────────────────────────────


class CommitInput(CamelModel):
    """A single commit record transferred in a push payload."""

    commit_id: str
    parent_ids: list[str] = Field(default_factory=list)
    message: str
    snapshot_id: str | None = None
    timestamp: datetime
    # Optional — falls back to the JWT ``sub`` when absent
    author: str | None = None


class ObjectInput(CamelModel):
    """A binary object transferred in a push payload.

    Content is base64-encoded.  For MVP, objects up to ~1 MB are fine; larger
    files will require pre-signed URL upload in a future release.
    """

    object_id: str = Field(..., description="Content-addressed ID, e.g. 'sha256:abc...'")
    path: str = Field(..., description="Relative path hint, e.g. 'tracks/jazz_4b.mid'")
    content_b64: str = Field(..., description="Base64-encoded binary content")


class PushRequest(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/push."""

    branch: str
    head_commit_id: str
    commits: list[CommitInput] = Field(default_factory=list)
    objects: list[ObjectInput] = Field(default_factory=list)
    # Set true to allow non-fast-forward updates (overwrites remote head)
    force: bool = False


class PushResponse(CamelModel):
    """Response for POST /musehub/repos/{repo_id}/push."""

    ok: bool
    remote_head: str


class PullRequest(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/pull."""

    branch: str
    # Commit IDs the client already has — missing ones will be returned
    have_commits: list[str] = Field(default_factory=list)
    # Object IDs the client already has — missing ones will be returned
    have_objects: list[str] = Field(default_factory=list)


class ObjectResponse(CamelModel):
    """A binary object returned in a pull response."""

    object_id: str
    path: str
    content_b64: str


class PullResponse(CamelModel):
    """Response for POST /musehub/repos/{repo_id}/pull."""

    commits: list[CommitResponse]
    objects: list[ObjectResponse]
    remote_head: str | None


# ── Request models ────────────────────────────────────────────────────────────


class CreateRepoRequest(CamelModel):
    """Body for POST /musehub/repos."""

    name: str = Field(..., min_length=1, max_length=255, description="Repo name")
    visibility: str = Field("private", pattern="^(public|private)$")


# ── Response models ───────────────────────────────────────────────────────────


class RepoResponse(CamelModel):
    """Wire representation of a Muse Hub repo."""

    repo_id: str
    name: str
    visibility: str
    owner_user_id: str
    clone_url: str
    created_at: datetime


class BranchResponse(CamelModel):
    """Wire representation of a branch pointer."""

    branch_id: str
    name: str
    head_commit_id: str | None = None


class CommitResponse(CamelModel):
    """Wire representation of a pushed commit."""

    commit_id: str
    branch: str
    parent_ids: list[str]
    message: str
    author: str
    timestamp: datetime
    snapshot_id: str | None = None


class BranchListResponse(CamelModel):
    """Paginated list of branches."""

    branches: list[BranchResponse]


class CommitListResponse(CamelModel):
    """Paginated list of commits (newest first)."""

    commits: list[CommitResponse]
    total: int


# ── Issue models ───────────────────────────────────────────────────────────────


class IssueCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/issues."""

    title: str = Field(..., min_length=1, max_length=500, description="Issue title")
    body: str = Field("", description="Issue description (Markdown)")
    labels: list[str] = Field(default_factory=list, description="Free-form label strings")


class IssueResponse(CamelModel):
    """Wire representation of a Muse Hub issue."""

    issue_id: str
    number: int
    title: str
    body: str
    state: str
    labels: list[str]
    created_at: datetime


class IssueListResponse(CamelModel):
    """List of issues for a repo."""

    issues: list[IssueResponse]


# ── Pull request models ────────────────────────────────────────────────────────


class PRCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/pull-requests."""

    title: str = Field(..., min_length=1, max_length=500, description="PR title")
    from_branch: str = Field(..., min_length=1, max_length=255, description="Source branch name")
    to_branch: str = Field(..., min_length=1, max_length=255, description="Target branch name")
    body: str = Field("", description="PR description (Markdown)")


class PRResponse(CamelModel):
    """Wire representation of a Muse Hub pull request."""

    pr_id: str
    title: str
    body: str
    state: str
    from_branch: str
    to_branch: str
    merge_commit_id: str | None = None
    created_at: datetime


class PRListResponse(CamelModel):
    """List of pull requests for a repo."""

    pull_requests: list[PRResponse]


class PRMergeRequest(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/pull-requests/{pr_id}/merge."""

    merge_strategy: str = Field(
        "merge_commit",
        pattern="^(merge_commit)$",
        description="Merge strategy — only 'merge_commit' is supported at MVP",
    )


class PRMergeResponse(CamelModel):
    """Confirmation that a PR was merged."""

    merged: bool
    merge_commit_id: str


# ── Object metadata model ─────────────────────────────────────────────────────


class ObjectMetaResponse(CamelModel):
    """Wire representation of a stored artifact — metadata only, no content bytes.

    Returned by GET /musehub/repos/{repo_id}/objects. Use the ``/content``
    sub-resource to download the raw bytes. The ``path`` field retains the
    client-supplied relative path hint (e.g. "tracks/jazz_4b.mid") and is
    the primary signal for choosing display treatment (.webp → img, .mid /
    .mp3 → audio/download).
    """

    object_id: str
    path: str
    size_bytes: int
    created_at: datetime


class ObjectMetaListResponse(CamelModel):
    """List of artifact metadata for a repo."""

    objects: list[ObjectMetaResponse]


# ── Timeline models ───────────────────────────────────────────────────────────


class TimelineEventType(str):
    """Discriminator values for timeline event layers."""

    COMMIT = "commit"
    EMOTION = "emotion"
    SECTION = "section"
    TRACK = "track"


class TimelineCommitEvent(CamelModel):
    """A commit plotted as a point on the timeline.

    Every pushed commit becomes a commit event regardless of its message content.
    The ``commit_id`` is the canonical identifier for audio-preview lookup and
    deep-linking to the commit detail page.
    """

    event_type: str = "commit"
    commit_id: str
    branch: str
    message: str
    author: str
    timestamp: datetime
    parent_ids: list[str]


class TimelineEmotionEvent(CamelModel):
    """An emotion-vector data point overlaid on the timeline as a line chart.

    Emotion values are derived deterministically from the commit SHA so the
    timeline is always reproducible without external inference. Each field is
    in the range [0.0, 1.0]. Agents use these values to understand how the
    emotional character of the composition shifted over time.
    """

    event_type: str = "emotion"
    commit_id: str
    timestamp: datetime
    valence: float
    energy: float
    tension: float


class TimelineSectionEvent(CamelModel):
    """A detected section change plotted as a marker on the timeline.

    Section names are extracted from commit messages using keyword heuristics
    (e.g. "added chorus", "intro complete", "bridge removed"). The ``action``
    field is either ``"added"`` or ``"removed"``.
    """

    event_type: str = "section"
    commit_id: str
    timestamp: datetime
    section_name: str
    action: str


class TimelineTrackEvent(CamelModel):
    """A detected track addition or removal plotted as a marker on the timeline.

    Track changes are extracted from commit messages using keyword heuristics
    (e.g. "added bass", "removed keys", "new drums track"). The ``action``
    field is either ``"added"`` or ``"removed"``.
    """

    event_type: str = "track"
    commit_id: str
    timestamp: datetime
    track_name: str
    action: str


class TimelineResponse(CamelModel):
    """Chronological timeline of musical evolution for a repo.

    Contains four parallel event streams that the client renders as
    independently toggleable layers:
    - ``commits``: every pushed commit (always present)
    - ``emotion``: emotion-vector data points per commit (always present)
    - ``sections``: section change events derived from commit messages
    - ``tracks``: track add/remove events derived from commit messages

    Agent use case: call this endpoint to understand how a project evolved —
    when sections were introduced, when the emotional character shifted, and
    which instruments were added or removed over time.
    """

    commits: list[TimelineCommitEvent]
    emotion: list[TimelineEmotionEvent]
    sections: list[TimelineSectionEvent]
    tracks: list[TimelineTrackEvent]
    total_commits: int
