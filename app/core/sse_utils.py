"""
SSE and reasoning-display helpers for Maestro (Cursor-of-DAWs).

Centralizes formatting of server-sent events, sanitization of LLM reasoning,
and BPE token buffering so the user sees clean, properly-spaced reasoning text.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

SSEEventInput = dict[str, Any]
"""Pre-validation SSE event dict.

Pydantic ``EVENT_REGISTRY`` validates the shape at runtime, so this
is the one place ``dict[str, Any]`` is genuinely correct.
"""


class SSESequencer:
    """Injects a monotonic ``seq`` counter into SSE ``data:`` frames.

    Each SSE stream must create its own instance so counters are
    independent.  The counter starts at 0 for the first event
    (``state``) and increments by 1 for every subsequent ``data:``
    frame.  SSE comments (e.g. ``: heartbeat``) pass through unchanged.

    Thread-safety: a single ``SSESequencer`` is used within one async
    generator — no concurrent access — so no lock is needed.
    """

    def __init__(self) -> None:
        from app.protocol.validation import ProtocolGuard

        self._seq: int = -1
        self._guard = ProtocolGuard()

    def __call__(self, event_str: str) -> str:
        """Inject seq into a ``data:`` SSE frame string."""
        if not event_str.startswith("data: "):
            return event_str
        self._seq += 1
        data = json.loads(event_str[6:].strip())
        data["seq"] = self._seq
        violations = self._guard.check_event(data.get("type", "unknown"), data)
        if violations:
            logger.error(f"❌ ProtocolGuard violations: {violations}")
        return f"data: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n"

    @property
    def count(self) -> int:
        """Number of ``data:`` events sequenced so far (0-indexed last seq)."""
        return self._seq


async def sse_event(data: dict[str, Any]) -> str:
    """Serialize a dict as a protocol-validated SSE event.

    Delegates to ``app.protocol.emitter.serialize_event`` which validates
    the dict against the registered Pydantic model before serialization.
    """
    from app.protocol.emitter import serialize_event

    return serialize_event(data)


# Maximum buffer size before forced flush (safety limit)
_REASONING_BUFFER_MAX = 200


class ReasoningBuffer:
    """Buffer raw BPE reasoning tokens and emit sanitized text at word boundaries.

    BPE tokenizers split words into sub-word pieces (e.g. "Trey" → "T" + "rey").
    Emitting each piece as a separate SSE event causes display artifacts.  This
    buffer accumulates tokens and flushes at word boundaries — when a new token
    starts with a space or newline, the previous accumulated text forms complete
    words and is safe to emit.
    """

    def __init__(self) -> None:
        self._buffer: str = ""

    def add(self, text: str) -> str | None:
        """Add a BPE token.  Returns sanitized text to emit, or None if still buffering."""
        if not text:
            return None

        # Word boundary: new token starts with whitespace, or buffer is full
        if self._buffer and (
            text[0] in (" ", "\n", "\t") or len(self._buffer) >= _REASONING_BUFFER_MAX
        ):
            result = sanitize_reasoning(self._buffer)
            self._buffer = text
            return result if result else None

        self._buffer += text
        return None

    def flush(self) -> str | None:
        """Flush remaining buffer.  Call at end of reasoning / transition to content."""
        if self._buffer:
            result = sanitize_reasoning(self._buffer)
            self._buffer = ""
            return result if result else None
        return None


def strip_tool_echoes(text: str) -> str:
    """Remove leaked tool-call argument syntax from LLM content text.

    When the LLM generates tool calls, it sometimes echoes fragments of the
    call syntax into the content stream — parenthesized keyword arguments,
    standalone closing parens, etc.  This function strips those fragments
    while preserving natural-language text.

    Examples of stripped content::

        (key="G major")
        (, instrument="Drum Kit")
        (,, )
        (, notes=
         # Many notes here
        )
    """
    if not text:
        return ""
    lines = text.split("\n")
    cleaned: list[str] = []
    in_tool_echo = False

    for line in lines:
        stripped = line.strip()

        # Inside a multi-line tool echo — skip until closing paren
        if in_tool_echo:
            if ")" in stripped:
                in_tool_echo = False
            continue

        # Single-line tool echo: (key="value"), (, key="value"), (,, )
        if stripped.startswith("(") and stripped.endswith(")"):
            if "=" in stripped or re.match(r"^\([,\s]*\)$", stripped):
                continue

        # Multi-line tool echo opening: (, notes=  or  (key=
        if stripped.startswith("(") and "=" in stripped and ")" not in stripped:
            in_tool_echo = True
            continue

        # Standalone closing paren (tail of multi-line echo)
        if stripped == ")":
            continue

        cleaned.append(line)

    result = "\n".join(cleaned)
    # Collapse runs of 3+ newlines down to 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


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
    # Clean horizontal whitespace only — preserve newlines for structured
    # reasoning (numbered lists, bullet points, section breaks).
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*,[ \t]*", ", ", text)
    # Preserve leading space for BPE token boundary concatenation.
    # Only strip trailing spaces/tabs; keep newlines and leading BPE spaces.
    text = text.rstrip(" \t")
    if not text:
        return ""
    return text
