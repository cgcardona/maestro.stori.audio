"""Muse Hub repo, branch, commit, credits, and agent context route handlers.

Endpoint summary:
  POST /musehub/repos                                      — create a new remote repo
  GET  /musehub/repos/{repo_id}                           — get repo metadata (by internal UUID)
  GET  /musehub/{owner}/{repo_slug}                       — get repo metadata (by owner/slug)
  GET  /musehub/repos/{repo_id}/branches                  — list all branches
  GET  /musehub/repos/{repo_id}/commits                   — list commits (newest first)
  GET  /musehub/repos/{repo_id}/credits                   — aggregated contributor credits
  GET  /musehub/repos/{repo_id}/context                   — agent context briefing
  GET  /musehub/repos/{repo_id}/timeline                  — chronological timeline with emotion/section/track layers
  GET  /musehub/repos/{repo_id}/form-structure/{ref}      — form and structure analysis
  POST /musehub/repos/{repo_id}/sessions                  — push a recording session
  GET  /musehub/repos/{repo_id}/sessions                  — list recording sessions
  GET  /musehub/repos/{repo_id}/sessions/{session_id}     — get a single session

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_repository, maestro.services.musehub_credits,
and maestro.services.musehub_context.
"""
from __future__ import annotations

import logging

import yaml  # PyYAML ships no py.typed marker
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    BranchListResponse,
    CommitListResponse,
    CommitResponse,
    CreateRepoRequest,
    DivergenceDimensionResponse,
    DivergenceResponse,
    TimelineResponse,
    DagGraphResponse,
    GrooveCheckResponse,
    GrooveCommitEntry,
    MuseHubContextResponse,
    RepoResponse,
    RepoStatsResponse,
    CreditsResponse,
    SessionCreate,
    SessionListResponse,
    SessionResponse,
    SessionStop,
)
from maestro.models.musehub_analysis import FormStructureResponse
from maestro.models.musehub_context import (
    ContextDepth,
    ContextFormat,
)
from maestro.services import musehub_analysis, musehub_context, musehub_credits, musehub_divergence, musehub_releases, musehub_repository, musehub_sessions
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
