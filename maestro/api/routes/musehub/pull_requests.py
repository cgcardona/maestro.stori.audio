"""Muse Hub pull request route handlers.

Endpoint summary:
  POST   /musehub/repos/{repo_id}/pull-requests                                    — open a PR
  GET    /musehub/repos/{repo_id}/pull-requests                                    — list PRs
  GET    /musehub/repos/{repo_id}/pull-requests/{pr_id}                            — get a PR
  GET    /musehub/repos/{repo_id}/pull-requests/{pr_id}/diff                       — musical diff (radar data)
  POST   /musehub/repos/{repo_id}/pull-requests/{pr_id}/merge                      — merge a PR
  POST   /musehub/repos/{repo_id}/pull-requests/{pr_id}/comments                   — create review comment
  GET    /musehub/repos/{repo_id}/pull-requests/{pr_id}/comments                   — list review comments (threaded)
  POST   /musehub/repos/{repo_id}/pull-requests/{pr_id}/reviewers                  — request review from users
  DELETE /musehub/repos/{repo_id}/pull-requests/{pr_id}/reviewers/{username}       — remove review request
  GET    /musehub/repos/{repo_id}/pull-requests/{pr_id}/reviews                    — list reviews
  POST   /musehub/repos/{repo_id}/pull-requests/{pr_id}/reviews                    — submit a review

All endpoints require a valid JWT Bearer token (except diff which accepts anonymous reads
of public repos, matching the same visibility rules as get_pull_request).
No business logic lives here — all persistence is delegated to
maestro.services.musehub_pull_requests.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.api.routes.musehub.pagination import PaginationParams, build_link_header, paginate_list
from maestro.db import get_db
from maestro.models.musehub import (
    PRCommentCreate,
    PRCommentListResponse,
    PRCreate,
    PRDiffResponse,
    PRListResponse,
    PRMergeRequest,
    PRMergeResponse,
    PRResponse,
    PRReviewCreate,
    PRReviewListResponse,
    PRReviewResponse,
    PRReviewerRequest,
    PullRequestEventPayload,
)
from maestro.services import musehub_divergence, musehub_pull_requests, musehub_repository
from maestro.services.musehub_webhook_dispatcher import dispatch_event_background

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/repos/{repo_id}/pull-requests",
    response_model=PRResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createPullRequest",
    summary="Open a pull request against a Muse Hub repo",
)
async def create_pull_request(
    repo_id: str,
    body: PRCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
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
            author=token.get("sub", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    await db.commit()

    open_pr_payload: PullRequestEventPayload = {
        "repoId": repo_id,
        "action": "opened",
        "prId": pr.pr_id,
        "title": pr.title,
        "fromBranch": pr.from_branch,
        "toBranch": pr.to_branch,
        "state": pr.state,
    }
    background_tasks.add_task(
        dispatch_event_background,
        repo_id,
        "pull_request",
        open_pr_payload,
    )
    return pr


@router.get(
    "/repos/{repo_id}/pull-requests",
    response_model=PRListResponse,
    operation_id="listPullRequests",
    summary="List pull requests for a Muse Hub repo",
)
async def list_pull_requests(
    repo_id: str,
    request: Request,
    response: Response,
    state: str = Query(
        "all",
        pattern="^(open|merged|closed|all)$",
        description="Filter by state (open, merged, closed, all)",
    ),
    pagination: PaginationParams = Depends(PaginationParams),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> PRListResponse:
    """Return pull requests for a repo, ordered by creation time.

    Supports RFC 8288 page-based pagination via ``?page=N&per_page=N``.
    The ``Link`` response header contains ``rel="first"``, ``rel="last"``,
    ``rel="prev"`` (when not on the first page), and ``rel="next"`` (when
    more pages remain).

    Use ?state=open to filter to open PRs only. Defaults to all states.
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
    all_prs = await musehub_pull_requests.list_prs(db, repo_id, state=state)
    page_prs, total = paginate_list(all_prs, pagination.page, pagination.per_page)
    response.headers["Link"] = build_link_header(request, total, pagination.page, pagination.per_page)
    return PRListResponse(pull_requests=page_prs, total=total)


@router.get(
    "/repos/{repo_id}/pull-requests/{pr_id}",
    response_model=PRResponse,
    operation_id="getPullRequest",
    summary="Get a single pull request by ID",
)
async def get_pull_request(
    repo_id: str,
    pr_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> PRResponse:
    """Return a single PR. Returns 404 if the repo or PR is not found."""
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    pr = await musehub_pull_requests.get_pr(db, repo_id, pr_id)
    if pr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pull request not found")
    return pr


@router.get(
    "/repos/{repo_id}/pull-requests/{pr_id}/diff",
    response_model=PRDiffResponse,
    operation_id="getPullRequestDiff",
    summary="Compute musical diff between the PR branches",
)
async def get_pull_request_diff(
    repo_id: str,
    pr_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> PRDiffResponse:
    """Return a five-dimension musical diff between from_branch and to_branch of a PR.

    Uses the Jaccard divergence engine to score harmonic, rhythmic, melodic,
    structural, and dynamic change magnitude between the two branches.

    This endpoint is consumed by the PR detail page to render the radar chart,
    piano roll diff, audio A/B toggle, and dimension badges.  AI agents use it
    to reason about musical impact before approving a merge.

    Returns:
        PRDiffResponse with per-dimension scores and overall divergence score.

    Raises:
        404: If the repo or PR is not found.
        401: If the repo is private and no token is provided.
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
    pr = await musehub_pull_requests.get_pr(db, repo_id, pr_id)
    if pr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pull request not found")

    try:
        result = await musehub_divergence.compute_hub_divergence(
            db,
            repo_id=repo_id,
            branch_a=pr.to_branch,
            branch_b=pr.from_branch,
        )
    except ValueError:
        return musehub_divergence.build_zero_diff_response(
            pr_id=pr_id,
            repo_id=repo_id,
            from_branch=pr.from_branch,
            to_branch=pr.to_branch,
        )

    return musehub_divergence.build_pr_diff_response(
        pr_id=pr_id,
        from_branch=pr.from_branch,
        to_branch=pr.to_branch,
        result=result,
    )


@router.post(
    "/repos/{repo_id}/pull-requests/{pr_id}/merge",
    response_model=PRMergeResponse,
    operation_id="mergePullRequest",
    summary="Merge an open pull request",
)
async def merge_pull_request(
    repo_id: str,
    pr_id: str,
    body: PRMergeRequest,
    background_tasks: BackgroundTasks,
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

    merge_pr_payload: PullRequestEventPayload = {
        "repoId": repo_id,
        "action": "merged",
        "prId": pr.pr_id,
        "title": pr.title,
        "fromBranch": pr.from_branch,
        "toBranch": pr.to_branch,
        "state": pr.state,
        "mergeCommitId": pr.merge_commit_id,
    }
    background_tasks.add_task(
        dispatch_event_background,
        repo_id,
        "pull_request",
        merge_pr_payload,
    )
    return PRMergeResponse(merged=True, merge_commit_id=pr.merge_commit_id)


@router.post(
    "/repos/{repo_id}/pull-requests/{pr_id}/comments",
    response_model=PRCommentListResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createPRComment",
    summary="Leave a review comment on a pull request musical diff",
)
async def create_pr_comment(
    repo_id: str,
    pr_id: str,
    body: PRCommentCreate,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> PRCommentListResponse:
    """Create a review comment on a PR and return the updated thread list.

    Comments can target the whole PR (general), a named track, a beat region,
    or a single note event.  Replies attach via ``parent_comment_id``.

    Returns the full threaded comment list after insertion so the UI can
    refresh in a single round-trip.

    Returns 404 if the repo or PR does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    try:
        await musehub_pull_requests.create_pr_comment(
            db,
            pr_id=pr_id,
            repo_id=repo_id,
            author=token.get("sub", ""),
            body=body.body,
            target_type=body.target_type,
            target_track=body.target_track,
            target_beat_start=body.target_beat_start,
            target_beat_end=body.target_beat_end,
            target_note_pitch=body.target_note_pitch,
            parent_comment_id=body.parent_comment_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    await db.commit()
    return await musehub_pull_requests.list_pr_comments(db, pr_id=pr_id, repo_id=repo_id)


@router.get(
    "/repos/{repo_id}/pull-requests/{pr_id}/comments",
    response_model=PRCommentListResponse,
    operation_id="listPRComments",
    summary="List review comments for a PR, assembled into threaded discussions",
)
async def list_pr_comments(
    repo_id: str,
    pr_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> PRCommentListResponse:
    """Return all review comments for a PR in a two-level thread structure.

    Top-level comments carry a ``replies`` list with their direct children.
    Public repo comments are readable without authentication; private repos
    require a Bearer token.

    Returns 404 if the repo or PR does not exist.
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
    pr = await musehub_pull_requests.get_pr(db, repo_id, pr_id)
    if pr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pull request not found")
    return await musehub_pull_requests.list_pr_comments(db, pr_id=pr_id, repo_id=repo_id)


# ---------------------------------------------------------------------------
# Reviewer assignment endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/repos/{repo_id}/pull-requests/{pr_id}/reviewers",
    response_model=PRReviewListResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="requestPRReviewers",
    summary="Request a review from one or more users",
)
async def request_pr_reviewers(
    repo_id: str,
    pr_id: str,
    body: PRReviewerRequest,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> PRReviewListResponse:
    """Add one or more users as requested reviewers on a PR.

    Creates a ``pending`` review row for each username that does not already
    have one.  Existing rows (any state) are left unchanged so submitted
    approvals are never silently reset.

    Returns the full updated review list for the PR.

    Returns 404 if the repo or PR is not found.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    try:
        result = await musehub_pull_requests.request_reviewers(
            db,
            repo_id=repo_id,
            pr_id=pr_id,
            reviewers=body.reviewers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    await db.commit()
    return result


@router.delete(
    "/repos/{repo_id}/pull-requests/{pr_id}/reviewers/{username}",
    response_model=PRReviewListResponse,
    operation_id="removePRReviewer",
    summary="Remove a pending review request for a user",
)
async def remove_pr_reviewer(
    repo_id: str,
    pr_id: str,
    username: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> PRReviewListResponse:
    """Remove a pending review request for ``username`` from a PR.

    Only ``pending`` assignments may be removed.  Submitted reviews (approved,
    changes_requested, dismissed) are immutable to preserve the audit trail.

    Returns the updated review list after deletion.

    Returns 404 if the repo, PR, or reviewer assignment is not found.
    Returns 409 if the reviewer has already submitted a review.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    try:
        result = await musehub_pull_requests.remove_reviewer(
            db,
            repo_id=repo_id,
            pr_id=pr_id,
            username=username,
        )
    except ValueError as exc:
        msg = str(exc)
        if "already submitted" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)

    await db.commit()
    return result


# ---------------------------------------------------------------------------
# Review submission endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/repos/{repo_id}/pull-requests/{pr_id}/reviews",
    response_model=PRReviewListResponse,
    operation_id="listPRReviews",
    summary="List reviews for a pull request",
)
async def list_pr_reviews(
    repo_id: str,
    pr_id: str,
    state: str | None = Query(
        None,
        pattern="^(pending|approved|changes_requested|dismissed)$",
        description="Filter by review state (pending, approved, changes_requested, dismissed)",
    ),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> PRReviewListResponse:
    """Return all reviews for a PR, optionally filtered by state.

    Includes both pending reviewer assignments and submitted reviews.
    Public repo reviews are readable without authentication; private repos
    require a Bearer token.

    Returns 404 if the repo or PR is not found.
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

    try:
        return await musehub_pull_requests.list_reviews(
            db,
            repo_id=repo_id,
            pr_id=pr_id,
            state=state,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/repos/{repo_id}/pull-requests/{pr_id}/reviews",
    response_model=PRReviewResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="submitPRReview",
    summary="Submit a formal review on a pull request",
)
async def submit_pr_review(
    repo_id: str,
    pr_id: str,
    body: PRReviewCreate,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> PRReviewResponse:
    """Submit a formal review for the authenticated user.

    ``event`` governs the resulting review state:
      - ``approve``          → sets state to ``approved``
      - ``request_changes``  → sets state to ``changes_requested``
      - ``comment``          → leaves state as ``pending`` (body-only feedback)

    If the user already has a review row on this PR it is updated in-place;
    otherwise a new row is created.  This allows reviewers to revise their
    verdict after seeing author responses.

    Returns 404 if the repo or PR is not found.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    reviewer = token.get("sub", "")

    try:
        result = await musehub_pull_requests.submit_review(
            db,
            repo_id=repo_id,
            pr_id=pr_id,
            reviewer_username=reviewer,
            event=body.event,
            body=body.body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    await db.commit()
    return result
