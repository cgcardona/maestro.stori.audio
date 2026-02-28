"""Pydantic v2 request/response models for the Muse Hub API.

All wire-format fields use camelCase via CamelModel.  Python code uses
snake_case throughout; only serialisation to JSON uses camelCase.
"""
from __future__ import annotations

from datetime import datetime
from typing import NotRequired, TypedDict

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


# ── Profile models ────────────────────────────────────────────────────────────


class ProfileUpdateRequest(CamelModel):
    """Body for PUT /api/v1/musehub/users/{username}.

    All fields are optional — send only the ones to change.
    """

    bio: str | None = Field(None, max_length=500, description="Short bio (Markdown supported)")
    avatar_url: str | None = Field(None, max_length=2048, description="Avatar image URL")
    pinned_repo_ids: list[str] | None = Field(
        None, max_length=6, description="Up to 6 repo_ids to pin on the profile page"
    )


class ProfileRepoSummary(CamelModel):
    """Compact repo summary shown on a user's profile page.

    Includes the last-activity timestamp derived from the most recent commit
    and a stub star_count (always 0 at MVP — no star mechanism yet).
    """

    repo_id: str
    name: str
    visibility: str
    star_count: int = 0
    last_activity_at: datetime | None = None
    created_at: datetime


class ContributionDay(CamelModel):
    """A single day in the contribution heatmap.

    ``date`` is ISO-8601 (YYYY-MM-DD). ``count`` is the number of commits
    authored on that day across all of the user's repos.
    """

    date: str
    count: int


class ProfileResponse(CamelModel):
    """Full wire representation of a Muse Hub user profile.

    Returned by GET /api/v1/musehub/users/{username}.
    ``repos`` contains only public repos when the caller is not the owner.
    ``contribution_graph`` is the last 52 weeks of daily commit activity.
    ``session_credits`` is the total number of commits across all repos
    (a proxy for creative session activity).
    """

    user_id: str
    username: str
    bio: str | None = None
    avatar_url: str | None = None
    pinned_repo_ids: list[str]
    repos: list[ProfileRepoSummary]
    contribution_graph: list[ContributionDay]
    session_credits: int
    created_at: datetime
    updated_at: datetime

# ── Cross-repo search models ───────────────────────────────────────────────────


class GlobalSearchCommitMatch(CamelModel):
    """A single commit that matched the search query in a cross-repo search.

    Consumers display ``repo_id`` / ``repo_name`` as the group header, then
    render ``commit_id``, ``message``, and ``author`` as the match row.
    Audio preview is surfaced via ``audio_object_id`` when an .mp3 or .ogg
    artifact is attached to the same repo.
    """

    commit_id: str
    message: str
    author: str
    branch: str
    timestamp: datetime
    repo_id: str
    repo_name: str
    repo_owner: str
    repo_visibility: str
    audio_object_id: str | None = None
# ── Webhook models ────────────────────────────────────────────────────────────

# Valid event types a subscriber may register for.
WEBHOOK_EVENT_TYPES: frozenset[str] = frozenset(
    [
        "push",
        "pull_request",
        "issue",
        "release",
        "branch",
        "tag",
        "session",
        "analysis",
    ]
)


class WebhookCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/webhooks.

    ``events`` must be a non-empty subset of the valid event-type strings
    (push, pull_request, issue, release, branch, tag, session, analysis).
    ``secret`` is optional; when provided it is used to sign every delivery
    with HMAC-SHA256 in the ``X-MuseHub-Signature`` header.
    """

    url: str = Field(..., min_length=1, max_length=2048, description="HTTPS endpoint to deliver events to")
    events: list[str] = Field(..., min_length=1, description="Event types to subscribe to")
    secret: str = Field("", description="Optional HMAC-SHA256 signing secret")


class WebhookResponse(CamelModel):
    """Wire representation of a registered webhook subscription."""

    webhook_id: str
    repo_id: str
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class WebhookListResponse(CamelModel):
    """List of webhook subscriptions for a repo."""

    webhooks: list[WebhookResponse]


class WebhookDeliveryResponse(CamelModel):
    """Wire representation of a single webhook delivery attempt."""

    delivery_id: str
    webhook_id: str
    event_type: str
    attempt: int
    success: bool
    response_status: int
    response_body: str
    delivered_at: datetime


class WebhookDeliveryListResponse(CamelModel):
    """Paginated list of delivery attempts for a webhook."""

    deliveries: list[WebhookDeliveryResponse]


# ── Webhook event payload TypedDicts ─────────────────────────────────────────
# These typed dicts are used as the payload argument to dispatch_event /
# dispatch_event_background, replacing dict[str, Any] at the service boundary.


class PushEventPayload(TypedDict):
    """Payload emitted when commits are pushed to a MuseHub repo.

    Used with event_type="push".
    """

    repoId: str
    branch: str
    headCommitId: str
    pushedBy: str
    commitCount: int


class IssueEventPayload(TypedDict):
    """Payload emitted when an issue is opened or closed.

    ``action`` is either ``"opened"`` or ``"closed"``.
    Used with event_type="issue".
    """

    repoId: str
    action: str
    issueId: str
    number: int
    title: str
    state: str


class PullRequestEventPayload(TypedDict):
    """Payload emitted when a PR is opened or merged.

    ``action`` is either ``"opened"`` or ``"merged"``.
    ``mergeCommitId`` is only present on the "merged" action.
    Used with event_type="pull_request".
    """

    repoId: str
    action: str
    prId: str
    title: str
    fromBranch: str
    toBranch: str
    state: str
    mergeCommitId: NotRequired[str]


# Union of all typed webhook event payloads.  The dispatcher accepts any of
# these; callers pass the specific TypedDict for their event type.
WebhookEventPayload = PushEventPayload | IssueEventPayload | PullRequestEventPayload

# ── Context models ────────────────────────────────────────────────────────────


class MuseHubContextCommitInfo(CamelModel):
    """Minimal commit metadata included in a MuseHub context document."""

    commit_id: str
    message: str
    author: str
    branch: str
    timestamp: datetime


class GlobalSearchRepoGroup(CamelModel):
    """All matching commits for a single repo, with repo-level metadata.

    Results are grouped by repo so consumers can render a collapsible section
    per repo (name, owner) and paginate within each group.
    """

    repo_id: str
    repo_name: str
    repo_owner: str
    repo_visibility: str
    matches: list[GlobalSearchCommitMatch]
    total_matches: int


class GlobalSearchResult(CamelModel):
    """Top-level response for GET /search?q={query}.

    ``groups`` contains one entry per public repo that had at least one
    matching commit.  ``total_repos`` is the count of repos searched, not just
    the repos with matches.  ``page`` / ``page_size`` enable offset pagination
    across groups.
    """

    query: str
    mode: str
    groups: list[GlobalSearchRepoGroup]
    total_repos_searched: int
    page: int
    page_size: int


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


# ── DAG graph models ───────────────────────────────────────────────────────────


class DagNode(CamelModel):
    """A single commit node in the repo's directed acyclic graph.

    Designed for consumption by interactive graph renderers. The ``is_head``
    flag marks the current HEAD commit across all branches. ``branch_labels``
    and ``tag_labels`` list all ref names pointing at this commit.
    """

    commit_id: str
    message: str
    author: str
    timestamp: datetime
    branch: str
    parent_ids: list[str]
    is_head: bool = False
    branch_labels: list[str] = Field(default_factory=list)
    tag_labels: list[str] = Field(default_factory=list)


class DagEdge(CamelModel):
    """A directed edge in the commit DAG.

    ``source`` is the child commit (the one that has the parent).
    ``target`` is the parent commit. This follows standard graph convention:
    edge flows from child → parent (newest to oldest).
    """

    source: str
    target: str


class DagGraphResponse(CamelModel):
    """Topologically sorted commit graph for a Muse Hub repo.

    ``nodes`` are ordered from oldest ancestor to newest commit (Kahn's
    algorithm). ``edges`` enumerate every parent→child relationship.
    Consumers can render this directly as a directed acyclic graph without
    further processing.

    Agent use case: an AI music agent can use this to identify which branches
    diverged from a common ancestor, find merge points, and reason about the
    project's compositional history.
    """

    nodes: list[DagNode]
    edges: list[DagEdge]
    head_commit_id: str | None = None
