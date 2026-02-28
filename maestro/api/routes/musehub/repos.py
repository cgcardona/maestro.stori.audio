"""Muse Hub repo, branch, commit, session, and agent context route handlers.

Endpoint summary:
  POST /musehub/repos                                   — create a new remote repo
  GET  /musehub/repos/{repo_id}                         — get repo metadata
  GET  /musehub/repos/{repo_id}/branches                — list all branches
  GET  /musehub/repos/{repo_id}/commits                 — list commits (newest first)
  POST /musehub/repos/{repo_id}/sessions                — push a session record
  GET  /musehub/repos/{repo_id}/sessions                — list sessions (newest first)
  GET  /musehub/repos/{repo_id}/sessions/{session_id}  — get a single session
  GET  /musehub/repos/{repo_id}/context                 — agent context briefing

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_repository, maestro.services.musehub_sessions, and
maestro.services.musehub_context.
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
    MuseHubContextResponse,
    RepoResponse,
    SessionCreate,
    SessionListResponse,
    SessionResponse,
)
from maestro.models.musehub_context import (
    AgentContextResponse,
    ContextDepth,
    ContextFormat,
)
from maestro.services import musehub_context, musehub_repository, musehub_sessions

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
