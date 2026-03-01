"""Muse Hub repo, branch, commit, credits, and agent context route handlers.

Endpoint summary:
  POST /musehub/repos                                                     — create a new remote repo
  GET  /musehub/repos/{repo_id}                                           — get repo metadata (by internal UUID)
  GET  /musehub/{owner}/{repo_slug}                                       — get repo metadata (by owner/slug)
  GET  /musehub/repos/{repo_id}/branches                                  — list all branches
  GET  /musehub/repos/{repo_id}/commits                                   — list commits (newest first)
  GET  /musehub/repos/{repo_id}/commits/{sha}/render-status               — render job status for a commit
  GET  /musehub/repos/{repo_id}/credits                                   — aggregated contributor credits
  GET  /musehub/repos/{repo_id}/context                                   — agent context briefing
  GET  /musehub/repos/{repo_id}/timeline                                  — chronological timeline with emotion/section/track layers
  GET  /musehub/repos/{repo_id}/form-structure/{ref}                      — form and structure analysis
  POST /musehub/repos/{repo_id}/sessions                                  — push a recording session
  GET  /musehub/repos/{repo_id}/sessions                                  — list recording sessions
  GET  /musehub/repos/{repo_id}/sessions/{session_id}                     — get a single session
  GET  /musehub/repos/{repo_id}/arrange/{ref}                             — arrangement matrix (instrument × section grid)

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_repository, maestro.services.musehub_credits,
and maestro.services.musehub_context.
"""
from __future__ import annotations

import logging

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.db import musehub_models as db_models
from maestro.db import musehub_collaborator_models as collab_models
from sqlalchemy import select
from maestro.models.musehub import (
    ActivityFeedResponse,
    ArrangementMatrixResponse,
    BranchDetailListResponse,
    BranchListResponse,
    CommitDiffDimensionScore,
    CommitDiffSummaryResponse,
    CommitListResponse,
    CommitResponse,
    CompareResponse,
    CreateRepoRequest,
    DivergenceDimensionResponse,
    DivergenceResponse,
    EmotionDiffResponse,
    StargazerEntry,
    StargazerListResponse,
    TimelineResponse,
    DagGraphResponse,
    GrooveCheckResponse,
    GrooveCommitEntry,
    MuseHubContextResponse,
    RepoListResponse,
    RepoResponse,
    RepoSettingsPatch,
    RepoSettingsResponse,
    RepoStatsResponse,
    CreditsResponse,
    RenderStatusResponse,
    SessionCreate,
    SessionListResponse,
    SessionResponse,
    SessionStop,
    TrackListingResponse,
)
from maestro.models.musehub_analysis import FormStructureResponse
from maestro.models.musehub_context import (
    ContextDepth,
    ContextFormat,
)
from maestro.services import musehub_analysis, musehub_context, musehub_credits, musehub_divergence, musehub_events, musehub_listen, musehub_releases, musehub_repository, musehub_sessions
from maestro.services.muse_groove_check import (
    DEFAULT_THRESHOLD,
    compute_groove_check,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _guard_visibility(repo: RepoResponse | None, claims: TokenClaims | None) -> None:
    """Raise 404 when the repo doesn't exist; 401 when it's private and unauthenticated."""
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get(
    "/repos",
    response_model=RepoListResponse,
    operation_id="listMyRepos",
    summary="List repos for the authenticated user (own + collaborated)",
    tags=["Repos"],
)
async def list_my_repos(
    limit: int = Query(20, ge=1, le=100, description="Max repos per page"),
    cursor: str | None = Query(None, description="Pagination cursor from a previous response"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> RepoListResponse:
    """Return repos owned by or collaborated on by the authenticated user.

    Results are ordered newest-first.  Pass the ``nextCursor`` value from the
    previous response as ``?cursor=`` to advance through subsequent pages.
    An absent ``nextCursor`` in the response means you have reached the last page.

    Auth: requires a valid JWT Bearer token.
    """
    user_id: str = claims.get("sub") or ""
    return await musehub_repository.list_repos_for_user(db, user_id, limit=limit, cursor=cursor)


@router.post(
    "/repos",
    response_model=RepoResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createRepo",
    summary="Create a remote Muse repo",
    tags=["Repos"],
)
async def create_repo(
    body: CreateRepoRequest,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> RepoResponse:
    """Create a new remote Muse Hub repository owned by the authenticated user.

    ``slug`` is auto-generated from ``name``.  Returns 409 if the ``(owner, slug)``
    pair already exists — the musician must rename the repo to get a distinct slug.

    Wizard behaviors (from the request body):
    - ``initialize=true``: an empty "Initial commit" + default branch are created
      immediately so the repo is browsable right away.
    - ``template_repo_id``: topics/description are copied from a public template repo.
    - ``license``: stored in the repo settings blob.
    - ``topics``: merged with ``tags`` into a single tag list.

    Clone URL: ``musehub://{owner}/{slug}``
    """
    owner_user_id: str = claims.get("sub") or ""
    try:
        repo = await musehub_repository.create_repo(
            db,
            name=body.name,
            owner=body.owner,
            visibility=body.visibility,
            owner_user_id=owner_user_id,
            description=body.description,
            tags=body.tags,
            key_signature=body.key_signature,
            tempo_bpm=body.tempo_bpm,
            license=body.license,
            topics=body.topics,
            initialize=body.initialize,
            default_branch=body.default_branch,
            template_repo_id=body.template_repo_id,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A repo with this owner and name already exists",
        )
    return repo




@router.get(
    "/repos/{repo_id}",
    response_model=RepoResponse,
    operation_id="getRepo",
    summary="Get remote repo metadata",
    tags=["Repos"],
)
async def get_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> RepoResponse:
    """Return metadata for the given repo. Returns 404 if not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    return repo  # type: ignore[return-value]  # _guard_visibility raises if None


@router.get(
    "/repos/{repo_id}/branches",
    response_model=BranchListResponse,
    operation_id="listRepoBranches",
    summary="List all branches in a remote repo",
    tags=["Branches"],
)
async def list_branches(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> BranchListResponse:
    """Return all branch pointers for a repo, ordered by name."""
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    branches = await musehub_repository.list_branches(db, repo_id)
    return BranchListResponse(branches=branches)


@router.get(
    "/repos/{repo_id}/branches/detail",
    response_model=BranchDetailListResponse,
    operation_id="listRepoBranchesDetail",
    summary="List branches with ahead/behind counts and divergence scores",
    tags=["Branches"],
)
async def list_branches_detail(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> BranchDetailListResponse:
    """Return branches enriched with ahead/behind counts vs the default branch.

    Each branch includes:
    - ``aheadCount``: commits on this branch not yet on the default branch
    - ``behindCount``: commits on the default branch not yet merged here
    - ``isDefault``: whether this is the repo's default branch
    - ``divergence``: musical divergence scores (placeholder ``null`` until computable)

    Used by the MuseHub branch list page to help musicians decide which branches
    to merge or discard.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    return await musehub_repository.list_branches_with_detail(db, repo_id)


@router.get(
    "/repos/{repo_id}/commits",
    response_model=CommitListResponse,
    operation_id="listRepoCommits",
    summary="List commits in a remote repo (newest first)",
    tags=["Commits"],
)
async def list_commits(
    repo_id: str,
    branch: str | None = Query(None, description="Filter by branch name"),
    limit: int = Query(50, ge=1, le=200, description="Max commits to return"),
    page: int = Query(1, ge=1, description="Page number (1-indexed, used with per_page)"),
    per_page: int = Query(0, ge=0, le=200, description="Page size (0 = use limit param instead)"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> CommitListResponse:
    """Return commits for a repo, newest first.

    Supports two pagination modes:
    - Legacy: ``limit`` controls max rows returned, no offset.
    - Page-based: ``per_page`` > 0 enables page/per_page navigation; ``limit`` is ignored.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    effective_limit = per_page if per_page > 0 else limit
    offset = (page - 1) * effective_limit if per_page > 0 else 0
    commits, total = await musehub_repository.list_commits(
        db, repo_id, branch=branch, limit=effective_limit, offset=offset
    )
    return CommitListResponse(commits=commits, total=total)


@router.get(
    "/repos/{repo_id}/commits/{commit_id}",
    response_model=CommitResponse,
    operation_id="getRepoCommit",
    summary="Get a single commit by ID",
    tags=["Commits"],
)
async def get_commit(
    repo_id: str,
    commit_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> CommitResponse:
    """Return a single commit by its ID.

    Returns 404 if the commit does not exist in this repo.
    Raises 401 if the repo is private and the caller is unauthenticated.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    commits, _ = await musehub_repository.list_commits(db, repo_id, limit=500)
    commit = next((c for c in commits if c.commit_id == commit_id), None)
    if commit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commit '{commit_id}' not found in repo '{repo_id}'",
        )
    return commit


@router.get(
    "/repos/{repo_id}/commits/{commit_id}/diff-summary",
    response_model=CommitDiffSummaryResponse,
    operation_id="getCommitDiffSummary",
    summary="Multi-dimensional diff summary between a commit and its parent",
    tags=["Commits"],
)
async def get_commit_diff_summary(
    repo_id: str,
    commit_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> CommitDiffSummaryResponse:
    """Return a five-dimension musical diff summary between a commit and its parent.

    Computes heuristic per-dimension change scores (harmonic, rhythmic, melodic,
    structural, dynamic) from the commit message keywords and metadata.  Scores
    are in [0.0, 1.0] where 0 = no change and 1 = complete replacement.

    Consumed by the commit detail page to render coloured dimension-change badges
    that help musicians understand *what* musically changed in this push.

    Returns:
        CommitDiffSummaryResponse with per-dimension scores and overall mean.

    Raises:
        404: If the commit is not found in this repo.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    commits, _ = await musehub_repository.list_commits(db, repo_id, limit=500)
    commit = next((c for c in commits if c.commit_id == commit_id), None)
    if commit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commit '{commit_id}' not found in repo '{repo_id}'",
        )
    parent_id = commit.parent_ids[0] if commit.parent_ids else None
    parent = next((c for c in commits if c.commit_id == parent_id), None) if parent_id else None

    dimensions = _compute_commit_diff_dimensions(commit, parent)
    overall = sum(d.score for d in dimensions) / len(dimensions) if dimensions else 0.0
    return CommitDiffSummaryResponse(
        commit_id=commit_id,
        parent_id=parent_id,
        dimensions=dimensions,
        overall_score=round(overall, 4),
    )


def _dim_label_color(score: float) -> tuple[str, str]:
    """Map a [0,1] score to a (label, CSS-class) pair for badge rendering."""
    if score < 0.15:
        return "none", "dim-none"
    if score < 0.40:
        return "low", "dim-low"
    if score < 0.70:
        return "medium", "dim-medium"
    return "high", "dim-high"


_HARMONIC_KEYWORDS = frozenset(
    ["key", "chord", "harmony", "harmonic", "tonal", "modulation", "progression", "pitch"]
)
_RHYTHMIC_KEYWORDS = frozenset(
    ["bpm", "tempo", "beat", "rhythm", "rhythmic", "groove", "swing", "meter", "time"]
)
_MELODIC_KEYWORDS = frozenset(
    ["melody", "melodic", "lead", "motif", "phrase", "contour", "scale", "mode"]
)
_STRUCTURAL_KEYWORDS = frozenset(
    [
        "section",
        "structural",
        "intro",
        "verse",
        "chorus",
        "bridge",
        "outro",
        "form",
        "arrangement",
        "structure",
    ]
)
_DYNAMIC_KEYWORDS = frozenset(
    [
        "dynamic",
        "volume",
        "velocity",
        "loud",
        "soft",
        "crescendo",
        "decrescendo",
        "fade",
        "mute",
        "swell",
    ]
)


def _keyword_score(message: str, keywords: frozenset[str]) -> float:
    """Return a [0, 1] score based on keyword density in a commit message.

    Presence of any keyword gives a base 0.35 score; each additional keyword
    adds 0.15 up to a ceiling of 0.95.  Root commits (empty parent) implicitly
    score 1.0 on all dimensions since everything is new.
    """
    msg_lower = message.lower()
    hits = sum(1 for kw in keywords if kw in msg_lower)
    if hits == 0:
        return 0.0
    return min(0.35 + (hits - 1) * 0.15, 0.95)


def _compute_commit_diff_dimensions(
    commit: CommitResponse,
    parent: CommitResponse | None,
) -> list[CommitDiffDimensionScore]:
    """Derive five-dimension diff scores from commit message keyword analysis.

    When ``parent`` is None the commit is a root commit — all dimensions score
    1.0 because every musical element is being introduced for the first time.
    """
    DIMS: list[tuple[str, frozenset[str]]] = [
        ("harmonic", _HARMONIC_KEYWORDS),
        ("rhythmic", _RHYTHMIC_KEYWORDS),
        ("melodic", _MELODIC_KEYWORDS),
        ("structural", _STRUCTURAL_KEYWORDS),
        ("dynamic", _DYNAMIC_KEYWORDS),
    ]

    results: list[CommitDiffDimensionScore] = []
    for dim_name, keywords in DIMS:
        if parent is None:
            raw = 1.0
        else:
            raw = _keyword_score(commit.message, keywords)
        label, color = _dim_label_color(raw)
        results.append(
            CommitDiffDimensionScore(
                dimension=dim_name,
                score=round(raw, 4),
                label=label,
                color=color,
            )
        )
    return results




@router.get(
    "/repos/{repo_id}/commits/{commit_id}/render-status",
    response_model=RenderStatusResponse,
    operation_id="getCommitRenderStatus",
    summary="Query render job status for auto-generated MP3 and piano-roll artifacts",
    tags=["Commits"],
)
async def get_commit_render_status(
    repo_id: str,
    commit_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> RenderStatusResponse:
    """Return the render job status for a commit's auto-generated artifacts.

    Called by the MuseHub UI and AI agents to poll whether the background
    render pipeline has finished generating MP3 and piano-roll images for a
    given commit.

    Status lifecycle: ``pending`` → ``rendering`` → ``complete`` | ``failed``.

    When no render job exists for the commit (e.g. the push contained no MIDI
    objects, or the job has not been created yet), the response returns
    ``status="not_found"`` with empty artifact lists rather than a 404.

    Args:
        repo_id: Internal repo UUID.
        commit_id: Commit SHA to query.

    Returns:
        ``RenderStatusResponse`` with current job status and artifact IDs.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)

    stmt = select(db_models.MusehubRenderJob).where(
        db_models.MusehubRenderJob.repo_id == repo_id,
        db_models.MusehubRenderJob.commit_id == commit_id,
    )
    job = (await db.execute(stmt)).scalar_one_or_none()

    if job is None:
        return RenderStatusResponse(
            commit_id=commit_id,
            status="not_found",
        )

    return RenderStatusResponse(
        commit_id=job.commit_id,
        status=job.status,
        midi_count=job.midi_count,
        mp3_object_ids=list(job.mp3_object_ids or []),
        image_object_ids=list(job.image_object_ids or []),
        error_message=job.error_message,
    )


@router.get(
    "/repos/{repo_id}/timeline",
    response_model=TimelineResponse,
    operation_id="getRepoTimeline",
    summary="Chronological timeline of musical evolution",
    tags=["Commits"],
)
async def get_timeline(
    repo_id: str,
    limit: int = Query(200, ge=1, le=500, description="Max commits to include in the timeline"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> TimelineResponse:
    """Return a chronological timeline of musical evolution for a repo.

    The response contains four parallel event streams, each independently
    toggleable by the client:
    - ``commits``: every pushed commit as a timeline marker (oldest-first)
    - ``emotion``: deterministic emotion vectors (valence/energy/tension) per commit
    - ``sections``: section-change events parsed from commit messages
    - ``tracks``: track add/remove events parsed from commit messages

    Content negotiation: the UI page at ``GET /musehub/ui/{repo_id}/timeline``
    fetches this endpoint for its layered visualisation. AI agents call this
    endpoint directly to understand the creative arc of a project.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    return await musehub_repository.get_timeline_events(db, repo_id, limit=limit)

@router.get(
    "/repos/{repo_id}/divergence",
    response_model=DivergenceResponse,
    operation_id="getRepoDivergence",
    summary="Compute musical divergence between two branches",
    tags=["Branches"],
)
async def get_divergence(
    repo_id: str,
    branch_a: str = Query(..., description="First branch name"),
    branch_b: str = Query(..., description="Second branch name"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> DivergenceResponse:
    """Return a five-dimension musical divergence report between two branches.

    Computes a per-dimension Jaccard divergence score by comparing each
    branch's commit history since their common ancestor.  Dimensions are:
    melodic, harmonic, rhythmic, structural, and dynamic.

    The ``overallScore`` field is the mean of all five dimension scores,
    expressed in [0.0, 1.0].  Multiply by 100 for a percentage display.

    Content negotiation: this endpoint always returns JSON.  The UI page at
    ``GET /musehub/ui/{repo_id}/divergence`` renders the radar chart.

    Returns:
        DivergenceResponse with per-dimension scores and overall score.

    Raises:
        404: If the repo is not found.
        422: If either branch has no commits in this repo.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    try:
        result = await musehub_divergence.compute_hub_divergence(
            db,
            repo_id=repo_id,
            branch_a=branch_a,
            branch_b=branch_b,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    dimensions = [
        DivergenceDimensionResponse(
            dimension=d.dimension,
            level=d.level.value,
            score=d.score,
            description=d.description,
            branch_a_commits=d.branch_a_commits,
            branch_b_commits=d.branch_b_commits,
        )
        for d in result.dimensions
    ]

    return DivergenceResponse(
        repo_id=repo_id,
        branch_a=branch_a,
        branch_b=branch_b,
        common_ancestor=result.common_ancestor,
        dimensions=dimensions,
        overall_score=result.overall_score,
    )


@router.get(
    "/repos/{repo_id}/credits",
    response_model=CreditsResponse,
    operation_id="getRepoCredits",
    summary="Get aggregated contributor credits for a repo",
    tags=["Repos"],
)
async def get_credits(
    repo_id: str,
    sort: str = Query(
        "count",
        pattern="^(count|recency|alpha)$",
        description="Sort order: 'count' (most prolific), 'recency' (most recent), 'alpha' (A–Z)",
    ),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> CreditsResponse:
    """Return dynamic contributor credits aggregated from all commits in a repo.

    Analogous to album liner notes: every contributor is listed with their
    session count, inferred contribution types (composer, arranger, producer,
    etc.), and activity window (first and last commit timestamps).

    Content negotiation: when the request ``Accept`` header includes
    ``application/ld+json``, clients should request the ``/credits`` endpoint
    directly — the JSON body is schema.org-compatible and can be wrapped in
    JSON-LD by the consumer.  This endpoint always returns ``application/json``.

    Returns 404 if the repo does not exist.
    Returns an empty ``contributors`` list when no commits have been pushed yet.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    return await musehub_credits.aggregate_credits(db, repo_id, sort=sort)


@router.get(
    "/repos/{repo_id}/context/{ref}",
    response_model=MuseHubContextResponse,
    operation_id="getRepoContextByRef",
    summary="Get musical context document for a commit",
    tags=["Commits"],
)
async def get_context(
    repo_id: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> MuseHubContextResponse:
    """Return a structured musical context document for the given commit ref.

    The context document is the same information the AI agent receives when
    generating music for this repo at this commit — making it human-inspectable
    for debugging and transparency.

    Raises 404 if either the repo or the commit does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    context = await musehub_repository.get_context_for_commit(db, repo_id, ref)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commit {ref!r} not found in repo",
        )
    return context


@router.get(
    "/repos/{repo_id}/context",
    operation_id="getAgentContext",
    summary="Get complete agent context for a repo ref",
    tags=["Repos"],
    responses={
        200: {"description": "Agent context document (JSON or YAML)"},
        404: {"description": "Repo not found or ref has no commits"},
    },
)
async def get_agent_context(
    repo_id: str,
    ref: str = Query(
        "HEAD",
        description="Branch name or commit ID to build context for. 'HEAD' resolves to the latest commit.",
    ),
    depth: ContextDepth = Query(
        ContextDepth.standard,
        description="Depth level: 'brief' (~2K tokens), 'standard' (~8K tokens), 'verbose' (uncapped)",
    ),
    format: ContextFormat = Query(
        ContextFormat.json,
        description="Response format: 'json' or 'yaml'",
    ),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> Response:
    """Return a complete musical context briefing for AI agent consumption.

    This endpoint is the canonical entry point for agents starting a composition
    session.  It aggregates musical state, commit history, per-dimension analysis,
    open PRs, open issues, and actionable suggestions into a single document.

    Use ``?depth=brief`` to fit the response in a small context window (~2 K tokens).
    Use ``?depth=verbose`` for full bodies and extended history.
    Use ``?format=yaml`` for human-readable output (e.g. in agent logs).
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    context = await musehub_context.build_agent_context(
        db,
        repo_id=repo_id,
        ref=ref,
        depth=depth,
    )
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found or ref has no commits",
        )

    if format == ContextFormat.yaml:
        payload = context.model_dump(by_alias=True)
        yaml_text: str = yaml.dump(payload, allow_unicode=True, sort_keys=False)
        return Response(content=yaml_text, media_type="application/x-yaml")

    return Response(
        content=context.model_dump_json(by_alias=True),
        media_type="application/json",
    )


@router.get(
    "/repos/{repo_id}/dag",
    response_model=DagGraphResponse,
    operation_id="getCommitDag",
    summary="Get the full commit DAG for a repo",
    tags=["Commits"],
)
async def get_commit_dag(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> DagGraphResponse:
    """Return the full commit history as a topologically sorted directed acyclic graph.

    Nodes are ordered oldest→newest (Kahn's topological sort). Edges express
    child→parent relationships (``source`` = child commit, ``target`` = parent
    commit). This endpoint is the data source for the interactive DAG graph UI
    at ``GET /musehub/ui/{repo_id}/graph``.

    Content negotiation: always returns JSON. The UI page fetches this endpoint
    with the stored JWT and renders it client-side with an SVG-based renderer.

    Performance: all commits are fetched (no limit) to ensure the graph is
    complete. For repos with 100+ commits the response may be several KB; the
    client-side renderer virtualises visible nodes.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    return await musehub_repository.list_commits_dag(db, repo_id)


@router.get(
    "/repos/{repo_id}/form-structure/{ref}",
    response_model=FormStructureResponse,
    summary="Get form and structure analysis for a commit ref",
)
async def get_form_structure(
    repo_id: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> FormStructureResponse:
    """Return combined form and structure analysis for the given commit ref.

    Combines three complementary structural views of the piece's formal
    architecture in a single response, optimised for the MuseHub
    form-structure UI page:

    - ``sectionMap``: timeline of sections with bar ranges and colour hints
    - ``repetitionStructure``: which sections repeat and how often
    - ``sectionComparison``: pairwise similarity heatmap for all sections

    Agents use this as the structural context document before generating
    a new section — it answers "where am I in the form?" and "what sounds
    like what?" without requiring multiple analysis requests.

    Returns 404 if the repo does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    return musehub_analysis.compute_form_structure(repo_id=repo_id, ref=ref)


@router.post(
    "/repos/{repo_id}/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSession",
    summary="Create a recording session entry",
    tags=["Sessions"],
)
async def create_session(
    repo_id: str,
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> SessionResponse:
    """Register a new recording session on the Hub.

    Called by the CLI on ``muse session start``. Returns the persisted session
    including its server-assigned ``session_id``.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    session_resp = await musehub_repository.create_session(
        db,
        repo_id,
        started_at=body.started_at,
        participants=body.participants,
        intent=body.intent,
        location=body.location,
    )
    await db.commit()
    return session_resp


@router.get(
    "/repos/{repo_id}/sessions",
    response_model=SessionListResponse,
    operation_id="listSessions",
    summary="List recording sessions for a repo (newest first)",
    tags=["Sessions"],
)
async def list_sessions(
    repo_id: str,
    limit: int = Query(50, ge=1, le=200, description="Max sessions to return"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> SessionListResponse:
    """Return sessions for a repo, sorted newest-first by started_at.

    Returns 404 if the repo does not exist.  Use ``limit`` to paginate large
    session histories (default 50, max 200).
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    sessions, total = await musehub_repository.list_sessions(db, repo_id, limit=limit)
    return SessionListResponse(sessions=sessions, total=total)


@router.get(
    "/repos/{repo_id}/sessions/{session_id}",
    response_model=SessionResponse,
    operation_id="getSession",
    summary="Get a single recording session by ID",
    tags=["Sessions"],
)
async def get_session(
    repo_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> SessionResponse:
    """Return a single session record.

    Returns 404 if the repo or session does not exist.  The ``session_id``
    must be an exact match — the hub does not support prefix lookups.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    session = await musehub_repository.get_session(db, repo_id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.post(
    "/repos/{repo_id}/sessions/{session_id}/stop",
    response_model=SessionResponse,
    operation_id="stopSession",
    summary="Mark a recording session as ended",
    tags=["Sessions"],
)
async def stop_session(
    repo_id: str,
    session_id: str,
    body: SessionStop,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> SessionResponse:
    """Close an active session and record its end time.

    Called by the CLI on ``muse session stop``. Idempotent — calling stop on
    an already-stopped session updates ``ended_at`` and returns the session.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    sess = await musehub_repository.stop_session(db, repo_id, session_id, body.ended_at)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await db.commit()
    return sess


@router.get(
    "/repos/{repo_id}/stats",
    response_model=RepoStatsResponse,
    summary="Aggregated counts for the repo home page stats bar",
)
async def get_repo_stats(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> RepoStatsResponse:
    """Return aggregated statistics for a repo: commit count, branch count, release count.

    This lightweight endpoint powers the stats bar on the repo home page and
    the JSON content-negotiation response from ``GET /musehub/ui/{owner}/{slug}``.
    All counts are 0 when the repo has no data yet.

    Returns 404 if the repo does not exist.
    Returns 401 if the repo is private and the caller is unauthenticated.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)

    branches = await musehub_repository.list_branches(db, repo_id)
    _, commit_total = await musehub_repository.list_commits(db, repo_id, limit=1)
    releases = await musehub_releases.list_releases(db, repo_id)

    return RepoStatsResponse(
        commit_count=commit_total,
        branch_count=len(branches),
        release_count=len(releases),
    )


@router.get(
    "/repos/{repo_id}/groove-check",
    response_model=GrooveCheckResponse,
    summary="Get rhythmic consistency analysis for a repo commit window",
)
async def get_groove_check(
    repo_id: str,
    threshold: float = Query(
        DEFAULT_THRESHOLD,
        ge=0.01,
        le=1.0,
        description="Drift threshold in beats — commits exceeding this are flagged WARN or FAIL",
    ),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of commits to analyse"),
    track: str | None = Query(None, description="Restrict analysis to a named instrument track"),
    section: str | None = Query(None, description="Restrict analysis to a named musical section"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> GrooveCheckResponse:
    """Return rhythmic consistency metrics for the most recent commits in a repo.

    Analyses note-onset deviation from the quantization grid across stored MIDI
    snapshots and classifies each commit as OK / WARN / FAIL based on how much
    the groove tightness changed relative to its predecessor.

    The ``threshold`` parameter controls sensitivity: lower values flag even
    small rhythmic shifts; the default of 0.1 beats works well for most projects.

    Returns 404 if the repo does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    commit_range = f"HEAD~{limit}..HEAD"
    result = compute_groove_check(
        commit_range=commit_range,
        threshold=threshold,
        track=track,
        section=section,
        limit=limit,
    )

    entries = [
        GrooveCommitEntry(
            commit=e.commit,
            groove_score=e.groove_score,
            drift_delta=e.drift_delta,
            status=e.status.value,
            track=e.track,
            section=e.section,
            midi_files=e.midi_files,
        )
        for e in result.entries
    ]

    return GrooveCheckResponse(
        commit_range=result.commit_range,
        threshold=result.threshold,
        total_commits=result.total_commits,
        flagged_commits=result.flagged_commits,
        worst_commit=result.worst_commit,
        entries=entries,
    )


@router.get(
    "/repos/{repo_id}/listen/{ref}/tracks",
    response_model=TrackListingResponse,
    operation_id="listListenTracks",
    summary="List audio tracks and full-mix URL for the listen page",
    tags=["Listen"],
)
async def list_listen_tracks(
    repo_id: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> TrackListingResponse:
    """Return the full-mix and per-stem track listing for the listen page.

    Thin wrapper around ``musehub_listen.build_track_listing``.  Auth/visibility
    is enforced here before delegating scanning logic to the service.

    Returns 404 if the repo does not exist or is not accessible.
    The ``has_renders`` flag distinguishes repos with no audio from repos that
    have audio but no recognised full-mix file.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)

    return await musehub_listen.build_track_listing(db, repo_id, ref)


def _derive_emotion_vector(commit_id: str) -> tuple[float, float, float, float]:
    """Derive a deterministic (valence, energy, tension, darkness) vector from a commit SHA.

    Mirrors the algorithm in musehub_repository._derive_emotion so that the
    compare endpoint produces values consistent with the timeline page.  Four
    non-overlapping 4-hex-char windows of the SHA are mapped to [0.0, 1.0].
    """
    sha = commit_id.ljust(16, "0")
    hex_chars = set("0123456789abcdefABCDEF")

    def _window(start: int) -> float:
        chunk = sha[start : start + 4]
        if all(c in hex_chars for c in chunk):
            return int(chunk, 16) / 0xFFFF
        return 0.5

    return _window(0), _window(4), _window(8), _window(12)


def _compute_emotion_diff(
    base_commits: list[CommitResponse],
    head_only_commits: list[CommitResponse],
) -> EmotionDiffResponse:
    """Compute the emotional delta between the base ref and the head-only commits.

    Each commit's emotion vector is derived deterministically from its SHA.
    The delta is ``mean(head) − mean(base)`` per axis, clamped to [−1.0, 1.0].

    When either side has no commits, that side's vector defaults to 0.5 for all axes.
    """

    def _mean_vector(commits: list[CommitResponse]) -> tuple[float, float, float, float]:
        if not commits:
            return 0.5, 0.5, 0.5, 0.5
        vecs = [_derive_emotion_vector(c.commit_id) for c in commits]
        n = len(vecs)
        return (
            sum(v[0] for v in vecs) / n,
            sum(v[1] for v in vecs) / n,
            sum(v[2] for v in vecs) / n,
            sum(v[3] for v in vecs) / n,
        )

    bv, be, bt, bd = _mean_vector(base_commits)
    hv, he, ht, hd = _mean_vector(head_only_commits)
    return EmotionDiffResponse(
        valence_delta=round(hv - bv, 4),
        energy_delta=round(he - be, 4),
        tension_delta=round(ht - bt, 4),
        darkness_delta=round(hd - bd, 4),
        base_valence=round(bv, 4),
        base_energy=round(be, 4),
        base_tension=round(bt, 4),
        base_darkness=round(bd, 4),
        head_valence=round(hv, 4),
        head_energy=round(he, 4),
        head_tension=round(ht, 4),
        head_darkness=round(hd, 4),
    )


@router.get(
    "/repos/{repo_id}/compare",
    response_model=CompareResponse,
    operation_id="compareRefs",
    summary="Compare two refs — multi-dimensional musical diff",
    tags=["Commits"],
)
async def compare_refs(
    repo_id: str,
    base: str = Query(..., description="Base ref (branch name or commit SHA)"),
    head: str = Query(..., description="Head ref (branch name or commit SHA)"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> CompareResponse:
    """Return a multi-dimensional musical comparison between two refs.

    Computes five per-dimension divergence scores (melodic, harmonic, rhythmic,
    structural, dynamic), lists commits unique to the head ref, and summarises
    the emotional delta between the two refs.

    ``base`` and ``head`` are resolved as branch names first.  If no commits
    are found on a branch with that exact name, the ref is treated as a commit
    SHA prefix and all commits for the repo are scanned.

    Returns:
        CompareResponse containing divergence dimensions, commit list, and
        emotion diff.

    Raises:
        404: Repo not found.
        422: Base or head ref resolves to zero commits.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)

    # ── Divergence (reuse existing engine; works on branch names) ────────────
    try:
        div_result = await musehub_divergence.compute_hub_divergence(
            db,
            repo_id=repo_id,
            branch_a=base,
            branch_b=head,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        )

    dimensions = [
        DivergenceDimensionResponse(
            dimension=d.dimension,
            level=d.level.value,
            score=d.score,
            description=d.description,
            branch_a_commits=d.branch_a_commits,
            branch_b_commits=d.branch_b_commits,
        )
        for d in div_result.dimensions
    ]

    # ── Commits unique to head ────────────────────────────────────────────────
    all_base_commits, _ = await musehub_repository.list_commits(db, repo_id, branch=base, limit=500)
    all_head_commits, _ = await musehub_repository.list_commits(db, repo_id, branch=head, limit=500)
    base_ids = {c.commit_id for c in all_base_commits}
    head_only = [c for c in all_head_commits if c.commit_id not in base_ids]

    # ── Emotion diff ──────────────────────────────────────────────────────────
    emotion_diff = _compute_emotion_diff(all_base_commits, head_only)

    # ── PR creation URL ──────────────────────────────────────────────────────
    # repo is guaranteed non-None here — _guard_visibility raised 404 otherwise.
    assert repo is not None
    create_pr_url = (
        f"/musehub/ui/{repo.owner}/{repo.slug}/pulls/new"
        f"?base={base}&head={head}"
    )

    return CompareResponse(
        repo_id=repo_id,
        base_ref=base,
        head_ref=head,
        common_ancestor=div_result.common_ancestor,
        dimensions=dimensions,
        overall_score=div_result.overall_score,
        commits=head_only,
        emotion_diff=emotion_diff,
        create_pr_url=create_pr_url,
    )


@router.get(
    "/repos/{repo_id}/arrange/{ref}",
    response_model=ArrangementMatrixResponse,
    operation_id="getArrangementMatrix",
    summary="Get the instrument × section arrangement matrix for a Muse commit ref",
    tags=["Repos"],
)
async def get_arrangement_matrix(
    repo_id: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> ArrangementMatrixResponse:
    """Return the arrangement matrix for a Muse Hub commit ref.

    The matrix encodes note density for every (instrument, section) pair so
    the arrangement page can render a colour-coded grid.  Row and column
    summaries are pre-computed to avoid redundant aggregation in the client.

    Deterministic stub data is seeded by ``ref`` so agents receive consistent
    responses across retries.  Full MIDI content analysis will be wired in
    once Storpheus exposes per-section introspection.

    Returns 404 if the repo does not exist.
    Returns 401 if the repo is private and the caller is unauthenticated.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    result = musehub_analysis.compute_arrangement_matrix(repo_id=repo_id, ref=ref)
    return result


# ── Stargazers endpoint — complements discover.py star/unstar ─────────────────


@router.get(
    "/repos/{repo_id}/stargazers",
    response_model=StargazerListResponse,
    operation_id="listRepoStargazers",
    summary="List users who starred a repo",
    tags=["Stars"],
)
async def list_stargazers(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> StargazerListResponse:
    """Return the list of users who have starred this repo.

    Public repos return this list unauthenticated.  Private repos require a
    valid JWT.  Returns an empty list when no one has starred the repo yet.

    Raises 404 if the repo does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)

    stmt = (
        select(db_models.MusehubStar)
        .where(db_models.MusehubStar.repo_id == repo_id)
        .order_by(db_models.MusehubStar.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    stargazers = [
        StargazerEntry(user_id=row.user_id, starred_at=row.created_at) for row in rows
    ]
    return StargazerListResponse(stargazers=stargazers, total=len(stargazers))
# ── Activity feed ─────────────────────────────────────────────────────────────


@router.get(
    "/repos/{repo_id}/activity",
    response_model=ActivityFeedResponse,
    operation_id="getRepoActivityFeed",
    summary="Get paginated activity feed for a Muse Hub repo",
    tags=["Repos"],
)
async def get_repo_activity(
    repo_id: str,
    event_type: str | None = Query(None, description="Filter to a single event type"),
    page: int = Query(1, ge=1, description="1-indexed page number"),
    page_size: int = Query(30, ge=1, le=100, description="Events per page"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> ActivityFeedResponse:
    """Return the chronological (newest-first) activity feed for a repo.

    Events cover: commit pushes, PR lifecycle, issue lifecycle, branch and tag
    operations, and recording sessions.  Pass ``event_type`` to filter to a
    single category; omit it to see all events.

    Pagination is 1-indexed; ``page_size`` is capped at 100.
    Returns 404 if the repo does not exist.
    Returns 401 if the repo is private and the caller is unauthenticated.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    _guard_visibility(repo, claims)
    return await musehub_events.list_events(
        db,
        repo_id,
        event_type=event_type,
        page=page,
        page_size=page_size,
    )


# ── Repo settings (GET + PATCH) — declared before catch-all ──────────────────


async def _guard_admin(
    repo: RepoResponse | None, caller_user_id: str, db: AsyncSession
) -> None:
    """Raise 404 if repo is absent; raise 403 if caller lacks admin permission.

    Admin permission is granted when the caller is the repo owner OR when they
    have an accepted collaborator row with ``permission='admin'``.

    Args:
        repo: The repo metadata (None triggers 404).
        caller_user_id: JWT ``sub`` of the authenticated caller.
        db: Active async DB session for the collaborator lookup.
    """
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.owner_user_id == caller_user_id:
        return
    stmt = (
        select(collab_models.MusehubCollaborator)
        .where(
            collab_models.MusehubCollaborator.repo_id == repo.repo_id,
            collab_models.MusehubCollaborator.user_id == caller_user_id,
            collab_models.MusehubCollaborator.permission == "admin",
            collab_models.MusehubCollaborator.accepted_at.is_not(None),
        )
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin permission required to access repo settings",
        )


@router.get(
    "/repos/{repo_id}/settings",
    response_model=RepoSettingsResponse,
    operation_id="getRepoSettings",
    summary="Get mutable settings for a repo",
    tags=["Repos"],
)
async def get_repo_settings(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> RepoSettingsResponse:
    """Return the mutable settings for a repo.

    Only the repo owner or an admin collaborator may call this endpoint.
    Returns 403 when the caller lacks admin permission; 404 when the repo
    does not exist.

    Settings combine dedicated-column values (name, description, visibility,
    topics) with feature flags stored in the ``settings`` JSON blob
    (has_issues, allow_merge_commit, etc.).  Missing flags are back-filled
    with canonical defaults on first read so every response is fully
    populated regardless of when the repo was created.

    Agent use case: read before updating project metadata or configuring the
    PR merge strategy.
    """
    caller_user_id: str = claims.get("sub") or ""
    repo = await musehub_repository.get_repo(db, repo_id)
    await _guard_admin(repo, caller_user_id, db)
    settings = await musehub_repository.get_repo_settings(db, repo_id)
    if settings is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    return settings


@router.patch(
    "/repos/{repo_id}/settings",
    response_model=RepoSettingsResponse,
    operation_id="patchRepoSettings",
    summary="Update mutable settings for a repo",
    tags=["Repos"],
)
async def patch_repo_settings(
    repo_id: str,
    body: RepoSettingsPatch,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> RepoSettingsResponse:
    """Partially update mutable settings for a repo.

    Only the repo owner or an admin collaborator may call this endpoint.
    All request body fields are optional — only non-null values are written.

    ``visibility`` must be ``'public'`` or ``'private'`` when supplied.
    ``topics`` replaces the full tag list when provided.

    Returns 403 when the caller lacks admin permission; 404 when the repo
    does not exist.  On success, the full updated settings object is returned
    so callers do not need a follow-up GET.

    Agent use case: update visibility, merge strategy, or homepage URL
    atomically without touching other settings fields.
    """
    caller_user_id: str = claims.get("sub") or ""
    repo = await musehub_repository.get_repo(db, repo_id)
    await _guard_admin(repo, caller_user_id, db)
    updated = await musehub_repository.update_repo_settings(db, repo_id, body)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    await db.commit()
    return updated


# ── Owner/slug resolver — declared LAST to avoid shadowing /repos/... routes ──


@router.get(
    "/{owner}/{repo_slug}",
    response_model=RepoResponse,
    operation_id="getRepoByOwnerSlug",
    summary="Get repo metadata by owner/slug",
    tags=["Repos"],
)
async def get_repo_by_owner_slug(
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> RepoResponse:
    """Return metadata for the repo identified by its canonical /{owner}/{slug} path.

    Declared last so that all /repos/... fixed-prefix routes take precedence.
    Returns 404 for unknown owner/slug combinations.
    """
    repo = await musehub_repository.get_repo_by_owner_slug(db, owner, repo_slug)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    _guard_visibility(repo, claims)
    return repo
