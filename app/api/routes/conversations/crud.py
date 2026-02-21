"""CRUD endpoints: create, list, get, update, delete conversations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.auth.dependencies import require_valid_token
from app.services import conversations as conv_service
from app.api.routes.conversations.models import (
    ConversationCreateRequest,
    ConversationUpdateRequest,
    ConversationResponse,
    ConversationListItem,
    ConversationListResponse,
    SearchResultItem,
    SearchResponse,
    MessageInfo,
    ToolCallInfo,
)
from app.api.routes.conversations.helpers import normalize_tool_arguments

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/conversations", response_model=ConversationResponse, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: ConversationCreateRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation."""
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain user ID.")

    try:
        conversation = await conv_service.create_conversation(
            db=db,
            user_id=user_id,
            title=request.title,
            project_id=request.project_id,
            project_context=request.project_context,
        )
        await db.commit()

        logger.info(f"User {user_id[:8]} created conversation {conversation.id[:8]}")

        return ConversationResponse(
            id=conversation.id,
            title=conversation.title,
            project_id=conversation.project_id,
            created_at=conversation.created_at.isoformat(),
            updated_at=conversation.updated_at.isoformat(),
            is_archived=conversation.is_archived,
            project_context=conversation.project_context,
            messages=[],
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create conversation for user {user_id[:8]}: {e}", exc_info=True)
        error_str = str(e).lower()
        if "foreign key" in error_str or "violates foreign key constraint" in error_str:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found. Please register first at /api/v1/users/register.",
            )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create conversation: {str(e)}")


@router.get("/conversations/search", response_model=SearchResponse, response_model_by_alias=True)
async def search_conversations(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, le=50),
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """Search conversations by title and message content."""
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain user ID.")

    conversations = await conv_service.search_conversations(db=db, user_id=user_id, query=q, limit=limit)

    results = []
    for conv in conversations:
        preview = await conv_service.get_conversation_preview(conv)
        relevance_score = 1.0 if q.lower() in conv.title.lower() else 0.7
        results.append(SearchResultItem(
            id=conv.id,
            title=conv.title,
            preview=preview,
            updated_at=conv.updated_at.isoformat(),
            relevance_score=relevance_score,
        ))

    return SearchResponse(results=results)


@router.get("/conversations", response_model=ConversationListResponse, response_model_by_alias=True)
async def list_conversations(
    project_id: Optional[str] = Query(default=None, description="Filter by project UUID, or 'null' for global conversations"),
    include_global: bool = Query(default=False, description="Include global conversations when filtering by project"),
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """List user's conversations with pagination and project filtering."""
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain user ID.")

    conversations, total = await conv_service.list_conversations(
        db=db,
        user_id=user_id,
        project_id=project_id,
        include_global=include_global,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
    )

    items = []
    for conv in conversations:
        items.append(ConversationListItem(
            id=conv.id,
            title=conv.title,
            project_id=conv.project_id,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            is_archived=conv.is_archived,
            message_count=0,
            preview="",
        ))

    return ConversationListResponse(conversations=items, total=total, limit=limit, offset=offset)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse, response_model_by_alias=True)
async def get_conversation(
    conversation_id: str,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """Get a conversation with full message history."""
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain user ID.")

    conversation = await conv_service.get_conversation(db=db, conversation_id=conversation_id, user_id=user_id)

    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    messages = []
    for msg in conversation.messages:
        actions = [
            {
                "id": action.id,
                "actionType": action.action_type,
                "description": action.description,
                "success": action.success,
                "errorMessage": action.error_message,
                "timestamp": action.timestamp.isoformat(),
            }
            for action in msg.actions
        ] if hasattr(msg, "actions") else []

        tool_calls_parsed = None
        if msg.tool_calls:
            try:
                normalized_calls = []
                for tc in msg.tool_calls:
                    tc_copy = tc.copy()
                    if "arguments" in tc_copy:
                        tc_copy["arguments"] = normalize_tool_arguments(tc_copy["arguments"])
                    normalized_calls.append(tc_copy)
                tool_calls_parsed = [ToolCallInfo(**tc) for tc in normalized_calls]
            except Exception as e:
                logger.warning(f"Failed to parse tool_calls for message {msg.id}: {e}")

        messages.append(MessageInfo(
            id=msg.id,
            role=msg.role,
            content=msg.content,
            timestamp=msg.timestamp.isoformat(),
            model_used=msg.model_used,
            tokens_used=msg.tokens_used,
            cost=msg.cost,
            tool_calls=tool_calls_parsed,
            sse_events=msg.sse_events if hasattr(msg, "sse_events") else None,
            actions=actions,
        ))

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        project_id=conversation.project_id,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        is_archived=conversation.is_archived,
        project_context=conversation.project_context,
        messages=messages,
    )


@router.patch("/conversations/{conversation_id}", response_model=dict, response_model_by_alias=True)
async def update_conversation(
    conversation_id: str,
    request: ConversationUpdateRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """Update conversation metadata (title and/or project_id)."""
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain user ID.")

    conversation = await conv_service.get_conversation(db=db, conversation_id=conversation_id, user_id=user_id)

    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    updated = False

    if request.title is not None:
        conversation.title = request.title
        updated = True

    if request.project_id is not None:
        conversation.project_id = None if request.project_id == "null" else request.project_id
        updated = True

    if updated:
        conversation.updated_at = datetime.now(timezone.utc)
        await db.commit()

    return {
        "id": conversation.id,
        "title": conversation.title,
        "projectId": conversation.project_id,
        "updatedAt": conversation.updated_at.isoformat(),
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    hard_delete: bool = Query(default=False),
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """Archive or delete a conversation."""
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain user ID.")

    if hard_delete:
        success = await conv_service.delete_conversation(db=db, conversation_id=conversation_id, user_id=user_id)
    else:
        success = await conv_service.archive_conversation(db=db, conversation_id=conversation_id, user_id=user_id)

    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    await db.commit()
    return None
