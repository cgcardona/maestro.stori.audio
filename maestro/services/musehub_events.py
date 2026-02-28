"""Muse Hub activity event service — single point of access for the event stream.

This module is the ONLY place that touches the ``musehub_events`` table.
Route handlers record events atomically alongside their primary action (e.g.
a commit push records both the commit row and a ``commit_pushed`` event in the
same DB transaction).

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.

Event type vocabulary
---------------------
commit_pushed   — a commit was pushed to the repo
pr_opened       — a pull request was opened
pr_merged       — a pull request was merged
pr_closed       — a pull request was closed without merge
issue_opened    — an issue was opened
issue_closed    — an issue was closed
branch_created  — a new branch was created
branch_deleted  — a branch was deleted
tag_pushed      — a tag was pushed
session_started — a recording session was started
session_ended   — a recording session was ended
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import ActivityEventResponse, ActivityFeedResponse

logger = logging.getLogger(__name__)

# Recognised event types — validated on record to prevent silent typos.
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "commit_pushed",
        "pr_opened",
        "pr_merged",
        "pr_closed",
        "issue_opened",
        "issue_closed",
        "branch_created",
        "branch_deleted",
        "tag_pushed",
        "session_started",
        "session_ended",
    }
)


def _to_response(row: db.MusehubEvent) -> ActivityEventResponse:
    return ActivityEventResponse(
        event_id=row.event_id,
        repo_id=row.repo_id,
        event_type=row.event_type,
        actor=row.actor,
        description=row.description,
        metadata=dict(row.event_metadata),
        created_at=row.created_at,
    )


async def record_event(
    session: AsyncSession,
    *,
    repo_id: str,
    event_type: str,
    actor: str,
    description: str,
    metadata: dict[str, object] | None = None,
) -> ActivityEventResponse:
    """Append a new event row to the activity stream for ``repo_id``.

    Call this inside the same DB transaction as the primary action so the event
    is committed atomically with the action it describes.  The caller is
    responsible for calling ``await session.commit()`` after the transaction.

    ``event_type`` must be one of ``KNOWN_EVENT_TYPES``; an unknown type is
    logged as a warning and stored anyway (no hard failure — append-only safety
    beats strict validation at the DB layer).
    """
    if event_type not in KNOWN_EVENT_TYPES:
        logger.warning("⚠️  Unknown event_type %r recorded for repo %s", event_type, repo_id)

    row = db.MusehubEvent(
        repo_id=repo_id,
        event_type=event_type,
        actor=actor,
        description=description,
        event_metadata=metadata or {},
    )
    session.add(row)
    await session.flush()  # populate event_id without committing
    logger.debug("✅ Queued event %s (%s) for repo %s", row.event_id, event_type, repo_id)
    return _to_response(row)


async def list_events(
    session: AsyncSession,
    repo_id: str,
    *,
    event_type: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> ActivityFeedResponse:
    """Return a paginated, newest-first slice of the activity feed for ``repo_id``.

    ``event_type`` filters to a single event type when provided; pass ``None``
    to include all event types.  ``page`` is 1-indexed.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    base_where = db.MusehubEvent.repo_id == repo_id
    if event_type is not None:
        type_filter = db.MusehubEvent.event_type == event_type
    else:
        type_filter = None

    # Count total matching rows
    count_stmt = select(func.count()).select_from(db.MusehubEvent).where(base_where)
    if type_filter is not None:
        count_stmt = count_stmt.where(type_filter)
    total: int = (await session.execute(count_stmt)).scalar_one()

    # Fetch the requested page (newest first)
    page_stmt = (
        select(db.MusehubEvent)
        .where(base_where)
        .order_by(db.MusehubEvent.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if type_filter is not None:
        page_stmt = page_stmt.where(type_filter)

    rows = (await session.execute(page_stmt)).scalars().all()

    return ActivityFeedResponse(
        events=[_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        event_type_filter=event_type,
    )
