"""Muse Hub repo, branch, commit, and session route handlers.

Endpoint summary:
  POST /musehub/repos                                   — create a new remote repo
  GET  /musehub/repos/{repo_id}                         — get repo metadata
  GET  /musehub/repos/{repo_id}/branches                — list all branches
  GET  /musehub/repos/{repo_id}/commits                 — list commits (newest first)
  POST /musehub/repos/{repo_id}/sessions                — push a session record
  GET  /musehub/repos/{repo_id}/sessions                — list sessions (newest first)
  GET  /musehub/repos/{repo_id}/sessions/{session_id}  — get a single session

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_repository and maestro.services.musehub_sessions.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    BranchListResponse,
    CommitListResponse,
    CreateRepoRequest,
    RepoResponse,
    SessionCreate,
    SessionListResponse,
    SessionResponse,
)
from maestro.services import musehub_repository, musehub_sessions

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
