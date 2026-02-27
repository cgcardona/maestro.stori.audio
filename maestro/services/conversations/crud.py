"""Conversation CRUD operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maestro.contracts.project_types import ProjectContext

from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from maestro.db.models import Conversation, ConversationMessage

logger = logging.getLogger(__name__)


async def create_conversation(
    db: AsyncSession,
    user_id: str,
    title: str = "New Conversation",
    project_id: str | None = None,
    project_context: ProjectContext | None = None,
) -> Conversation:
    conversation = Conversation(
        user_id=user_id,
        title=title,
        project_id=project_id,
        project_context=project_context,
    )
    db.add(conversation)
    await db.flush()
    logger.info(f"Created conversation {conversation.id[:8]} for user {user_id[:8]}")
    return conversation


async def get_conversation(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
) -> Conversation | None:
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages).selectinload(ConversationMessage.actions))
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_conversations(
    db: AsyncSession,
    user_id: str,
    project_id: str | None = None,
    include_global: bool = False,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> tuple[list[Conversation], int]:
    query = select(Conversation).where(Conversation.user_id == user_id)

    if not include_archived:
        query = query.where(Conversation.is_archived == False)

    if project_id == "null":
        query = query.where(Conversation.project_id == None)
    elif project_id:
        if include_global:
            query = query.where(
                or_(Conversation.project_id == project_id, Conversation.project_id == None)
            )
        else:
            query = query.where(Conversation.project_id == project_id)
    elif not include_global:
        query = query.where(Conversation.project_id == None)

    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    query = query.order_by(desc(Conversation.updated_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    conversations = list(result.scalars().all())

    logger.debug(f"Listed {len(conversations)}/{total} conversations for user {user_id[:8]}")
    return conversations, total


async def update_conversation_title(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
    title: str,
) -> Conversation | None:
    conversation = await get_conversation(db, conversation_id, user_id)
    if not conversation:
        return None

    conversation.title = title
    conversation.updated_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info(f"Updated conversation {conversation_id[:8]} title to '{title[:30]}'")
    return conversation


async def archive_conversation(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
) -> bool:
    conversation = await get_conversation(db, conversation_id, user_id)
    if not conversation:
        return False

    conversation.is_archived = True
    conversation.updated_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info(f"Archived conversation {conversation_id[:8]}")
    return True


async def delete_conversation(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
) -> bool:
    conversation = await get_conversation(db, conversation_id, user_id)
    if not conversation:
        return False

    await db.delete(conversation)
    await db.flush()
    logger.info(f"Deleted conversation {conversation_id[:8]}")
    return True
