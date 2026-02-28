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
    description: str = Field("", description="Short description shown on the explore page")
    tags: list[str] = Field(
        default_factory=list,
        description="Free-form tags — genre, key, instrumentation (e.g. 'jazz', 'F# minor', 'bass')",
    )
    key_signature: str | None = Field(None, max_length=50, description="Musical key (e.g. 'C major', 'F# minor')")
    tempo_bpm: int | None = Field(None, ge=20, le=300, description="Tempo in BPM")


# ── Response models ───────────────────────────────────────────────────────────


class RepoResponse(CamelModel):
    """Wire representation of a Muse Hub repo."""

    repo_id: str
    name: str
    visibility: str
    owner_user_id: str
    clone_url: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    key_signature: str | None = None
    tempo_bpm: int | None = None
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


# ── Explore / Discover models ──────────────────────────────────────────────────


class ExploreRepoResult(CamelModel):
    """A public repo card shown on the explore/discover page.

    Extends RepoResponse with aggregated counts (star_count, commit_count)
    that are computed at query time for efficient pagination and sorting.
    These counts are read-only signals — they are never persisted directly on
    the repo row to avoid write amplification on every push/star.
    """

    repo_id: str
    name: str
    owner_user_id: str
    description: str
    tags: list[str]
    key_signature: str | None
    tempo_bpm: int | None
    star_count: int
    commit_count: int
    created_at: datetime


class ExploreResponse(CamelModel):
    """Paginated response from GET /api/v1/musehub/discover/repos.

    ``total`` reflects the full filtered result set size — not just the current
    page — so clients can render pagination controls without a second query.
    """

    repos: list[ExploreRepoResult]
    total: int
    page: int
    page_size: int


class StarResponse(CamelModel):
    """Confirmation that a star was added or removed."""

    starred: bool
    star_count: int
