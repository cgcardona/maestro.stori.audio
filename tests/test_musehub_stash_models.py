"""Unit tests for MusehubStash and MusehubStashEntry ORM models.

Verifies:
- MusehubStash instantiation: id auto-generated, is_applied=False by default,
  created_at populated, applied_at None on creation.
- MusehubStashEntry instantiation: id auto-generated, position stored correctly.
- Relationship: MusehubStash.entries returns entries ordered by position.
- applied_at lifecycle: None on creation, can be set to a UTC datetime.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from maestro.db.database import Base
from maestro.db import models as _user_models  # noqa: F401 — register maestro_users table
from maestro.db import musehub_models as _hub_models  # noqa: F401 — register musehub_repos table
from maestro.db.musehub_stash_models import MusehubStash, MusehubStashEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite async session.

    SQLite does not enforce foreign-key constraints by default, so we can
    insert stash records without needing real parent repo/user rows.  All
    tables registered on Base.metadata are created so the schema is
    consistent across the whole suite.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def _repo_id() -> str:
    return str(uuid.uuid4())


def _user_id() -> str:
    return str(uuid.uuid4())


def _make_stash(
    repo_id: str | None = None,
    user_id: str | None = None,
    branch: str = "main",
    message: str | None = None,
) -> MusehubStash:
    """Build a MusehubStash without committing it to any session."""
    return MusehubStash(
        repo_id=repo_id or _repo_id(),
        user_id=user_id or _user_id(),
        branch=branch,
        message=message,
    )


# ---------------------------------------------------------------------------
# MusehubStash — instantiation defaults
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_defaults_id_generated(async_session: AsyncSession) -> None:
    """id is auto-generated as a non-empty UUID string on flush."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()
    assert stash.id is not None
    assert len(stash.id) == 36  # UUID canonical form: 8-4-4-4-12


@pytest.mark.anyio
async def test_stash_defaults_is_applied_false(async_session: AsyncSession) -> None:
    """is_applied is False by default."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()
    assert stash.is_applied is False


@pytest.mark.anyio
async def test_stash_defaults_created_at_set(async_session: AsyncSession) -> None:
    """created_at is populated automatically on flush and is not None."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()
    assert stash.created_at is not None
    assert isinstance(stash.created_at, datetime)


@pytest.mark.anyio
async def test_stash_defaults_applied_at_none(async_session: AsyncSession) -> None:
    """applied_at is None on creation — stash has not been popped yet."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()
    assert stash.applied_at is None


@pytest.mark.anyio
async def test_stash_optional_message_stored(async_session: AsyncSession) -> None:
    """Optional message field is persisted when provided."""
    stash = _make_stash(message="WIP: rough intro")
    async_session.add(stash)
    await async_session.flush()
    assert stash.message == "WIP: rough intro"


# ---------------------------------------------------------------------------
# MusehubStash — applied_at lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_applied_at_can_be_set(async_session: AsyncSession) -> None:
    """applied_at can be set to a UTC datetime after creation."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()

    applied_ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stash.applied_at = applied_ts
    stash.is_applied = True
    await async_session.flush()

    assert stash.applied_at == applied_ts
    assert stash.is_applied is True


# ---------------------------------------------------------------------------
# MusehubStashEntry — instantiation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entry_id_generated(async_session: AsyncSession) -> None:
    """MusehubStashEntry id is auto-generated as a UUID string on flush."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()

    entry = MusehubStashEntry(
        stash_id=stash.id,
        path="tracks/bass.mid",
        object_id="sha256:abc123",
        position=0,
    )
    async_session.add(entry)
    await async_session.flush()

    assert entry.id is not None
    assert len(entry.id) == 36


@pytest.mark.anyio
async def test_entry_position_stored_correctly(async_session: AsyncSession) -> None:
    """position field is persisted exactly as provided."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()

    entry = MusehubStashEntry(
        stash_id=stash.id,
        path="tracks/keys.mid",
        object_id="sha256:deadbeef",
        position=7,
    )
    async_session.add(entry)
    await async_session.flush()

    assert entry.position == 7


# ---------------------------------------------------------------------------
# Relationship: entries ordered by position
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_entries_ordered_by_position(async_session: AsyncSession) -> None:
    """MusehubStash.entries returns entries in ascending position order."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()
    stash_id = stash.id

    # Insert in reverse order to ensure ordering is by position, not insert order.
    for pos in [2, 0, 1]:
        entry = MusehubStashEntry(
            stash_id=stash_id,
            path=f"tracks/track-{pos}.mid",
            object_id=f"sha256:{pos:04x}",
            position=pos,
        )
        async_session.add(entry)

    await async_session.commit()

    result = await async_session.execute(
        select(MusehubStash)
        .where(MusehubStash.id == stash_id)
        .options(selectinload(MusehubStash.entries))
    )
    loaded = result.scalar_one()
    positions = [e.position for e in loaded.entries]
    assert positions == [0, 1, 2]


@pytest.mark.anyio
async def test_stash_entries_empty_on_creation(async_session: AsyncSession) -> None:
    """A freshly created stash has an empty entries list."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.commit()
    stash_id = stash.id

    result = await async_session.execute(
        select(MusehubStash)
        .where(MusehubStash.id == stash_id)
        .options(selectinload(MusehubStash.entries))
    )
    loaded = result.scalar_one()
    assert loaded.entries == []


@pytest.mark.anyio
async def test_stash_entries_path_and_object_id_stored(async_session: AsyncSession) -> None:
    """path and object_id are persisted exactly as provided on each entry."""
    stash = _make_stash()
    async_session.add(stash)
    await async_session.flush()
    stash_id = stash.id

    entry = MusehubStashEntry(
        stash_id=stash_id,
        path="tracks/lead.mid",
        object_id="sha256:cafebabe",
        position=0,
    )
    async_session.add(entry)
    await async_session.commit()

    result = await async_session.execute(
        select(MusehubStash)
        .where(MusehubStash.id == stash_id)
        .options(selectinload(MusehubStash.entries))
    )
    loaded = result.scalar_one()
    assert len(loaded.entries) == 1
    persisted = loaded.entries[0]
    assert persisted.path == "tracks/lead.mid"
    assert persisted.object_id == "sha256:cafebabe"
