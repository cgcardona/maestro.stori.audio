"""Tests for token management service (app/services/token_service.py).

Covers register, revoke, check revoked, cleanup, and list active tokens.
"""
from __future__ import annotations

from app.db.models import AccessToken, User
from sqlalchemy.ext.asyncio import AsyncSession

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from app.services.token_service import (
    register_token,
    is_token_revoked,
    revoke_token,
    revoke_all_user_tokens,
    cleanup_expired_tokens,
    get_user_active_tokens,
)
from app.auth.tokens import create_access_token


USER_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest_asyncio.fixture
async def user_with_token(db_session: AsyncSession, test_user: User) -> tuple[User, str, AccessToken]:

    """Create a test user and register a token."""
    token = create_access_token(user_id=test_user.id, expires_hours=1)
    access_token = await register_token(db_session, token, test_user.id)
    await db_session.commit()
    return test_user, token, access_token


class TestRegisterToken:

    @pytest.mark.anyio
    async def test_register_creates_record(self, db_session: AsyncSession, test_user: User) -> None:

        token = create_access_token(user_id=test_user.id, expires_hours=1)
        record = await register_token(db_session, token, test_user.id)
        await db_session.commit()
        assert record.user_id == test_user.id
        assert record.revoked is False
        assert record.token_hash is not None


class TestIsTokenRevoked:

    @pytest.mark.anyio
    async def test_active_token_not_revoked(self, db_session: AsyncSession, user_with_token: tuple[User, str, AccessToken]) -> None:

        _, token, _ = user_with_token
        revoked = await is_token_revoked(db_session, token)
        assert revoked is False

    @pytest.mark.anyio
    async def test_legacy_token_not_revoked(self, db_session: AsyncSession) -> None:

        """Token not in DB (legacy) should return False."""
        revoked = await is_token_revoked(db_session, "legacy-jwt-not-in-db")
        assert revoked is False

    @pytest.mark.anyio
    async def test_revoked_token_returns_true(self, db_session: AsyncSession, user_with_token: tuple[User, str, AccessToken]) -> None:

        user, token, _ = user_with_token
        await revoke_token(db_session, token)
        await db_session.commit()
        revoked = await is_token_revoked(db_session, token)
        assert revoked is True


class TestRevokeToken:

    @pytest.mark.anyio
    async def test_revoke_existing(self, db_session: AsyncSession, user_with_token: tuple[User, str, AccessToken]) -> None:

        _, token, _ = user_with_token
        result = await revoke_token(db_session, token)
        assert result is True

    @pytest.mark.anyio
    async def test_revoke_nonexistent(self, db_session: AsyncSession) -> None:

        result = await revoke_token(db_session, "nonexistent-token")
        assert result is False


class TestRevokeAllUserTokens:

    @pytest.mark.anyio
    async def test_revoke_all(self, db_session: AsyncSession, test_user: User) -> None:

        # Each token needs a unique payload (different expires)
        for i in range(3):
            token = create_access_token(user_id=test_user.id, expires_hours=1 + i)
            await register_token(db_session, token, test_user.id)
        await db_session.commit()
        count = await revoke_all_user_tokens(db_session, test_user.id)
        assert count == 3


class TestCleanupExpiredTokens:

    @pytest.mark.anyio
    async def test_cleanup_removes_expired(self, db_session: AsyncSession, test_user: User) -> None:

        from app.db.models import AccessToken
        from app.auth.tokens import hash_token

        token_str = "expired-token-abc"
        expired = AccessToken(
            user_id=test_user.id,
            token_hash=hash_token(token_str),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            revoked=False,
        )
        db_session.add(expired)
        await db_session.commit()

        count = await cleanup_expired_tokens(db_session)
        assert count >= 1


class TestGetUserActiveTokens:

    @pytest.mark.anyio
    async def test_returns_active_tokens(self, db_session: AsyncSession, test_user: User) -> None:

        token = create_access_token(user_id=test_user.id, expires_hours=1)
        await register_token(db_session, token, test_user.id)
        await db_session.commit()
        active = await get_user_active_tokens(db_session, test_user.id)
        assert len(active) >= 1

    @pytest.mark.anyio
    async def test_excludes_revoked(self, db_session: AsyncSession, test_user: User) -> None:

        token = create_access_token(user_id=test_user.id, expires_hours=1)
        await register_token(db_session, token, test_user.id)
        await db_session.commit()
        await revoke_token(db_session, token)
        await db_session.commit()
        active = await get_user_active_tokens(db_session, test_user.id)
        assert len(active) == 0
