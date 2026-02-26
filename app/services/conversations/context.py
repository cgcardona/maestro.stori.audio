"""Context management for long conversations: summarization and optimization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.contracts.llm_types import ChatMessage
from app.db.models import Conversation, ConversationMessage

if TYPE_CHECKING:
    from app.core.llm_client import LLMClient
from app.services.conversations.formatting import _format_single_message

logger = logging.getLogger(__name__)

MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_TOKENS = 8000


def _extract_entity_summary(messages: list[ConversationMessage]) -> str:
    """Extract summary of entities (tracks, regions, buses) created in messages."""
    tracks: dict[str, str] = {}
    regions: dict[str, str] = {}
    buses: dict[str, str] = {}

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
    """Build a concise extractive summary of conversation history."""
    user_intents: list[str] = []
    actions_taken: list[str] = []

    for msg in messages:
        if msg.role == "user":
            content = msg.content.strip()
            if len(content) > 100:
                content = content[:97] + "..."
            user_intents.append(content)
        elif msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name", "").replace("stori_", "")
                actions_taken.append(name)

    summary_parts: list[str] = []

    if user_intents:
        summary_parts.append(f"User requests: {'; '.join(user_intents[-5:])}")

    if actions_taken:
        action_counts: dict[str, int] = {}
        for a in actions_taken:
            action_counts[a] = action_counts.get(a, 0) + 1

        action_summary = ", ".join(
            f"{a}×{c}" if c > 1 else a
            for a, c in sorted(action_counts.items(), key=lambda x: -x[1])[:10]
        )
        summary_parts.append(f"Actions taken: {action_summary}")

    return "\n".join(summary_parts) if summary_parts else ""


async def get_optimized_context(
    messages: list[ConversationMessage],
    max_messages: int = MAX_CONTEXT_MESSAGES,
    include_entity_summary: bool = True,
) -> tuple[list[ChatMessage], str | None]:
    """Get optimized conversation context for LLM.

    Short conversations: return all messages.
    Long conversations: return extractive summary + recent messages.
    """
    formatted: list[ChatMessage] = []

    if len(messages) <= max_messages:
        for msg in messages:
            formatted.extend(_format_single_message(msg))
        return formatted, None

    old_messages = messages[:-max_messages]
    recent_messages = messages[-max_messages:]

    entity_summary = (
        _extract_entity_summary(old_messages) if include_entity_summary else None
    )
    context_summary = _build_context_summary(old_messages)

    if context_summary:
        formatted.append({
            "role": "system",
            "content": (
                f"Previous conversation summary ({len(old_messages)} messages):\n{context_summary}"
            ),
        })

    if entity_summary:
        formatted.append({
            "role": "system",
            "content": f"Entities created in previous messages:\n{entity_summary}",
        })

    for msg in recent_messages:
        formatted.extend(_format_single_message(msg))

    logger.info(
        f"📚 Optimized context: {len(old_messages)} old → summary, "
        f"{len(recent_messages)} recent → full"
    )

    return formatted, entity_summary


async def summarize_conversation_for_llm(
    conversation: Conversation,
    llm: LLMClient | None = None,
) -> str:
    """Use LLM to generate a high-quality summary; falls back to extractive."""
    if llm is None:
        return _build_context_summary(conversation.messages)

    content_parts: list[str] = []
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

    prompt = (
        "Summarize this DAW (music production) conversation in 2-3 sentences.\n"
        "Focus on: what the user wanted, what was created (tracks, regions, effects), "
        "and current state.\n\n"
        f"Conversation:\n{conversation_text}\n\nSummary:"
    )

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
