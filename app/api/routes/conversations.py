"""
Conversation history endpoints.

Provides CRUD operations for managing conversation threads and messages.
"""
import json
import logging
import re
from typing import Optional, Any
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.auth.dependencies import require_valid_token
from app.services import conversations as conv_service
from app.models.requests import MaestroRequest
from app.api.routes.maestro import orchestrate, UsageTracker
from app.services.budget import (
    check_budget,
    deduct_budget,
    calculate_cost_cents,
    reserve_budget,
    estimate_request_cost,
    InsufficientBudgetError,
    BudgetError,
)
from app.services.conversations import get_optimized_context

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class ConversationCreateRequest(BaseModel):
    """Request to create a new conversation."""
    title: str = Field(default="New Conversation", max_length=255)
    project_id: Optional[str] = Field(default=None, description="Project UUID to link conversation to")
    project_context: Optional[dict] = Field(default=None)


class ConversationUpdateRequest(BaseModel):
    """Request to update conversation metadata."""
    title: Optional[str] = Field(None, max_length=255)
    project_id: Optional[str] = Field(None, description="Project UUID (set to 'null' string to unlink)")


class ToolCallInfo(BaseModel):
    """
    Tool call information in flat format (hybrid of OpenAI + our storage).
    
    Storage format: {id, type, name, arguments} - flat for easy API consumption
    LLM format: {id, type, function: {name, arguments}} - nested OpenAI standard
    """
    id: Optional[str] = None  # Tool call ID (sanitized for Bedrock compatibility)
    type: str = "function"  # Always "function" for OpenAI compatibility
    name: str  # Tool name (e.g., "stori_add_midi_track")
    arguments: dict  # Tool parameters


class MessageInfo(BaseModel):
    """Message information in conversation responses."""
    model_config = {"from_attributes": True}  # Allow Pydantic to parse dicts as models
    
    id: str
    role: str
    content: str
    timestamp: str
    model_used: Optional[str] = None
    tokens_used: Optional[dict] = None
    cost: float
    tool_calls: Optional[list[ToolCallInfo]] = None  # Properly typed for OpenAI format
    sse_events: Optional[list[dict]] = None  # Complete SSE event stream for replay
    actions: Optional[list[dict]] = None  # Tool execution tracking


class ConversationResponse(BaseModel):
    """Full conversation with messages."""
    id: str
    title: str
    project_id: Optional[str] = None
    created_at: str
    updated_at: str
    is_archived: bool
    project_context: Optional[dict] = None
    messages: list[MessageInfo] = []


class ConversationListItem(BaseModel):
    """Conversation list item (without messages)."""
    id: str
    title: str
    project_id: Optional[str] = None
    created_at: str
    updated_at: str
    is_archived: bool
    message_count: int
    preview: str


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""
    conversations: list[ConversationListItem]
    total: int
    limit: int
    offset: int


class SearchResultItem(BaseModel):
    """Search result item."""
    id: str
    title: str
    preview: str
    updated_at: str
    relevance_score: float = 1.0


class SearchResponse(BaseModel):
    """Search results."""
    results: list[SearchResultItem]


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: ConversationCreateRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new conversation.
    
    Each conversation represents a chat thread with Maestro.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    try:
        # Create conversation
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
        
        # Check if it's a foreign key constraint error (user doesn't exist)
        error_str = str(e).lower()
        if "foreign key" in error_str or "violates foreign key constraint" in error_str:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found. Please register first at /api/v1/users/register."
            )
        
        # Generic error for other database issues
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create conversation: {str(e)}"
        )


@router.get("/conversations/search", response_model=SearchResponse)
async def search_conversations(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, le=50),
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Search conversations by title and message content.
    
    Returns matching conversations with highlighted snippets.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    conversations = await conv_service.search_conversations(
        db=db,
        user_id=user_id,
        query=q,
        limit=limit,
    )
    
    # Build results with previews
    results = []
    for conv in conversations:
        preview = await conv_service.get_conversation_preview(conv)
        
        # Simple relevance scoring (can be enhanced)
        relevance_score = 1.0
        if q.lower() in conv.title.lower():
            relevance_score = 1.0
        else:
            relevance_score = 0.7
        
        results.append(SearchResultItem(
            id=conv.id,
            title=conv.title,
            preview=preview,
            updated_at=conv.updated_at.isoformat(),
            relevance_score=relevance_score,
        ))
    
    return SearchResponse(results=results)


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    project_id: Optional[str] = Query(default=None, description="Filter by project UUID, or 'null' for global conversations"),
    include_global: bool = Query(default=False, description="Include global conversations when filtering by project"),
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    List user's conversations with pagination and project filtering.
    
    Returns conversations ordered by most recently updated.
    
    Query Parameters:
    - project_id: Filter by project UUID, or "null" for global conversations only
    - include_global: When filtering by project_id, also include global conversations
    - limit: Number of results (max 100)
    - offset: Pagination offset
    - include_archived: Include archived conversations
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    conversations, total = await conv_service.list_conversations(
        db=db,
        user_id=user_id,
        project_id=project_id,
        include_global=include_global,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
    )
    
    # Build response with message counts and previews
    items = []
    for conv in conversations:
        # Note: We don't load messages in list view for performance
        # Message count would require a separate query or join
        items.append(ConversationListItem(
            id=conv.id,
            title=conv.title,
            project_id=conv.project_id,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            is_archived=conv.is_archived,
            message_count=0,  # Not loaded in list view for performance
            preview="",  # Not loaded in list view for performance
        ))
    
    return ConversationListResponse(
        conversations=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a conversation with full message history.
    
    Returns all messages, tool calls, and actions.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    conversation = await conv_service.get_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )
    
    # Build messages with actions
    messages = []
    for msg in conversation.messages:
        actions = [
            {
                "id": action.id,
                "action_type": action.action_type,
                "description": action.description,
                "success": action.success,
                "error_message": action.error_message,
                "timestamp": action.timestamp.isoformat(),
            }
            for action in msg.actions
        ] if hasattr(msg, 'actions') else []
        
        # Parse tool_calls from database (JSON field returns raw list of dicts)
        tool_calls_parsed = None
        if msg.tool_calls:
            try:
                # Convert ALL numeric values to strings (Swift expects all args as Strings)
                normalized_calls = []
                for tc in msg.tool_calls:
                    tc_copy = tc.copy()
                    if "arguments" in tc_copy:
                        # Normalize all arguments (converts numbers to strings)
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
            sse_events=msg.sse_events if hasattr(msg, 'sse_events') else None,
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


@router.patch("/conversations/{conversation_id}", response_model=dict)
async def update_conversation(
    conversation_id: str,
    request: ConversationUpdateRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Update conversation metadata (title and/or project_id).
    
    Use Cases:
    - Update title: {"title": "New Title"}
    - Link to project: {"project_id": "project-uuid"}
    - Unlink from project: {"project_id": "null"}
    - Update both: {"title": "New Title", "project_id": "project-uuid"}
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    # Get conversation first
    conversation = await conv_service.get_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )
    
    # Update fields
    updated = False
    
    if request.title is not None:
        conversation.title = request.title
        updated = True
    
    if request.project_id is not None:
        # Handle "null" string to unlink
        if request.project_id == "null":
            conversation.project_id = None
        else:
            conversation.project_id = request.project_id
        updated = True
    
    if updated:
        from datetime import datetime, timezone
        conversation.updated_at = datetime.now(timezone.utc)
        await db.commit()
    
    return {
        "id": conversation.id,
        "title": conversation.title,
        "project_id": conversation.project_id,
        "updated_at": conversation.updated_at.isoformat(),
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    hard_delete: bool = Query(default=False),
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Archive or delete a conversation.
    
    By default, archives (soft delete). Use hard_delete=true to permanently delete.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    if hard_delete:
        success = await conv_service.delete_conversation(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
        )
    else:
        success = await conv_service.archive_conversation(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
        )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )
    
    await db.commit()
    return None


@router.post("/conversations/{conversation_id}/messages")
async def add_message_to_conversation(
    conversation_id: str,
    maestro_request: MaestroRequest,
    request: Request,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Add a message to a conversation and generate AI response.
    
    This endpoint:
    1. Saves the user's message
    2. Streams the AI response (SSE)
    3. Saves the assistant's message with tokens/cost
    4. Auto-generates title if still "New Conversation"
    5. Deducts budget from user
    
    Streams in the same format as /api/v1/maestro/stream.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    # Verify conversation exists and belongs to user
    conversation = await conv_service.get_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )
    
    # Check budget before processing
    try:
        await check_budget(db, user_id)
    except InsufficientBudgetError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e)
        )
    
    # Save user message (respecting store_prompt flag)
    user_message_content = maestro_request.prompt if maestro_request.store_prompt else "[content not stored]"
    user_message = await conv_service.add_message(
        db=db,
        conversation_id=conversation_id,
        role="user",
        content=user_message_content,
    )
    await db.commit()
    
    # Stream AI response
    async def stream_with_save():
        """Stream response and save assistant message with complete SSE event tracking."""
        from datetime import datetime, timezone
        
        usage_tracker = UsageTracker()
        assistant_content_parts = []
        tool_calls_made = []
        sse_events_captured = []
        tool_actions = {}  # Track actions by tool name for updating on complete/error
        assistant_message_id = None
        
        try:
            # Build conversation history for LLM context (CRITICAL for entity ID tracking)
            # Load all previous messages EXCEPT the one we just added
            conversation_history: list[dict[str, Any]] = []
            entity_summary: Optional[str] = None
            if conversation.messages:
                # Get all messages except the last one (which is the user message we just added)
                previous_messages = [m for m in conversation.messages if m.id != user_message.id]
                
                # Use optimized context for long conversations
                conversation_history, entity_summary = await get_optimized_context(
                    previous_messages,
                    max_messages=20,
                    include_entity_summary=True,
                )
                logger.info(f"🔗 Built optimized context: {len(conversation_history)} messages for entity ID tracking")
            
            # Stream the orchestration with conversation history
            async for event in orchestrate(
                prompt=maestro_request.prompt,
                project_context=maestro_request.project,
                model=maestro_request.model,
                usage_tracker=usage_tracker,
                conversation_history=conversation_history,
                is_cancelled=request.is_disconnected,
            ):
                # Parse SSE event
                if event.startswith("data: "):
                    event_data = json.loads(event[6:])
                    event_type = event_data.get("type")
                    
                    # Capture SSE event with timestamp
                    sse_event_record = {
                        "type": event_type,
                        "data": event_data,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    sse_events_captured.append(sse_event_record)
                    logger.debug(f"📡 Captured SSE event: {event_type}")
                    
                    # Track content
                    if event_type == "content":
                        assistant_content_parts.append(event_data.get("content", ""))
                    
                    # Track tool calls (hybrid format: flat structure + id for both API and LLM replay)
                    if event_type == "tool_call":
                        # Get arguments from SSE event
                        arguments = event_data.get("params", {})
                        
                        # Sanitize tool_call_id for Bedrock compatibility (alphanumeric, underscore, hyphen only)
                        # CRITICAL: fallback IDs must be unique — duplicate tool_use IDs
                        # cause Anthropic to reject the request on conversation replay.
                        tool_call_id = event_data.get("id", "")
                        if tool_call_id:
                            sanitized_id = re.sub(r'[^a-zA-Z0-9_-]', '_', tool_call_id)
                        else:
                            sanitized_id = f"call_{uuid.uuid4().hex[:12]}"
                        
                        # Store in FLAT format that works for both ToolCallInfo (API) and LLM replay
                        # We'll convert to nested OpenAI format when replaying to LLM
                        tool_call_storage = {
                            "id": sanitized_id,        # For LLM replay
                            "type": "function",        # For ToolCallInfo
                            "name": event_data.get("name"),  # For ToolCallInfo
                            "arguments": arguments,    # For ToolCallInfo
                        }
                        tool_calls_made.append(tool_call_storage)
                        logger.info(f"📝 Tracked tool_call: {event_data.get('name')} (ID: {sanitized_id})")
                    
                    # Track tool execution for MessageAction table
                    if event_type == "tool_start":
                        tool_name = event_data.get("name")
                        # Create pending action (will be updated on complete/error)
                        tool_actions[tool_name] = {
                            "action_type": "tool_execution",
                            "description": f"Execute {tool_name}",
                            "tool_name": tool_name,
                            "params": event_data.get("params", {}),
                            "start_time": datetime.now(timezone.utc).isoformat(),
                            "success": None,  # Unknown until complete/error
                        }
                        logger.debug(f"🔧 Tool started: {tool_name}")
                    
                    elif event_type == "tool_complete":
                        tool_name = event_data.get("name")
                        if tool_name in tool_actions:
                            tool_actions[tool_name].update({
                                "success": event_data.get("success", True),
                                "result": event_data.get("result"),
                                "end_time": datetime.now(timezone.utc).isoformat(),
                            })
                            logger.debug(f"✅ Tool completed: {tool_name}")
                    
                    elif event_type == "tool_error":
                        tool_name = event_data.get("name")
                        if tool_name in tool_actions:
                            tool_actions[tool_name].update({
                                "success": False,
                                "error_message": event_data.get("error"),
                                "end_time": datetime.now(timezone.utc).isoformat(),
                            })
                            logger.debug(f"❌ Tool error: {tool_name}")
                    
                    # Track variation proposals (for variation mode)
                    elif event_type == "variation_proposal":
                        variation_data = event_data.get("data", {})
                        logger.info(
                            f"🎭 Variation proposal: {variation_data.get('variation_id', 'unknown')[:8]} "
                            f"with {len(variation_data.get('phrases', []))} phrases"
                        )
                    
                    # Yield event to client
                    yield event
            
            # Calculate cost
            total_tokens = usage_tracker.prompt_tokens + usage_tracker.completion_tokens
            cost_cents = calculate_cost_cents(
                model=maestro_request.model or settings.llm_model,
                prompt_tokens=usage_tracker.prompt_tokens,
                completion_tokens=usage_tracker.completion_tokens,
            )
            
            # Save assistant message with SSE events
            assistant_content = "".join(assistant_content_parts)
            logger.info(f"💾 Saving message with {len(tool_calls_made)} tool_calls and {len(sse_events_captured)} SSE events")
            assistant_message = await conv_service.add_message(
                db=db,
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                model_used=maestro_request.model or settings.llm_model,
                tokens_used={
                    "prompt": usage_tracker.prompt_tokens,
                    "completion": usage_tracker.completion_tokens,
                },
                cost_cents=cost_cents,
                tool_calls=tool_calls_made,
                sse_events=sse_events_captured,
            )
            assistant_message_id = assistant_message.id
            
            # Save MessageAction entries for each tool execution
            logger.info(f"📝 Creating {len(tool_actions)} MessageAction entries")
            for tool_name, action_data in tool_actions.items():
                success = action_data.get("success", False)
                await conv_service.add_action(
                    db=db,
                    message_id=assistant_message_id,
                    action_type=action_data["action_type"],
                    description=action_data["description"],
                    success=success if success is not None else False,
                    error_message=action_data.get("error_message"),
                    extra_metadata={
                        "tool_name": tool_name,
                        "params": action_data.get("params"),
                        "result": action_data.get("result"),
                        "start_time": action_data.get("start_time"),
                        "end_time": action_data.get("end_time"),
                    },
                )
            logger.info(f"✅ Saved message with complete tracking data")
            
            # Deduct budget
            await deduct_budget(
                db=db,
                user_id=user_id,
                cost_cents=cost_cents,
                model=maestro_request.model or settings.llm_model,
                prompt_tokens=usage_tracker.prompt_tokens,
                completion_tokens=usage_tracker.completion_tokens,
                prompt=user_message_content if maestro_request.store_prompt else None,
            )
            
            # Auto-generate title if needed
            if conversation.title == "New Conversation" and maestro_request.prompt:
                new_title = conv_service.generate_title_from_prompt(maestro_request.prompt)
                await conv_service.update_conversation_title(
                    db=db,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    title=new_title,
                )
            
            await db.commit()
            
            # Send budget update event
            budget_remaining = await get_user_budget(db, user_id)
            yield await sse_event({
                "type": "budget_update",
                "budget_remaining": budget_remaining,
                "cost": cost_cents / 100.0,
            })
            
            logger.info(
                f"Conversation {conversation_id[:8]}: "
                f"tokens={total_tokens} cost=${cost_cents/100:.4f}"
            )
            
        except Exception as e:
            logger.error(f"Error in conversation message stream: {e}", exc_info=True)
            await db.rollback()
            yield await sse_event({
                "type": "error",
                "error": str(e),
            })
    
    return StreamingResponse(
        stream_with_save(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# =============================================================================
# Helper Functions
# =============================================================================

def build_conversation_history_for_llm(messages: list) -> list[dict[str, Any]]:
    """
    Build conversation history in OpenAI format for LLM context.
    
    This is CRITICAL for entity ID tracking - the LLM needs to see previous
    tool calls and their parameters (including generated UUIDs) to reuse them
    in subsequent tool calls.
    
    Args:
        messages: List of Message objects from database
    
    Returns:
        List of messages in OpenAI format:
        - {"role": "user", "content": "..."}
        - {"role": "assistant", "content": "...", "tool_calls": [...]}
        - {"role": "tool", "tool_call_id": "...", "content": "..."}
    """
    history = []
    
    for msg in messages:
        if msg.role == "user":
            # User messages are simple
            history.append({
                "role": "user",
                "content": msg.content or ""
            })
        
        elif msg.role == "assistant":
            # Assistant messages may have tool calls
            assistant_msg = {
                "role": "assistant",
                "content": msg.content or ""
            }
            
            # Add tool calls if present (convert from flat storage to OpenAI nested format)
            if msg.tool_calls:
                openai_tool_calls = []
                seen_ids: set[str] = set()
                for tc in msg.tool_calls:
                    tc_id = tc.get("id", "")
                    # Ensure uniqueness — Anthropic rejects duplicate tool_use IDs
                    if not tc_id or tc_id in seen_ids:
                        tc_id = f"call_{uuid.uuid4().hex[:12]}"
                    seen_ids.add(tc_id)
                    openai_tool_calls.append({
                        "id": tc_id,
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(tc.get("arguments", {}))
                        }
                    })
                assistant_msg["tool_calls"] = openai_tool_calls
            
            history.append(assistant_msg)
            
            # Add tool results matched to the deduplicated IDs above
            if msg.tool_calls and openai_tool_calls:
                for otc in openai_tool_calls:
                    history.append({
                        "role": "tool",
                        "tool_call_id": otc["id"],
                        "content": json.dumps({
                            "success": True,
                            "message": f"Tool {otc['function']['name']} executed successfully"
                        })
                    })
    
    return history


def normalize_tool_arguments(arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Normalize tool arguments for API responses.

    Converts all numeric values (int, float) to strings for Swift/client
    compatibility where argument types are expected as strings.
    """
    if arguments is None or not arguments:
        return arguments
    out: dict[str, Any] = {}
    for k, v in arguments.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = normalize_tool_arguments(v)
        elif isinstance(v, list):
            out[k] = [
                normalize_tool_arguments(x) if isinstance(x, dict) else (str(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else x)
                for x in v
            ]
        else:
            out[k] = v
    return out


async def sse_event(data: dict) -> str:
    """Format data as an SSE event."""
    import json
    return f"data: {json.dumps(data)}\n\n"


async def get_user_budget(db: AsyncSession, user_id: str) -> float:
    """Get user's remaining budget in dollars."""
    from app.db.models import User
    from sqlalchemy import select
    
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    return user.budget_remaining if user else 0.0
