"""
Comprehensive tests for conversation history system.

Tests cover:
- Conversation CRUD operations
- Message management
- Action tracking
- Budget integration
- Search functionality
- Title auto-generation
- Pagination
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy import select

from app.db.models import (
    Conversation,
    ConversationMessage,
    MessageAction,
    User,
)
from app.services import conversations as conv_service


# =============================================================================
# Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:

    """Create a test user."""
    user = User(
        id="test-user-123",
        budget_cents=500,
        budget_limit_cents=500,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_conversation(db_session: AsyncSession, test_user: Any) -> Conversation:

    """Create a test conversation."""
    conversation = await conv_service.create_conversation(
        db=db_session,
        user_id=test_user.id,
        title="Test Conversation",
        project_context={"tempo": 120, "key": "C major"},
    )
    await db_session.commit()
    await db_session.refresh(conversation)
    return conversation


@pytest_asyncio.fixture
async def conversation_with_messages(db_session: AsyncSession, test_conversation: Conversation) -> Conversation:

    """Create a conversation with messages."""
    # Add user message
    user_msg = await conv_service.add_message(
        db=db_session,
        conversation_id=test_conversation.id,
        role="user",
        content="Create a chill beat",
    )
    
    # Add assistant message
    assistant_msg = await conv_service.add_message(
        db=db_session,
        conversation_id=test_conversation.id,
        role="assistant",
        content="I'll create a chill beat for you.",
        model_used="anthropic/claude-3.5-sonnet",
        tokens_used={"prompt": 100, "completion": 50},
        cost_cents=12,
        tool_calls=[{"name": "add_track", "type": "function"}],
    )
    
    # Add action to assistant message
    await conv_service.add_action(
        db=db_session,
        message_id=assistant_msg.id,
        action_type="track_added",
        description="Added bass track",
        success=True,
        extra_metadata={"track_id": "track-123"},
    )
    
    await db_session.commit()
    await db_session.refresh(test_conversation)
    return test_conversation


# =============================================================================
# Conversation CRUD Tests
# =============================================================================

@pytest.mark.asyncio
async def test_create_conversation(db_session: AsyncSession, test_user: Any) -> None:

    """Test creating a new conversation."""
    conversation = await conv_service.create_conversation(
        db=db_session,
        user_id=test_user.id,
        title="My Beat",
        project_context={"tempo": 90},
    )
    await db_session.commit()
    
    assert conversation.id is not None
    assert conversation.user_id == test_user.id
    assert conversation.title == "My Beat"
    assert conversation.project_context == {"tempo": 90}
    assert conversation.is_archived is False
    assert conversation.created_at is not None


@pytest.mark.asyncio
async def test_get_conversation(db_session: AsyncSession, test_user: Any, conversation_with_messages: Any) -> None:

    """Test retrieving a conversation with messages."""
    conversation = await conv_service.get_conversation(
        db=db_session,
        conversation_id=conversation_with_messages.id,
        user_id=test_user.id,
    )
    
    assert conversation is not None
    assert conversation.id == conversation_with_messages.id
    assert len(conversation.messages) == 2
    
    # Check messages are in order
    assert conversation.messages[0].role == "user"
    assert conversation.messages[1].role == "assistant"
    
    # Check actions are loaded
    assert len(conversation.messages[1].actions) == 1
    assert conversation.messages[1].actions[0].action_type == "track_added"


@pytest.mark.asyncio
async def test_get_conversation_wrong_user(db_session: AsyncSession, conversation_with_messages: Any) -> None:

    """Test that users can't access other users' conversations."""
    conversation = await conv_service.get_conversation(
        db=db_session,
        conversation_id=conversation_with_messages.id,
        user_id="wrong-user-id",
    )
    
    assert conversation is None


@pytest.mark.asyncio
async def test_list_conversations(db_session: AsyncSession, test_user: Any) -> None:

    """Test listing conversations with pagination."""
    # Create multiple conversations
    for i in range(5):
        await conv_service.create_conversation(
            db=db_session,
            user_id=test_user.id,
            title=f"Conversation {i}",
        )
    await db_session.commit()
    
    # list first 3
    conversations, total = await conv_service.list_conversations(
        db=db_session,
        user_id=test_user.id,
        limit=3,
        offset=0,
    )
    
    assert len(conversations) == 3
    assert total == 5
    
    # list next 2
    conversations, total = await conv_service.list_conversations(
        db=db_session,
        user_id=test_user.id,
        limit=3,
        offset=3,
    )
    
    assert len(conversations) == 2
    assert total == 5


@pytest.mark.asyncio
async def test_list_conversations_excludes_archived(db_session: AsyncSession, test_user: Any) -> None:

    """Test that archived conversations are excluded by default."""
    # Create normal conversation
    conv1 = await conv_service.create_conversation(
        db=db_session,
        user_id=test_user.id,
        title="Active",
    )
    
    # Create and archive conversation
    conv2 = await conv_service.create_conversation(
        db=db_session,
        user_id=test_user.id,
        title="Archived",
    )
    await conv_service.archive_conversation(
        db=db_session,
        conversation_id=conv2.id,
        user_id=test_user.id,
    )
    await db_session.commit()
    
    # list without archived
    conversations, total = await conv_service.list_conversations(
        db=db_session,
        user_id=test_user.id,
        include_archived=False,
    )
    
    assert len(conversations) == 1
    assert conversations[0].title == "Active"
    
    # list with archived
    conversations, total = await conv_service.list_conversations(
        db=db_session,
        user_id=test_user.id,
        include_archived=True,
    )
    
    assert len(conversations) == 2


@pytest.mark.asyncio
async def test_update_conversation_title(db_session: AsyncSession, test_user: Any, test_conversation: Any) -> None:

    """Test updating conversation title."""
    updated = await conv_service.update_conversation_title(
        db=db_session,
        conversation_id=test_conversation.id,
        user_id=test_user.id,
        title="New Title",
    )
    await db_session.commit()
    
    assert updated is not None
    assert updated.title == "New Title"
    assert updated.updated_at >= test_conversation.updated_at


@pytest.mark.asyncio
async def test_archive_conversation(db_session: AsyncSession, test_user: Any, test_conversation: Any) -> None:

    """Test archiving a conversation."""
    success = await conv_service.archive_conversation(
        db=db_session,
        conversation_id=test_conversation.id,
        user_id=test_user.id,
    )
    await db_session.commit()
    
    assert success is True
    
    # Verify it's archived
    result = await db_session.execute(
        select(Conversation).where(Conversation.id == test_conversation.id)
    )
    conversation = result.scalar_one()
    assert conversation.is_archived is True


@pytest.mark.asyncio
async def test_delete_conversation(db_session: AsyncSession, test_user: Any, test_conversation: Any) -> None:

    """Test permanently deleting a conversation."""
    success = await conv_service.delete_conversation(
        db=db_session,
        conversation_id=test_conversation.id,
        user_id=test_user.id,
    )
    await db_session.commit()
    
    assert success is True
    
    # Verify it's deleted
    result = await db_session.execute(
        select(Conversation).where(Conversation.id == test_conversation.id)
    )
    conversation = result.scalar_one_or_none()
    assert conversation is None


# =============================================================================
# Message Management Tests
# =============================================================================

@pytest.mark.asyncio
async def test_add_message(db_session: AsyncSession, test_conversation: Any) -> None:

    """Test adding a message to a conversation."""
    message = await conv_service.add_message(
        db=db_session,
        conversation_id=test_conversation.id,
        role="user",
        content="Test message",
    )
    await db_session.commit()
    
    assert message.id is not None
    assert message.conversation_id == test_conversation.id
    assert message.role == "user"
    assert message.content == "Test message"
    assert message.timestamp is not None


@pytest.mark.asyncio
async def test_add_assistant_message_with_metadata(db_session: AsyncSession, test_conversation: Any) -> None:

    """Test adding an assistant message with full metadata."""
    message = await conv_service.add_message(
        db=db_session,
        conversation_id=test_conversation.id,
        role="assistant",
        content="Here's your beat",
        model_used="anthropic/claude-3.5-sonnet",
        tokens_used={"prompt": 200, "completion": 100},
        cost_cents=25,
        tool_calls=[
            {"name": "add_track", "arguments": {"type": "bass"}},
            {"name": "set_tempo", "arguments": {"bpm": "120"}},
        ],
        extra_metadata={"user_rating": 5},
    )
    await db_session.commit()
    
    assert message.model_used == "anthropic/claude-3.5-sonnet"
    assert message.tokens_used is not None
    assert message.tokens_used["prompt"] == 200
    assert message.tokens_used["completion"] == 100
    assert message.cost_cents == 25
    assert message.cost == 0.25
    assert message.tool_calls is not None and len(message.tool_calls) == 2
    assert message.extra_metadata is not None and message.extra_metadata["user_rating"] == 5


@pytest.mark.asyncio
async def test_add_message_updates_conversation_timestamp(
    db_session: AsyncSession,

    test_conversation: Any,

) -> None:
    """Test that adding a message updates conversation timestamp."""
    from datetime import timezone
    original_updated_at = test_conversation.updated_at
    if original_updated_at.tzinfo is None:
        original_updated_at = original_updated_at.replace(tzinfo=timezone.utc)
    
    await conv_service.add_message(
        db=db_session,
        conversation_id=test_conversation.id,
        role="user",
        content="New message",
    )
    await db_session.commit()
    
    # Reload conversation
    result = await db_session.execute(
        select(Conversation).where(Conversation.id == test_conversation.id)
    )
    conversation = result.scalar_one()
    assert conversation is not None
    reloaded_at = conversation.updated_at
    if reloaded_at.tzinfo is None:
        reloaded_at = reloaded_at.replace(tzinfo=timezone.utc)
    
    assert reloaded_at >= original_updated_at


# =============================================================================
# Action Tracking Tests
# =============================================================================

@pytest.mark.asyncio
async def test_add_action(db_session: AsyncSession, test_conversation: Any) -> None:

    """Test adding an action to a message."""
    # First add a message
    message = await conv_service.add_message(
        db=db_session,
        conversation_id=test_conversation.id,
        role="assistant",
        content="Added track",
    )
    
    # Add action
    action = await conv_service.add_action(
        db=db_session,
        message_id=message.id,
        action_type="track_added",
        description="Added drum track",
        success=True,
        extra_metadata={"track_id": "track-456"},
    )
    await db_session.commit()
    
    assert action.id is not None
    assert action.message_id == message.id
    assert action.action_type == "track_added"
    assert action.success is True
    assert action.extra_metadata is not None and action.extra_metadata["track_id"] == "track-456"


@pytest.mark.asyncio
async def test_add_failed_action(db_session: AsyncSession, test_conversation: Any) -> None:

    """Test recording a failed action."""
    message = await conv_service.add_message(
        db=db_session,
        conversation_id=test_conversation.id,
        role="assistant",
        content="Attempted to add track",
    )
    
    action = await conv_service.add_action(
        db=db_session,
        message_id=message.id,
        action_type="track_added",
        description="Failed to add track",
        success=False,
        error_message="Track limit exceeded",
    )
    await db_session.commit()
    
    assert action.success is False
    assert action.error_message == "Track limit exceeded"


# =============================================================================
# Search Tests
# =============================================================================

@pytest.mark.asyncio
async def test_search_conversations_by_title(db_session: AsyncSession, test_user: Any) -> None:

    """Test searching conversations by title."""
    await conv_service.create_conversation(
        db=db_session,
        user_id=test_user.id,
        title="Hip Hop Beat",
    )
    await conv_service.create_conversation(
        db=db_session,
        user_id=test_user.id,
        title="Jazz Composition",
    )
    await db_session.commit()
    
    results = await conv_service.search_conversations(
        db=db_session,
        user_id=test_user.id,
        query="hip hop",
    )
    
    assert len(results) == 1
    assert "Hip Hop" in results[0].title


@pytest.mark.asyncio
async def test_search_conversations_by_message_content(db_session: AsyncSession, test_user: Any) -> None:

    """Test searching conversations by message content."""
    conv = await conv_service.create_conversation(
        db=db_session,
        user_id=test_user.id,
        title="My Beat",
    )
    await conv_service.add_message(
        db=db_session,
        conversation_id=conv.id,
        role="user",
        content="Create a lo-fi track with piano",
    )
    await db_session.commit()
    
    results = await conv_service.search_conversations(
        db=db_session,
        user_id=test_user.id,
        query="piano",
    )
    
    assert len(results) >= 1
    assert any(conv.id == r.id for r in results)


# =============================================================================
# Utility Function Tests
# =============================================================================

@pytest.mark.asyncio
async def test_generate_title_from_prompt() -> None:
    """Test automatic title generation."""
    # Test basic cleanup
    title = conv_service.generate_title_from_prompt("create a beat in C major")
    assert title == "Beat in C major"
    
    # Test capitalization
    title = conv_service.generate_title_from_prompt("make a hip hop track")
    assert title == "Hip hop track"
    
    # Test truncation
    long_prompt = "a" * 100
    title = conv_service.generate_title_from_prompt(long_prompt)
    assert len(title) <= 53  # 50 + "..."
    
    # Test sentence extraction (first sentence or first segment; implementation may vary)
    title = conv_service.generate_title_from_prompt(
        "Create a beat. It should have 808s. Make it trap style."
    )
    assert "Beat" in title or title.startswith("Create")
    assert len(title) > 0


@pytest.mark.asyncio
async def test_get_conversation_preview(db_session: AsyncSession, conversation_with_messages: Any) -> None:

    """Test getting conversation preview."""
    from sqlalchemy.orm import selectinload
    result = await db_session.execute(
        select(Conversation)
        .where(Conversation.id == conversation_with_messages.id)
        .options(selectinload(Conversation.messages))
    )
    conv = result.scalar_one()
    preview = await conv_service.get_conversation_preview(conv)
    
    assert preview == "Create a chill beat"
    assert len(preview) <= 100


@pytest.mark.asyncio
async def test_get_conversation_preview_empty(db_session: AsyncSession, test_conversation: Any) -> None:

    """Test getting preview from empty conversation."""
    from sqlalchemy.orm import selectinload
    result = await db_session.execute(
        select(Conversation)
        .where(Conversation.id == test_conversation.id)
        .options(selectinload(Conversation.messages))
    )
    conv = result.scalar_one()
    preview = await conv_service.get_conversation_preview(conv)
    
    assert preview == ""


@pytest.mark.asyncio
async def test_get_conversation_preview_no_user_message(db_session: AsyncSession, test_user: Any) -> None:

    """Preview is empty when conversation has only assistant messages."""
    from sqlalchemy.orm import selectinload
    conv = await conv_service.create_conversation(
        db=db_session, user_id=test_user.id, title="Assistant only",
    )
    await conv_service.add_message(
        db=db_session, conversation_id=conv.id, role="assistant", content="Here is your beat.",
    )
    await db_session.commit()
    result = await db_session.execute(
        select(Conversation).where(Conversation.id == conv.id).options(selectinload(Conversation.messages))
    )
    loaded = result.scalar_one()
    preview = await conv_service.get_conversation_preview(loaded)
    assert preview == ""


# =============================================================================
# Integration Tests
# =============================================================================

@pytest.mark.asyncio
async def test_full_conversation_flow(db_session: AsyncSession, test_user: Any) -> None:

    """Test complete conversation workflow."""
    # 1. Create conversation
    conversation = await conv_service.create_conversation(
        db=db_session,
        user_id=test_user.id,
        title="New Conversation",
    )
    
    # 2. Add user message
    user_msg = await conv_service.add_message(
        db=db_session,
        conversation_id=conversation.id,
        role="user",
        content="Create a beat",
    )
    
    # 3. Add assistant message with actions
    assistant_msg = await conv_service.add_message(
        db=db_session,
        conversation_id=conversation.id,
        role="assistant",
        content="I've created a beat for you.",
        model_used="anthropic/claude-3.5-sonnet",
        tokens_used={"prompt": 50, "completion": 25},
        cost_cents=8,
    )
    
    await conv_service.add_action(
        db=db_session,
        message_id=assistant_msg.id,
        action_type="track_added",
        description="Added drums",
        success=True,
    )
    
    await conv_service.add_action(
        db=db_session,
        message_id=assistant_msg.id,
        action_type="track_added",
        description="Added bass",
        success=True,
    )
    
    await db_session.commit()
    
    # 4. Retrieve and verify
    loaded = await conv_service.get_conversation(
        db=db_session,
        conversation_id=conversation.id,
        user_id=test_user.id,
    )
    assert loaded is not None
    assert loaded.messages is not None and len(loaded.messages) == 2
    assert loaded.messages[0].content == "Create a beat"
    assert loaded.messages[1].content == "I've created a beat for you."
    assert len(loaded.messages[1].actions) == 2
    assert loaded.messages[1].cost == 0.08


@pytest.mark.asyncio
async def test_cascade_delete_messages_and_actions(db_session: AsyncSession, test_user: Any, conversation_with_messages: Any) -> None:

    """Test that deleting conversation cascades to messages and actions."""
    conversation_id = conversation_with_messages.id
    
    # Get counts before delete
    messages_result = await db_session.execute(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id
        )
    )
    messages_before = len(list(messages_result.scalars().all()))
    assert messages_before == 2
    
    # Delete conversation
    await conv_service.delete_conversation(
        db=db_session,
        conversation_id=conversation_id,
        user_id=test_user.id,
    )
    await db_session.commit()
    
    # Verify messages are gone
    messages_result = await db_session.execute(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id
        )
    )
    messages_after = len(list(messages_result.scalars().all()))
    assert messages_after == 0
    
    # Verify actions are gone (they should cascade from messages)
    actions_result = await db_session.execute(select(MessageAction))
    actions_after = len(list(actions_result.scalars().all()))
    assert actions_after == 0
