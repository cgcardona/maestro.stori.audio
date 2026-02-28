"""Muse Hub issue tracking route handlers.

Endpoint summary:
  POST /musehub/repos/{repo_id}/issues                              — create issue (auth required)
  GET  /musehub/repos/{repo_id}/issues                              — list issues (public: no auth)
  GET  /musehub/repos/{repo_id}/issues/{issue_number}               — get issue (public: no auth)
  PATCH /musehub/repos/{repo_id}/issues/{issue_number}              — edit title/body/labels (auth required)
  POST /musehub/repos/{repo_id}/issues/{issue_number}/close         — close issue (auth required)
  POST /musehub/repos/{repo_id}/issues/{issue_number}/reopen        — reopen issue (auth required)
  POST /musehub/repos/{repo_id}/issues/{issue_number}/assign        — set/clear assignee (auth required)
  POST /musehub/repos/{repo_id}/issues/{issue_number}/milestone     — set/clear milestone (auth required)

  GET  /musehub/repos/{repo_id}/issues/{issue_number}/comments      — list comments (public: no auth)
  POST /musehub/repos/{repo_id}/issues/{issue_number}/comments      — create comment (auth required)
  DELETE /musehub/repos/{repo_id}/issues/{issue_number}/comments/{comment_id} — delete comment (auth)

  GET  /musehub/repos/{repo_id}/milestones                          — list milestones (public: no auth)
  POST /musehub/repos/{repo_id}/milestones                          — create milestone (auth required)
  GET  /musehub/repos/{repo_id}/milestones/{milestone_number}       — get milestone (public: no auth)

Read endpoints use optional_token — unauthenticated access is allowed for public repos.
Write endpoints always require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
maestro.services.musehub_issues.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    IssueAssignRequest,
    IssueCommentCreate,
    IssueCommentListResponse,
    IssueCreate,
    IssueEventPayload,
    IssueListResponse,
    IssueResponse,
    IssueUpdate,
    MilestoneCreate,
    MilestoneListResponse,
    MilestoneResponse,
)
from maestro.services import musehub_issues
from maestro.services import musehub_repository
from maestro.services.musehub_webhook_dispatcher import dispatch_event_background

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Issue CRUD ────────────────────────────────────────────────────────────────


@router.post(
    "/repos/{repo_id}/issues",
    response_model=IssueResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIssue",
    summary="Open a new issue against a Muse Hub repo",
)
async def create_issue(
    repo_id: str,
    body: IssueCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
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
        author=token.get("sub", ""),
    )
    await db.commit()

    open_payload: IssueEventPayload = {
        "repoId": repo_id,
        "action": "opened",
        "issueId": issue.issue_id,
        "number": issue.number,
        "title": issue.title,
        "state": issue.state,
    }
    background_tasks.add_task(
        dispatch_event_background,
        repo_id,
        "issue",
        open_payload,
    )
    return issue


@router.get(
    "/repos/{repo_id}/issues",
    response_model=IssueListResponse,
    operation_id="listIssues",
    summary="List issues for a Muse Hub repo",
)
async def list_issues(
    repo_id: str,
    state: str = Query("open", pattern="^(open|closed|all)$", description="Filter by state"),
    label: str | None = Query(None, description="Filter by label string"),
    milestone_id: str | None = Query(None, description="Filter by milestone UUID"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> IssueListResponse:
    """Return issues for a repo. Defaults to open issues only.

    Use ``?state=all`` to include closed issues, ``?state=closed`` for closed only.
    Use ``?label=<string>`` to filter by a specific label.
    Use ``?milestone_id=<uuid>`` to filter to a specific milestone.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    issues = await musehub_issues.list_issues(
        db, repo_id, state=state, label=label, milestone_id=milestone_id
    )
    return IssueListResponse(issues=issues)


@router.get(
    "/repos/{repo_id}/issues/{issue_number}",
    response_model=IssueResponse,
    operation_id="getIssue",
    summary="Get a single issue by its per-repo number",
)
async def get_issue(
    repo_id: str,
    issue_number: int,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> IssueResponse:
    """Return a single issue. Returns 404 if the repo or issue number is not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    issue = await musehub_issues.get_issue(db, repo_id, issue_number)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    return issue


@router.patch(
    "/repos/{repo_id}/issues/{issue_number}",
    response_model=IssueResponse,
    operation_id="updateIssue",
    summary="Edit an issue's title, body, or labels",
)
async def update_issue(
    repo_id: str,
    issue_number: int,
    body: IssueUpdate,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> IssueResponse:
    """Partially update an issue. Only provided fields are changed."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    issue = await musehub_issues.update_issue(
        db,
        repo_id,
        issue_number,
        title=body.title,
        body=body.body,
        labels=body.labels,
    )
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    await db.commit()
    return issue


@router.post(
    "/repos/{repo_id}/issues/{issue_number}/close",
    response_model=IssueResponse,
    operation_id="closeIssue",
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

    close_payload: IssueEventPayload = {
        "repoId": repo_id,
        "action": "closed",
        "issueId": issue.issue_id,
        "number": issue.number,
        "title": issue.title,
        "state": issue.state,
    }
    background_tasks.add_task(
        dispatch_event_background,
        repo_id,
        "issue",
        close_payload,
    )
    return issue


@router.post(
    "/repos/{repo_id}/issues/{issue_number}/reopen",
    response_model=IssueResponse,
    operation_id="reopenIssue",
    summary="Reopen a closed issue",
)
async def reopen_issue(
    repo_id: str,
    issue_number: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> IssueResponse:
    """Set a closed issue's state back to ``open``. Returns 404 if not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    issue = await musehub_issues.reopen_issue(db, repo_id, issue_number)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    await db.commit()

    reopen_payload: IssueEventPayload = {
        "repoId": repo_id,
        "action": "opened",
        "issueId": issue.issue_id,
        "number": issue.number,
        "title": issue.title,
        "state": issue.state,
    }
    background_tasks.add_task(
        dispatch_event_background,
        repo_id,
        "issue",
        reopen_payload,
    )
    return issue


@router.post(
    "/repos/{repo_id}/issues/{issue_number}/assign",
    response_model=IssueResponse,
    operation_id="assignIssue",
    summary="Assign or unassign a collaborator on an issue",
)
async def assign_issue(
    repo_id: str,
    issue_number: int,
    body: IssueAssignRequest,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> IssueResponse:
    """Set or clear the assignee. Pass ``assignee: null`` to unassign."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    issue = await musehub_issues.assign_issue(
        db, repo_id, issue_number, assignee=body.assignee
    )
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    await db.commit()
    return issue


@router.post(
    "/repos/{repo_id}/issues/{issue_number}/milestone",
    response_model=IssueResponse,
    operation_id="setIssueMilestone",
    summary="Assign or remove a milestone from an issue",
)
async def set_issue_milestone(
    repo_id: str,
    issue_number: int,
    milestone_id: str | None = Query(None, description="Milestone UUID to assign; omit or null to remove"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> IssueResponse:
    """Link an issue to a milestone or remove the milestone link.

    Pass ``?milestone_id=<uuid>`` to assign. Omit or pass ``milestone_id=null``
    to remove the milestone link.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    try:
        issue = await musehub_issues.set_issue_milestone(
            db, repo_id, issue_number, milestone_id=milestone_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    await db.commit()
    return issue


# ── Issue comments ─────────────────────────────────────────────────────────────


@router.get(
    "/repos/{repo_id}/issues/{issue_number}/comments",
    response_model=IssueCommentListResponse,
    operation_id="listIssueComments",
    summary="List comments on a Muse Hub issue",
)
async def list_comments(
    repo_id: str,
    issue_number: int,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> IssueCommentListResponse:
    """Return all non-deleted comments on an issue, oldest first."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    issue = await musehub_issues.get_issue(db, repo_id, issue_number)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    return await musehub_issues.list_comments(db, issue.issue_id)


@router.post(
    "/repos/{repo_id}/issues/{issue_number}/comments",
    response_model=IssueCommentListResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIssueComment",
    summary="Post a comment on a Muse Hub issue",
)
async def create_comment(
    repo_id: str,
    issue_number: int,
    body: IssueCommentCreate,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> IssueCommentListResponse:
    """Create a comment (optionally a threaded reply) on an issue.

    Musical context references in the body (``track:bass``, ``section:chorus``,
    ``beats:16-24``) are parsed and returned in ``musical_refs``.

    Returns the full updated comment list so clients don't need a second request.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    issue = await musehub_issues.get_issue(db, repo_id, issue_number)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")

    try:
        await musehub_issues.create_comment(
            db,
            issue_id=issue.issue_id,
            repo_id=repo_id,
            body=body.body,
            author=token.get("sub", ""),
            parent_id=body.parent_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    await db.commit()
    return await musehub_issues.list_comments(db, issue.issue_id)


@router.delete(
    "/repos/{repo_id}/issues/{issue_number}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteIssueComment",
    summary="Soft-delete a comment from an issue",
)
async def delete_comment(
    repo_id: str,
    issue_number: int,
    comment_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> None:
    """Soft-delete a comment (marks it as deleted; it is excluded from list results)."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    issue = await musehub_issues.get_issue(db, repo_id, issue_number)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")

    deleted = await musehub_issues.delete_comment(db, comment_id, issue.issue_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    await db.commit()


# ── Milestones ─────────────────────────────────────────────────────────────────


@router.get(
    "/repos/{repo_id}/milestones",
    response_model=MilestoneListResponse,
    operation_id="listMilestones",
    summary="List milestones for a Muse Hub repo",
)
async def list_milestones(
    repo_id: str,
    state: str = Query("open", pattern="^(open|closed|all)$", description="Filter by state"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> MilestoneListResponse:
    """Return milestones for a repo. Defaults to open milestones only."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await musehub_issues.list_milestones(db, repo_id, state=state)


@router.post(
    "/repos/{repo_id}/milestones",
    response_model=MilestoneResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMilestone",
    summary="Create a milestone for a Muse Hub repo",
)
async def create_milestone(
    repo_id: str,
    body: MilestoneCreate,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> MilestoneResponse:
    """Create a new milestone in ``open`` state."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    milestone = await musehub_issues.create_milestone(
        db,
        repo_id=repo_id,
        title=body.title,
        description=body.description,
        author=token.get("sub", ""),
        due_on=body.due_on,
    )
    await db.commit()
    return milestone


@router.get(
    "/repos/{repo_id}/milestones/{milestone_number}",
    response_model=MilestoneResponse,
    operation_id="getMilestone",
    summary="Get a single milestone by its per-repo number",
)
async def get_milestone(
    repo_id: str,
    milestone_number: int,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> MilestoneResponse:
    """Return a single milestone with open and closed issue counts."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    milestone = await musehub_issues.get_milestone(db, repo_id, milestone_number)
    if milestone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found")
    return milestone
