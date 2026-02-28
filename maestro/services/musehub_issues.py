"""Muse Hub issue persistence adapter — single point of DB access for issues.

This module is the ONLY place that touches the ``musehub_issues`` table.
Route handlers delegate here; no business logic lives in routes.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import IssueResponse

logger = logging.getLogger(__name__)


def _to_issue_response(row: db.MusehubIssue) -> IssueResponse:
    return IssueResponse(
        issue_id=row.issue_id,
        number=row.number,
        title=row.title,
        body=row.body,
        state=row.state,
        labels=list(row.labels or []),
        author=row.author,
        created_at=row.created_at,
    )


async def _next_issue_number(session: AsyncSession, repo_id: str) -> int:
    """Return the next sequential issue number for the given repo (1-based)."""
    stmt = select(func.max(db.MusehubIssue.number)).where(
        db.MusehubIssue.repo_id == repo_id
    )
    current_max: int | None = (await session.execute(stmt)).scalar_one_or_none()
    return (current_max or 0) + 1


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
) -> list[IssueResponse]:
    """Return issues for a repo, filtered by state and/or label.

    ``state`` may be ``"open"``, ``"closed"``, or ``"all"``.
    ``label`` filters to issues whose labels list contains the given string.
    Results are ordered by issue number ascending.
    """
    stmt = select(db.MusehubIssue).where(db.MusehubIssue.repo_id == repo_id)

    if state != "all":
        stmt = stmt.where(db.MusehubIssue.state == state)

    stmt = stmt.order_by(db.MusehubIssue.number)
    rows = (await session.execute(stmt)).scalars().all()

    results = [_to_issue_response(r) for r in rows]

    if label is not None:
        results = [r for r in results if label in r.labels]

    return results


async def get_issue(
    session: AsyncSession,
    repo_id: str,
    issue_number: int,
) -> IssueResponse | None:
    """Return a single issue by its per-repo number, or None if not found."""
    stmt = select(db.MusehubIssue).where(
        db.MusehubIssue.repo_id == repo_id,
        db.MusehubIssue.number == issue_number,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return _to_issue_response(row)


async def close_issue(
    session: AsyncSession,
    repo_id: str,
    issue_number: int,
) -> IssueResponse | None:
    """Set the issue state to ``closed``. Returns None if the issue does not exist."""
    stmt = select(db.MusehubIssue).where(
        db.MusehubIssue.repo_id == repo_id,
        db.MusehubIssue.number == issue_number,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.state = "closed"
    await session.flush()
    await session.refresh(row)
    logger.info("✅ Closed issue #%d for repo %s", issue_number, repo_id)
    return _to_issue_response(row)
