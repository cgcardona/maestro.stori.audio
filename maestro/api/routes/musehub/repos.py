"""Muse Hub repo, branch, commit, and session route handlers.

Endpoint summary:
  POST /musehub/repos                                    — create a new remote repo
  GET  /musehub/repos/{repo_id}                          — get repo metadata
  GET  /musehub/repos/{repo_id}/branches                 — list all branches
  GET  /musehub/repos/{repo_id}/commits                  — list commits (newest first)
  POST /musehub/repos/{repo_id}/sessions                 — create a session entry
  GET  /musehub/repos/{repo_id}/sessions                 — list sessions (newest first)
  GET  /musehub/repos/{repo_id}/sessions/{session_id}    — get a single session
  POST /musehub/repos/{repo_id}/sessions/{session_id}/stop — mark session as ended

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_repository.
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
    SessionStop,
)
from maestro.services import musehub_repository

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
    summary="Create a recording session entry",
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
        is_active=body.is_active,
    )
    await db.commit()
    return session_resp


@router.get(
    "/repos/{repo_id}/sessions",
    response_model=SessionListResponse,
    summary="List recording sessions (newest first)",
)
async def list_sessions(
    repo_id: str,
    limit: int = Query(50, ge=1, le=200, description="Max sessions to return"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> SessionListResponse:
    """Return sessions for a repo, active ones first then newest by start time.

    JSON content-negotiation: this endpoint returns structured session data for
    agent consumers, the Hub UI, and the CLI ``muse session log`` display.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    sessions, total = await musehub_repository.list_sessions(db, repo_id, limit=limit)
    return SessionListResponse(sessions=sessions, total=total)


@router.get(
    "/repos/{repo_id}/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get a single recording session",
)
async def get_session(
    repo_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> SessionResponse:
    """Return a single session by ID. Returns 404 if not found in this repo."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    sess = await musehub_repository.get_session(db, repo_id, session_id)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return sess


@router.post(
    "/repos/{repo_id}/sessions/{session_id}/stop",
    response_model=SessionResponse,
    summary="Mark a recording session as ended",
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
