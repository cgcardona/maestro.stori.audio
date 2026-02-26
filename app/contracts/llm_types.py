"""Typed structures for OpenAI-format chat messages and API boundaries.

Instead of ``list[dict[str, Any]]`` for conversation messages,
use ``list[ChatMessage]``.  Each message role has its own TypedDict
so callers must narrow by ``role`` before accessing role-specific
fields like ``tool_calls``.

**Boundary types** (``OpenAIData`` and derivatives):
These alias ``dict[str, Any]`` because the OpenAI/Anthropic streaming and
response formats are deeply nested and dynamic — static TypedDicts would
duplicate the upstream SDK's own type definitions.  ``Any`` lives here and
ONLY here; all other modules import the named alias rather than spelling
out ``dict[str, Any]`` themselves.
"""
from __future__ import annotations

from typing import Any, Literal, Union

from typing_extensions import Required, TypedDict

# ── External API boundary (Any lives here, exactly once) ──────────────────────

# Base alias: JSON-decoded dict from OpenAI/Anthropic/OpenRouter APIs.
# Nested access patterns (chunk["choices"][0]["delta"]) require Any.
OpenAIData = dict[str, Any]

# Specific named shapes — distinct names clarify call-site intent.
OpenAIStreamChunk = OpenAIData   # one SSE chunk from the streaming API
OpenAITool = OpenAIData          # tool schema sent to the model
UsageStats = OpenAIData          # token usage / cost stats
OpenAIRequestPayload = OpenAIData  # full request body sent to OpenRouter
OpenAIResponse = OpenAIData      # full (non-streaming) response body
OpenAIToolChoice = str | OpenAIData  # "auto" | "required" | "none" | specific tool dict
StreamEvent = OpenAIData         # internal stream event yielded by LLMClient.chat()


class ToolCallFunction(TypedDict):
    """The ``function`` field inside an OpenAI tool call."""

    name: str
    arguments: str


class ToolCallEntry(TypedDict):
    """One tool call in an assistant message."""

    id: str
    type: str
    function: ToolCallFunction


class SystemMessage(TypedDict):
    """A system prompt message."""

    role: Literal["system"]
    content: str


class UserMessage(TypedDict):
    """A user message."""

    role: Literal["user"]
    content: str


class AssistantMessage(TypedDict, total=False):
    """An assistant reply (may contain tool calls)."""

    role: Required[Literal["assistant"]]
    content: str | None
    tool_calls: list[ToolCallEntry]


class ToolResultMessage(TypedDict):
    """A tool result returned to the LLM."""

    role: Literal["tool"]
    tool_call_id: str
    content: str


ChatMessage = Union[
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolResultMessage,
]
"""Discriminated union of all OpenAI chat message shapes."""
