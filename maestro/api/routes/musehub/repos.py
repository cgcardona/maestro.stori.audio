"""Muse Hub repo, branch, and commit route handlers.

Endpoint summary:
  POST /musehub/repos                        — create a new remote repo
  GET  /musehub/repos/{repo_id}              — get repo metadata
  GET  /musehub/repos/{repo_id}/branches     — list all branches
  GET  /musehub/repos/{repo_id}/commits      — list commits (newest first)

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
    DivergenceDimensionResponse,
    DivergenceResponse,
    RepoResponse,
)
from maestro.services import musehub_divergence, musehub_repository

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
