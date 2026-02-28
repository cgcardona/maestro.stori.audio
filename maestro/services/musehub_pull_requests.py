"""Muse Hub pull request persistence adapter — single point of DB access for PRs.

This module is the ONLY place that touches the ``musehub_pull_requests`` table.
Route handlers delegate here; no business logic lives in routes.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.

Merge strategy
--------------
``merge_commit`` is the only strategy at MVP. It creates a new commit on
``to_branch`` whose parent_ids are [to_branch head, from_branch head], then
updates the ``to_branch`` head pointer and marks the PR as merged.

If either branch has no commits yet (no head commit), the merge is rejected with
a ``ValueError`` — there is nothing to merge.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import PRCommentListResponse, PRCommentResponse, PRResponse

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _to_pr_response(row: db.MusehubPullRequest) -> PRResponse:
    return PRResponse(
        pr_id=row.pr_id,
        title=row.title,
        body=row.body,
        state=row.state,
        from_branch=row.from_branch,
        to_branch=row.to_branch,
        merge_commit_id=row.merge_commit_id,
        author=row.author,
        created_at=row.created_at,
    )


async def _get_branch(
    session: AsyncSession, repo_id: str, branch_name: str
) -> db.MusehubBranch | None:
    """Return the branch record by repo + name, or None."""
    stmt = select(db.MusehubBranch).where(
        db.MusehubBranch.repo_id == repo_id,
        db.MusehubBranch.name == branch_name,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_pr(
    session: AsyncSession,
    *,
    repo_id: str,
    title: str,
    from_branch: str,
    to_branch: str,
    body: str = "",
    author: str = "",
) -> PRResponse:
    """Persist a new pull request in ``open`` state and return its wire representation.

    ``author`` identifies the user opening the PR — typically the JWT ``sub``
    claim from the request token, or a display name from the seed script.

    Raises ``ValueError`` if ``from_branch`` does not exist in the repo —
    the caller should surface this as HTTP 404.
    """
    branch = await _get_branch(session, repo_id, from_branch)
    if branch is None:
        raise ValueError(f"Branch '{from_branch}' not found in repo {repo_id}")

    pr = db.MusehubPullRequest(
        repo_id=repo_id,
        title=title,
        body=body,
        state="open",
        from_branch=from_branch,
        to_branch=to_branch,
        author=author,
    )
    session.add(pr)
    await session.flush()
    await session.refresh(pr)
    logger.info("✅ Created PR '%s' (%s → %s) in repo %s", title, from_branch, to_branch, repo_id)
    return _to_pr_response(pr)


async def list_prs(
    session: AsyncSession,
    repo_id: str,
    *,
    state: str = "all",
) -> list[PRResponse]:
    """Return pull requests for a repo, ordered by created_at ascending.

    ``state`` may be ``"open"``, ``"merged"``, ``"closed"``, or ``"all"``.
    """
    stmt = select(db.MusehubPullRequest).where(
        db.MusehubPullRequest.repo_id == repo_id
    )
    if state != "all":
        stmt = stmt.where(db.MusehubPullRequest.state == state)
    stmt = stmt.order_by(db.MusehubPullRequest.created_at)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_pr_response(r) for r in rows]


async def get_pr(
    session: AsyncSession,
    repo_id: str,
    pr_id: str,
) -> PRResponse | None:
    """Return a single PR by its ID, or None if not found."""
    stmt = select(db.MusehubPullRequest).where(
        db.MusehubPullRequest.repo_id == repo_id,
        db.MusehubPullRequest.pr_id == pr_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return _to_pr_response(row)


async def merge_pr(
    session: AsyncSession,
    repo_id: str,
    pr_id: str,
    *,
    merge_strategy: str = "merge_commit",
) -> PRResponse:
    """Merge an open PR using the given strategy.

    Creates a merge commit on ``to_branch`` with parent_ids =
    [to_branch head, from_branch head], updates the branch head pointer, and
    marks the PR as ``merged``.

    Raises:
        ValueError: PR not found or ``from_branch`` does not exist or has no commits.
        RuntimeError: PR is already merged or closed (caller surfaces as 409).
    """
    stmt = select(db.MusehubPullRequest).where(
        db.MusehubPullRequest.repo_id == repo_id,
        db.MusehubPullRequest.pr_id == pr_id,
    )
    pr = (await session.execute(stmt)).scalar_one_or_none()
    if pr is None:
        raise ValueError(f"Pull request {pr_id} not found in repo {repo_id}")

    if pr.state != "open":
        raise RuntimeError(f"Pull request {pr_id} is already {pr.state}")

    from_b = await _get_branch(session, repo_id, pr.from_branch)
    to_b = await _get_branch(session, repo_id, pr.to_branch)

    # Collect parent commit IDs for the merge commit.
    parent_ids: list[str] = []
    if to_b is not None and to_b.head_commit_id is not None:
        parent_ids.append(to_b.head_commit_id)
    if from_b is not None and from_b.head_commit_id is not None:
        parent_ids.append(from_b.head_commit_id)

    if not parent_ids:
        raise ValueError(
            f"Cannot merge: neither '{pr.from_branch}' nor '{pr.to_branch}' has any commits"
        )

    # Create the merge commit on to_branch.
    merge_commit_id = str(uuid.uuid4()).replace("-", "")
    merge_commit = db.MusehubCommit(
        commit_id=merge_commit_id,
        repo_id=repo_id,
        branch=pr.to_branch,
        parent_ids=parent_ids,
        message=f"Merge '{pr.from_branch}' into '{pr.to_branch}' — PR: {pr.title}",
        author="musehub-server",
        timestamp=_utc_now(),
    )
    session.add(merge_commit)

    # Advance (or create) the to_branch head pointer.
    if to_b is None:
        to_b = db.MusehubBranch(
            repo_id=repo_id,
            name=pr.to_branch,
            head_commit_id=merge_commit_id,
        )
        session.add(to_b)
    else:
        to_b.head_commit_id = merge_commit_id

    # Mark PR as merged.
    pr.state = "merged"
    pr.merge_commit_id = merge_commit_id

    await session.flush()
    await session.refresh(pr)
    logger.info(
        "✅ Merged PR %s ('%s' → '%s') in repo %s, merge commit %s",
        pr_id,
        pr.from_branch,
        pr.to_branch,
        repo_id,
        merge_commit_id,
    )
    return _to_pr_response(pr)


# ---------------------------------------------------------------------------
# PR review comments
# ---------------------------------------------------------------------------


def _to_comment_response(row: db.MusehubPRComment) -> PRCommentResponse:
    return PRCommentResponse(
        comment_id=row.comment_id,
        pr_id=row.pr_id,
        author=row.author,
        body=row.body,
        target_type=row.target_type,
        target_track=row.target_track,
        target_beat_start=row.target_beat_start,
        target_beat_end=row.target_beat_end,
        target_note_pitch=row.target_note_pitch,
        parent_comment_id=row.parent_comment_id,
        created_at=row.created_at,
    )


async def create_pr_comment(
    session: AsyncSession,
    *,
    pr_id: str,
    repo_id: str,
    author: str,
    body: str,
    target_type: str = "general",
    target_track: str | None = None,
    target_beat_start: float | None = None,
    target_beat_end: float | None = None,
    target_note_pitch: int | None = None,
    parent_comment_id: str | None = None,
) -> PRCommentResponse:
    """Persist a new review comment on a PR and return its wire representation.

    ``author`` is the JWT ``sub`` claim of the reviewer.
    ``parent_comment_id`` must be an existing top-level comment on the same PR
    when creating a threaded reply; the caller validates this constraint before
    calling here.

    Raises ``ValueError`` if the PR does not exist in the given repo.
    """
    stmt = select(db.MusehubPullRequest).where(
        db.MusehubPullRequest.pr_id == pr_id,
        db.MusehubPullRequest.repo_id == repo_id,
    )
    pr = (await session.execute(stmt)).scalar_one_or_none()
    if pr is None:
        raise ValueError(f"Pull request {pr_id} not found in repo {repo_id}")

    comment = db.MusehubPRComment(
        pr_id=pr_id,
        repo_id=repo_id,
        author=author,
        body=body,
        target_type=target_type,
        target_track=target_track,
        target_beat_start=target_beat_start,
        target_beat_end=target_beat_end,
        target_note_pitch=target_note_pitch,
        parent_comment_id=parent_comment_id,
    )
    session.add(comment)
    await session.flush()
    await session.refresh(comment)
    logger.info("✅ Created PR comment %s on PR %s by %s", comment.comment_id, pr_id, author)
    return _to_comment_response(comment)


async def list_pr_comments(
    session: AsyncSession,
    pr_id: str,
    repo_id: str,
) -> PRCommentListResponse:
    """Return all review comments for a PR, assembled into a two-level thread tree.

    Top-level comments (``parent_comment_id`` is None) form the root list.
    Each carries a ``replies`` list with direct children sorted by
    ``created_at`` ascending.  Grandchildren are not supported — the caller
    should reply to the original top-level comment.

    Returns ``PRCommentListResponse`` with ``total`` covering all levels.
    """
    stmt = (
        select(db.MusehubPRComment)
        .where(
            db.MusehubPRComment.pr_id == pr_id,
            db.MusehubPRComment.repo_id == repo_id,
        )
        .order_by(db.MusehubPRComment.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()

    # Build id → response map first; attach replies in a second pass.
    top_level: list[PRCommentResponse] = []
    by_id: dict[str, PRCommentResponse] = {}
    for row in rows:
        resp = _to_comment_response(row)
        by_id[row.comment_id] = resp
        if row.parent_comment_id is None:
            top_level.append(resp)

    for row in rows:
        if row.parent_comment_id is not None:
            parent = by_id.get(row.parent_comment_id)
            if parent is not None:
                parent.replies.append(by_id[row.comment_id])

    return PRCommentListResponse(comments=top_level, total=len(rows))
