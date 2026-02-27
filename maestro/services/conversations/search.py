"""Conversation search by title and message content."""

from __future__ import annotations

import logging

from sqlalchemy import select, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from maestro.db.models import Conversation, ConversationMessage

logger = logging.getLogger(__name__)


async def search_conversations(
    db: AsyncSession,
    user_id: str,
    query: str,
    limit: int = 20,
) -> list[Conversation]:
    """Search conversations by title and message content (ILIKE)."""
    search_pattern = f"%{query}%"

    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            or_(
                Conversation.title.ilike(search_pattern),
                Conversation.id.in_(
                    select(ConversationMessage.conversation_id)
                    .where(ConversationMessage.content.ilike(search_pattern))
                ),
            ),
        )
        .options(selectinload(Conversation.messages))
        .order_by(desc(Conversation.updated_at))
        .limit(limit)
    )

    conversations = list(result.scalars().all())
    logger.debug(
        f"Found {len(conversations)} conversations matching '{query}' for user {user_id[:8]}"
    )
    return conversations
