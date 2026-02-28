"""Muse Hub repo, branch, commit, and credits route handlers.

Endpoint summary:
  POST /musehub/repos                        — create a new remote repo
  GET  /musehub/repos/{repo_id}              — get repo metadata
  GET  /musehub/repos/{repo_id}/branches     — list all branches
  GET  /musehub/repos/{repo_id}/commits      — list commits (newest first)
  GET  /musehub/repos/{repo_id}/credits      — aggregated contributor credits

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_repository and maestro.services.musehub_credits.
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
    CreditsResponse,
    RepoResponse,
)
from maestro.services import musehub_credits, musehub_repository

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
