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
    return SessionResponse(
        session_id=session.session_id,
        repo_id=session.repo_id,
        schema_version=session.schema_version,
        started_at=session.started_at,
        ended_at=session.ended_at,
        participants=list(session.participants),
        location=session.location,
        intent=session.intent,
        commits=list(session.commits),
        notes=session.notes,
        created_at=session.created_at,
    )


async def upsert_session(
    db: AsyncSession,
    repo_id: str,
    data: SessionCreate,
) -> SessionResponse:
    """Persist a session record for the given repo (insert or update).

    If a session with the same ``session_id`` already exists in ``repo_id``,
    all mutable fields are overwritten.  This makes re-push idempotent — a
    session whose notes were edited after the initial push will always reflect
    the latest state.

    Args:
        db: Active async database session.
        repo_id: The target Muse Hub repo ID.
        data: Session payload from the CLI push.

    Returns:
        The persisted session as a ``SessionResponse``.
    """
    _existing_q = select(MusehubSession).where(
        MusehubSession.session_id == data.session_id,
        MusehubSession.repo_id == repo_id,
    )
    existing = (await db.execute(_existing_q)).scalar_one_or_none()
    if existing is not None:
        existing.schema_version = data.schema_version
        existing.started_at = data.started_at
        existing.ended_at = data.ended_at
        existing.participants = list(data.participants)
        existing.location = data.location
        existing.intent = data.intent
        existing.commits = list(data.commits)
        existing.notes = data.notes
        logger.info("✅ Updated session %s in repo %s", data.session_id, repo_id)
        return _to_response(existing)

    session = MusehubSession(
        session_id=data.session_id,
        repo_id=repo_id,
        schema_version=data.schema_version,
        started_at=data.started_at,
        ended_at=data.ended_at,
        participants=list(data.participants),
        location=data.location,
        intent=data.intent,
        commits=list(data.commits),
        notes=data.notes,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(session)
    await db.flush()
    logger.info("✅ Created session %s in repo %s", data.session_id, repo_id)
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
