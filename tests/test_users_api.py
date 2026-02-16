"""Tests for user management API endpoints (app/api/routes/users.py).

Covers registration, profile retrieval, model listing, token management.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch


class TestRegisterUser:

    @pytest.mark.anyio
    async def test_register_new_user(self, client, db_session):
        """Register a new user by device UUID."""
        resp = await client.post("/api/v1/users/register", json={
            "user_id": "550e8400-e29b-41d4-a716-999955550001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data
        assert "budget_remaining" in data

    @pytest.mark.anyio
    async def test_register_existing_user_returns_profile(self, client, db_session, test_user):
        """Re-registering an existing user returns their profile."""
        resp = await client.post("/api/v1/users/register", json={
            "user_id": test_user.id,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == test_user.id

    @pytest.mark.anyio
    async def test_register_invalid_uuid(self, client, db_session):
        """Invalid user UUID returns 400."""
        resp = await client.post("/api/v1/users/register", json={
            "user_id": "not-a-uuid",
        })
        assert resp.status_code == 400


class TestGetCurrentUser:

    @pytest.mark.anyio
    async def test_get_current_user(self, client, auth_headers, test_user):
        resp = await client.get("/api/v1/users/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == test_user.id
        assert "budget_remaining" in data

    @pytest.mark.anyio
    async def test_get_current_user_no_auth(self, client, db_session):
        resp = await client.get("/api/v1/users/me")
        assert resp.status_code in (401, 403)


class TestListModels:

    @pytest.mark.anyio
    async def test_list_models(self, client, db_session):
        resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert isinstance(data["models"], list)
        assert len(data["models"]) >= 1


class TestTokenManagement:

    @pytest.mark.anyio
    async def test_list_my_tokens(self, client, auth_headers, test_user, db_session):
        """List tokens returns token list."""
        resp = await client.get("/api/v1/users/me/tokens", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "tokens" in data
        assert isinstance(data["tokens"], list)

    @pytest.mark.anyio
    async def test_revoke_my_tokens(self, client, auth_headers, test_user, db_session):
        """Revoke all tokens returns success."""
        resp = await client.post("/api/v1/users/me/tokens/revoke-all", headers=auth_headers)
        assert resp.status_code == 200
