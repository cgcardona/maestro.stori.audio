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


# ── Divergence visualization models ───────────────────────────────────────────


class DivergenceDimensionResponse(CamelModel):
    """Wire representation of divergence scores for a single musical dimension.

    Mirrors :class:`maestro.services.musehub_divergence.MuseHubDimensionDivergence`
    for JSON serialization.  AI agents consume this to decide which dimension
    of a branch needs creative attention before merging.
    """

    dimension: str
    level: str
    score: float
    description: str
    branch_a_commits: int
    branch_b_commits: int


class DivergenceResponse(CamelModel):
    """Full musical divergence report between two Muse Hub branches.

    Returned by ``GET /musehub/repos/{repo_id}/divergence``.  Contains five
    per-dimension scores (melodic, harmonic, rhythmic, structural, dynamic)
    and an overall score computed as the mean of those five scores.

    The ``overall_score`` is in [0.0, 1.0]; multiply by 100 for a percentage.
    A score of 0.0 means identical, 1.0 means completely diverged.
    """

    repo_id: str
    branch_a: str
    branch_b: str
    common_ancestor: str | None
    dimensions: list[DivergenceDimensionResponse]
    overall_score: float

# ── Context models ────────────────────────────────────────────────────────────


class MuseHubContextCommitInfo(CamelModel):
    """Minimal commit metadata included in a MuseHub context document."""

    commit_id: str
    message: str
    author: str
    branch: str
    timestamp: datetime


class MuseHubContextHistoryEntry(CamelModel):
    """A single ancestor commit in the evolutionary history of the composition.

    History is built by walking parent_ids from the target commit.
    Entries are returned newest-first and limited to the last 5 ancestors.
    """

    commit_id: str
    message: str
    author: str
    timestamp: datetime
    active_tracks: list[str]


class MuseHubContextMusicalState(CamelModel):
    """Musical state at the target commit, derived from stored artifact paths.

    ``active_tracks`` is populated from object paths in the repo.
    All analytical fields (key, tempo, etc.) are None until Storpheus MIDI
    analysis is integrated — agents should treat None as "unknown."
    """

    active_tracks: list[str]
    key: str | None = None
    mode: str | None = None
    tempo_bpm: int | None = None
    time_signature: str | None = None
    form: str | None = None
    emotion: str | None = None


class MuseHubContextResponse(CamelModel):
    """Human-readable and agent-consumable musical context document for a commit.

    Returned by ``GET /api/v1/musehub/repos/{repo_id}/context/{ref}``.

    This is the MuseHub equivalent of ``MuseContextResult`` — built from
    the remote repo's commit graph and stored objects rather than the local
    ``.muse`` filesystem.  The structure deliberately mirrors ``MuseContextResult``
    so that agents consuming either source see the same schema.

    Fields:
        repo_id:        The hub repo identifier.
        current_branch: Branch name for the target commit.
        head_commit:    Metadata for the resolved commit (ref).
        musical_state:  Active tracks and any available musical dimensions.
        history:        Up to 5 ancestor commits, newest-first.
        missing_elements: Dimensions that could not be determined from stored data.
        suggestions:    Composer-facing hints about what to work on next.
    """

    repo_id: str
    current_branch: str
    head_commit: MuseHubContextCommitInfo
    musical_state: MuseHubContextMusicalState
    history: list[MuseHubContextHistoryEntry]
    missing_elements: list[str]
    suggestions: dict[str, str]


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
