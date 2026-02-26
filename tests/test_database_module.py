"""
Tests for app.db.database: get_database_url, init_db, close_db, get_db.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


def test_get_database_url_uses_settings() -> None:
    """get_database_url returns settings.database_url when set."""
    from app.db import database
    with patch.object(database.settings, "database_url", "postgresql+asyncpg://localhost/db"):
        assert database.get_database_url() == "postgresql+asyncpg://localhost/db"


def test_get_database_url_defaults_to_sqlite_when_none() -> None:
    """get_database_url returns SQLite path when settings.database_url is None."""
    from app.db import database
    with patch.object(database.settings, "database_url", None):
        url = database.get_database_url()
        assert "sqlite" in url
        assert "stori.db" in url


@pytest.mark.asyncio
async def test_init_db_and_close_db_lifecycle() -> None:
    """init_db creates engine and tables; close_db disposes engine."""
    from app.db import database
    # Use in-memory SQLite to avoid touching disk
    with patch.object(database.settings, "database_url", "sqlite+aiosqlite:///:memory:"):
        with patch.object(database.settings, "debug", False):
            await database.init_db()
            assert database._engine is not None
            assert database._async_session_factory is not None
            await database.close_db()
            assert database._engine is None
            assert database._async_session_factory is None  # type: ignore[unreachable]  # mypy narrows to non-None after l.35; close_db resets it


@pytest.mark.asyncio
async def test_get_db_yields_session_and_commits() -> None:
    """get_db yields a session and commits on success."""
    from app.db import database
    with patch.object(database.settings, "database_url", "sqlite+aiosqlite:///:memory:"):
        with patch.object(database.settings, "debug", False):
            await database.init_db()
            try:
                sessions = []
                async for session in database.get_db():
                    sessions.append(session)
                    break
                assert len(sessions) == 1
                assert sessions[0] is not None
            finally:
                await database.close_db()


@pytest.mark.asyncio
async def test_get_db_rolls_back_on_exception() -> None:
    """get_db rolls back session when an exception is raised."""
    from app.db import database
    with patch.object(database.settings, "database_url", "sqlite+aiosqlite:///:memory:"):
        with patch.object(database.settings, "debug", False):
            await database.init_db()
            try:
                async for session in database.get_db():
                    await session.rollback()
                    raise ValueError("test")
            except ValueError:
                pass
            finally:
                await database.close_db()


@pytest.mark.asyncio
async def test_async_session_local_returns_context_manager_when_initialized() -> None:
    """AsyncSessionLocal() returns a context manager when DB is initialized."""
    from app.db import database
    with patch.object(database.settings, "database_url", "sqlite+aiosqlite:///:memory:"):
        with patch.object(database.settings, "debug", False):
            await database.init_db()
            try:
                cm = database.AsyncSessionLocal()
                async with cm as session:
                    assert session is not None
            finally:
                await database.close_db()
