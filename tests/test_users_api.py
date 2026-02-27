"""Tests for user management API endpoints (app/api/routes/users.py).

Covers registration, profile retrieval, model listing, token management.
"""
from __future__ import annotations

from maestro.db.models import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from maestro.config import ALLOWED_MODEL_IDS, APPROVED_MODELS


class TestRegisterUser:

    @pytest.mark.anyio
    async def test_register_new_user(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """Register a new user by device UUID."""
        resp = await client.post("/api/v1/users/register", json={
            "user_id": "550e8400-e29b-41d4-a716-999955550001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "userId" in data
        assert "budgetRemaining" in data

    @pytest.mark.anyio
    async def test_register_existing_user_returns_profile(self, client: AsyncClient, db_session: AsyncSession, test_user: User) -> None:

        """Re-registering an existing user returns their profile."""
        resp = await client.post("/api/v1/users/register", json={
            "user_id": test_user.id,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["userId"] == test_user.id

    @pytest.mark.anyio
    async def test_register_invalid_uuid(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """Invalid user UUID returns 400."""
        resp = await client.post("/api/v1/users/register", json={
            "user_id": "not-a-uuid",
        })
        assert resp.status_code == 400


class TestGetCurrentUser:

    @pytest.mark.anyio
    async def test_get_current_user(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        resp = await client.get("/api/v1/users/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["userId"] == test_user.id
        assert "budgetRemaining" in data

    @pytest.mark.anyio
    async def test_get_current_user_no_auth(self, client: AsyncClient, db_session: AsyncSession) -> None:

        resp = await client.get("/api/v1/users/me")
        assert resp.status_code in (401, 403)


class TestListModels:

    @pytest.mark.anyio
    async def test_list_models_shape(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """GET /models returns valid shape with models list and default_model."""
        resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "defaultModel" in data
        assert isinstance(data["models"], list)
        assert len(data["models"]) >= 1

    @pytest.mark.anyio
    async def test_list_models_only_allowlisted(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """Only models in ALLOWED_MODEL_IDS are returned."""
        resp = await client.get("/api/v1/models")
        data = resp.json()
        returned_ids = {m["id"] for m in data["models"]}
        expected_ids = {mid for mid in ALLOWED_MODEL_IDS if mid in APPROVED_MODELS}
        assert returned_ids == expected_ids

    @pytest.mark.anyio
    async def test_list_models_exactly_two(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """Exactly 2 models are returned (Sonnet and Opus)."""
        resp = await client.get("/api/v1/models")
        data = resp.json()
        assert len(data["models"]) == 2

    @pytest.mark.anyio
    async def test_list_models_sorted_cheapest_first(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """Models are sorted by cost_per_1m_input ascending."""
        resp = await client.get("/api/v1/models")
        costs = [m["costPer1mInput"] for m in resp.json()["models"]]
        assert costs == sorted(costs)

    @pytest.mark.anyio
    async def test_list_models_default_is_cheapest(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """default_model is the cheapest (Sonnet) model."""
        resp = await client.get("/api/v1/models")
        data = resp.json()
        cheapest_id = min(data["models"], key=lambda m: m["costPer1mInput"])["id"]
        assert data["defaultModel"] == cheapest_id

    @pytest.mark.anyio
    async def test_list_models_have_pricing(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """All returned models have non-zero cost fields."""
        resp = await client.get("/api/v1/models")
        for model in resp.json()["models"]:
            assert model["costPer1mInput"] > 0
            assert model["costPer1mOutput"] > 0

    @pytest.mark.anyio
    async def test_list_models_supports_reasoning_field_present(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """Every model object includes the supports_reasoning boolean field."""
        resp = await client.get("/api/v1/models")
        for model in resp.json()["models"]:
            assert "supportsReasoning" in model
            assert isinstance(model["supportsReasoning"], bool)

    @pytest.mark.anyio
    async def test_list_models_all_support_reasoning(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """All picker models (Sonnet 4.6, Opus 4.6) report supports_reasoning=True."""
        resp = await client.get("/api/v1/models")
        for model in resp.json()["models"]:
            assert model["supportsReasoning"] is True, (
                f"{model['id']} missing from REASONING_MODELS but is in the picker"
            )

    @pytest.mark.anyio
    async def test_list_models_fallback_when_allowlist_empty(self, client: AsyncClient, db_session: AsyncSession) -> None:

        """Falls back to Claude models and logs a warning when allowlist has no APPROVED_MODELS matches."""
        with patch("maestro.api.routes.users.ALLOWED_MODEL_IDS", ["anthropic/claude-does-not-exist-99"]):
            resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) >= 1
        for model in data["models"]:
            assert "claude" in model["id"]


class TestTokenManagement:

    @pytest.mark.anyio
    async def test_list_my_tokens(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User, db_session: AsyncSession) -> None:

        """list tokens returns token list."""
        resp = await client.get("/api/v1/users/me/tokens", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "tokens" in data
        assert isinstance(data["tokens"], list)

    @pytest.mark.anyio
    async def test_revoke_my_tokens(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User, db_session: AsyncSession) -> None:

        """Revoke all tokens returns success."""
        resp = await client.post("/api/v1/users/me/tokens/revoke-all", headers=auth_headers)
        assert resp.status_code == 200
