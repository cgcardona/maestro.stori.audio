"""Muse Hub pull request route handlers.

Endpoint summary:
  POST /musehub/repos/{repo_id}/pull-requests                        — open a PR
  GET  /musehub/repos/{repo_id}/pull-requests                        — list PRs
  GET  /musehub/repos/{repo_id}/pull-requests/{pr_id}                — get a PR
  POST /musehub/repos/{repo_id}/pull-requests/{pr_id}/merge          — merge a PR

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_pull_requests.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    PRCreate,
    PRListResponse,
    PRMergeRequest,
    PRMergeResponse,
    PRResponse,
)
from maestro.services import musehub_pull_requests, musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/repos/{repo_id}/pull-requests",
    response_model=PRResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a pull request against a Muse Hub repo",
)
async def create_pull_request(
    repo_id: str,
    body: PRCreate,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> PRResponse:
    """Open a new pull request proposing to merge from_branch into to_branch.

    Returns 422 if from_branch == to_branch.
    Returns 404 if from_branch does not exist in the repo.
    """
    if body.from_branch == body.to_branch:
        raise HTTPException(
            status_code=422,
            detail="from_branch and to_branch must be different",
        )

    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    try:
        pr = await musehub_pull_requests.create_pr(
            db,
            repo_id=repo_id,
            title=body.title,
            from_branch=body.from_branch,
            to_branch=body.to_branch,
            body=body.body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    await db.commit()
    return pr


@router.get(
    "/repos/{repo_id}/pull-requests",
    response_model=PRListResponse,
    summary="List pull requests for a Muse Hub repo",
)
async def list_pull_requests(
    repo_id: str,
    state: str = Query(
        "all",
        pattern="^(open|merged|closed|all)$",
        description="Filter by state (open, merged, closed, all)",
    ),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> PRListResponse:
    """Return pull requests for a repo, ordered by creation time.

    Use ?state=open to filter to open PRs only. Defaults to all states.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    prs = await musehub_pull_requests.list_prs(db, repo_id, state=state)
    return PRListResponse(pull_requests=prs)


@router.get(
    "/repos/{repo_id}/pull-requests/{pr_id}",
    response_model=PRResponse,
    summary="Get a single pull request by ID",
)
async def get_pull_request(
    repo_id: str,
    pr_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> PRResponse:
    """Return a single PR. Returns 404 if the repo or PR is not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    pr = await musehub_pull_requests.get_pr(db, repo_id, pr_id)
    if pr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pull request not found")
    return pr


@router.post(
    "/repos/{repo_id}/pull-requests/{pr_id}/merge",
    response_model=PRMergeResponse,
    summary="Merge an open pull request",
)
async def merge_pull_request(
    repo_id: str,
    pr_id: str,
    body: PRMergeRequest,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> PRMergeResponse:
    """Merge an open PR using the requested strategy.

    Creates a merge commit on to_branch with parent_ids from both
    branch heads, advances the branch head pointer, and marks the PR as merged.

    Returns 404 if the PR or repo is not found.
    Returns 409 if the PR is already merged or closed.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    try:
        pr = await musehub_pull_requests.merge_pr(
            db,
            repo_id,
            pr_id,
            merge_strategy=body.merge_strategy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    await db.commit()

    if pr.merge_commit_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Merge completed but merge_commit_id is missing",
        )
    return PRMergeResponse(merged=True, merge_commit_id=pr.merge_commit_id)
