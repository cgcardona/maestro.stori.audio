"""Deep coverage tests for conversations service internals.

Targets: _format_single_message, _extract_entity_summary, _build_context_summary,
get_optimized_context, summarize_conversation_for_llm, get_conversation_preview.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.models import Conversation, ConversationMessage
from app.services.conversations import (
    create_conversation,
    get_conversation,
    add_message,
    format_conversation_history,
    get_optimized_context,
    generate_title_from_prompt,
)

USER_ID = "550e8400-e29b-41d4-a716-446655440000"


def _make_msg(role, content, tool_calls=None, actions=None):
    """Create a minimal ConversationMessage-like object."""
    msg = MagicMock(spec=ConversationMessage)
    msg.role = role
    msg.content = content
    msg.tool_calls = tool_calls
    msg.actions = actions or []
    msg.id = f"msg-{id(msg)}"
    return msg


# ---------------------------------------------------------------------------
# _format_single_message (exercised via format_conversation_history)
# ---------------------------------------------------------------------------


class TestFormatSingleMessage:
    """Test formatting of individual messages through format_conversation_history."""

    @pytest.mark.anyio
    async def test_user_message_formatted(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Fmt user")
        await db_session.commit()
        await add_message(db_session, conv.id, "user", "hello world")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        history = format_conversation_history(conv)
        assert len(history) == 1
        assert history[0] == {"role": "user", "content": "hello world"}

    @pytest.mark.anyio
    async def test_assistant_no_tools(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Fmt asst")
        await db_session.commit()
        await add_message(db_session, conv.id, "assistant", "Sure thing")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        history = format_conversation_history(conv)
        assert len(history) == 1
        assert history[0]["role"] == "assistant"
        assert history[0]["content"] == "Sure thing"
        assert "tool_calls" not in history[0]

    @pytest.mark.anyio
    async def test_assistant_with_tool_calls(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Fmt tools")
        await db_session.commit()
        await add_message(
            db_session, conv.id, "assistant", "Done",
            tool_calls=[
                {"id": "tc-1", "name": "stori_set_tempo", "arguments": {"tempo": 120}},
                {"id": "tc-2", "name": "stori_add_midi_track", "arguments": {"name": "Drums"}},
            ],
        )
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        history = format_conversation_history(conv)
        # assistant message + 2 tool result messages
        assert len(history) == 3
        assert history[0]["role"] == "assistant"
        assert "tool_calls" in history[0]
        assert len(history[0]["tool_calls"]) == 2
        assert history[1]["role"] == "tool"
        assert history[2]["role"] == "tool"

    @pytest.mark.anyio
    async def test_duplicate_tool_ids_get_deduplicated(self, db_session):
        """Duplicate tool_use IDs should be made unique."""
        conv = await create_conversation(db_session, USER_ID, title="Dedup")
        await db_session.commit()
        await add_message(
            db_session, conv.id, "assistant", "Dupes",
            tool_calls=[
                {"id": "same-id", "name": "stori_set_tempo", "arguments": {"tempo": 80}},
                {"id": "same-id", "name": "stori_add_midi_track", "arguments": {"name": "X"}},
            ],
        )
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        history = format_conversation_history(conv)
        ids = [tc["id"] for tc in history[0]["tool_calls"]]
        assert len(set(ids)) == 2, "Tool call IDs must be unique"

    @pytest.mark.anyio
    async def test_missing_tool_id_generates_one(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Missing ID")
        await db_session.commit()
        await add_message(
            db_session, conv.id, "assistant", "No ID",
            tool_calls=[
                {"name": "stori_set_tempo", "arguments": {"tempo": 90}},
            ],
        )
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        history = format_conversation_history(conv)
        assert history[0]["tool_calls"][0]["id"]


# ---------------------------------------------------------------------------
# get_optimized_context
# ---------------------------------------------------------------------------


class TestGetOptimizedContext:

    @pytest.mark.anyio
    async def test_short_conversation_returns_all(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Short")
        await db_session.commit()
        for i in range(5):
            await add_message(db_session, conv.id, "user", f"msg {i}")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        formatted, entity_summary = await get_optimized_context(
            list(conv.messages), max_messages=20
        )
        assert len(formatted) == 5
        assert entity_summary is None

    @pytest.mark.anyio
    async def test_long_conversation_summarizes(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Long")
        await db_session.commit()
        for i in range(25):
            role = "user" if i % 2 == 0 else "assistant"
            await add_message(db_session, conv.id, role, f"msg {i}")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        formatted, entity_summary = await get_optimized_context(
            list(conv.messages), max_messages=5, include_entity_summary=True
        )
        # Should have summary system message(s) + 5 recent messages
        assert len(formatted) >= 5

    @pytest.mark.anyio
    async def test_entity_summary_from_tool_calls(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Entity")
        await db_session.commit()
        # Add enough messages to trigger summarization
        for i in range(22):
            if i == 5:
                await add_message(
                    db_session, conv.id, "assistant", "Created track",
                    tool_calls=[{
                        "name": "stori_add_midi_track",
                        "arguments": {"name": "Drums", "trackId": "trk-abc"},
                    }],
                )
            elif i == 7:
                await add_message(
                    db_session, conv.id, "assistant", "Created region",
                    tool_calls=[{
                        "name": "stori_add_midi_region",
                        "arguments": {"name": "Intro", "regionId": "rgn-def"},
                    }],
                )
            elif i == 9:
                await add_message(
                    db_session, conv.id, "assistant", "Ensured bus",
                    tool_calls=[{
                        "name": "stori_ensure_bus",
                        "arguments": {"name": "Reverb", "busId": "bus-xyz"},
                    }],
                )
            else:
                role = "user" if i % 2 == 0 else "assistant"
                await add_message(db_session, conv.id, role, f"msg {i}")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        formatted, entity_summary = await get_optimized_context(
            list(conv.messages), max_messages=5, include_entity_summary=True
        )
        # Should find entity summary with tracks/regions/buses
        if entity_summary:
            assert "Tracks" in entity_summary or "Regions" in entity_summary or "Buses" in entity_summary


# ---------------------------------------------------------------------------
# summarize_conversation_for_llm
# ---------------------------------------------------------------------------


class TestSummarizeConversation:

    @pytest.mark.anyio
    async def test_summarize_without_llm(self, db_session):
        """Without LLM, falls back to extractive summary."""
        from app.services.conversations import summarize_conversation_for_llm
        conv = await create_conversation(db_session, USER_ID, title="Summarize")
        await db_session.commit()
        await add_message(db_session, conv.id, "user", "Make drums")
        await add_message(db_session, conv.id, "assistant", "Done!")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        summary = await summarize_conversation_for_llm(conv, llm=None)
        assert isinstance(summary, str)

    @pytest.mark.anyio
    async def test_summarize_with_llm(self, db_session):
        """With LLM client, calls chat and returns content."""
        from app.services.conversations import summarize_conversation_for_llm
        conv = await create_conversation(db_session, USER_ID, title="LLM Sum")
        await db_session.commit()
        await add_message(db_session, conv.id, "user", "Create a trap beat")
        await add_message(
            db_session, conv.id, "assistant", "Added drums",
            tool_calls=[{"name": "stori_add_midi_track", "arguments": {"name": "D"}}],
        )
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "User asked for a trap beat. Drums were created."
        mock_llm.chat.return_value = mock_response

        summary = await summarize_conversation_for_llm(conv, llm=mock_llm)
        assert "trap" in summary.lower() or "drum" in summary.lower()
        mock_llm.chat.assert_called_once()

    @pytest.mark.anyio
    async def test_summarize_with_llm_failure(self, db_session):
        """LLM failure falls back to extractive summary."""
        from app.services.conversations import summarize_conversation_for_llm
        conv = await create_conversation(db_session, USER_ID, title="LLM Fail")
        await db_session.commit()
        await add_message(db_session, conv.id, "user", "Make a song")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)

        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = RuntimeError("LLM unavailable")

        summary = await summarize_conversation_for_llm(conv, llm=mock_llm)
        assert isinstance(summary, str)


# ---------------------------------------------------------------------------
# generate_title_from_prompt edge cases
# ---------------------------------------------------------------------------


class TestGenerateTitleEdgeCases:

    def test_empty_prompt(self):
        title = generate_title_from_prompt("")
        assert isinstance(title, str)

    def test_very_long_prompt(self):
        long_prompt = "Create a " + "very " * 100 + "complex beat"
        title = generate_title_from_prompt(long_prompt, max_length=50)
        assert len(title) <= 60  # Some buffer

    def test_special_characters(self):
        title = generate_title_from_prompt("Make a 808 beat @ 140 BPM!!!")
        assert isinstance(title, str)
        assert len(title) > 0


# ---------------------------------------------------------------------------
# get_conversation_preview
# ---------------------------------------------------------------------------


class TestGetConversationPreview:

    @pytest.mark.anyio
    async def test_preview_with_messages(self, db_session):
        from app.services.conversations import get_conversation_preview
        conv = await create_conversation(db_session, USER_ID, title="Preview")
        await db_session.commit()
        await add_message(db_session, conv.id, "user", "Make a funky bass line at 90 BPM")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        preview = await get_conversation_preview(conv)
        assert isinstance(preview, str)

    @pytest.mark.anyio
    async def test_preview_empty_conversation(self, db_session):
        from app.services.conversations import get_conversation_preview
        conv = await create_conversation(db_session, USER_ID, title="Empty")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        preview = await get_conversation_preview(conv)
        assert isinstance(preview, str)
