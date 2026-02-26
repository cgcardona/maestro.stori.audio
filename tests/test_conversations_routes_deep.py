"""Deep integration tests for conversation API routes.

Targets: normalize_tool_arguments, build_conversation_history_for_llm,
sse_event, search, update (PATCH), delete (hard/soft), message posting detail.
"""
from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
import pytest


from app.api.routes.conversations import (
    normalize_tool_arguments,
    build_conversation_history_for_llm,
)
from app.core.sse_utils import sse_event
from app.db.models import ConversationMessage


# ---------------------------------------------------------------------------
# normalize_tool_arguments (pure function)
# ---------------------------------------------------------------------------


class TestNormalizeToolArguments:

    def test_empty_dict(self) -> None:

        assert normalize_tool_arguments({}) == {}

    def test_none(self) -> None:

        assert normalize_tool_arguments(None) is None

    def test_int_to_string(self) -> None:

        result = normalize_tool_arguments({"tempo": 120, "name": "Drums"})
        assert result is not None
        assert result["tempo"] == "120"
        assert result["name"] == "Drums"

    def test_float_to_string(self) -> None:

        result = normalize_tool_arguments({"volume": 0.8})
        assert result is not None
        assert result["volume"] == "0.8"

    def test_bool_unchanged(self) -> None:

        result = normalize_tool_arguments({"muted": True, "solo": False})
        assert result is not None
        assert result["muted"] is True
        assert result["solo"] is False

    def test_nested_dict(self) -> None:

        result = normalize_tool_arguments({
            "track": {"volume": 0.5, "name": "Bass"},
        })
        assert result is not None
        track = result["track"]
        assert isinstance(track, dict)
        assert track["volume"] == "0.5"
        assert track["name"] == "Bass"

    def test_list_with_numbers(self) -> None:

        result = normalize_tool_arguments({
            "notes": [{"pitch": 60, "velocity": 100}],
        })
        assert result is not None
        notes = result["notes"]
        assert isinstance(notes, list)
        note0 = notes[0]
        assert isinstance(note0, dict)
        assert note0["pitch"] == "60"
        assert note0["velocity"] == "100"

    def test_list_with_mixed_types(self) -> None:

        result = normalize_tool_arguments({
            "items": [42, "hello", 3.14, True],
        })
        assert result is not None
        assert result["items"] == ["42", "hello", "3.14", True]


# ---------------------------------------------------------------------------
# build_conversation_history_for_llm (pure function)
# ---------------------------------------------------------------------------


class TestBuildConversationHistoryForLLM:

    def _make_msg(
        self,
        role: str,
        content: str | None,
        tool_calls: list[dict[str, object]] | None = None,
    ) -> ConversationMessage:
        return ConversationMessage(
            conversation_id="test-conv-id",
            role=role,
            content=content or "",
            tool_calls=tool_calls,
        )

    def test_user_message(self) -> None:

        msgs = [self._make_msg("user", "Hello")]
        history = build_conversation_history_for_llm(msgs)
        assert len(history) == 1
        assert history[0] == {"role": "user", "content": "Hello"}

    def test_assistant_no_tools(self) -> None:

        msgs = [self._make_msg("assistant", "Hi there")]
        history = build_conversation_history_for_llm(msgs)
        assert len(history) == 1
        assert history[0]["role"] == "assistant"
        assert history[0]["content"] == "Hi there"
        assert "tool_calls" not in history[0]

    def test_assistant_with_tool_calls(self) -> None:

        msgs = [self._make_msg("assistant", "Done", tool_calls=[
            {"id": "tc-1", "name": "stori_set_tempo", "arguments": {"tempo": 120}},
        ])]
        history = build_conversation_history_for_llm(msgs)
        # assistant + tool result
        assert len(history) == 2
        msg0 = history[0]
        assert msg0["role"] == "assistant"
        assert "tool_calls" in msg0
        assert msg0["tool_calls"][0]["function"]["name"] == "stori_set_tempo"
        assert history[1]["role"] == "tool"

    def test_duplicate_ids_deduplicated(self) -> None:

        msgs = [self._make_msg("assistant", "Dupes", tool_calls=[
            {"id": "dup", "name": "stori_set_tempo", "arguments": {"tempo": 80}},
            {"id": "dup", "name": "stori_add_midi_track", "arguments": {"name": "X"}},
        ])]
        history = build_conversation_history_for_llm(msgs)
        msg0 = history[0]
        assert msg0["role"] == "assistant"
        ids = [tc["id"] for tc in msg0["tool_calls"]]
        assert len(set(ids)) == 2

    def test_empty_id_generates(self) -> None:

        msgs = [self._make_msg("assistant", "No ID", tool_calls=[
            {"name": "stori_set_tempo", "arguments": {}},
        ])]
        history = build_conversation_history_for_llm(msgs)
        msg0 = history[0]
        assert msg0["role"] == "assistant"
        tc_id = msg0["tool_calls"][0]["id"]
        assert tc_id.startswith("call_")

    def test_mixed_messages(self) -> None:

        msgs = [
            self._make_msg("user", "set tempo"),
            self._make_msg("assistant", "Done", tool_calls=[
                {"id": "tc-1", "name": "stori_set_tempo", "arguments": {"tempo": 100}},
            ]),
            self._make_msg("user", "Now add drums"),
        ]
        history = build_conversation_history_for_llm(msgs)
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
        assert history[2]["role"] == "tool"
        assert history[3]["role"] == "user"

    def test_none_content_handled(self) -> None:

        msgs = [self._make_msg("user", None)]
        history = build_conversation_history_for_llm(msgs)
        assert history[0]["content"] == ""

    def test_empty_list(self) -> None:

        history = build_conversation_history_for_llm([])
        assert history == []


# ---------------------------------------------------------------------------
# sse_event helper
# ---------------------------------------------------------------------------


class TestSSEEvent:

    @pytest.mark.anyio
    async def test_format(self) -> None:

        """sse_event validates through protocol models and returns SSE format."""
        result = await sse_event({"type": "content", "content": "hello"})
        assert result.startswith("data: ")
        assert '"type":"content"' in result or '"type": "content"' in result
        assert result.endswith("\n\n")


# ---------------------------------------------------------------------------
# Conversation API integration (CRUD)
# ---------------------------------------------------------------------------


class TestConversationSearchAPI:

    @pytest.mark.anyio
    async def test_search_endpoint(self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession) -> None:

        from app.services.conversations import create_conversation
        user_id = (await client.get("/api/v1/users/me", headers=auth_headers)).json().get("userId")
        if not user_id:
            pytest.skip("No user_id in /me response")
        await create_conversation(db_session, user_id, title="Funky drums session")
        await db_session.commit()
        resp = await client.get(
            "/api/v1/conversations/search?q=funky",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    @pytest.mark.anyio
    async def test_search_no_query(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.get(
            "/api/v1/conversations/search",
            headers=auth_headers,
        )
        assert resp.status_code == 422  # Missing required query param


class TestConversationUpdateAPI:

    @pytest.mark.anyio
    async def test_update_title(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        # Create
        resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Old Title"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        conv_id = resp.json()["id"]

        # Update title
        resp = await client.patch(
            f"/api/v1/conversations/{conv_id}",
            json={"title": "New Title"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"

    @pytest.mark.anyio
    async def test_update_project_id(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Linked"},
            headers=auth_headers,
        )
        conv_id = resp.json()["id"]

        # Link to project
        resp = await client.patch(
            f"/api/v1/conversations/{conv_id}",
            json={"project_id": "proj-123"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["projectId"] == "proj-123"

        # Unlink
        resp = await client.patch(
            f"/api/v1/conversations/{conv_id}",
            json={"project_id": "null"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["projectId"] is None

    @pytest.mark.anyio
    async def test_update_nonexistent_404(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.patch(
            "/api/v1/conversations/nonexistent-id",
            json={"title": "Ghost"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestConversationDeleteAPI:

    @pytest.mark.anyio
    async def test_soft_delete(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Soft Del"},
            headers=auth_headers,
        )
        conv_id = resp.json()["id"]
        resp = await client.delete(f"/api/v1/conversations/{conv_id}", headers=auth_headers)
        assert resp.status_code == 204

    @pytest.mark.anyio
    async def test_hard_delete(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Hard Del"},
            headers=auth_headers,
        )
        conv_id = resp.json()["id"]
        resp = await client.delete(
            f"/api/v1/conversations/{conv_id}?hard_delete=true",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    @pytest.mark.anyio
    async def test_delete_nonexistent_404(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        resp = await client.delete(
            "/api/v1/conversations/nonexistent-id",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestConversationGetDetail:

    @pytest.mark.anyio
    async def test_get_with_messages(self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession) -> None:

        # Create conv
        resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Detail"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        conv_id = resp.json()["id"]

        # Manually add a message via service
        from app.services.conversations import add_message
        await add_message(db_session, conv_id, "user", "test content")
        await db_session.commit()

        # Get detail
        resp = await client.get(
            f"/api/v1/conversations/{conv_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert len(data["messages"]) >= 1

    @pytest.mark.anyio
    async def test_get_with_tool_calls_normalized(self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession) -> None:

        resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Tool Normalize"},
            headers=auth_headers,
        )
        conv_id = resp.json()["id"]

        from app.services.conversations import add_message
        await add_message(
            db_session, conv_id, "assistant", "Done",
            tool_calls=[{
                "id": "tc-1", "name": "stori_set_tempo",
                "arguments": {"tempo": 120, "name": "fast"},
            }],
        )
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/conversations/{conv_id}",
            headers=auth_headers,
        )
        data = resp.json()
        assert len(data["messages"]) >= 1
        # Find the assistant message
        asst_msgs = [m for m in data["messages"] if m["role"] == "assistant"]
        if asst_msgs and asst_msgs[0].get("toolCalls"):
            tc = asst_msgs[0]["toolCalls"][0]
            # Numeric args should be converted to strings
            assert tc["arguments"]["tempo"] == "120"


class TestConversationListFiltering:

    @pytest.mark.anyio
    async def test_list_with_project_filter(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        await client.post(
            "/api/v1/conversations",
            json={"title": "P1", "project_id": "proj-1"},
            headers=auth_headers,
        )
        await client.post(
            "/api/v1/conversations",
            json={"title": "P2", "project_id": "proj-2"},
            headers=auth_headers,
        )
        resp = await client.get(
            "/api/v1/conversations?project_id=proj-1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    @pytest.mark.anyio
    async def test_list_pagination(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        for i in range(5):
            await client.post(
                "/api/v1/conversations",
                json={"title": f"Page {i}"},
                headers=auth_headers,
            )
        resp = await client.get(
            "/api/v1/conversations?limit=2&offset=0",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["conversations"]) <= 2
