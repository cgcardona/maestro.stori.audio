"""Fixtures for muse_cli tests requiring an in-memory database."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from maestro.db.database import Base

# Register MuseCli* models with Base.metadata before create_all is called.
import maestro.muse_cli.models  # noqa: F401, E402


@pytest_asyncio.fixture
async def muse_cli_db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with muse_cli (and all other) tables.

    Isolated per test: tables are created fresh and dropped on teardown.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
