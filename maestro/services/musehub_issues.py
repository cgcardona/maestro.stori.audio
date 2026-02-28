"""Muse Hub issue persistence adapter — single point of DB access for issues.

This module is the ONLY place that touches the ``musehub_issues``,
``musehub_issue_comments``, and ``musehub_milestones`` tables.
Route handlers delegate here; no business logic lives in routes.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    IssueCommentListResponse,
    IssueCommentResponse,
    IssueResponse,
    MilestoneListResponse,
    MilestoneResponse,
    MusicalRef,
)

logger = logging.getLogger(__name__)

# Regex to parse musical context references: track:bass, section:chorus, beats:16-24
_MUSICAL_REF_RE = re.compile(
    r"\b(track|section|beats):([A-Za-z0-9_\-]+(?:-[A-Za-z0-9_\-]+)*)\b"
)


def _parse_musical_refs(body: str) -> list[dict[str, str]]:
    """Extract musical context references from comment body text.

    Returns a list of dicts with keys ``type``, ``value``, and ``raw`` so
    they can be stored in the JSON ``musical_refs`` column and round-tripped
    without re-parsing on every read.
    """
    refs: list[dict[str, str]] = []
    for m in _MUSICAL_REF_RE.finditer(body):
        refs.append({"type": m.group(1), "value": m.group(2), "raw": m.group(0)})
    return refs


def _to_issue_response(row: db.MusehubIssue, comment_count: int = 0) -> IssueResponse:
    """Convert a DB row to an IssueResponse wire model.

    ``comment_count`` must be passed in by the caller — it requires a separate
    aggregate query so we avoid N+1 loads on list endpoints.
    """
    milestone_title: str | None = None
    if row.milestone is not None:
        milestone_title = row.milestone.title
    return IssueResponse(
        issue_id=row.issue_id,
        number=row.number,
        title=row.title,
        body=row.body,
        state=row.state,
        labels=list(row.labels or []),
        author=row.author,
        assignee=row.assignee,
        milestone_id=row.milestone_id,
        milestone_title=milestone_title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        comment_count=comment_count,
    )


def _to_comment_response(row: db.MusehubIssueComment) -> IssueCommentResponse:
    """Convert a DB comment row to the wire representation."""
    musical_refs = [
        MusicalRef(type=r["type"], value=r["value"], raw=r["raw"])
        for r in (row.musical_refs or [])
    ]
    return IssueCommentResponse(
        comment_id=row.comment_id,
        issue_id=row.issue_id,
        author=row.author,
        body=row.body,
        parent_id=row.parent_id,
        musical_refs=musical_refs,
        is_deleted=row.is_deleted,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_milestone_response(
    row: db.MusehubMilestone, open_count: int = 0, closed_count: int = 0
) -> MilestoneResponse:
    """Convert a DB milestone row to the wire representation."""
    return MilestoneResponse(
        milestone_id=row.milestone_id,
        number=row.number,
        title=row.title,
        description=row.description,
        state=row.state,
        author=row.author,
        due_on=row.due_on,
        open_issues=open_count,
        closed_issues=closed_count,
        created_at=row.created_at,
    )


async def _next_issue_number(session: AsyncSession, repo_id: str) -> int:
    """Return the next sequential issue number for the given repo (1-based)."""
    stmt = select(func.max(db.MusehubIssue.number)).where(
        db.MusehubIssue.repo_id == repo_id
    )
    current_max: int | None = (await session.execute(stmt)).scalar_one_or_none()
    return (current_max or 0) + 1


async def _next_milestone_number(session: AsyncSession, repo_id: str) -> int:
    """Return the next sequential milestone number for the given repo (1-based)."""
    stmt = select(func.max(db.MusehubMilestone.number)).where(
        db.MusehubMilestone.repo_id == repo_id
    )
    current_max: int | None = (await session.execute(stmt)).scalar_one_or_none()
    return (current_max or 0) + 1


async def _count_comments(session: AsyncSession, issue_id: str) -> int:
    """Return the non-deleted comment count for a single issue."""
    stmt = select(func.count(db.MusehubIssueComment.comment_id)).where(
        db.MusehubIssueComment.issue_id == issue_id,
        db.MusehubIssueComment.is_deleted.is_(False),
    )
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def create_issue(
    session: AsyncSession,
    *,
    repo_id: str,
    title: str,
    body: str,
    labels: list[str],
    author: str = "",
) -> IssueResponse:
    """Persist a new issue in ``open`` state and return its wire representation.

    ``author`` identifies the user opening the issue — typically the JWT ``sub``
    claim from the request token, or a display name from the seed script.
    """
    number = await _next_issue_number(session, repo_id)
    issue = db.MusehubIssue(
        repo_id=repo_id,
        number=number,
        title=title,
        body=body,
        state="open",
        labels=labels,
        author=author,
    )
    session.add(issue)
    await session.flush()
    await session.refresh(issue)
    logger.info("✅ Created issue #%d for repo %s: %s", number, repo_id, title)
    return _to_issue_response(issue)


async def list_issues(
    session: AsyncSession,
    repo_id: str,
    *,
    state: str = "open",
    label: str | None = None,
    milestone_id: str | None = None,
) -> list[IssueResponse]:
    """Return issues for a repo, filtered by state, label, and/or milestone.

    ``state`` may be ``"open"``, ``"closed"``, or ``"all"``.
    ``label`` filters to issues whose labels list contains the given string.
    ``milestone_id`` filters to issues assigned to that milestone.
    Results are ordered by issue number ascending.
    """
    stmt = (
        select(db.MusehubIssue)
        .options(selectinload(db.MusehubIssue.milestone))
        .where(db.MusehubIssue.repo_id == repo_id)
    )

    if state != "all":
        stmt = stmt.where(db.MusehubIssue.state == state)

    if milestone_id is not None:
        stmt = stmt.where(db.MusehubIssue.milestone_id == milestone_id)

    stmt = stmt.order_by(db.MusehubIssue.number)
    rows = (await session.execute(stmt)).scalars().all()

    results: list[IssueResponse] = []
    for r in rows:
        count = await _count_comments(session, r.issue_id)
        results.append(_to_issue_response(r, count))

    if label is not None:
        results = [r for r in results if label in r.labels]

    return results


async def get_issue(
    session: AsyncSession,
    repo_id: str,
    issue_number: int,
) -> IssueResponse | None:
    """Return a single issue by its per-repo number, or None if not found."""
    stmt = (
        select(db.MusehubIssue)
        .options(selectinload(db.MusehubIssue.milestone))
        .where(
            db.MusehubIssue.repo_id == repo_id,
            db.MusehubIssue.number == issue_number,
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    count = await _count_comments(session, row.issue_id)
    return _to_issue_response(row, count)


async def get_issue_by_id(
    session: AsyncSession,
    issue_id: str,
) -> db.MusehubIssue | None:
    """Return the raw ORM row for an issue by its UUID primary key."""
    stmt = (
        select(db.MusehubIssue)
        .options(selectinload(db.MusehubIssue.milestone))
        .where(db.MusehubIssue.issue_id == issue_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def close_issue(
    session: AsyncSession,
    repo_id: str,
    issue_number: int,
) -> IssueResponse | None:
    """Set the issue state to ``closed``. Returns None if the issue does not exist."""
    stmt = (
        select(db.MusehubIssue)
        .options(selectinload(db.MusehubIssue.milestone))
        .where(
            db.MusehubIssue.repo_id == repo_id,
            db.MusehubIssue.number == issue_number,
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.state = "closed"
    await session.flush()
    await session.refresh(row)
    logger.info("✅ Closed issue #%d for repo %s", issue_number, repo_id)
    count = await _count_comments(session, row.issue_id)
    return _to_issue_response(row, count)


async def reopen_issue(
    session: AsyncSession,
    repo_id: str,
    issue_number: int,
) -> IssueResponse | None:
    """Set the issue state back to ``open``. Returns None if the issue does not exist."""
    stmt = (
        select(db.MusehubIssue)
        .options(selectinload(db.MusehubIssue.milestone))
        .where(
            db.MusehubIssue.repo_id == repo_id,
            db.MusehubIssue.number == issue_number,
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.state = "open"
    await session.flush()
    await session.refresh(row)
    logger.info("✅ Reopened issue #%d for repo %s", issue_number, repo_id)
    count = await _count_comments(session, row.issue_id)
    return _to_issue_response(row, count)


async def update_issue(
    session: AsyncSession,
    repo_id: str,
    issue_number: int,
    *,
    title: str | None = None,
    body: str | None = None,
    labels: list[str] | None = None,
) -> IssueResponse | None:
    """Partially update an issue's title, body, and/or labels.

    Only non-None arguments are applied. Returns None if the issue is not found.
    """
    stmt = (
        select(db.MusehubIssue)
        .options(selectinload(db.MusehubIssue.milestone))
        .where(
            db.MusehubIssue.repo_id == repo_id,
            db.MusehubIssue.number == issue_number,
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    if title is not None:
        row.title = title
    if body is not None:
        row.body = body
    if labels is not None:
        row.labels = labels
    await session.flush()
    await session.refresh(row)
    count = await _count_comments(session, row.issue_id)
    return _to_issue_response(row, count)


async def assign_issue(
    session: AsyncSession,
    repo_id: str,
    issue_number: int,
    *,
    assignee: str | None,
) -> IssueResponse | None:
    """Set or clear the assignee on an issue.

    Pass ``assignee=None`` to unassign. Returns None if the issue is not found.
    """
    stmt = (
        select(db.MusehubIssue)
        .options(selectinload(db.MusehubIssue.milestone))
        .where(
            db.MusehubIssue.repo_id == repo_id,
            db.MusehubIssue.number == issue_number,
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.assignee = assignee
    await session.flush()
    await session.refresh(row)
    logger.info(
        "✅ %s issue #%d for repo %s",
        f"Assigned {assignee} to" if assignee else "Unassigned",
        issue_number,
        repo_id,
    )
    count = await _count_comments(session, row.issue_id)
    return _to_issue_response(row, count)


async def set_issue_milestone(
    session: AsyncSession,
    repo_id: str,
    issue_number: int,
    *,
    milestone_id: str | None,
) -> IssueResponse | None:
    """Assign or remove a milestone from an issue.

    Pass ``milestone_id=None`` to remove the milestone.
    Returns None if the issue is not found.
    Raises ValueError if the milestone_id does not belong to the same repo.
    """
    stmt = (
        select(db.MusehubIssue)
        .options(selectinload(db.MusehubIssue.milestone))
        .where(
            db.MusehubIssue.repo_id == repo_id,
            db.MusehubIssue.number == issue_number,
        )
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    if milestone_id is not None:
        ms_stmt = select(db.MusehubMilestone).where(
            db.MusehubMilestone.milestone_id == milestone_id,
            db.MusehubMilestone.repo_id == repo_id,
        )
        ms_row = (await session.execute(ms_stmt)).scalar_one_or_none()
        if ms_row is None:
            raise ValueError(f"Milestone {milestone_id!r} not found in repo {repo_id!r}")

    row.milestone_id = milestone_id
    await session.flush()
    await session.refresh(row)
    count = await _count_comments(session, row.issue_id)
    return _to_issue_response(row, count)


# ── Issue comment operations ───────────────────────────────────────────────────


async def create_comment(
    session: AsyncSession,
    *,
    issue_id: str,
    repo_id: str,
    body: str,
    author: str,
    parent_id: str | None = None,
) -> IssueCommentResponse:
    """Create a new comment on an issue.

    Musical context references (``track:bass``, ``section:chorus``,
    ``beats:16-24``) are parsed from ``body`` at write time and persisted in
    the ``musical_refs`` JSON column for fast retrieval.
    """
    if parent_id is not None:
        parent_stmt = select(db.MusehubIssueComment).where(
            db.MusehubIssueComment.comment_id == parent_id,
            db.MusehubIssueComment.issue_id == issue_id,
        )
        parent_row = (await session.execute(parent_stmt)).scalar_one_or_none()
        if parent_row is None:
            raise ValueError(f"Parent comment {parent_id!r} not found on issue {issue_id!r}")

    musical_refs = _parse_musical_refs(body)
    comment = db.MusehubIssueComment(
        issue_id=issue_id,
        repo_id=repo_id,
        author=author,
        body=body,
        parent_id=parent_id,
        musical_refs=musical_refs,
    )
    session.add(comment)
    await session.flush()
    await session.refresh(comment)
    logger.info("✅ Created comment %s on issue %s by %s", comment.comment_id, issue_id, author)
    return _to_comment_response(comment)


async def list_comments(
    session: AsyncSession,
    issue_id: str,
    *,
    include_deleted: bool = False,
) -> IssueCommentListResponse:
    """Return all comments on an issue, ordered chronologically.

    Deleted comments are omitted by default; pass ``include_deleted=True``
    to include them (e.g. for moderation views).
    """
    stmt = select(db.MusehubIssueComment).where(
        db.MusehubIssueComment.issue_id == issue_id
    )
    if not include_deleted:
        stmt = stmt.where(db.MusehubIssueComment.is_deleted.is_(False))
    stmt = stmt.order_by(db.MusehubIssueComment.created_at)
    rows = (await session.execute(stmt)).scalars().all()
    comments = [_to_comment_response(r) for r in rows]
    return IssueCommentListResponse(comments=comments, total=len(comments))


async def delete_comment(
    session: AsyncSession,
    comment_id: str,
    issue_id: str,
) -> bool:
    """Soft-delete a comment. Returns True if the comment existed and was deleted."""
    stmt = select(db.MusehubIssueComment).where(
        db.MusehubIssueComment.comment_id == comment_id,
        db.MusehubIssueComment.issue_id == issue_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    row.is_deleted = True
    await session.flush()
    logger.info("✅ Soft-deleted comment %s", comment_id)
    return True


# ── Milestone operations ────────────────────────────────────────────────────────


async def create_milestone(
    session: AsyncSession,
    *,
    repo_id: str,
    title: str,
    description: str = "",
    author: str = "",
    due_on: datetime | None = None,
) -> MilestoneResponse:
    """Create a new milestone for the given repo in ``open`` state."""
    due: datetime | None = due_on
    number = await _next_milestone_number(session, repo_id)
    milestone = db.MusehubMilestone(
        repo_id=repo_id,
        number=number,
        title=title,
        description=description,
        author=author,
        due_on=due,
    )
    session.add(milestone)
    await session.flush()
    await session.refresh(milestone)
    logger.info("✅ Created milestone #%d for repo %s: %s", number, repo_id, title)
    return _to_milestone_response(milestone)


async def list_milestones(
    session: AsyncSession,
    repo_id: str,
    *,
    state: str = "open",
) -> MilestoneListResponse:
    """Return milestones for a repo filtered by state.

    ``state`` may be ``"open"``, ``"closed"``, or ``"all"``.
    """
    stmt = select(db.MusehubMilestone).where(db.MusehubMilestone.repo_id == repo_id)
    if state != "all":
        stmt = stmt.where(db.MusehubMilestone.state == state)
    stmt = stmt.order_by(db.MusehubMilestone.number)
    rows = (await session.execute(stmt)).scalars().all()

    milestones: list[MilestoneResponse] = []
    for ms in rows:
        open_count_stmt = select(func.count(db.MusehubIssue.issue_id)).where(
            db.MusehubIssue.milestone_id == ms.milestone_id,
            db.MusehubIssue.state == "open",
        )
        closed_count_stmt = select(func.count(db.MusehubIssue.issue_id)).where(
            db.MusehubIssue.milestone_id == ms.milestone_id,
            db.MusehubIssue.state == "closed",
        )
        open_count: int = (await session.execute(open_count_stmt)).scalar_one()
        closed_count: int = (await session.execute(closed_count_stmt)).scalar_one()
        milestones.append(_to_milestone_response(ms, open_count, closed_count))

    return MilestoneListResponse(milestones=milestones)


async def get_milestone(
    session: AsyncSession,
    repo_id: str,
    milestone_number: int,
) -> MilestoneResponse | None:
    """Return a single milestone by its per-repo number, or None if not found."""
    stmt = select(db.MusehubMilestone).where(
        db.MusehubMilestone.repo_id == repo_id,
        db.MusehubMilestone.number == milestone_number,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    open_count_stmt = select(func.count(db.MusehubIssue.issue_id)).where(
        db.MusehubIssue.milestone_id == row.milestone_id,
        db.MusehubIssue.state == "open",
    )
    closed_count_stmt = select(func.count(db.MusehubIssue.issue_id)).where(
        db.MusehubIssue.milestone_id == row.milestone_id,
        db.MusehubIssue.state == "closed",
    )
    open_count: int = (await session.execute(open_count_stmt)).scalar_one()
    closed_count: int = (await session.execute(closed_count_stmt)).scalar_one()
    return _to_milestone_response(row, open_count, closed_count)
