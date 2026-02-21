"""Conversation formatting utilities for LLM context and display."""

from __future__ import annotations

import json
import logging
import re
import uuid as _uuid
from typing import Any

from app.db.models import Conversation, ConversationMessage

logger = logging.getLogger(__name__)


def _sanitize_tool_call_id(tool_call_id: str) -> str:
    """Sanitize tool_call_id to match Bedrock/Anthropic pattern ^[a-zA-Z0-9_-]+$."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)


def generate_title_from_prompt(prompt: str, max_length: int = 50) -> str:
    """Generate a concise title from a user prompt."""
    title = prompt.strip()

    prefixes = [
        "create a ", "make a ", "generate a ", "can you ",
        "please ", "i want to ", "i need to ", "help me ",
    ]
    for prefix in prefixes:
        if title.lower().startswith(prefix):
            title = title[len(prefix):]
            break

    title = title[0].upper() + title[1:] if title else title

    if len(title) > max_length:
        period_idx = title[:max_length].rfind(".")
        if period_idx > max_length // 2:
            title = title[:period_idx + 1]
        else:
            space_idx = title[:max_length].rfind(" ")
            title = (title[:space_idx] + "...") if space_idx > 0 else (title[:max_length] + "...")

    return title


async def get_conversation_preview(conversation: Conversation) -> str:
    """Get a preview snippet from the first user message (up to 100 chars)."""
    for message in conversation.messages:
        if message.role == "user":
            preview = message.content.strip()
            return preview[:100] + "..." if len(preview) > 100 else preview
    return ""


def _format_single_message(message: ConversationMessage) -> list[dict[str, Any]]:
    """Format a single ConversationMessage for LLM context."""
    formatted: list[dict[str, Any]] = []

    if message.role == "user":
        formatted.append({"role": "user", "content": message.content})

    elif message.role == "assistant":
        msg: dict[str, Any] = {"role": "assistant", "content": message.content or ""}

        if message.tool_calls:
            openai_tool_calls = []
            seen_ids: set[str] = set()
            for tc in message.tool_calls:
                tool_call_id = _sanitize_tool_call_id(tc.get("id", "")) or ""
                if not tool_call_id or tool_call_id in seen_ids:
                    tool_call_id = f"call_{_uuid.uuid4().hex[:12]}"
                seen_ids.add(tool_call_id)

                openai_tool_calls.append({
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(tc.get("arguments", {})),
                    },
                })

            msg["tool_calls"] = openai_tool_calls
            formatted.append(msg)

            for _tc, openai_tc in zip(message.tool_calls, openai_tool_calls):
                formatted.append({
                    "role": "tool",
                    "tool_call_id": openai_tc["id"],
                    "content": json.dumps({"status": "success"}),
                })
        else:
            formatted.append(msg)

    return formatted


def format_conversation_history(conversation: Conversation) -> list[dict[str, Any]]:
    """Format all conversation messages for LLM context.

    Converts stored messages into OpenAI-compatible format:
    - User: {"role": "user", "content": "..."}
    - Assistant: {"role": "assistant", "content": "...", "tool_calls": [...]}
    - Tool result: {"role": "tool", "tool_call_id": "...", "content": "..."}
    """
    formatted_messages: list[dict[str, Any]] = []

    for message in conversation.messages:
        if message.role == "user":
            formatted_messages.append({"role": "user", "content": message.content})

        elif message.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": message.content or ""}

            if message.tool_calls:
                openai_tool_calls = []
                seen_ids: set[str] = set()
                for tc in message.tool_calls:
                    tool_call_id = _sanitize_tool_call_id(tc.get("id", "")) or ""
                    if not tool_call_id or tool_call_id in seen_ids:
                        tool_call_id = f"call_{_uuid.uuid4().hex[:12]}"
                    seen_ids.add(tool_call_id)

                    arguments_str = (
                        json.dumps(tc.get("arguments", {}))
                        if isinstance(tc.get("arguments"), dict)
                        else str(tc.get("arguments", {}))
                    )
                    openai_tool_calls.append({
                        "id": tool_call_id,
                        "type": "function",
                        "function": {"name": tc.get("name", ""), "arguments": arguments_str},
                    })

                msg["tool_calls"] = openai_tool_calls
                formatted_messages.append(msg)

                for tc, openai_tc in zip(message.tool_calls, openai_tool_calls):
                    tool_name = tc.get("name", "")
                    output = {}
                    if message.actions:
                        for action in message.actions:
                            if action.action_type == tool_name or tool_name in action.description:
                                output = action.extra_metadata or {}
                                break
                    formatted_messages.append({
                        "role": "tool",
                        "tool_call_id": openai_tc["id"],
                        "content": str(output),
                    })
            else:
                formatted_messages.append(msg)

    logger.info(f"Formatted {len(formatted_messages)} messages from conversation history")
    return formatted_messages
