"""
Database layer tests including schema, constraints, and relationships.

Tests:
- Model relationships
- Cascade deletes
- Constraints
- Data integrity
- PostgreSQL-specific features
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db.models import (
    User,
    UsageLog,
    AccessToken,
    Conversation,
    ConversationMessage,
    MessageAction,
)


# =============================================================================
# User Model Tests
# =============================================================================

@pytest.mark.asyncio
async def test_user_creation(db_session: AsyncSession) -> None:

    """Test creating a user with all fields."""
    user = User(
        id="test-user-db-123",
        budget_cents=500,
        budget_limit_cents=500,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    
    assert user.id == "test-user-db-123"
    assert user.budget_cents == 500
    assert user.budget_limit_cents == 500
    assert user.created_at is not None
    assert user.updated_at is not None


@pytest.mark.asyncio
async def test_user_budget_properties(db_session: AsyncSession) -> None:

    """Test user budget calculation properties."""
    user = User(
        id="test-user-props",
        budget_cents=350,
        budget_limit_cents=500,
    )
    db_session.add(user)
    await db_session.commit()
    
    assert user.budget_remaining == 3.50
    assert user.budget_spent == 1.50
    assert user.budget_limit == 5.00


# =============================================================================
# Conversation Model Tests
# =============================================================================

@pytest.mark.asyncio
async def test_conversation_creation(db_session: AsyncSession) -> None:

    """Test creating a conversation."""
    user = User(id="conv-test-user", budget_cents=500, budget_limit_cents=500)
    db_session.add(user)
    
    conversation = Conversation(
        user_id=user.id,
        title="Test Conv",
        project_context={"tempo": 120},
    )
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation)
    
    assert conversation.id is not None
    assert conversation.user_id == user.id
    assert conversation.title == "Test Conv"
    assert conversation.project_context is not None and conversation.project_context["tempo"] == 120
    assert conversation.is_archived is False


@pytest.mark.asyncio
async def test_conversation_cascade_delete(db_session: AsyncSession) -> None:

    """Test that deleting user cascades to conversations."""
    user = User(id="cascade-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    
    db_session.add(user)
    db_session.add(conversation)
    await db_session.commit()
    
    conversation_id = conversation.id
    
    # Delete user
    await db_session.delete(user)
    await db_session.commit()
    
    # Verify conversation is gone
    result = await db_session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    assert result.scalar_one_or_none() is None


# =============================================================================
# Message Model Tests
# =============================================================================

@pytest.mark.asyncio
async def test_message_creation(db_session: AsyncSession) -> None:

    """Test creating a message."""
    user = User(id="msg-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    db_session.add_all([user, conversation])
    await db_session.commit()
    
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Test message",
        cost_cents=25,
    )
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)
    
    assert message.id is not None
    assert message.conversation_id == conversation.id
    assert message.role == "user"
    assert message.content == "Test message"
    assert message.cost_cents == 25
    assert message.cost == 0.25


@pytest.mark.asyncio
async def test_message_with_tokens_and_tools(db_session: AsyncSession) -> None:

    """Test message with token usage and tool calls."""
    user = User(id="tools-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    db_session.add_all([user, conversation])
    await db_session.commit()
    
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="Response",
        model_used="anthropic/claude-3.5-sonnet",
        tokens_used={"prompt": 100, "completion": 50},
        cost_cents=12,
        tool_calls=[
            {"name": "add_track", "type": "function"},
            {"name": "set_tempo", "type": "function"},
        ],
        extra_metadata={"user_feedback": "good"},
    )
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)
    
    assert message.model_used == "anthropic/claude-3.5-sonnet"
    assert message.tokens_used is not None
    assert message.tokens_used["prompt"] == 100
    assert message.tokens_used["completion"] == 50
    assert message.tool_calls is not None and len(message.tool_calls) == 2
    assert message.extra_metadata is not None and message.extra_metadata["user_feedback"] == "good"


@pytest.mark.asyncio
async def test_message_cascade_from_conversation(db_session: AsyncSession) -> None:

    """Test that deleting conversation cascades to messages."""
    user = User(id="cascade-msg-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    db_session.add_all([user, conversation])
    await db_session.commit()
    
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Test",
    )
    db_session.add(message)
    await db_session.commit()
    
    message_id = message.id
    
    # Delete conversation
    await db_session.delete(conversation)
    await db_session.commit()
    
    # Verify message is gone
    result = await db_session.execute(
        select(ConversationMessage).where(ConversationMessage.id == message_id)
    )
    assert result.scalar_one_or_none() is None


# =============================================================================
# Action Model Tests
# =============================================================================

@pytest.mark.asyncio
async def test_action_creation(db_session: AsyncSession) -> None:

    """Test creating an action."""
    user = User(id="action-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    db_session.add_all([user, conversation])
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="Done",
    )
    db_session.add(message)
    await db_session.commit()
    
    action = MessageAction(
        message_id=message.id,
        action_type="track_added",
        description="Added drums",
        success=True,
        extra_metadata={"track_id": "123"},
    )
    db_session.add(action)
    await db_session.commit()
    await db_session.refresh(action)
    
    assert action.id is not None
    assert action.message_id == message.id
    assert action.action_type == "track_added"
    assert action.success is True
    assert action.extra_metadata is not None and action.extra_metadata["track_id"] == "123"


@pytest.mark.asyncio
async def test_action_failure_recording(db_session: AsyncSession) -> None:

    """Test recording a failed action."""
    user = User(id="fail-action-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    db_session.add_all([user, conversation])
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="Tried to add track",
    )
    db_session.add(message)
    await db_session.commit()
    
    action = MessageAction(
        message_id=message.id,
        action_type="track_added",
        description="Failed to add track",
        success=False,
        error_message="Track limit exceeded",
    )
    db_session.add(action)
    await db_session.commit()
    
    assert action.success is False
    assert action.error_message == "Track limit exceeded"


@pytest.mark.asyncio
async def test_action_cascade_from_message(db_session: AsyncSession) -> None:

    """Test that deleting message cascades to actions."""
    user = User(id="cascade-action-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    db_session.add_all([user, conversation])
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="Test",
    )
    db_session.add(message)
    await db_session.commit()
    
    action = MessageAction(
        message_id=message.id,
        action_type="test",
        description="Test action",
        success=True,
    )
    db_session.add(action)
    await db_session.commit()
    
    action_id = action.id
    
    # Delete message
    await db_session.delete(message)
    await db_session.commit()
    
    # Verify action is gone
    result = await db_session.execute(
        select(MessageAction).where(MessageAction.id == action_id)
    )
    assert result.scalar_one_or_none() is None


# =============================================================================
# Relationship Tests
# =============================================================================

@pytest.mark.asyncio
async def test_user_conversation_relationship(db_session: AsyncSession) -> None:

    """Test user-conversation relationship loading."""
    user = User(id="rel-user", budget_cents=500, budget_limit_cents=500)
    conv1 = Conversation(user_id=user.id, title="Conv 1")
    conv2 = Conversation(user_id=user.id, title="Conv 2")
    
    db_session.add_all([user, conv1, conv2])
    await db_session.commit()
    
    # Load user with conversations (eager load to avoid async lazy-load)
    result = await db_session.execute(
        select(User).where(User.id == user.id).options(selectinload(User.conversations))
    )
    loaded_user = result.scalar_one()
    assert len(loaded_user.conversations) == 2


@pytest.mark.asyncio
async def test_conversation_message_relationship(db_session: AsyncSession) -> None:

    """Test conversation-message relationship loading."""
    user = User(id="conv-msg-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    db_session.add_all([user, conversation])
    await db_session.flush()
    msg1 = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Message 1",
    )
    msg2 = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="Message 2",
    )
    db_session.add_all([msg1, msg2])
    await db_session.commit()
    
    # Load conversation with messages (eager load to avoid async lazy-load)
    result = await db_session.execute(
        select(Conversation)
        .where(Conversation.id == conversation.id)
        .options(selectinload(Conversation.messages))
    )
    loaded_conv = result.scalar_one()
    assert len(loaded_conv.messages) == 2


@pytest.mark.asyncio
async def test_message_action_relationship(db_session: AsyncSession) -> None:

    """Test message-action relationship loading."""
    user = User(id="msg-action-user", budget_cents=500, budget_limit_cents=500)
    conversation = Conversation(user_id=user.id, title="Test")
    db_session.add_all([user, conversation])
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="Test",
    )
    db_session.add(message)
    await db_session.flush()
    action1 = MessageAction(
        message_id=message.id,
        action_type="action1",
        description="Action 1",
        success=True,
    )
    action2 = MessageAction(
        message_id=message.id,
        action_type="action2",
        description="Action 2",
        success=True,
    )
    db_session.add_all([action1, action2])
    await db_session.commit()
    
    # Load message with actions (eager load to avoid async lazy-load)
    result = await db_session.execute(
        select(ConversationMessage)
        .where(ConversationMessage.id == message.id)
        .options(selectinload(ConversationMessage.actions))
    )
    loaded_msg = result.scalar_one()
    assert len(loaded_msg.actions) == 2


# =============================================================================
# UsageLog and AccessToken Tests
# =============================================================================

@pytest.mark.asyncio
async def test_usage_log_creation(db_session: AsyncSession) -> None:

    """Test creating a usage log."""
    user = User(id="usage-user", budget_cents=500, budget_limit_cents=500)
    db_session.add(user)
    await db_session.commit()
    
    log = UsageLog(
        user_id=user.id,
        prompt="Test prompt",
        model="test-model",
        prompt_tokens=100,
        completion_tokens=50,
        cost_cents=25,
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    
    assert log.id is not None
    assert log.user_id == user.id
    assert log.total_tokens == 150
    assert log.cost == 0.25


@pytest.mark.asyncio
async def test_access_token_creation(db_session: AsyncSession) -> None:

    """Test creating an access token."""
    user = User(id="token-user", budget_cents=500, budget_limit_cents=500)
    db_session.add(user)
    await db_session.commit()
    
    from datetime import datetime, timedelta
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    
    token = AccessToken(
        user_id=user.id,
        token_hash="test-hash-123",
        expires_at=expires_at,
        revoked=False,
    )
    db_session.add(token)
    await db_session.commit()
    await db_session.refresh(token)
    
    assert token.id is not None
    assert token.user_id == user.id
    assert token.revoked is False
