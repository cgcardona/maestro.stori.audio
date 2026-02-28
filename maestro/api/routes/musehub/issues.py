"""Muse Hub issue tracking route handlers.

Endpoint summary:
  POST /musehub/repos/{repo_id}/issues                   — create an issue
  GET  /musehub/repos/{repo_id}/issues                   — list issues
  GET  /musehub/repos/{repo_id}/issues/{issue_number}    — get a single issue
  POST /musehub/repos/{repo_id}/issues/{issue_number}/close — close an issue

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_issues.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import IssueCreate, IssueListResponse, IssueResponse
from maestro.services import musehub_issues
from maestro.services import musehub_repository
from maestro.services.musehub_webhook_dispatcher import dispatch_event_background

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/repos/{repo_id}/issues",
    response_model=IssueResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a new issue against a Muse Hub repo",
)
async def create_issue(
    repo_id: str,
    body: IssueCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> IssueResponse:
    """Create a new issue in ``open`` state with an auto-incremented per-repo number."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    issue = await musehub_issues.create_issue(
        db,
        repo_id=repo_id,
        title=body.title,
        body=body.body,
        labels=body.labels,
    )
    await db.commit()

    background_tasks.add_task(
        dispatch_event_background,
        repo_id,
        "issue",
        {
            "repoId": repo_id,
            "action": "opened",
            "issueId": issue.issue_id,
            "number": issue.number,
            "title": issue.title,
            "state": issue.state,
        },
    )
    return issue


@router.get(
    "/repos/{repo_id}/issues",
    response_model=IssueListResponse,
    summary="List issues for a Muse Hub repo",
)
async def list_issues(
    repo_id: str,
    state: str = Query("open", pattern="^(open|closed|all)$", description="Filter by state"),
    label: str | None = Query(None, description="Filter by label string"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> IssueListResponse:
    """Return issues for a repo. Defaults to open issues only.

    Use ``?state=all`` to include closed issues, ``?state=closed`` for closed only.
    Use ``?label=<string>`` to filter by a specific label.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    issues = await musehub_issues.list_issues(db, repo_id, state=state, label=label)
    return IssueListResponse(issues=issues)


@router.get(
    "/repos/{repo_id}/issues/{issue_number}",
    response_model=IssueResponse,
    summary="Get a single issue by its per-repo number",
)
async def get_issue(
    repo_id: str,
    issue_number: int,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> IssueResponse:
    """Return a single issue. Returns 404 if the repo or issue number is not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    issue = await musehub_issues.get_issue(db, repo_id, issue_number)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    return issue


@router.post(
    "/repos/{repo_id}/issues/{issue_number}/close",
    response_model=IssueResponse,
    summary="Close an issue",
)
async def close_issue(
    repo_id: str,
    issue_number: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> IssueResponse:
    """Set an issue's state to ``closed``. Returns 404 if not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    issue = await musehub_issues.close_issue(db, repo_id, issue_number)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    await db.commit()

    background_tasks.add_task(
        dispatch_event_background,
        repo_id,
        "issue",
        {
            "repoId": repo_id,
            "action": "closed",
            "issueId": issue.issue_id,
            "number": issue.number,
            "title": issue.title,
            "state": issue.state,
        },
    )
    return issue
