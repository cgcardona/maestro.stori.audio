"""Pytest configuration and fixtures."""
import pytest
import pytest_asyncio


def pytest_configure(config):
    """Ensure asyncio_mode is auto so async fixtures work (e.g. in Docker when pyproject not in cwd)."""
    if hasattr(config.option, "asyncio_mode") and config.option.asyncio_mode is None:
        config.option.asyncio_mode = "auto"
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db import database
from app.db.database import Base, get_db


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_session():
    """Create an in-memory test database session."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    # Inject so require_valid_token's AsyncSessionLocal() uses test DB
    old_engine = database._engine
    old_factory = database._async_session_factory
    database._engine = engine
    database._async_session_factory = async_session_factory
    try:
        async with async_session_factory() as session:
            async def override_get_db():
                yield session
            app.dependency_overrides[get_db] = override_get_db
            yield session
            app.dependency_overrides.clear()
    finally:
        database._engine = old_engine
        database._async_session_factory = old_factory
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    """Create an async test client. Depends on db_session so auth revocation check uses test DB."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# -----------------------------------------------------------------------------
# Auth fixtures for API contract and integration tests
# -----------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_user(db_session):
    """Create a test user with budget (for authenticated route tests)."""
    from app.db.models import User
    user = User(
        id="550e8400-e29b-41d4-a716-446655440000",
        budget_cents=500,
        budget_limit_cents=500,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
def auth_token(test_user):
    """JWT for test_user (1 hour)."""
    from app.auth.tokens import create_access_token
    return create_access_token(user_id=test_user.id, expires_hours=1)


@pytest.fixture
def auth_headers(auth_token):
    """Headers with Bearer token and JSON content type."""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
