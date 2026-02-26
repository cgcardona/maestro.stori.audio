"""Message and action CRUD operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, ConversationMessage, MessageAction

logger = logging.getLogger(__name__)


async def add_message(
    db: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    model_used: str | None = None,
    tokens_used: dict[str, int] | None = None,
    cost_cents: int = 0,
    tool_calls: list[dict[str, object]] | None = None,
    sse_events: list[dict[str, object]] | None = None,
    extra_metadata: dict[str, object] | None = None,
) -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        model_used=model_used,
        tokens_used=tokens_used,
        cost_cents=cost_cents,
        tool_calls=tool_calls or [],
        sse_events=sse_events or [],
        extra_metadata=extra_metadata,
    )
    db.add(message)
    await db.flush()

    await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conversation = result.scalar_one()
    conversation.updated_at = datetime.now(timezone.utc)

    logger.debug(f"Added {role} message to conversation {conversation_id[:8]}")
    return message


async def add_action(
    db: AsyncSession,
    message_id: str,
    action_type: str,
    description: str,
    success: bool,
    error_message: str | None = None,
    extra_metadata: dict[str, object] | None = None,
) -> MessageAction:
    action = MessageAction(
        message_id=message_id,
        action_type=action_type,
        description=description,
        success=success,
        error_message=error_message,
        extra_metadata=extra_metadata,
    )
    db.add(action)
    await db.flush()

    status = "✓" if success else "✗"
    logger.debug(f"Recorded action {status} {action_type} for message {message_id[:8]}")
    return action
