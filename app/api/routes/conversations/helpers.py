"""Helper functions for conversation routes."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from app.contracts.llm_types import AssistantMessage, ChatMessage, ToolCallEntry

if TYPE_CHECKING:
    from app.db.models import ConversationMessage


def build_conversation_history_for_llm(messages: list[ConversationMessage]) -> list[ChatMessage]:
    """
    Build conversation history in OpenAI format for LLM context.

    Critical for entity ID tracking — the LLM needs to see previous tool calls
    and their parameters (including generated UUIDs) to reuse them in subsequent
    tool calls.
    """
    history: list[ChatMessage] = []

    for msg in messages:
        if msg.role == "user":
            history.append({"role": "user", "content": msg.content or ""})

        elif msg.role == "assistant":
            openai_tool_calls: list[ToolCallEntry] = []
            if msg.tool_calls:
                seen_ids: set[str] = set()
                for tc in msg.tool_calls:
                    _tc_id = tc.get("id")
                    tc_id = _tc_id if isinstance(_tc_id, str) else ""
                    if not tc_id or tc_id in seen_ids:
                        tc_id = f"call_{uuid.uuid4().hex[:12]}"
                    seen_ids.add(tc_id)
                    _tc_type = tc.get("type")
                    _tc_name = tc.get("name")
                    _tc_args = tc.get("arguments")
                    openai_tool_calls.append({
                        "id": tc_id,
                        "type": _tc_type if isinstance(_tc_type, str) else "function",
                        "function": {
                            "name": _tc_name if isinstance(_tc_name, str) else "",
                            "arguments": json.dumps(_tc_args if isinstance(_tc_args, dict) else {}),
                        },
                    })

            if openai_tool_calls:
                assistant_msg: AssistantMessage = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": openai_tool_calls,
                }
                history.append(assistant_msg)

                for otc in openai_tool_calls:
                    history.append({
                        "role": "tool",
                        "tool_call_id": otc["id"],
                        "content": json.dumps({
                            "success": True,
                            "message": f"Tool {otc['function']['name']} executed successfully",
                        }),
                    })
            else:
                history.append({"role": "assistant", "content": msg.content or ""})

    return history


def normalize_tool_arguments(arguments: dict[str, object] | None) -> dict[str, object] | None:
    """
    Normalize tool arguments for API responses.

    Converts all numeric values (int, float) to strings for Swift/client
    compatibility where argument types are expected as strings.
    """
    if arguments is None or not arguments:
        return arguments
    out: dict[str, object] = {}
    for k, v in arguments.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = normalize_tool_arguments(v)
        elif isinstance(v, list):
            out[k] = [
                normalize_tool_arguments(x) if isinstance(x, dict)
                else (str(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else x)
                for x in v
            ]
        else:
            out[k] = v
    return out


