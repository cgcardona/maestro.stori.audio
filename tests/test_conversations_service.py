"""Unit tests for the conversations service layer (app/services/conversations.py).

Tests CRUD operations, message management, search, title generation,
and conversation history formatting directly against the service functions.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.models import Conversation, ConversationMessage, MessageAction
from app.services.conversations import (
    create_conversation,
    get_conversation,
    list_conversations,
    update_conversation_title,
    archive_conversation,
    delete_conversation,
    add_message,
    add_action,
    search_conversations,
    generate_title_from_prompt,
    format_conversation_history,
)


USER_ID = "550e8400-e29b-41d4-a716-446655440000"


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------


class TestCreateConversation:

    @pytest.mark.anyio
    async def test_create_basic(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Test")
        await db_session.commit()
        assert conv.id is not None
        assert conv.title == "Test"
        assert conv.user_id == USER_ID

    @pytest.mark.anyio
    async def test_create_with_project(self, db_session):
        conv = await create_conversation(
            db_session, USER_ID,
            title="Project conv",
            project_id="proj-1",
            project_context={"key": "value"},
        )
        await db_session.commit()
        assert conv.project_id == "proj-1"


class TestGetConversation:

    @pytest.mark.anyio
    async def test_get_existing(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Get me")
        await db_session.commit()
        fetched = await get_conversation(db_session, conv.id, USER_ID)
        assert fetched is not None
        assert fetched.id == conv.id

    @pytest.mark.anyio
    async def test_get_wrong_user_returns_none(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Secret")
        await db_session.commit()
        fetched = await get_conversation(db_session, conv.id, "other-user-id")
        assert fetched is None

    @pytest.mark.anyio
    async def test_get_nonexistent_returns_none(self, db_session):
        fetched = await get_conversation(db_session, "nonexistent-id", USER_ID)
        assert fetched is None


class TestListConversations:

    @pytest.mark.anyio
    async def test_list_returns_user_conversations(self, db_session):
        await create_conversation(db_session, USER_ID, title="Conv 1")
        await create_conversation(db_session, USER_ID, title="Conv 2")
        await db_session.commit()
        convs, total = await list_conversations(db_session, USER_ID)
        assert total == 2
        assert len(convs) == 2

    @pytest.mark.anyio
    async def test_list_pagination(self, db_session):
        for i in range(5):
            await create_conversation(db_session, USER_ID, title=f"Conv {i}")
        await db_session.commit()
        convs, total = await list_conversations(db_session, USER_ID, limit=2, offset=0)
        assert total == 5
        assert len(convs) == 2

    @pytest.mark.anyio
    async def test_list_excludes_archived(self, db_session):
        c1 = await create_conversation(db_session, USER_ID, title="Active")
        c2 = await create_conversation(db_session, USER_ID, title="Archived")
        c2.is_archived = True
        await db_session.commit()
        convs, total = await list_conversations(db_session, USER_ID, include_archived=False)
        assert total == 1
        assert convs[0].id == c1.id

    @pytest.mark.anyio
    async def test_list_includes_archived(self, db_session):
        await create_conversation(db_session, USER_ID, title="Active")
        c2 = await create_conversation(db_session, USER_ID, title="Archived")
        c2.is_archived = True
        await db_session.commit()
        convs, total = await list_conversations(db_session, USER_ID, include_archived=True)
        assert total == 2

    @pytest.mark.anyio
    async def test_list_filter_by_project(self, db_session):
        await create_conversation(db_session, USER_ID, title="P1", project_id="proj-1")
        await create_conversation(db_session, USER_ID, title="P2", project_id="proj-2")
        await db_session.commit()
        convs, total = await list_conversations(db_session, USER_ID, project_id="proj-1")
        assert total == 1
        assert convs[0].title == "P1"


class TestUpdateConversationTitle:

    @pytest.mark.anyio
    async def test_update_title(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Old title")
        await db_session.commit()
        updated = await update_conversation_title(db_session, conv.id, USER_ID, "New title")
        assert updated is not None
        assert updated.title == "New title"

    @pytest.mark.anyio
    async def test_update_title_wrong_user(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Title")
        await db_session.commit()
        result = await update_conversation_title(db_session, conv.id, "wrong-user", "Hacked")
        assert result is None


class TestArchiveConversation:

    @pytest.mark.anyio
    async def test_archive(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="To archive")
        await db_session.commit()
        result = await archive_conversation(db_session, conv.id, USER_ID)
        assert result is True

    @pytest.mark.anyio
    async def test_archive_nonexistent(self, db_session):
        result = await archive_conversation(db_session, "nope", USER_ID)
        assert result is False


class TestDeleteConversation:

    @pytest.mark.anyio
    async def test_delete(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Delete me")
        await db_session.commit()
        result = await delete_conversation(db_session, conv.id, USER_ID)
        assert result is True
        fetched = await get_conversation(db_session, conv.id, USER_ID)
        assert fetched is None

    @pytest.mark.anyio
    async def test_delete_wrong_user(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Keep")
        await db_session.commit()
        result = await delete_conversation(db_session, conv.id, "attacker")
        assert result is False


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------


class TestAddMessage:

    @pytest.mark.anyio
    async def test_add_user_message(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Chat")
        await db_session.commit()
        msg = await add_message(db_session, conv.id, "user", "Hello!")
        await db_session.commit()
        assert msg.role == "user"
        assert msg.content == "Hello!"
        assert msg.conversation_id == conv.id

    @pytest.mark.anyio
    async def test_add_assistant_message_with_metadata(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Chat")
        await db_session.commit()
        msg = await add_message(
            db_session, conv.id, "assistant", "Here you go.",
            model_used="claude-3",
            tokens_used={"prompt": 100, "completion": 50},
            cost_cents=2,
            tool_calls=[{"name": "stori_set_tempo", "params": {"tempo": 120}}],
        )
        await db_session.commit()
        assert msg.model_used == "claude-3"
        assert msg.cost_cents == 2


class TestAddAction:

    @pytest.mark.anyio
    async def test_add_action_to_message(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Chat")
        await db_session.commit()
        msg = await add_message(db_session, conv.id, "assistant", "Action!")
        await db_session.commit()
        action = await add_action(
            db_session, msg.id,
            action_type="tool_call",
            description="stori_set_tempo(tempo=120)",
            success=True,
        )
        await db_session.commit()
        assert action.action_type == "tool_call"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearchConversations:

    @pytest.mark.anyio
    async def test_search_by_title(self, db_session):
        await create_conversation(db_session, USER_ID, title="Drum pattern session")
        await create_conversation(db_session, USER_ID, title="Bass line ideas")
        await db_session.commit()
        results = await search_conversations(db_session, USER_ID, "drum")
        assert len(results) >= 1
        assert any("Drum" in c.title for c in results)


# ---------------------------------------------------------------------------
# Title generation
# ---------------------------------------------------------------------------


class TestGenerateTitle:

    def test_generate_title(self):
        """generate_title_from_prompt is sync and returns a short string."""
        title = generate_title_from_prompt("Make a funky bass line at 100 BPM")
        assert isinstance(title, str)
        assert len(title) > 0
        assert len(title) <= 60


# ---------------------------------------------------------------------------
# Format conversation history
# ---------------------------------------------------------------------------


class TestFormatConversationHistory:

    @pytest.mark.anyio
    async def test_format_empty_messages(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Empty")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        history = format_conversation_history(conv)
        assert history == []

    @pytest.mark.anyio
    async def test_format_user_assistant_messages(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Chat")
        await db_session.commit()
        await add_message(db_session, conv.id, "user", "Hello")
        await add_message(db_session, conv.id, "assistant", "Hi there")
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        history = format_conversation_history(conv)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"
        assert history[1]["role"] == "assistant"

    @pytest.mark.anyio
    async def test_format_with_tool_calls(self, db_session):
        conv = await create_conversation(db_session, USER_ID, title="Tools")
        await db_session.commit()
        await add_message(
            db_session, conv.id, "assistant", "Done.",
            tool_calls=[{"name": "stori_set_tempo", "params": {"tempo": 120}, "id": "tc-1"}],
        )
        await db_session.commit()
        conv = await get_conversation(db_session, conv.id, USER_ID)
        history = format_conversation_history(conv)
        assert len(history) >= 1
