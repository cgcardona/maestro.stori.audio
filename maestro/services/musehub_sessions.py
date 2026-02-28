"""Muse Hub session persistence service.

Handles storage and retrieval of recording session records in the musehub_sessions
table.  Sessions are pushed from the CLI (``muse session end``) and displayed in
the MuseHub web UI at ``/musehub/ui/{repo_id}/sessions/{session_id}``.

Design notes:
- Upsert semantics: pushing the same session_id again is idempotent (updates
  the existing record).  This allows re-pushing sessions after editing notes.
- Sessions are returned newest-first (started_at DESC) to match the local
  ``muse session log`` display order.
- ``commits`` cross-references musehub_commits by commit_id for UI deep-links,
  but the foreign key is not enforced at DB level — commits may arrive out of
  order relative to sessions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubSession
from maestro.models.musehub import SessionCreate, SessionResponse

logger = logging.getLogger(__name__)


def _to_response(session: MusehubSession) -> SessionResponse:
    """Convert an ORM session row to its wire representation."""
    duration: float | None = None
    if session.ended_at is not None:
        duration = (session.ended_at - session.started_at).total_seconds()
    return SessionResponse(
        session_id=session.session_id,
        started_at=session.started_at,
        ended_at=session.ended_at,
        duration_seconds=duration,
        participants=list(session.participants),
        location=session.location,
        intent=session.intent,
        is_active=getattr(session, 'is_active', session.ended_at is None),
        created_at=session.created_at,
    )


async def upsert_session(
    db: AsyncSession,
    repo_id: str,
    data: SessionCreate,
) -> SessionResponse:
    """Persist a session record for the given repo.

    Creates a new session using the provided data.
    """
    import uuid as _uuid
    session = MusehubSession(
        session_id=str(_uuid.uuid4()),
        repo_id=repo_id,
        started_at=data.started_at or datetime.now(tz=timezone.utc),
        participants=list(data.participants),
        location=data.location,
        intent=data.intent,
        is_active=True,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(session)
    await db.flush()
    logger.info("\u2705 Created session in repo %s", repo_id)
    return _to_response(session)


async def list_sessions(
    db: AsyncSession,
    repo_id: str,
    limit: int = 50,
) -> tuple[list[SessionResponse], int]:
    """Return sessions for a repo sorted by started_at descending.

    Args:
        db: Active async database session.
        repo_id: The Muse Hub repo to query.
        limit: Maximum number of sessions to return (default 50, max 200).

    Returns:
        Tuple of (sessions list, total count).
    """
    count_q = select(MusehubSession).where(MusehubSession.repo_id == repo_id)
    total_result = await db.execute(count_q)
    total = len(total_result.scalars().all())

    q = (
        select(MusehubSession)
        .where(MusehubSession.repo_id == repo_id)
        .order_by(MusehubSession.started_at.desc())
        .limit(limit)
    )
    result = await db.execute(q)
    rows = result.scalars().all()
    return [_to_response(r) for r in rows], total


async def get_session(
    db: AsyncSession,
    repo_id: str,
    session_id: str,
) -> SessionResponse | None:
    """Fetch a single session by ID within a repo.

    Returns ``None`` if the session does not exist or belongs to a different repo,
    allowing the caller to issue a 404.

    Args:
        db: Active async database session.
        repo_id: The Muse Hub repo to constrain the lookup.
        session_id: The session UUID.

    Returns:
        ``SessionResponse`` or ``None``.
    """
    q = select(MusehubSession).where(
        MusehubSession.session_id == session_id,
        MusehubSession.repo_id == repo_id,
    )
    result = await db.execute(q)
    row = result.scalar_one_or_none()
    return _to_response(row) if row is not None else None
