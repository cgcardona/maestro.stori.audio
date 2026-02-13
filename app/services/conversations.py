"""
Conversation management service.

Handles CRUD operations for conversations, messages, and actions.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Conversation,
    ConversationMessage,
    MessageAction,
    User,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Conversation CRUD
# =============================================================================

async def create_conversation(
    db: AsyncSession,
    user_id: str,
    title: str = "New Conversation",
    project_id: Optional[str] = None,
    project_context: Optional[dict] = None,
) -> Conversation:
    """
    Create a new conversation for a user.
    
    Args:
        db: Database session
        user_id: User UUID
        title: Conversation title
        project_id: Optional project UUID (None for global conversations)
        project_context: Optional project metadata snapshot
        
    Returns:
        Created Conversation instance
    """
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
) -> Optional[Conversation]:
    """
    Get a conversation by ID, verifying ownership.
    
    Args:
        db: Database session
        conversation_id: Conversation UUID
        user_id: User UUID (for authorization)
        
    Returns:
        Conversation if found and owned by user, None otherwise
    """
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
    project_id: Optional[str] = None,
    include_global: bool = False,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> tuple[list[Conversation], int]:
    """
    List user's conversations with pagination and project filtering.
    
    Args:
        db: Database session
        user_id: User UUID
        project_id: Optional project UUID to filter by
            - If provided: Return conversations for that project
            - If None and include_global=False: Return only global conversations
            - If provided and include_global=True: Return project + global conversations
        include_global: Include global conversations (project_id is null)
        limit: Number of conversations to return
        offset: Pagination offset
        include_archived: Include archived conversations
        
    Returns:
        Tuple of (conversations list, total count)
    """
    # Build base query
    query = select(Conversation).where(Conversation.user_id == user_id)
    
    if not include_archived:
        query = query.where(Conversation.is_archived == False)
    
    # Apply project filtering
    if project_id == "null":
        # Explicitly requesting global conversations only
        query = query.where(Conversation.project_id == None)
    elif project_id:
        if include_global:
            # Project conversations + global conversations
            query = query.where(
                or_(
                    Conversation.project_id == project_id,
                    Conversation.project_id == None
                )
            )
        else:
            # Only this project's conversations
            query = query.where(Conversation.project_id == project_id)
    elif not include_global:
        # No project_id specified and not including global -> only global
        query = query.where(Conversation.project_id == None)
    
    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0
    
    # Get paginated results
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
) -> Optional[Conversation]:
    """
    Update conversation title.
    
    Args:
        db: Database session
        conversation_id: Conversation UUID
        user_id: User UUID (for authorization)
        title: New title
        
    Returns:
        Updated Conversation or None if not found/authorized
    """
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
    """
    Archive a conversation (soft delete).
    
    Args:
        db: Database session
        conversation_id: Conversation UUID
        user_id: User UUID (for authorization)
        
    Returns:
        True if archived, False if not found/authorized
    """
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
    """
    Permanently delete a conversation.
    
    Args:
        db: Database session
        conversation_id: Conversation UUID
        user_id: User UUID (for authorization)
        
    Returns:
        True if deleted, False if not found/authorized
    """
    conversation = await get_conversation(db, conversation_id, user_id)
    if not conversation:
        return False
    
    await db.delete(conversation)
    await db.flush()
    
    logger.info(f"Deleted conversation {conversation_id[:8]}")
    return True


# =============================================================================
# Message CRUD
# =============================================================================

async def add_message(
    db: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    model_used: Optional[str] = None,
    tokens_used: Optional[dict] = None,
    cost_cents: int = 0,
    tool_calls: Optional[list] = None,
    sse_events: Optional[list] = None,
    extra_metadata: Optional[dict] = None,
) -> ConversationMessage:
    """
    Add a message to a conversation.
    
    Args:
        db: Database session
        conversation_id: Conversation UUID
        role: Message role ('user', 'assistant', 'system')
        content: Message content
        model_used: Model identifier (for assistant messages)
        tokens_used: Token usage dict {"prompt": N, "completion": M}
        cost_cents: Cost in cents
        tool_calls: List of tool calls made
        sse_events: Complete SSE event stream with timestamps
        metadata: Additional metadata
        
    Returns:
        Created ConversationMessage instance
    """
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
    
    # Update conversation timestamp
    await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
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
    error_message: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> MessageAction:
    """
    Record an action performed during message execution.
    
    Args:
        db: Database session
        message_id: Message UUID
        action_type: Action type (track_added, region_created, etc.)
        description: Human-readable description
        success: Whether action succeeded
        error_message: Error message if failed
        metadata: Additional context
        
    Returns:
        Created MessageAction instance
    """
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


# =============================================================================
# Search
# =============================================================================

async def search_conversations(
    db: AsyncSession,
    user_id: str,
    query: str,
    limit: int = 20,
) -> list[Conversation]:
    """
    Search conversations by title and message content.
    
    Args:
        db: Database session
        user_id: User UUID
        query: Search query
        limit: Results limit
        
    Returns:
        List of matching conversations
    """
    # Simple ILIKE search (PostgreSQL full-text search can be added later)
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
                )
            )
        )
        .options(selectinload(Conversation.messages))
        .order_by(desc(Conversation.updated_at))
        .limit(limit)
    )
    
    conversations = list(result.scalars().all())
    logger.debug(f"Found {len(conversations)} conversations matching '{query}' for user {user_id[:8]}")
    return conversations


# =============================================================================
# Utilities
# =============================================================================

def generate_title_from_prompt(prompt: str, max_length: int = 50) -> str:
    """
    Generate a concise title from a user prompt.
    
    Args:
        prompt: User's first message
        max_length: Maximum title length
        
    Returns:
        Generated title string
    """
    # Clean up the prompt
    title = prompt.strip()
    
    # Remove common prefixes
    prefixes = [
        "create a ",
        "make a ",
        "generate a ",
        "can you ",
        "please ",
        "i want to ",
        "i need to ",
        "help me ",
    ]
    for prefix in prefixes:
        if title.lower().startswith(prefix):
            title = title[len(prefix):]
            break
    
    # Capitalize first letter
    title = title[0].upper() + title[1:] if title else title
    
    # Truncate at sentence end or max length
    if len(title) > max_length:
        # Try to break at a period
        period_idx = title[:max_length].rfind('.')
        if period_idx > max_length // 2:
            title = title[:period_idx + 1]
        else:
            # Break at last space before max_length
            space_idx = title[:max_length].rfind(' ')
            if space_idx > 0:
                title = title[:space_idx] + "..."
            else:
                title = title[:max_length] + "..."
    
    return title


async def get_conversation_preview(conversation: Conversation) -> str:
    """
    Get a preview snippet from the first user message.
    
    Args:
        conversation: Conversation instance with messages loaded
        
    Returns:
        Preview string (first 100 chars of first user message)
    """
    for message in conversation.messages:
        if message.role == "user":
            preview = message.content.strip()
            return preview[:100] + "..." if len(preview) > 100 else preview
    
    return ""


def _sanitize_tool_call_id(tool_call_id: str) -> str:
    """
    Sanitize tool_call_id to match Bedrock's required pattern: ^[a-zA-Z0-9_-]+$
    
    Bedrock rejects tool_call_ids with special characters, so we replace
    any invalid characters with underscores.
    """
    import re
    # Replace any character that's not alphanumeric, underscore, or hyphen
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', tool_call_id)
    return sanitized


def format_conversation_history(conversation: Conversation) -> list[dict]:
    """
    Format conversation messages for LLM context.
    
    Converts stored messages into the format expected by LLM APIs:
    - User messages: {"role": "user", "content": "..."}
    - Assistant messages: {"role": "assistant", "content": "...", "tool_calls": [...]}
    - Tool results: {"role": "tool", "tool_call_id": "...", "content": "..."}
    
    Args:
        conversation: Conversation instance with messages loaded (via selectinload)
        
    Returns:
        List of message dictionaries ready for LLM API
    """
    formatted_messages = []
    
    for message in conversation.messages:
        if message.role == "user":
            formatted_messages.append({
                "role": "user",
                "content": message.content,
            })
        
        elif message.role == "assistant":
            msg = {
                "role": "assistant",
                "content": message.content or "",
            }
            
            # Include tool calls if present
            if message.tool_calls:
                # Convert from flat storage format to nested OpenAI format for LLM
                openai_tool_calls = []
                for tc in message.tool_calls:
                    # Storage format: {id, type, name, arguments}
                    # LLM expects: {id, type, function: {name, arguments as JSON string}}
                    tool_call_id = tc.get("id", "")
                    tool_name = tc.get("name", "")
                    tool_args = tc.get("arguments", {})
                    
                    # Sanitize ID as safety net (should already be sanitized when saved)
                    tool_call_id = _sanitize_tool_call_id(tool_call_id) if tool_call_id else "unknown"
                    
                    # OpenAI requires arguments as JSON string, not dict
                    arguments_str = json.dumps(tool_args) if isinstance(tool_args, dict) else str(tool_args)
                    
                    openai_format = {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": arguments_str  # JSON string, not dict
                        }
                    }
                    openai_tool_calls.append(openai_format)
                
                msg["tool_calls"] = openai_tool_calls
                formatted_messages.append(msg)
                
                # Add tool results as separate messages (matched by tool_call id)
                for tc, openai_tc in zip(message.tool_calls, openai_tool_calls):
                    tool_name = tc.get("name", "")
                    tool_call_id = openai_tc["id"]
                    
                    # Find corresponding action to get output
                    output = {}
                    if message.actions:
                        for action in message.actions:
                            if action.action_type == tool_name or tool_name in action.description:
                                output = action.extra_metadata or {}
                                break
                    
                    # Format tool result in OpenAI format
                    tool_result = {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": str(output),
                    }
                    formatted_messages.append(tool_result)
            else:
                # Assistant message without tool calls (pure text response)
                formatted_messages.append(msg)
    
    logger.info(f"Formatted {len(formatted_messages)} messages from conversation history")
    return formatted_messages


# =============================================================================
# Context Management (for long conversations)
# =============================================================================

MAX_CONTEXT_MESSAGES = 20  # Maximum recent messages to include
MAX_CONTEXT_TOKENS = 8000  # Rough token estimate limit


async def get_optimized_context(
    messages: list[ConversationMessage],
    max_messages: int = MAX_CONTEXT_MESSAGES,
    include_entity_summary: bool = True,
) -> tuple[list[dict], Optional[str]]:
    """
    Get optimized conversation context for LLM.
    
    For short conversations: return all messages.
    For long conversations: return summary + recent messages.
    
    Args:
        messages: All messages in conversation
        max_messages: Maximum number of recent messages to include
        include_entity_summary: Include summary of created entities
        
    Returns:
        Tuple of (formatted_messages, entity_summary)
    """
    if len(messages) <= max_messages:
        # Short conversation - include everything
        formatted = []
        for msg in messages:
            formatted.extend(_format_single_message(msg))
        return formatted, None
    
    # Long conversation - summarize old messages
    old_messages = messages[:-max_messages]
    recent_messages = messages[-max_messages:]
    
    # Extract entity summary from old messages
    entity_summary = _extract_entity_summary(old_messages) if include_entity_summary else None
    
    # Build context summary
    context_summary = _build_context_summary(old_messages)
    
    # Format recent messages
    formatted = []
    
    # Add summary as system context
    if context_summary:
        formatted.append({
            "role": "system",
            "content": f"Previous conversation summary ({len(old_messages)} messages):\n{context_summary}"
        })
    
    # Add entity summary if available
    if entity_summary:
        formatted.append({
            "role": "system", 
            "content": f"Entities created in previous messages:\n{entity_summary}"
        })
    
    # Add recent messages
    for msg in recent_messages:
        formatted.extend(_format_single_message(msg))
    
    logger.info(
        f"📚 Optimized context: {len(old_messages)} old → summary, "
        f"{len(recent_messages)} recent → full"
    )
    
    return formatted, entity_summary


def _format_single_message(message: ConversationMessage) -> list[dict]:
    """Format a single message for LLM context."""
    formatted = []
    
    if message.role == "user":
        formatted.append({"role": "user", "content": message.content})
    
    elif message.role == "assistant":
        msg = {"role": "assistant", "content": message.content or ""}
        
        if message.tool_calls:
            openai_tool_calls = []
            for tc in message.tool_calls:
                tool_call_id = _sanitize_tool_call_id(tc.get("id", "")) or "unknown"
                openai_format = {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(tc.get("arguments", {}))
                    }
                }
                openai_tool_calls.append(openai_format)
            
            msg["tool_calls"] = openai_tool_calls
            formatted.append(msg)
            
            # Add tool results
            for tc, openai_tc in zip(message.tool_calls, openai_tool_calls):
                formatted.append({
                    "role": "tool",
                    "tool_call_id": openai_tc["id"],
                    "content": json.dumps({"status": "success"}),
                })
        else:
            formatted.append(msg)
    
    return formatted


def _extract_entity_summary(messages: list[ConversationMessage]) -> str:
    """
    Extract summary of entities created in messages.
    
    This is crucial for entity ID tracking across long conversations.
    """
    tracks = {}  # name -> id
    regions = {}  # name -> id
    buses = {}  # name -> id
    
    for msg in messages:
        if not msg.tool_calls:
            continue
        
        for tc in msg.tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            
            if name == "stori_add_midi_track":
                track_name = args.get("name", "")
                track_id = args.get("trackId", "")
                if track_name and track_id:
                    tracks[track_name] = track_id
            
            elif name == "stori_add_midi_region":
                region_name = args.get("name", "")
                region_id = args.get("regionId", "")
                if region_name and region_id:
                    regions[region_name] = region_id
            
            elif name == "stori_ensure_bus":
                bus_name = args.get("name", "")
                bus_id = args.get("busId", "")
                if bus_name and bus_id:
                    buses[bus_name] = bus_id
    
    if not tracks and not regions and not buses:
        return ""
    
    lines = []
    if tracks:
        lines.append(f"Tracks: {', '.join(f'{n}={id[:8]}' for n, id in tracks.items())}")
    if regions:
        lines.append(f"Regions: {', '.join(f'{n}={id[:8]}' for n, id in regions.items())}")
    if buses:
        lines.append(f"Buses: {', '.join(f'{n}={id[:8]}' for n, id in buses.items())}")
    
    return "\n".join(lines)


def _build_context_summary(messages: list[ConversationMessage]) -> str:
    """
    Build a natural language summary of conversation history.
    
    This is a simple extractive summary. For production, consider
    using an LLM to generate abstractive summaries.
    """
    user_intents = []
    actions_taken = []
    
    for msg in messages:
        if msg.role == "user":
            # Extract first sentence or truncate
            content = msg.content.strip()
            if len(content) > 100:
                content = content[:97] + "..."
            user_intents.append(content)
        
        elif msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name", "").replace("stori_", "")
                actions_taken.append(name)
    
    summary_parts = []
    
    if user_intents:
        summary_parts.append(f"User requests: {'; '.join(user_intents[-5:])}")
    
    if actions_taken:
        # Deduplicate and count
        action_counts = {}
        for a in actions_taken:
            action_counts[a] = action_counts.get(a, 0) + 1
        
        action_summary = ", ".join(
            f"{a}×{c}" if c > 1 else a 
            for a, c in sorted(action_counts.items(), key=lambda x: -x[1])[:10]
        )
        summary_parts.append(f"Actions taken: {action_summary}")
    
    return "\n".join(summary_parts) if summary_parts else ""


async def summarize_conversation_for_llm(
    conversation: Conversation,
    llm = None,
) -> str:
    """
    Use LLM to generate a high-quality summary of conversation history.
    
    This is more expensive but produces better summaries for very long
    conversations.
    
    Args:
        conversation: Conversation with messages loaded
        llm: LLM client for summarization
        
    Returns:
        Summary string
    """
    if llm is None:
        # Fall back to extractive summary
        return _build_context_summary(conversation.messages)
    
    # Build content to summarize
    content_parts = []
    for msg in conversation.messages:
        if msg.role == "user":
            content_parts.append(f"User: {msg.content[:200]}")
        elif msg.role == "assistant":
            if msg.tool_calls:
                tools = [tc.get("name", "") for tc in msg.tool_calls]
                content_parts.append(f"Assistant: Called {', '.join(tools)}")
            elif msg.content:
                content_parts.append(f"Assistant: {msg.content[:200]}")
    
    conversation_text = "\n".join(content_parts)
    
    prompt = f"""Summarize this DAW (music production) conversation in 2-3 sentences.
Focus on: what the user wanted, what was created (tracks, regions, effects), and current state.

Conversation:
{conversation_text}

Summary:"""
    
    try:
        response = await llm.chat(
            system="You are a concise summarizer. Output only the summary, nothing else.",
            user=prompt,
            tools=[],
            tool_choice="none",
            context={},
        )
        return response.content or _build_context_summary(conversation.messages)
    except Exception as e:
        logger.warning(f"LLM summarization failed: {e}")
        return _build_context_summary(conversation.messages)
