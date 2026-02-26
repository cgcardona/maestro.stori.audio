"""Typed structures for OpenAI-format chat messages and API boundaries.

Every shape used by ``LLMClient`` is defined here as a typed TypedDict so
mypy can verify all field access statically.  No ``Any`` lives in this file.

Organisation:
  Chat messages          → ``SystemMessage``, ``UserMessage``,
                           ``AssistantMessage``, ``ToolResultMessage``,
                           ``ChatMessage`` (union)
  Tool schemas           → ``ToolParametersDict``, ``ToolFunctionDict``,
                           ``ToolSchemaDict``, ``ToolCallFunction``,
                           ``ToolCallEntry``
  Token usage            → ``PromptTokenDetails``, ``UsageStats``
  Request payload        → ``ProviderConfig``, ``ReasoningConfig``,
                           ``OpenAIRequestPayload``
  Non-streaming response → ``ResponseFunction``, ``ResponseToolCall``,
                           ``ResponseMessage``, ``ResponseChoice``,
                           ``OpenAIResponse``
  Streaming chunks       → ``ReasoningDetail``, ``ToolCallFunctionDelta``,
                           ``ToolCallDelta``, ``StreamDelta``,
                           ``StreamChoice``, ``OpenAIStreamChunk``
  Stream events          → ``ReasoningDeltaEvent``, ``ContentDeltaEvent``,
                           ``DoneStreamEvent``, ``StreamEvent`` (union)
"""
from __future__ import annotations

from typing import Literal, Union

from typing_extensions import NotRequired, Required, TypedDict


# ── Chat message shapes ────────────────────────────────────────────────────────


class ToolCallFunction(TypedDict):
    """The ``function`` field inside an OpenAI tool call.

    ``arguments`` is a JSON-encoded string — callers must ``json.loads`` it.
    """

    name: str
    arguments: str


class ToolCallEntry(TypedDict):
    """One tool call in an assistant message (streaming accumulator or response)."""

    id: str
    type: str
    function: ToolCallFunction


class SystemMessage(TypedDict):
    """A system-role prompt message."""

    role: Literal["system"]
    content: str


class UserMessage(TypedDict):
    """A user-role message."""

    role: Literal["user"]
    content: str


class AssistantMessage(TypedDict, total=False):
    """An assistant reply — may be text-only or contain tool calls."""

    role: Required[Literal["assistant"]]
    content: str | None
    tool_calls: list[ToolCallEntry]


class ToolResultMessage(TypedDict):
    """A tool result message returned to the LLM after a tool call."""

    role: Literal["tool"]
    tool_call_id: str
    content: str


ChatMessage = Union[SystemMessage, UserMessage, AssistantMessage, ToolResultMessage]
"""Discriminated union of all OpenAI chat message shapes."""


# ── Tool schema shapes (OpenAI function-calling format) ───────────────────────


class ToolParametersDict(TypedDict, total=False):
    """JSON Schema ``parameters`` block inside an OpenAI tool definition."""

    type: str
    properties: dict[str, object]
    required: list[str]


class ToolFunctionDict(TypedDict):
    """The ``function`` field of an OpenAI tool definition."""

    name: str
    description: str
    parameters: NotRequired[ToolParametersDict]


class ToolSchemaDict(TypedDict):
    """A single OpenAI-format tool definition (``{type: function, function: {...}}``)."""

    type: str
    function: ToolFunctionDict


# ── Token usage shapes ────────────────────────────────────────────────────────


class PromptTokenDetails(TypedDict, total=False):
    """Nested token-detail block inside ``UsageStats``.

    OpenRouter surfaces cache data in at least two field names depending on
    model and API version — both are included here.
    """

    cached_tokens: int       # cache read hits (OR standard)
    cache_write_tokens: int  # cache write/creation (OR standard)


class UsageStats(TypedDict, total=False):
    """Token usage and cost stats returned by OpenAI/Anthropic/OpenRouter.

    All fields are optional because the exact set varies by model and API
    version.  ``_extract_cache_stats`` normalises all known field names.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_tokens_details: PromptTokenDetails
    # OpenRouter / Anthropic direct cache fields
    native_tokens_cached: int
    cache_read_input_tokens: int
    prompt_cache_hit_tokens: int
    cache_creation_input_tokens: int
    prompt_cache_miss_tokens: int
    cache_discount: float


# ── Request payload shapes ────────────────────────────────────────────────────


class ProviderConfig(TypedDict, total=False):
    """OpenRouter provider-routing config sent in ``payload["provider"]``.

    Used to lock generation to a specific provider (e.g. direct Anthropic)
    for reliable prompt caching and reasoning token support.
    """

    order: list[str]
    allow_fallbacks: bool


class ReasoningConfig(TypedDict, total=False):
    """OpenRouter extended-reasoning config sent in ``payload["reasoning"]``."""

    max_tokens: int


class OpenAIRequestPayload(TypedDict, total=False):
    """Full request body sent to OpenRouter's chat completions endpoint.

    ``tools`` is typed as ``list[dict[str, object]]`` rather than
    ``list[ToolSchemaDict]`` because prompt-caching adds an extra
    ``cache_control`` key to the last tool definition before sending.
    """

    model: Required[str]
    messages: Required[list[ChatMessage]]
    temperature: float
    max_tokens: int
    stream: bool
    tools: list[dict[str, object]]
    tool_choice: str | dict[str, object]
    provider: ProviderConfig
    reasoning: ReasoningConfig


# ── Non-streaming response shapes ─────────────────────────────────────────────


class ResponseFunction(TypedDict, total=False):
    """The ``function`` field of a tool call in a non-streaming response."""

    name: str
    arguments: str


class ResponseToolCall(TypedDict, total=False):
    """One tool call in a non-streaming assistant response choice."""

    id: str
    type: str
    function: ResponseFunction


class ResponseMessage(TypedDict, total=False):
    """The ``message`` field inside a non-streaming response choice."""

    content: str | None
    tool_calls: list[ResponseToolCall]


class ResponseChoice(TypedDict, total=False):
    """One choice in a non-streaming API response."""

    message: ResponseMessage
    finish_reason: str | None


class OpenAIResponse(TypedDict, total=False):
    """Full (non-streaming) response body from an OpenAI-compatible API."""

    choices: list[ResponseChoice]
    usage: UsageStats


# ── Streaming chunk shapes ────────────────────────────────────────────────────


class ReasoningDetail(TypedDict, total=False):
    """One element of ``delta.reasoning_details`` in a stream chunk.

    OpenRouter uses ``type="reasoning.text"`` for incremental text and
    ``type="reasoning.summary"`` for the final consolidated summary.
    """

    type: str
    text: str
    summary: str


class ToolCallFunctionDelta(TypedDict, total=False):
    """Incremental function info in a streaming tool call delta."""

    name: str
    arguments: str


class ToolCallDelta(TypedDict, total=False):
    """One tool call fragment in a streaming delta."""

    index: int
    id: str
    type: str
    function: ToolCallFunctionDelta


class StreamDelta(TypedDict, total=False):
    """The ``delta`` field inside a streaming choice."""

    reasoning_details: list[ReasoningDetail]
    content: str
    tool_calls: list[ToolCallDelta]


class StreamChoice(TypedDict, total=False):
    """One choice in a streaming SSE chunk."""

    delta: StreamDelta
    finish_reason: str | None


class OpenAIStreamChunk(TypedDict, total=False):
    """One SSE data chunk from the OpenRouter streaming API."""

    choices: list[StreamChoice]
    usage: UsageStats


# ── Stream event shapes (yielded by LLMClient.chat_completion_stream) ─────────


class ReasoningDeltaEvent(TypedDict):
    """Incremental reasoning text from an extended-thinking model."""

    type: Literal["reasoning_delta"]
    text: str


class ContentDeltaEvent(TypedDict):
    """Incremental content text from the model."""

    type: Literal["content_delta"]
    text: str


class DoneStreamEvent(TypedDict):
    """Terminal event yielded when streaming completes.

    ``tool_calls`` holds the fully-accumulated list of tool calls built up
    from the streaming deltas — consumers should not read ``ToolCallEntry``
    fields before this event arrives.
    """

    type: Literal["done"]
    content: str | None
    tool_calls: list[ToolCallEntry]
    finish_reason: str | None
    usage: UsageStats


StreamEvent = Union[ReasoningDeltaEvent, ContentDeltaEvent, DoneStreamEvent]
"""Discriminated union of all events yielded by ``LLMClient.chat_completion_stream``."""

# Kept as a type alias: either a string shorthand ("auto", "none", "required")
# or an explicit tool-selector dict.  The dict form is rarely used but
# specified by the OpenAI API.
OpenAIToolChoice = str | dict[str, object]
