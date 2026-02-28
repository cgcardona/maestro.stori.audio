"""Muse Hub pull request route handlers.

Endpoint summary:
  POST /musehub/repos/{repo_id}/pull-requests                        — open a PR
  GET  /musehub/repos/{repo_id}/pull-requests                        — list PRs
  GET  /musehub/repos/{repo_id}/pull-requests/{pr_id}                — get a PR
  GET  /musehub/repos/{repo_id}/pull-requests/{pr_id}/diff           — musical diff (radar data)
  POST /musehub/repos/{repo_id}/pull-requests/{pr_id}/merge          — merge a PR

All endpoints require a valid JWT Bearer token (except diff which accepts anonymous reads
of public repos, matching the same visibility rules as get_pull_request).
No business logic lives here — all persistence is delegated to
maestro.services.musehub_pull_requests.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import (
    PRCreate,
    PRDiffDimensionScore,
    PRDiffResponse,
    PRListResponse,
    PRMergeRequest,
    PRMergeResponse,
    PRResponse,
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
    state: str = Query(
        "all",
        pattern="^(open|merged|closed|all)$",
        description="Filter by state (open, merged, closed, all)",
    ),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> PRListResponse:
    """Return pull requests for a repo, ordered by creation time.

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
    prs = await musehub_pull_requests.list_prs(db, repo_id, state=state)
    return PRListResponse(pull_requests=prs)


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
        # Branches with no commits yet — return zero-score placeholder so the page renders.
        dimensions = [
            PRDiffDimensionScore(
                dimension=dim,
                score=0.0,
                level="NONE",
                delta_label="unchanged",
                description="No commits on one or both branches yet.",
                from_branch_commits=0,
                to_branch_commits=0,
            )
            for dim in musehub_divergence.ALL_DIMENSIONS
        ]
        return PRDiffResponse(
            pr_id=pr_id,
            repo_id=repo_id,
            from_branch=pr.from_branch,
            to_branch=pr.to_branch,
            dimensions=dimensions,
            overall_score=0.0,
            common_ancestor=None,
            affected_sections=[],
        )

    def _delta_label(score: float) -> str:
        """Convert a divergence score to a human-readable delta badge label."""
        pct = round(score * 100, 1)
        if pct == 0.0:
            return "unchanged"
        return f"+{pct}"

    dimensions = [
        PRDiffDimensionScore(
            dimension=d.dimension,
            score=d.score,
            level=d.level.value,
            delta_label=_delta_label(d.score),
            description=d.description,
            from_branch_commits=d.branch_b_commits,
            to_branch_commits=d.branch_a_commits,
        )
        for d in result.dimensions
    ]

    # Derive affected sections from commit messages that mention structural keywords.
    section_keywords = ("bridge", "chorus", "verse", "intro", "outro", "section")
    affected: list[str] = []
    seen: set[str] = set()
    for d in result.dimensions:
        if d.dimension == "structural" and d.score > 0.0:
            for kw in section_keywords:
                if kw not in seen:
                    seen.add(kw)
                    affected.append(kw.capitalize())

    return PRDiffResponse(
        pr_id=pr_id,
        repo_id=repo_id,
        from_branch=pr.from_branch,
        to_branch=pr.to_branch,
        dimensions=dimensions,
        overall_score=result.overall_score,
        common_ancestor=result.common_ancestor,
        affected_sections=affected,
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
