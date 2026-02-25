"""Tests for conversation API endpoints (app/api/routes/conversations.py).

Covers conversation CRUD, message retrieval, compose-in-conversation,
and streaming within conversation context.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from app.db.models import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.models import Conversation, ConversationMessage


USER_ID = "550e8400-e29b-41d4-a716-446655440000"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_sse_events(body: str) -> list[dict[str, Any]]:

    events = []
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


# ---------------------------------------------------------------------------
# Conversation CRUD endpoints
# ---------------------------------------------------------------------------


class TestCreateConversation:

    @pytest.mark.anyio
    async def test_create_conversation(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        resp = await client.post("/api/v1/conversations", json={
            "title": "My session",
        }, headers=auth_headers)
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert "id" in data
        assert data["title"] == "My session"

    @pytest.mark.anyio
    async def test_create_conversation_with_project(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        resp = await client.post("/api/v1/conversations", json={
            "title": "Project session",
            "project_id": "proj-abc",
        }, headers=auth_headers)
        assert resp.status_code in (200, 201)

    @pytest.mark.anyio
    async def test_create_no_auth(self, client: AsyncClient, db_session: AsyncSession) -> None:

        resp = await client.post("/api/v1/conversations", json={"title": "Test"})
        assert resp.status_code in (401, 403)


class TestListConversations:

    @pytest.mark.anyio
    async def test_list_empty(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        resp = await client.get("/api/v1/conversations", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "conversations" in data
        assert isinstance(data["conversations"], list)

    @pytest.mark.anyio
    async def test_list_after_create(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        await client.post("/api/v1/conversations", json={"title": "A"}, headers=auth_headers)
        await client.post("/api/v1/conversations", json={"title": "B"}, headers=auth_headers)
        resp = await client.get("/api/v1/conversations", headers=auth_headers)
        data = resp.json()
        assert data["total"] >= 2


class TestGetConversation:

    @pytest.mark.anyio
    async def test_get_existing(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        create_resp = await client.post("/api/v1/conversations", json={"title": "Fetch me"}, headers=auth_headers)
        conv_id = create_resp.json()["id"]
        resp = await client.get(f"/api/v1/conversations/{conv_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == conv_id

    @pytest.mark.anyio
    async def test_get_nonexistent(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        resp = await client.get("/api/v1/conversations/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404


class TestUpdateConversation:

    @pytest.mark.anyio
    async def test_update_title(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        create_resp = await client.post("/api/v1/conversations", json={"title": "Old"}, headers=auth_headers)
        conv_id = create_resp.json()["id"]
        resp = await client.patch(f"/api/v1/conversations/{conv_id}", json={"title": "New"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"


class TestDeleteConversation:

    @pytest.mark.anyio
    async def test_delete(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        create_resp = await client.post("/api/v1/conversations", json={"title": "Delete me"}, headers=auth_headers)
        conv_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/conversations/{conv_id}", headers=auth_headers)
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestConversationMessages:

    @pytest.mark.anyio
    async def test_get_conversation_has_messages(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        """Get conversation detail includes messages."""
        create_resp = await client.post("/api/v1/conversations", json={"title": "Chat"}, headers=auth_headers)
        conv_id = create_resp.json()["id"]
        resp = await client.get(f"/api/v1/conversations/{conv_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data


# ---------------------------------------------------------------------------
# Compose within conversation context (streaming)
# ---------------------------------------------------------------------------


class TestConversationCompose:

    @pytest.mark.anyio
    async def test_compose_in_conversation(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        """Adding a message (compose) within a conversation streams SSE events."""
        create_resp = await client.post("/api/v1/conversations", json={"title": "Compose"}, headers=auth_headers)
        conv_id = create_resp.json()["id"]

        async def fake_orchestrate(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:

            from app.core.sse_utils import sse_event
            yield await sse_event({"type": "state", "state": "composing"})
            yield await sse_event({"type": "complete", "success": True, "tool_calls": []})

        with patch("app.api.routes.conversations.messages.orchestrate", side_effect=fake_orchestrate):
            resp = await client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"prompt": "make a beat"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    @pytest.mark.anyio
    async def test_maestro_no_auth(self, client: AsyncClient, db_session: AsyncSession) -> None:

        resp = await client.post("/api/v1/conversations/some-id/messages", json={"prompt": "test"})
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearchConversations:

    @pytest.mark.anyio
    async def test_search(self, client: AsyncClient, auth_headers: dict[str, str], test_user: User) -> None:

        await client.post("/api/v1/conversations", json={"title": "Drum patterns"}, headers=auth_headers)
        resp = await client.get("/api/v1/conversations/search?q=drum", headers=auth_headers)
        assert resp.status_code == 200
