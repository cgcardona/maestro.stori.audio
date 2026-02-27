"""Async database helpers for the Muse CLI commit pipeline.

Provides:
- ``open_session()`` — async context manager that opens and commits a
  standalone AsyncSession (for use in the CLI, outside FastAPI DI).
- CRUD helpers called by ``commands/commit.py``.

The session factory created by ``open_session()`` reads DATABASE_URL
from ``maestro.config.settings`` — the same env var used by the main
FastAPI app.  Inside Docker all containers have this set; outside Docker
users need to export it before running ``muse commit``.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from maestro.config import settings
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def open_session(url: str | None = None) -> AsyncGenerator[AsyncSession, None]:
    """Open a standalone async DB session suitable for CLI commands.

    Commits on clean exit, rolls back on exception.  Disposes the engine
    on exit so the process does not linger with open connections.

    ``url`` defaults to ``settings.database_url`` which reads the
    ``DATABASE_URL`` env var.  Pass an explicit URL in tests.
    """
    db_url = url or settings.database_url
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Run inside Docker or export DATABASE_URL before calling muse commit."
        )
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        await engine.dispose()


async def upsert_object(session: AsyncSession, object_id: str, size_bytes: int) -> None:
    """Insert a MuseCliObject row, ignoring duplicates (content-addressed)."""
    existing = await session.get(MuseCliObject, object_id)
    if existing is None:
        session.add(MuseCliObject(object_id=object_id, size_bytes=size_bytes))
        logger.debug("✅ New object %s (%d bytes)", object_id[:8], size_bytes)
    else:
        logger.debug("⚠️ Object %s already exists — skipped", object_id[:8])


async def upsert_snapshot(
    session: AsyncSession, manifest: dict[str, str], snapshot_id: str
) -> MuseCliSnapshot:
    """Insert a MuseCliSnapshot row, ignoring duplicates."""
    existing = await session.get(MuseCliSnapshot, snapshot_id)
    if existing is not None:
        logger.debug("⚠️ Snapshot %s already exists — skipped", snapshot_id[:8])
        return existing
    snap = MuseCliSnapshot(snapshot_id=snapshot_id, manifest=manifest)
    session.add(snap)
    logger.debug("✅ New snapshot %s (%d files)", snapshot_id[:8], len(manifest))
    return snap


async def insert_commit(session: AsyncSession, commit: MuseCliCommit) -> None:
    """Insert a new MuseCliCommit row.

    Does NOT ignore duplicates — calling this twice with the same commit_id
    is a programming error and will raise an IntegrityError.
    """
    session.add(commit)
    logger.debug("✅ New commit %s branch=%r", commit.commit_id[:8], commit.branch)


async def get_head_snapshot_id(
    session: AsyncSession, repo_id: str, branch: str
) -> str | None:
    """Return the snapshot_id of the most recent commit on *branch*, or None."""
    result = await session.execute(
        select(MuseCliCommit.snapshot_id)
        .where(MuseCliCommit.repo_id == repo_id, MuseCliCommit.branch == branch)
        .order_by(MuseCliCommit.committed_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row
