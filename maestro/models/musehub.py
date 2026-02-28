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


# ── Session models ─────────────────────────────────────────────────────────────


class SessionCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/sessions.

    Accepts a session record pushed from ``muse session end``.  The
    ``session_id`` field uses the local UUID so the same session can be
    pushed multiple times idempotently (upsert semantics in the service).
    """

    session_id: str = Field(..., description="UUIDv4 from the local .muse/sessions/<id>.json")
    schema_version: str = Field("1", description="JSON schema version string")
    started_at: datetime = Field(..., description="ISO-8601 UTC session start timestamp")
    ended_at: datetime | None = Field(None, description="ISO-8601 UTC session end timestamp")
    participants: list[str] = Field(default_factory=list, description="Ordered participant names")
    location: str = Field("", description="Recording location or studio name")
    intent: str = Field("", description="Creative intent declared at session start")
    commits: list[str] = Field(default_factory=list, description="Muse commit IDs from this session")
    notes: str = Field("", description="Closing notes added by muse session end --notes")


class SessionResponse(CamelModel):
    """Wire representation of a pushed Muse Hub recording session.

    Returned by GET /musehub/repos/{repo_id}/sessions and
    GET /musehub/repos/{repo_id}/sessions/{session_id}.  Agents use this
    to reconstruct creative context — who was present, what was intended,
    which commits were produced, and any post-session notes.
    """

    session_id: str
    repo_id: str
    schema_version: str
    started_at: datetime
    ended_at: datetime | None
    participants: list[str]
    location: str
    intent: str
    commits: list[str]
    notes: str
    created_at: datetime


class SessionListResponse(CamelModel):
    """Paginated list of sessions for a repo, newest first."""

    sessions: list[SessionResponse]
    total: int


# ── In-repo search models ─────────────────────────────────────────────────────


class SearchCommitMatch(CamelModel):
    """A single commit returned by a search query.

    Carries enough metadata to render a result row and launch an audio preview.
    The ``score`` field is populated by keyword/recall modes (0–1 overlap ratio);
    property and grep modes always return 1.0.
    """

    commit_id: str
    branch: str
    message: str
    author: str
    timestamp: datetime
    score: float = Field(1.0, ge=0.0, le=1.0, description="Match score (0–1); always 1.0 for exact-match modes")
    match_source: str = Field("message", description="Where the match was found: 'message', 'branch', or 'property'")


class SearchResponse(CamelModel):
    """Response envelope for all four in-repo search modes.

    ``mode`` echoes back the requested search mode so clients can render
    mode-appropriate headers.  ``total_scanned`` is the number of commits
    examined before limit was applied; useful for indicating search depth.
    """

    mode: str
    query: str
    matches: list[SearchCommitMatch]
    total_scanned: int
    limit: int
