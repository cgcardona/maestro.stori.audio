"""
Tests for database initialization and get_db dependency.

Ensures get_db raises when DB is not initialized (fail-fast).
"""
import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_get_db_raises_when_not_initialized():
    """get_db raises RuntimeError when init_db has not been called."""
    from app.db.database import get_db

    with patch("app.db.database._async_session_factory", None):
        gen = get_db()
        with pytest.raises(RuntimeError, match="Database not initialized"):
            await gen.__anext__()


@pytest.mark.asyncio
async def test_async_session_local_raises_when_not_initialized():
    """AsyncSessionLocal() raises RuntimeError when init_db has not been called."""
    from app.db.database import AsyncSessionLocal

    with patch("app.db.database._async_session_factory", None):
        with pytest.raises(RuntimeError, match="Database not initialized"):
            AsyncSessionLocal()
