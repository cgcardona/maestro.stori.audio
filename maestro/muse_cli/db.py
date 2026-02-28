"""Async database helpers for the Muse CLI commit pipeline.

Provides:
- ``open_session()`` — async context manager that opens and commits a
  standalone AsyncSession (for use in the CLI, outside FastAPI DI).
- CRUD helpers called by ``commands/commit.py``, ``commands/meter.py``,
  and ``commands/read_tree.py``.

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


async def get_commit_snapshot_manifest(
    session: AsyncSession, commit_id: str
) -> dict[str, str] | None:
    """Return the file manifest for the snapshot attached to *commit_id*, or None.

    Fetches the :class:`MuseCliCommit` row by primary key, then loads its
    :class:`MuseCliSnapshot` to retrieve the manifest.  Returns ``None``
    when either row is missing (which should not occur in a consistent DB).
    """
    commit = await session.get(MuseCliCommit, commit_id)
    if commit is None:
        logger.warning("⚠️ Commit %s not found in DB", commit_id[:8])
        return None
    snapshot = await session.get(MuseCliSnapshot, commit.snapshot_id)
    if snapshot is None:
        logger.warning(
            "⚠️ Snapshot %s referenced by commit %s not found in DB",
            commit.snapshot_id[:8],
            commit_id[:8],
        )
        return None
    return dict(snapshot.manifest)


async def resolve_commit_ref(
    session: AsyncSession,
    repo_id: str,
    branch: str,
    ref: str | None,
) -> MuseCliCommit | None:
    """Resolve a commit reference to a ``MuseCliCommit`` row.

    *ref* may be:

    - ``None`` / ``"HEAD"`` — returns the most recent commit on *branch*.
    - A full or abbreviated commit SHA — looks up by exact or prefix match.

    Returns ``None`` when no matching commit is found.
    """
    if ref is None or ref.upper() == "HEAD":
        result = await session.execute(
            select(MuseCliCommit)
            .where(MuseCliCommit.repo_id == repo_id, MuseCliCommit.branch == branch)
            .order_by(MuseCliCommit.committed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # Try exact match first
    commit = await session.get(MuseCliCommit, ref)
    if commit is not None:
        return commit

    # Abbreviated SHA prefix match (scan required — acceptable for CLI use)
    result = await session.execute(
        select(MuseCliCommit).where(
            MuseCliCommit.repo_id == repo_id,
            MuseCliCommit.commit_id.startswith(ref),
        )
    )
    return result.scalars().first()


async def set_commit_tempo_bpm(
    session: AsyncSession,
    commit_id: str,
    bpm: float,
) -> MuseCliCommit | None:
    """Annotate *commit_id* with an explicit BPM in its ``metadata`` JSON blob.

    Merges into the existing metadata dict so other annotations are preserved.
    Returns the updated ``MuseCliCommit`` row, or ``None`` when not found.
    """
    commit = await session.get(MuseCliCommit, commit_id)
    if commit is None:
        return None
    existing: dict[str, object] = dict(commit.commit_metadata or {})
    existing["tempo_bpm"] = bpm
    commit.commit_metadata = existing
    session.add(commit)
    logger.debug("✅ Set tempo %.2f BPM on commit %s", bpm, commit_id[:8])
    return commit


async def get_commits_for_branch(
    session: AsyncSession,
    repo_id: str,
    branch: str,
) -> list[MuseCliCommit]:
    """Return all commits on *branch* for *repo_id*, newest first.

    Used by ``muse push`` to collect commits since the last known remote head.
    Ordering is newest-first so callers can slice from the front to get the
    delta since a known commit.
    """
    result = await session.execute(
        select(MuseCliCommit)
        .where(MuseCliCommit.repo_id == repo_id, MuseCliCommit.branch == branch)
        .order_by(MuseCliCommit.committed_at.desc())
    )
    return list(result.scalars().all())


async def get_all_object_ids(
    session: AsyncSession,
    repo_id: str,
) -> list[str]:
    """Return all object IDs referenced by any snapshot in this repo.

    Used by ``muse pull`` to tell the Hub which objects we already have so
    the Hub only sends the missing ones.
    """
    from sqlalchemy import distinct

    result = await session.execute(
        select(MuseCliCommit.snapshot_id).where(
            MuseCliCommit.repo_id == repo_id
        )
    )
    snapshot_ids = [row for row in result.scalars().all()]
    if not snapshot_ids:
        return []

    # Collect all object_ids from all known snapshots
    object_ids: set[str] = set()
    for snap_id in snapshot_ids:
        snapshot = await session.get(MuseCliSnapshot, snap_id)
        if snapshot is not None and snapshot.manifest:
            object_ids.update(snapshot.manifest.values())

    return sorted(object_ids)


async def store_pulled_commit(
    session: AsyncSession,
    commit_data: dict[str, object],
) -> bool:
    """Persist a commit received from the Hub into local Postgres.

    Idempotent — silently skips if the commit already exists.  Returns
    ``True`` if the row was newly inserted, ``False`` if it already existed.

    The *commit_data* dict must contain the keys defined in
    :class:`~maestro.muse_cli.hub_client.PullCommitPayload`.
    """
    import datetime

    commit_id = str(commit_data.get("commit_id", ""))
    if not commit_id:
        logger.warning("⚠️ store_pulled_commit: missing commit_id — skipping")
        return False

    existing = await session.get(MuseCliCommit, commit_id)
    if existing is not None:
        logger.debug("⚠️ Pulled commit %s already exists — skipped", commit_id[:8])
        return False

    snapshot_id = str(commit_data.get("snapshot_id", ""))
    branch = str(commit_data.get("branch", ""))
    message = str(commit_data.get("message", ""))
    author = str(commit_data.get("author", ""))
    committed_at_raw = str(commit_data.get("committed_at", ""))
    parent_commit_id_raw = commit_data.get("parent_commit_id")
    parent_commit_id: str | None = (
        str(parent_commit_id_raw) if parent_commit_id_raw is not None else None
    )
    metadata_raw = commit_data.get("metadata")
    commit_metadata: dict[str, object] | None = (
        dict(metadata_raw)
        if isinstance(metadata_raw, dict)
        else None
    )

    try:
        committed_at = datetime.datetime.fromisoformat(committed_at_raw)
    except ValueError:
        committed_at = datetime.datetime.now(datetime.timezone.utc)

    # Ensure the snapshot row exists (as a stub if not present — objects are
    # content-addressed so the manifest may arrive separately or be empty for
    # Hub-side storage).
    existing_snap = await session.get(MuseCliSnapshot, snapshot_id)
    if existing_snap is None:
        stub_snap = MuseCliSnapshot(snapshot_id=snapshot_id, manifest={})
        session.add(stub_snap)
        await session.flush()

    new_commit = MuseCliCommit(
        commit_id=commit_id,
        repo_id=str(commit_data.get("repo_id", "")),
        branch=branch,
        parent_commit_id=parent_commit_id,
        snapshot_id=snapshot_id,
        message=message,
        author=author,
        committed_at=committed_at,
        commit_metadata=commit_metadata,
    )
    session.add(new_commit)
    logger.debug("✅ Stored pulled commit %s branch=%r", commit_id[:8], branch)
    return True


async def store_pulled_object(
    session: AsyncSession,
    object_data: dict[str, object],
) -> bool:
    """Persist an object descriptor received from the Hub into local Postgres.

    Idempotent — silently skips if the object already exists.  Returns
    ``True`` if the row was newly inserted, ``False`` if it already existed.
    """
    object_id = str(object_data.get("object_id", ""))
    if not object_id:
        logger.warning("⚠️ store_pulled_object: missing object_id — skipping")
        return False

    size_raw = object_data.get("size_bytes", 0)
    size_bytes = int(size_raw) if isinstance(size_raw, (int, float)) else 0

    existing = await session.get(MuseCliObject, object_id)
    if existing is not None:
        logger.debug("⚠️ Pulled object %s already exists — skipped", object_id[:8])
        return False

    session.add(MuseCliObject(object_id=object_id, size_bytes=size_bytes))
    logger.debug("✅ Stored pulled object %s (%d bytes)", object_id[:8], size_bytes)
    return True


async def find_commits_by_prefix(
    session: AsyncSession,
    prefix: str,
) -> list[MuseCliCommit]:
    """Return all commits whose ``commit_id`` starts with *prefix*.

    Used by commands that accept a short commit ID (e.g. ``muse export``,
    ``muse open``, ``muse play``) to resolve a user-supplied prefix to a
    full commit record before DB lookups.
    """
    result = await session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id.startswith(prefix))
    )
    return list(result.scalars().all())


async def get_head_snapshot_manifest(
    session: AsyncSession, repo_id: str, branch: str
) -> dict[str, str] | None:
    """Return the file manifest of the most recent commit on *branch*, or None.

    Fetches the latest commit's ``snapshot_id`` and then loads the
    corresponding :class:`MuseCliSnapshot` row to retrieve its manifest.
    Returns ``None`` when the branch has no commits or the snapshot row is
    missing (which should not occur in a consistent database).
    """
    snapshot_id = await get_head_snapshot_id(session, repo_id, branch)
    if snapshot_id is None:
        return None
    snapshot = await session.get(MuseCliSnapshot, snapshot_id)
    if snapshot is None:
        logger.warning("⚠️ Snapshot %s referenced by HEAD not found in DB", snapshot_id[:8])
        return None
    return dict(snapshot.manifest)


async def get_commit_extra_metadata(
    session: AsyncSession, commit_id: str
) -> dict[str, object] | None:
    """Return the ``commit_metadata`` JSON blob for *commit_id*, or None.

    Returns ``None`` when the commit does not exist or when no metadata has
    been stored yet (the column is nullable).
    """
    commit = await session.get(MuseCliCommit, commit_id)
    if commit is None:
        return None
    return dict(commit.commit_metadata) if commit.commit_metadata else None


async def set_commit_extra_metadata_key(
    session: AsyncSession,
    commit_id: str,
    key: str,
    value: object,
) -> bool:
    """Set a single key in the ``commit_metadata`` blob for *commit_id*.

    Merges *key* into the existing metadata dict (creating it if absent).
    Returns ``True`` on success, ``False`` when *commit_id* is not found.

    The session must be committed by the caller (``open_session()`` commits
    on clean exit).
    """
    commit = await session.get(MuseCliCommit, commit_id)
    if commit is None:
        logger.warning("⚠️ Commit %s not found — cannot set metadata", commit_id[:8])
        return False
    existing: dict[str, object] = dict(commit.commit_metadata) if commit.commit_metadata else {}
    existing[key] = value
    commit.commit_metadata = existing
    # Mark the column as modified so SQLAlchemy flushes the JSON change.
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(commit, "commit_metadata")
    logger.debug("✅ Set %s=%r on commit %s", key, value, commit_id[:8])
    return True
