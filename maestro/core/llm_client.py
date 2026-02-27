"""
LLM Client for Maestro.

Provides a clean interface for LLM interactions with:
- OpenRouter support
- Streaming for real-time thinking/reasoning
- Prompt caching for cost reduction
- Single-tool enforcement for deterministic execution
"""

from __future__ import annotations

import asyncio
import httpx
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator

from typing_extensions import TypedDict

from maestro.config import settings
from maestro.contracts.json_types import JSONValue
from maestro.contracts.llm_types import (
    CachedToolSchemaDict,
    CacheControlDict,
    ChatMessage,
    ContentDeltaEvent,
    DoneStreamEvent,
    OpenAIRequestPayload,
    OpenAIResponse,
    OpenAIStreamChunk,
    OpenAIToolChoice,
    ReasoningDeltaEvent,
    StreamEvent,
    ToolCallEntry,
    ToolCallFunction,
    ToolSchemaDict,
    UsageStats,
)
from maestro.core.expansion import ToolCall


class ChatContext(TypedDict, total=False):
    """Pipeline context passed to chat (project_state, route, etc.)."""

    project_state: object
    route: object

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Supported LLM provider (OpenRouter only)."""
    OPENROUTER = "openrouter"


@dataclass
class LLMResponse:
    """Response from the LLM."""
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: UsageStats | None = None

    @property
    def has_tool_calls(self) -> bool:
        """``True`` when the LLM response includes at least one tool call."""
        return len(self.tool_calls) > 0


def _extract_cache_stats(usage: UsageStats) -> tuple[int, int, float]:
    """
    Normalise OpenRouter / Anthropic cache fields into (read_tokens, write_tokens, discount).

    OpenRouter surfaces cache data in at least two ways depending on model and
    version — check all known field names and return the first non-zero value:

    prompt_tokens_details.cached_tokens      → read hits (OR standard)
    prompt_tokens_details.cache_write_tokens → write/creation (OR standard)
    native_tokens_cached                     → confirmed cache hit (OR alt)
    cache_read_input_tokens                  → Anthropic direct API
    cache_creation_input_tokens              → Anthropic direct API
    cache_discount                           → dollar savings (OR)
    """
    details = usage.get("prompt_tokens_details", {})
    read = (
        details.get("cached_tokens", 0)
        or usage.get("native_tokens_cached", 0)
        or usage.get("cache_read_input_tokens", 0)
        or usage.get("prompt_cache_hit_tokens", 0)
    )
    write = (
        details.get("cache_write_tokens", 0)
        or usage.get("cache_creation_input_tokens", 0)
        or usage.get("prompt_cache_miss_tokens", 0)
    )
    discount = float(usage.get("cache_discount", 0) or 0)
    return int(read), int(write), discount


def enforce_single_tool(response: LLMResponse) -> LLMResponse:
    """Enforce single tool call for deterministic execution."""
    if len(response.tool_calls) <= 1:
        return response
    # Keep only the first; the rest are over-completion.
    response.tool_calls = response.tool_calls[:1]
    logger.warning(f"Enforced single tool: dropped {len(response.tool_calls) - 1} extra calls")
    return response


class LLMClient:
    """
    LLM client for the Cursor-of-DAWs architecture.
    
    Supports:
    - OpenRouter
    - Streaming with thinking/reasoning
    - Prompt caching for Claude models
    - Single-tool enforcement
    """

    # Anthropic uses date-stamped beta headers (their versioning scheme, not ours).
    # Extract to a constant so there's exactly one place to bump it when they release
    # a new prompt-caching beta.
    ANTHROPIC_CACHE_BETA = "prompt-caching-2024-07-31"

    # Models we actively use. Update both sets when upgrading.
    # Sonnet 4.6: everyday driver  — $3/M in, $15/M out
    # Opus 4.6:   pro / composing  — $5/M in, $25/M out
    SUPPORTED_MODELS = {
        "anthropic/claude-sonnet-4.6",
        "anthropic/claude-opus-4.6",
    }

    # Both models route to direct Anthropic on OpenRouter and support
    # prompt caching + reasoning. Caching is locked to Anthropic direct
    # (see _enable_prompt_caching provider lock) so Bedrock/Vertex variants
    # are excluded — they use different tool-ID prefixes and may not honour
    # cache_control.
    CACHE_SUPPORTED_MODELS = SUPPORTED_MODELS
    REASONING_MODELS = SUPPORTED_MODELS
    
    def __init__(
        self,
        provider: LLMProvider | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ):
        self.provider = provider or settings.llm_provider
        self.api_key = api_key or self._get_api_key()
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout
        self.base_url = self._get_base_url()
        self._client: httpx.AsyncClient | None = None
    
    def supports_reasoning(self) -> bool:
        """Check if current model supports extended reasoning."""
        return self.model in self.REASONING_MODELS
    
    def _get_api_key(self) -> str:
        """Resolve the API key for the configured provider from settings.

        Raises ``ValueError`` when the required key is absent (misconfigured
        environment) so the failure surfaces at startup rather than at the
        first request.
        """
        if self.provider == LLMProvider.OPENROUTER:
            key = settings.openrouter_api_key
            if key is None:
                raise ValueError("OpenRouter API key not configured")
            return key
        raise ValueError(f"No API key configured for provider: {self.provider}")
    
    def _get_base_url(self) -> str:
        """Return the base URL for the configured provider's API."""
        if self.provider == LLMProvider.OPENROUTER:
            return "https://openrouter.ai/api"
        raise ValueError(f"Unknown provider: {self.provider}")

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the shared ``httpx.AsyncClient``, creating it lazily on first access.

        Injects auth and referrer headers at construction time.  Notably, the
        ``anthropic-beta`` header is intentionally omitted: Claude 4.x has
        stable prompt caching and sending the 2024-07-31 beta value causes
        silent failures on newer model versions.
        """
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            if self.provider == LLMProvider.OPENROUTER:
                headers["HTTP-Referer"] = "https://maestro.ai"
                headers["X-Title"] = "Stori Maestro"
            self._client = httpx.AsyncClient(timeout=self.timeout, headers=headers)
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def chat(
        self,
        system: str,
        user: str,
        tools: list[ToolSchemaDict],
        tool_choice: str,
        context: ChatContext,
    ) -> LLMResponse:
        """
        Simple chat interface for pipeline/planner.
        
        Args:
            system: System prompt
            user: User message
            tools: Tool definitions
            tool_choice: "auto", "required", or "none"
            context: Additional context (project_state, route, etc.)
        
        Returns:
            LLMResponse with content and/or tool calls
        """
        messages: list[ChatMessage] = [{"role": "system", "content": system}]
        
        # Add project context if available
        if context.get("project_state"):
            context_str = f"Project state: {json.dumps(context['project_state'], indent=2)}"
            messages.append({"role": "system", "content": context_str})
        
        messages.append({"role": "user", "content": user})
        
        return await self.chat_completion(
            messages=messages,
            tools=tools if tools else None,
            tool_choice=tool_choice if tools else None,
            temperature=0.1,
        )
    
    def _supports_caching(self) -> bool:
        """Return True if the active model supports Anthropic prompt caching via OpenRouter."""
        return self.model in self.CACHE_SUPPORTED_MODELS

    def _enable_prompt_caching(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchemaDict] | None = None,
    ) -> tuple[list[ChatMessage], list[CachedToolSchemaDict] | None, None]:
        """
        Add Anthropic cache_control breakpoints to the system prompt and tools.

        Returns (non_system_messages, cached_tools, system_blocks):
          - non_system_messages: messages list with the role:system entry removed
          - cached_tools:        tools with cache_control on the last entry
          - system_blocks:       Anthropic-native content-block array for the
                                 top-level ``system`` payload key

        Strategy (per external infra diagnosis, Feb 2026):
        OpenRouter's OpenAI-compatible message converter flattens role:system
        messages to a plain string, silently stripping cache_control metadata.
        Sending the system prompt as a TOP-LEVEL ``system`` array (Anthropic's
        native Messages-API shape) bypasses that normalization and delivers
        cache_control to Anthropic intact.

        The beta capability is passed via provider.anthropic.extra_headers in
        the request body (not as an HTTP header, which OR does not forward).

        The tools array gets cache_control on its last entry so the full
        schema is also covered as a second prefix breakpoint.

        Only fires for models in CACHE_SUPPORTED_MODELS; others pass through
        unchanged (system_blocks=None → caller leaves system in messages).
        """
        if not self._supports_caching():
            if "claude" in self.model.lower():
                logger.debug(
                    f"Prompt caching skipped: {self.model} not in CACHE_SUPPORTED_MODELS"
                )
            _pass_through: list[CachedToolSchemaDict] | None = (
                [CachedToolSchemaDict(type=t["type"], function=t["function"]) for t in tools]
                if tools else None
            )
            return messages, _pass_through, None

        # OpenRouter's OpenAI-compatible interface does not forward a top-level
        # `system` array (Anthropic-native format) to Anthropic — it silently
        # drops it, causing the model to run without any system instructions.
        # Tool-schema caching (cache_control on the last tool definition) DOES
        # work because OpenRouter forwards the tools array as-is to Anthropic's
        # tools API. So we cache only tools here and keep system messages in the
        # messages array where OR handles them correctly.
        #
        # Result: system_blocks is always None (no top-level system injection);
        # callers see system_blocks=None and leave the messages array untouched.

        # Cache the full tools schema by marking the last tool definition.
        # Anthropic requires the cacheable prefix to be ≥ 1024 tokens.
        # For COMPOSING (22 tools, ~2500+ tok) this fires reliably.
        # For EDITING (1 tool, ~200-800 tok) it is below threshold — accepted.
        cached_tools: list[CachedToolSchemaDict] | None = None
        if tools:
            cached_tools = [
                CachedToolSchemaDict(type=t["type"], function=t["function"]) for t in tools
            ]
            cached_tools[-1] = CachedToolSchemaDict(
                type=cached_tools[-1]["type"],
                function=cached_tools[-1]["function"],
                cache_control=CacheControlDict(type="ephemeral"),
            )

        n_tools = len(tools) if tools else 0
        logger.debug(
            f"Prompt caching enabled for {self.model}: {n_tools} tools marked "
            f"(system stays in messages — OR does not forward top-level system arrays)"
        )
        return messages, cached_tools, None
    
    async def chat_completion(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchemaDict] | None = None,
        tool_choice: OpenAIToolChoice | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 2,
    ) -> LLMResponse:
        """Send a chat completion request with retry logic."""
        messages, cached_tools, _ = self._enable_prompt_caching(messages, tools)
        
        payload: OpenAIRequestPayload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": False,
        }

        if cached_tools:
            payload["tools"] = cached_tools
            payload["tool_choice"] = tool_choice if tool_choice is not None else "required"
        
        logger.debug(f"LLM request: {len(messages)} messages, {len(tools) if tools else 0} tools, caching={self._supports_caching()}")
        
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff = 2 ** attempt
                logger.warning(f"Retry {attempt}/{max_retries} after {backoff}s")
                await asyncio.sleep(backoff)
            
            start = time.time()
            try:
                response = await self.client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                
                duration = time.time() - start
                data: OpenAIResponse = response.json()
                
                usage = data.get("usage", {})
                logger.debug(f"Raw LLM usage from OpenRouter (non-stream): {usage}")
                cache_read, cache_write, cache_discount = _extract_cache_stats(usage)
                cache_info = (
                    f", cache_read={cache_read} cache_write={cache_write} discount=${cache_discount:.4f}"
                    if (cache_read or cache_write or cache_discount) else ""
                )
                logger.info(
                    f"LLM: {duration:.2f}s, {usage.get('prompt_tokens', 0)} prompt, "
                    f"{usage.get('completion_tokens', 0)} completion tokens{cache_info}"
                )
                
                return self._parse_response(data)
                
            except httpx.HTTPStatusError as e:
                last_error = e
                # Log detailed error for 400s to help debug
                if e.response.status_code == 400:
                    error_body = e.response.text
                    logger.error(f"400 Bad Request: {error_body}")
                    msgs = payload.get("messages") or []
                    logger.debug(f"Request payload had {len(msgs) if isinstance(msgs, list) else 0} messages")
                if e.response.status_code in (429, 500, 502, 503, 504):
                    continue
                raise
            except httpx.TimeoutException as e:
                last_error = e
                continue
            except Exception as e:
                last_error = e
                continue
        
        raise last_error or Exception("LLM request failed after retries")
    
    async def chat_completion_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchemaDict] | None = None,
        tool_choice: OpenAIToolChoice | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_fraction: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream chat completion with real-time reasoning."""
        logger.info(f"🚀 chat_completion_stream called: model={self.model}, supports_reasoning={self.supports_reasoning()}")
        # _enable_prompt_caching always returns system_blocks=None (tool-only caching).
        # Messages (including role:system) are returned unchanged; cached_tools has
        # cache_control on the last tool definition for COMPOSING-scale caching.
        messages, cached_tools, _ = self._enable_prompt_caching(messages, tools)
        
        payload: OpenAIRequestPayload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": True,
        }
        
        # Lock to direct Anthropic for both reasoning and caching.
        # Bedrock/Vertex variants use different tool-ID prefixes and may not
        # honour cache_control, so we always prefer the direct Anthropic route
        # when the model is an Anthropic model that supports caching or reasoning.
        if self._supports_caching() and "anthropic" in self.model:
            payload["provider"] = {
                "order": ["anthropic"],
                "allow_fallbacks": False,
            }
            logger.debug("🔒 Routing locked to direct Anthropic")

        # Enable reasoning for reasoning models via OpenRouter's reasoning parameter
        # https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
        if self.supports_reasoning():
            max_tok = max_tokens or settings.llm_max_tokens
            # Default 80% for reasoning; composition uses a lower fraction
            # (e.g. 0.4) because tool calls with MIDI data need more tokens
            fraction = reasoning_fraction if reasoning_fraction is not None else 0.8
            reasoning_budget = int(max_tok * fraction)
            payload["reasoning"] = {
                "max_tokens": max(reasoning_budget, 1024),  # Minimum 1K tokens for reasoning
            }
            logger.info(f"🧠 Reasoning enabled: {reasoning_budget} tokens")

        if cached_tools:
            payload["tools"] = cached_tools
            payload["tool_choice"] = tool_choice if tool_choice is not None else "required"
        
        accumulated_content: list[str] = []
        accumulated_tool_calls: dict[int, ToolCallEntry] = {}
        finish_reason: str | None = None
        usage: UsageStats = {}
        debug_logged = False  # Flag to log first delta
        
        try:
            logger.info(f"🚀 Streaming request to OpenRouter: model={self.model}, reasoning_enabled={self.supports_reasoning()}")
            async with self.client.stream("POST", f"{self.base_url}/v1/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    error_str = error_text.decode()[:500]
                    logger.error(f"Stream error {response.status_code}: {error_str}")
                    if response.status_code == 400:
                        msgs = payload.get("messages") or []
                        logger.debug(f"Request had {len(msgs) if isinstance(msgs, list) else 0} messages, caching_enabled={bool(cached_tools != tools)}")
                    response.raise_for_status()
                
                chunk_count = 0
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    
                    try:
                        chunk: OpenAIStreamChunk = json.loads(data_str)
                        chunk_count += 1

                        # Log first chunk to see structure
                        if chunk_count == 1:
                            logger.info(f"🔍 First chunk keys: {list(chunk.keys())}")
                            logger.info(f"🔍 First chunk: {json.dumps(chunk)[:300]}")
                    except json.JSONDecodeError:
                        continue

                    chunk_choices = chunk.get("choices") or []
                    choice = chunk_choices[0] if chunk_choices else None

                    if chunk_stats := chunk.get("usage"):
                        usage = chunk_stats

                    if choice is None:
                        continue

                    if fr := choice.get("finish_reason"):
                        finish_reason = fr

                    delta = choice.get("delta")
                    if delta is None:
                        continue

                    # DEBUG: Log first meaningful delta to see OpenRouter's format
                    if not debug_logged and delta:
                        logger.info(f"🔍 Chunk #{chunk_count}, delta keys: {list(delta.keys())}")
                        delta_str = json.dumps(delta, indent=2)[:500]
                        logger.info(f"🔍 Delta sample: {delta_str}")
                        debug_logged = True

                    # OpenRouter reasoning format: delta.reasoning_details array.
                    # Both delta.reasoning (string) and delta.reasoning_details (array)
                    # are present in every chunk with identical text — use only the
                    # structured array to avoid double-emitting.
                    # https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
                    for detail in delta.get("reasoning_details") or []:
                        detail_type = detail.get("type")
                        if detail_type == "reasoning.text":
                            if text := detail.get("text"):
                                yield ReasoningDeltaEvent(type="reasoning_delta", text=text)
                        elif detail_type == "reasoning.summary":
                            if summary := detail.get("summary"):
                                yield ReasoningDeltaEvent(type="reasoning_delta", text=summary)

                    if content := delta.get("content"):
                        accumulated_content.append(content)
                        yield ContentDeltaEvent(type="content_delta", text=content)

                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index") or 0
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = ToolCallEntry(
                                id=tc.get("id") or "",
                                type="function",
                                function=ToolCallFunction(name="", arguments=""),
                            )
                        if tc_id := tc.get("id"):
                            accumulated_tool_calls[idx]["id"] = tc_id
                        if tc_func := tc.get("function"):
                            if tc_name := tc_func.get("name"):
                                accumulated_tool_calls[idx]["function"]["name"] = tc_name
                            if tc_args := tc_func.get("arguments"):
                                accumulated_tool_calls[idx]["function"]["arguments"] += tc_args
        
        except httpx.HTTPError as e:
            logger.error(f"Stream HTTP error: {e}")
            raise

        # Log cache token stats. OpenRouter returns these in several places;
        # _extract_cache_stats normalises all known field names.
        if usage:
            logger.debug(f"Raw LLM usage from OpenRouter: {usage}")
            cache_read, cache_write, cache_discount = _extract_cache_stats(usage)
            if cache_read or cache_write or cache_discount:
                logger.info(
                    f"🗃️ Prompt cache: read={cache_read} tok, "
                    f"write={cache_write} tok, discount=${cache_discount:.4f}"
                )
            else:
                logger.debug(
                    f"Prompt cache: no cache fields in usage "
                    f"(keys: {list(usage.keys())})"
                )

        yield DoneStreamEvent(
            type="done",
            content="".join(accumulated_content) if accumulated_content else None,
            tool_calls=[accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls.keys())],
            finish_reason=finish_reason,
            usage=usage,
        )
    
    def _parse_response(self, data: OpenAIResponse) -> LLMResponse:
        """Parse an OpenAI-compatible non-streaming response into an ``LLMResponse``."""
        choices = data.get("choices") or []
        choice = choices[0] if choices else None
        message = choice.get("message") if choice else None

        response = LLMResponse(
            content=message.get("content") if message else None,
            finish_reason=choice.get("finish_reason") if choice else None,
            usage=data.get("usage"),
        )

        for tc in (message.get("tool_calls") if message else None) or []:
            try:
                fn = tc.get("function")
                raw_args = fn.get("arguments", "{}") if fn else "{}"
                args: dict[str, JSONValue] = json.loads(raw_args) if isinstance(raw_args, str) and raw_args else {}
                response.tool_calls.append(ToolCall(
                    id=tc.get("id") or "",
                    name=fn.get("name", "") if fn else "",
                    params=args,
                ))
            except Exception as e:
                logger.error(f"Error parsing tool call: {e}")
                continue

        return response


async def get_llm_client() -> LLMClient:
    """Get a configured LLM client instance."""
    return LLMClient()
