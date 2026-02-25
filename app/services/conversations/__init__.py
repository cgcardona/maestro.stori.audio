"""
Conversation management service package.

Handles CRUD operations for conversations, messages, and actions,
plus context optimization for long conversation histories.
"""
from __future__ import annotations

from app.services.conversations.crud import (
    create_conversation,
    get_conversation,
    list_conversations,
    update_conversation_title,
    archive_conversation,
    delete_conversation,
)
from app.services.conversations.messages import add_message, add_action
from app.services.conversations.search import search_conversations
from app.services.conversations.formatting import (
    _sanitize_tool_call_id,
    generate_title_from_prompt,
    get_conversation_preview,
    format_conversation_history,
    _format_single_message,
)
from app.services.conversations.context import (
    MAX_CONTEXT_MESSAGES,
    MAX_CONTEXT_TOKENS,
    _extract_entity_summary,
    _build_context_summary,
    get_optimized_context,
    summarize_conversation_for_llm,
)

__all__ = [
    # CRUD
    "create_conversation",
    "get_conversation",
    "list_conversations",
    "update_conversation_title",
    "archive_conversation",
    "delete_conversation",
    # Messages
    "add_message",
    "add_action",
    # Search
    "search_conversations",
    # Formatting
    "_sanitize_tool_call_id",
    "generate_title_from_prompt",
    "get_conversation_preview",
    "format_conversation_history",
    "_format_single_message",
    # Context
    "MAX_CONTEXT_MESSAGES",
    "MAX_CONTEXT_TOKENS",
    "_extract_entity_summary",
    "_build_context_summary",
    "get_optimized_context",
    "summarize_conversation_for_llm",
]
