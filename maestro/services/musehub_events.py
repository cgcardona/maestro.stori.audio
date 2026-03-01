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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    ActivityEventResponse,
    ActivityFeedResponse,
    UserActivityEventItem,
    UserActivityFeedResponse,
)

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


# ---------------------------------------------------------------------------
# User public activity feed
# ---------------------------------------------------------------------------

# Maps public API type vocabulary → internal DB event_type values.
# Types with no DB equivalent (star, fork, comment) map to empty lists and
# will always return an empty result when used as a filter.
_USER_TYPE_TO_DB_TYPES: dict[str, list[str]] = {
    "push": ["commit_pushed", "branch_created", "branch_deleted"],
    "pull_request": ["pr_opened", "pr_merged", "pr_closed"],
    "issue": ["issue_opened", "issue_closed"],
    "release": ["tag_pushed"],
    "star": [],
    "fork": [],
    "comment": [],
}

# Maps DB event_type → public API type vocabulary for the response payload.
_DB_TYPE_TO_USER_TYPE: dict[str, str] = {
    "commit_pushed": "push",
    "branch_created": "push",
    "branch_deleted": "push",
    "pr_opened": "pull_request",
    "pr_merged": "pull_request",
    "pr_closed": "pull_request",
    "issue_opened": "issue",
    "issue_closed": "issue",
    "tag_pushed": "release",
    "session_started": "push",
    "session_ended": "push",
}


def _to_user_activity_item(
    event: db.MusehubEvent,
    repo: db.MusehubRepo,
) -> UserActivityEventItem:
    """Convert an ORM event row + its repo into the public user activity response shape."""
    return UserActivityEventItem(
        id=event.event_id,
        type=_DB_TYPE_TO_USER_TYPE.get(event.event_type, event.event_type),
        actor=event.actor,
        repo=f"{repo.owner}/{repo.slug}",
        payload=dict(event.event_metadata),
        created_at=event.created_at,
    )


async def list_user_activity(
    session: AsyncSession,
    username: str,
    *,
    caller_user_id: str | None = None,
    type_filter: str | None = None,
    limit: int = 30,
    before_id: str | None = None,
) -> UserActivityFeedResponse:
    """Return a cursor-paginated public activity feed for ``username``.

    Events are drawn from the ``musehub_events`` table, filtered to repos the
    caller is allowed to see:
    - Public repos: always visible.
    - Private repos: visible only when ``caller_user_id`` matches the repo owner.

    ``type_filter`` accepts the public API vocabulary (push, pull_request, issue,
    release, star, fork, comment) and maps it to the DB's internal event_type
    values.  Types without DB equivalents (star, fork, comment) always return
    an empty feed.

    Cursor pagination: pass the ``next_cursor`` value from a previous response
    as ``before_id`` to fetch the next page.  Events are returned newest-first;
    ``before_id`` is the event UUID of the *oldest* event on the last page, and
    this function returns events created *before* that event's timestamp.
    """
    limit = max(1, min(limit, 100))

    # Resolve the db event_type list from the public API filter.
    db_types: list[str] | None = None
    if type_filter is not None:
        db_types = _USER_TYPE_TO_DB_TYPES.get(type_filter, [])
        if not db_types:
            # Type has no DB equivalent (star/fork/comment) or is unknown.
            return UserActivityFeedResponse(
                events=[], next_cursor=None, type_filter=type_filter
            )

    # Resolve the cursor anchor (before_id → created_at timestamp).
    cursor_dt = None
    cursor_event_id: str | None = None
    if before_id is not None:
        anchor = (
            await session.execute(
                select(db.MusehubEvent).where(db.MusehubEvent.event_id == before_id)
            )
        ).scalar_one_or_none()
        if anchor is not None:
            cursor_dt = anchor.created_at
            cursor_event_id = anchor.event_id

    # Build the query: join events → repos, filter by actor and visibility.
    # Public repos are always visible; private repos are visible only to their owner.
    if caller_user_id is not None:
        visibility_filter = or_(
            db.MusehubRepo.visibility == "public",
            db.MusehubRepo.owner_user_id == caller_user_id,
        )
    else:
        visibility_filter = db.MusehubRepo.visibility == "public"

    stmt = (
        select(db.MusehubEvent, db.MusehubRepo)
        .join(db.MusehubRepo, db.MusehubEvent.repo_id == db.MusehubRepo.repo_id)
        .where(
            and_(
                db.MusehubEvent.actor == username,
                visibility_filter,
            )
        )
        .order_by(db.MusehubEvent.created_at.desc(), db.MusehubEvent.event_id.desc())
        .limit(limit + 1)  # fetch one extra to detect if there is a next page
    )

    if db_types is not None:
        stmt = stmt.where(db.MusehubEvent.event_type.in_(db_types))

    if cursor_dt is not None:
        # Cursor pagination: events strictly before the anchor timestamp,
        # or same timestamp but earlier event_id (deterministic ordering).
        stmt = stmt.where(
            or_(
                db.MusehubEvent.created_at < cursor_dt,
                and_(
                    db.MusehubEvent.created_at == cursor_dt,
                    db.MusehubEvent.event_id < (cursor_event_id or ""),
                ),
            )
        )

    rows = (await session.execute(stmt)).all()

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    items = [_to_user_activity_item(ev, repo) for ev, repo in page_rows]
    next_cursor = items[-1].id if has_more and items else None

    logger.debug(
        "✅ User activity feed username=%s count=%d has_more=%s",
        username,
        len(items),
        has_more,
    )
    return UserActivityFeedResponse(
        events=items,
        next_cursor=next_cursor,
        type_filter=type_filter,
    )
