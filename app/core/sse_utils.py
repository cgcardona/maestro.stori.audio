"""
SSE and reasoning-display helpers for Composer (Cursor-of-DAWs).

Centralizes formatting of server-sent events and sanitization of LLM reasoning
so internal implementation details are not shown to the user.
"""

from __future__ import annotations

import json
import re
from typing import Any


async def sse_event(data: dict[str, Any]) -> str:
    """Format data as an SSE event (data: {...}\\n\\n)."""
    return f"data: {json.dumps(data)}\n\n"


def sanitize_reasoning(text: str) -> str:
    """
    Filter out internal implementation details from reasoning output.

    Removes function names (stori_add_*, etc.), UUIDs, parameter assignments,
    and code-like syntax so the user sees musical reasoning, not implementation.
    """
    # Remove function call syntax: stori_function_name(...)
    text = re.sub(r"stori_\w+\([^)]*\)", "", text)
    # Remove standalone function names
    text = re.sub(r"\bstori_\w+\b", "", text)
    # Remove UUIDs (8-4-4-4-12)
    text = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove parameter assignments
    text = re.sub(r'\b\w+Id\s*=\s*"[^"]*"', "", text)
    text = re.sub(r"\b\w+Beat\s*=\s*\d+", "", text)
    text = re.sub(r'\b\w+\s*=\s*"[^"]*"', "", text, flags=re.MULTILINE)
    text = re.sub(r"\b\w+\s*=\s*\d+", "", text)
    # Remove code markers
    text = re.sub(r"```\w*", "", text)
    text = re.sub(r"[{}[\]]", "", text)
    # Clean whitespace
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    return text.strip()
