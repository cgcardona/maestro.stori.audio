"""
Async SQLAlchemy database setup.

Supports PostgreSQL (production) and SQLite (development).
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings as settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass


# Global engine and session factory (initialized on startup)
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> str:
    """Get the database URL from settings."""
    url = settings.database_url
    if not url:
        # Default to SQLite for development
        url = "sqlite+aiosqlite:///./stori.db"
        logger.warning(f"No database URL configured, using SQLite: {url}")
    return url


async def init_db() -> None:
    """Initialize database engine and session factory.

    Schema is managed by Alembic (``alembic upgrade head`` runs in
    the container entrypoint *before* the app starts).  This function
    only creates the async engine and session factory.
    """
    global _engine, _async_session_factory
    
    database_url = get_database_url()
    logger.info(f"Initializing database: {database_url.split('@')[-1] if '@' in database_url else database_url}")
    
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    
    _engine = create_async_engine(
        database_url,
        echo=settings.debug,
        connect_args=connect_args,
    )
    
    _async_session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    # Import models so relationships resolve even though Alembic owns DDL.
    from app.db import models  # noqa: F401
    from app.db import muse_models  # noqa: F401
    
    logger.info("Database initialized successfully")


async def close_db() -> None:
    """Close database connection."""
    global _engine, _async_session_factory
    
    if _engine:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
        logger.info("Database connection closed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting async database sessions.
    
    Usage:
        @app.get("/users")
        async def get_users(db: AsyncSession = Depends(get_db)):
            ...
    """
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    
    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def AsyncSessionLocal() -> AsyncSession:
    """
    Get a new async session directly (for non-FastAPI contexts).
    
    Usage:
        async with AsyncSessionLocal() as session:
            ...
    """
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _async_session_factory()
