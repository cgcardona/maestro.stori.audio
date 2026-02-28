"""Muse Hub repo, branch, commit, credits, and agent context route handlers.

Endpoint summary:
  POST /musehub/repos                                      — create a new remote repo
  GET  /musehub/repos/{repo_id}                           — get repo metadata
  GET  /musehub/repos/{repo_id}/branches                  — list all branches
  GET  /musehub/repos/{repo_id}/commits                   — list commits (newest first)
  GET  /musehub/repos/{repo_id}/credits                   — aggregated contributor credits
  GET  /musehub/repos/{repo_id}/context                   — agent context briefing
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

import yaml  # type: ignore[import-untyped]  # PyYAML ships no py.typed marker
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    BranchListResponse,
    CommitListResponse,
    CreateRepoRequest,
    DivergenceDimensionResponse,
    DivergenceResponse,
    DagGraphResponse,
    MuseHubContextResponse,
    RepoResponse,
    CreditsResponse,
    SessionCreate,
    SessionListResponse,
    SessionResponse,
)
from maestro.models.musehub_analysis import FormStructureResponse
from maestro.models.musehub_context import (
    ContextDepth,
    ContextFormat,
)
from maestro.services import musehub_analysis, musehub_context, musehub_credits, musehub_divergence, musehub_repository, musehub_sessions

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/repos",
    response_model=RepoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a remote Muse repo",
)
async def create_repo(
    body: CreateRepoRequest,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> RepoResponse:
    """Create a new remote Muse Hub repository owned by the authenticated user."""
    owner_user_id: str = claims.get("sub") or ""
    repo = await musehub_repository.create_repo(
        db,
        name=body.name,
        visibility=body.visibility,
        owner_user_id=owner_user_id,
        description=body.description,
        tags=body.tags,
        key_signature=body.key_signature,
        tempo_bpm=body.tempo_bpm,
    )
    await db.commit()
    return repo


@router.get(
    "/repos/{repo_id}",
    response_model=RepoResponse,
    summary="Get remote repo metadata",
)
async def get_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> RepoResponse:
    """Return metadata for the given repo. Returns 404 if not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    return repo


@router.get(
    "/repos/{repo_id}/branches",
    response_model=BranchListResponse,
    summary="List all branches in a remote repo",
)
async def list_branches(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> BranchListResponse:
    """Return all branch pointers for a repo, ordered by name."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    branches = await musehub_repository.list_branches(db, repo_id)
    return BranchListResponse(branches=branches)


@router.get(
    "/repos/{repo_id}/commits",
    response_model=CommitListResponse,
    summary="List commits in a remote repo (newest first)",
)
async def list_commits(
    repo_id: str,
    branch: str | None = Query(None, description="Filter by branch name"),
    limit: int = Query(50, ge=1, le=200, description="Max commits to return"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> CommitListResponse:
    """Return commits for a repo, newest first. Optionally filter by branch."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    commits, total = await musehub_repository.list_commits(
        db, repo_id, branch=branch, limit=limit
    )
    return CommitListResponse(commits=commits, total=total)


@router.get(
    "/repos/{repo_id}/divergence",
    response_model=DivergenceResponse,
    summary="Compute musical divergence between two branches",
)
async def get_divergence(
    repo_id: str,
    branch_a: str = Query(..., description="First branch name"),
    branch_b: str = Query(..., description="Second branch name"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
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
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

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
    summary="Get aggregated contributor credits for a repo",
)
async def get_credits(
    repo_id: str,
    sort: str = Query(
        "count",
        pattern="^(count|recency|alpha)$",
        description="Sort order: 'count' (most prolific), 'recency' (most recent), 'alpha' (A–Z)",
    ),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
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
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    return await musehub_credits.aggregate_credits(db, repo_id, sort=sort)


@router.get(
    "/repos/{repo_id}/context/{ref}",
    response_model=MuseHubContextResponse,
    summary="Get musical context document for a commit",
)
async def get_context(
    repo_id: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> MuseHubContextResponse:
    """Return a structured musical context document for the given commit ref.

    The context document is the same information the AI agent receives when
    generating music for this repo at this commit — making it human-inspectable
    for debugging and transparency.

    Raises 404 if either the repo or the commit does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    context = await musehub_repository.get_context_for_commit(db, repo_id, ref)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commit {ref!r} not found in repo",
        )
    return context


@router.get(
    "/repos/{repo_id}/context",
    summary="Get complete agent context for a repo ref",
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
    _: TokenClaims = Depends(require_valid_token),
) -> Response:
    """Return a complete musical context briefing for AI agent consumption.

    This endpoint is the canonical entry point for agents starting a composition
    session.  It aggregates musical state, commit history, per-dimension analysis,
    open PRs, open issues, and actionable suggestions into a single document.

    Use ``?depth=brief`` to fit the response in a small context window (~2 K tokens).
    Use ``?depth=verbose`` for full bodies and extended history.
    Use ``?format=yaml`` for human-readable output (e.g. in agent logs).
    """
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
    summary="Get the full commit DAG for a repo",
)
async def get_commit_dag(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
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
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

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
    summary="Push a recording session record to the hub",
)
async def push_session(
    repo_id: str,
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> SessionResponse:
    """Upsert a session record for the given repo.

    Accepts a ``MuseSessionRecord`` JSON payload pushed by ``muse session end``.
    If a session with the same ``session_id`` already exists, it is updated —
    re-push is idempotent.  Returns 404 if the repo does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    session = await musehub_sessions.upsert_session(db, repo_id, body)
    await db.commit()
    logger.info("✅ Session %s pushed to repo %s", body.session_id, repo_id)
    return session


@router.get(
    "/repos/{repo_id}/sessions",
    response_model=SessionListResponse,
    summary="List recording sessions for a repo (newest first)",
)
async def list_sessions(
    repo_id: str,
    limit: int = Query(50, ge=1, le=200, description="Max sessions to return"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> SessionListResponse:
    """Return sessions for a repo, sorted newest-first by started_at.

    Returns 404 if the repo does not exist.  Use ``limit`` to paginate large
    session histories (default 50, max 200).
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    sessions, total = await musehub_sessions.list_sessions(db, repo_id, limit=limit)
    return SessionListResponse(sessions=sessions, total=total)


@router.get(
    "/repos/{repo_id}/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get a single recording session by ID",
)
async def get_session(
    repo_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> SessionResponse:
    """Return a single session record.

    Returns 404 if the repo or session does not exist.  The ``session_id``
    must be an exact match — the hub does not support prefix lookups.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    session = await musehub_sessions.get_session(db, repo_id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session