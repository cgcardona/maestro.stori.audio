"""
LLM Client for Stori Maestro (Cursor-of-DAWs).

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
from typing import Any, AsyncIterator, Optional, cast

from app.config import settings
from app.core.expansion import ToolCall

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Supported LLM provider (OpenRouter only)."""
    OPENROUTER = "openrouter"


@dataclass
class LLMResponse:
    """Response from the LLM."""
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Optional[dict[str, Any]] = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


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
    
    # All models support reasoning via OpenRouter's `reasoning` API parameter
    REASONING_MODELS = {
        # Anthropic Claude models
        "anthropic/claude-sonnet-4.6",               # Latest Sonnet - $3/M in, $15/M out
        "anthropic/claude-opus-4.6",                 # Latest Opus - $5/M in, $25/M out
        # Previous generations kept for backward-compat with existing sessions
        "anthropic/claude-sonnet-4.5",
        "anthropic/claude-opus-4.5",
        "anthropic/claude-3.7-sonnet",
    }
    
    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.provider = provider or settings.llm_provider
        self.api_key = api_key or self._get_api_key()
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout
        self.base_url = self._get_base_url()
        self._client: Optional[httpx.AsyncClient] = None
    
    def supports_reasoning(self) -> bool:
        """Check if current model supports extended reasoning."""
        return self.model in self.REASONING_MODELS
    
    def _get_api_key(self) -> str:
        if self.provider == LLMProvider.OPENROUTER:
            key = settings.openrouter_api_key
            if key is None:
                raise ValueError("OpenRouter API key not configured")
            return key
        raise ValueError(f"No API key configured for provider: {self.provider}")
    
    def _get_base_url(self) -> str:
        if self.provider == LLMProvider.OPENROUTER:
            return "https://openrouter.ai/api"
        raise ValueError(f"Unknown provider: {self.provider}")
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            if self.provider == LLMProvider.OPENROUTER:
                headers["HTTP-Referer"] = "https://stori.ai"
                headers["X-Title"] = "Stori Maestro"
            self._client = httpx.AsyncClient(timeout=self.timeout, headers=headers)
        return self._client
    
    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def chat(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        tool_choice: str,
        context: dict[str, Any],
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
        messages = [{"role": "system", "content": system}]
        
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
    
    def _enable_prompt_caching(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict]] = None
    ) -> tuple[list[dict[str, Any]], Optional[list[dict]]]:
        """
        Enable prompt caching for Claude models via Anthropic's cache_control API.

        Applies cache breakpoints to:
        1. The system prompt content block (always, for Claude models)
        2. The last tool definition in the tools array (when tools are present)

        The cache is keyed on the static prefix (system + tools), so it hits on
        every subsequent request in the same session even when the conversation
        history contains tool_result messages. This cuts input token cost by ~90%
        on cache hits — the highest-leverage optimisation for EDITING/COMPOSING.

        Only applied to Claude/Anthropic models; other models receive the payload
        unchanged. OpenRouter forwards cache_control to Anthropic transparently.
        """
        if "claude" not in self.model.lower():
            return messages, tools

        # 1. Wrap system messages as content blocks with cache_control.
        cached_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                # Already a content-block list (e.g. from a previous pass) — just
                # ensure the last block carries the breakpoint.
                if isinstance(content, list):
                    blocks = [dict(b) for b in content]
                    if blocks:
                        blocks[-1]["cache_control"] = {"type": "ephemeral"}
                    cached_messages.append({"role": "system", "content": blocks})
                else:
                    cached_messages.append({
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": content,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    })
            else:
                cached_messages.append(msg)

        # 2. Add cache_control to the last tool definition so the full tools array
        #    is cached as a single block. The 22-tool schema never changes between
        #    calls, so this eliminates its token cost on every subsequent request.
        cached_tools: Optional[list[dict]] = None
        if tools:
            cached_tools = [dict(t) for t in tools]
            cached_tools[-1] = dict(cached_tools[-1])
            cached_tools[-1]["cache_control"] = {"type": "ephemeral"}

        n_tools = len(tools) if tools else 0
        logger.debug(
            f"Prompt caching enabled: system prompt cached, {n_tools} tools "
            f"({'last tool marked' if n_tools else 'no tools'})"
        )
        return cached_messages, cached_tools
    
    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: int = 2,
    ) -> LLMResponse:
        """Send a chat completion request with retry logic."""
        cached_messages, cached_tools = self._enable_prompt_caching(messages, tools)
        
        payload = {
            "model": self.model,
            "messages": cached_messages,
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": False,
        }
        
        if cached_tools:
            payload["tools"] = cached_tools
            payload["tool_choice"] = tool_choice if tool_choice is not None else "required"
        
        logger.debug(f"LLM request: {len(messages)} messages, {len(tools) if tools else 0} tools")
        
        last_error: Optional[Exception] = None
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
                data = response.json()
                
                usage = data.get("usage", {})
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_write = usage.get("cache_creation_input_tokens", 0)
                cache_info = (
                    f", cache_read={cache_read} cache_write={cache_write}"
                    if (cache_read or cache_write) else ""
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
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        reasoning_fraction: Optional[float] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream chat completion with real-time reasoning."""
        logger.info(f"🚀 chat_completion_stream called: model={self.model}, supports_reasoning={self.supports_reasoning()}")
        cached_messages, cached_tools = self._enable_prompt_caching(messages, tools)
        
        payload = {
            "model": self.model,
            "messages": cached_messages,
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": True,
        }
        
        # Enable reasoning for reasoning models via OpenRouter's reasoning parameter
        # https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
        if self.supports_reasoning():
            max_tok = max_tokens or settings.llm_max_tokens
            # Default 80% for reasoning; composition uses a lower fraction
            # (e.g. 0.4) because tool calls with MIDI data need more tokens
            fraction = reasoning_fraction if reasoning_fraction is not None else 0.8
            reasoning_budget = int(max_tok * fraction)
            payload["reasoning"] = {
                "max_tokens": max(reasoning_budget, 2048),  # Minimum 2K tokens for reasoning
            }
            
            # Prefer Anthropic routing on OpenRouter for Claude (reasoning parameter)
            if "anthropic" in self.model:
                payload["provider"] = {
                    "order": ["anthropic"],
                    "allow_fallbacks": False,
                }
                logger.info(f"🧠 Reasoning enabled: {reasoning_budget} tokens, OpenRouter provider preference: anthropic")
            else:
                logger.info(f"🧠 Reasoning enabled: {reasoning_budget} tokens")
        
        if cached_tools:
            payload["tools"] = cached_tools
            payload["tool_choice"] = tool_choice if tool_choice is not None else "required"
        
        accumulated_content = []
        accumulated_tool_calls = {}
        finish_reason = None
        usage = {}
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
                        chunk = json.loads(data_str)
                        chunk_count += 1
                        
                        # Log first chunk to see structure
                        if chunk_count == 1:
                            logger.info(f"🔍 First chunk keys: {list(chunk.keys())}")
                            logger.info(f"🔍 First chunk: {json.dumps(chunk)[:300]}")
                    except json.JSONDecodeError:
                        continue
                    
                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    
                    # DEBUG: Log first meaningful delta to see OpenRouter's format
                    if not debug_logged and delta and len(delta.keys()) > 0:
                        logger.info(f"🔍 Chunk #{chunk_count}, delta keys: {list(delta.keys())}")
                        delta_str = json.dumps(delta, indent=2)[:500]
                        logger.info(f"🔍 Delta sample: {delta_str}")
                        debug_logged = True
                    
                    # OpenRouter reasoning format: delta.reasoning_details array.
                    # Both delta.reasoning (string) and delta.reasoning_details (array)
                    # are present in every chunk with identical text — use only the
                    # structured array to avoid double-emitting.
                    # https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
                    if "reasoning_details" in delta and delta["reasoning_details"]:
                        for detail in delta["reasoning_details"]:
                            if detail.get("type") == "reasoning.text" and detail.get("text"):
                                yield {"type": "reasoning_delta", "text": detail["text"]}
                            elif detail.get("type") == "reasoning.summary" and detail.get("summary"):
                                yield {"type": "reasoning_delta", "text": detail["summary"]}
                    
                    if "content" in delta and delta["content"]:
                        # Regular content (user-facing response)
                        content = delta["content"]
                        accumulated_content.append(content)
                        yield {"type": "content_delta", "text": content}
                    
                    if "tool_calls" in delta:
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            if idx not in accumulated_tool_calls:
                                accumulated_tool_calls[idx] = {
                                    "id": tc.get("id", ""),
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                }
                            if "id" in tc:
                                accumulated_tool_calls[idx]["id"] = tc["id"]
                            if "function" in tc:
                                if "name" in tc["function"]:
                                    accumulated_tool_calls[idx]["function"]["name"] = tc["function"]["name"]
                                if "arguments" in tc["function"]:
                                    accumulated_tool_calls[idx]["function"]["arguments"] += tc["function"]["arguments"]
                    
                    if chunk.get("choices", [{}])[0].get("finish_reason"):
                        finish_reason = chunk["choices"][0]["finish_reason"]
                    
                    if "usage" in chunk:
                        usage = chunk["usage"]
        
        except httpx.HTTPError as e:
            logger.error(f"Stream HTTP error: {e}")
            raise

        # Log cache token stats so we can verify caching is working in prod logs.
        if usage:
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)
            if cache_read or cache_write:
                logger.info(
                    f"🗃️ Prompt cache: read={cache_read} tokens "
                    f"(~${cache_read * 0.0000003:.4f} saved), "
                    f"write={cache_write} tokens"
                )
            else:
                logger.debug("Prompt cache: no cache tokens in usage (cache miss or non-Claude model)")

        yield {
            "type": "done",
            "content": "".join(accumulated_content) if accumulated_content else None,
            "tool_calls": [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls.keys())],
            "finish_reason": finish_reason,
            "usage": usage,
        }
    
    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        """Parse OpenAI-compatible response."""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        
        response = LLMResponse(
            content=message.get("content"),
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage"),
        )
        
        for tc in message.get("tool_calls", []):
            try:
                args = tc.get("function", {}).get("arguments", "{}")
                if isinstance(args, str):
                    args = json.loads(args) if args else {}

                response.tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("function", {}).get("name", ""),
                    params=args,
                ))
            except Exception as e:
                logger.error(f"Error parsing tool call: {e}")
                continue

        return response


async def get_llm_client() -> LLMClient:
    """Get a configured LLM client instance."""
    return LLMClient()
