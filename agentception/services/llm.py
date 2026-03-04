"""Async OpenRouter client for AgentCeption's direct LLM calls.

AgentCeption calls OpenRouter directly (not via Cursor) for tasks that are
pure text-in / text-out and need no file-system tools — e.g. converting a
brain dump into a structured phase plan.

Usage::

    from agentception.services.llm import call_openrouter

    text = await call_openrouter(
        user_prompt="...",
        system_prompt="You are a planner...",
    )

The key is read from ``settings.openrouter_api_key`` (env var
``AC_OPENROUTER_API_KEY``).  A missing key raises ``RuntimeError`` so callers
can catch it and fall back to a heuristic path.
"""
from __future__ import annotations

import logging

import httpx

from agentception.config import settings

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODEL = "anthropic/claude-sonnet-4.6"
_DEFAULT_TIMEOUT = 90.0  # seconds — LLM calls can be slow


async def call_openrouter(
    user_prompt: str,
    *,
    system_prompt: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> str:
    """Call Claude Sonnet via OpenRouter and return the text of the first choice.

    Args:
        user_prompt: The user-turn message content.
        system_prompt: Optional system-turn message prepended before the user turn.
        temperature: Sampling temperature (0.0–1.0).  Lower = more deterministic.
        max_tokens: Maximum tokens in the completion.

    Returns:
        The raw text string of the model's first completion choice.

    Raises:
        RuntimeError: When ``AC_OPENROUTER_API_KEY`` is not set.
        httpx.HTTPStatusError: On non-2xx responses from OpenRouter.
        httpx.TimeoutException: When the request exceeds ``_DEFAULT_TIMEOUT``.
    """
    api_key = settings.openrouter_api_key
    if not api_key:
        raise RuntimeError(
            "AC_OPENROUTER_API_KEY is not configured — "
            "set it in .env and restart the agentception service."
        )

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    payload: dict[str, object] = {
        "model": _MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    logger.info("✅ LLM call — model=%s prompt_chars=%d", _MODEL, len(user_prompt))

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        resp = await client.post(
            _OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://agentception.local",
                "X-Title": "AgentCeption",
            },
            json=payload,
        )
        resp.raise_for_status()

    data: object = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected OpenRouter response type: {type(data)}")

    choices: object = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"OpenRouter returned no choices: {data}")

    first: object = choices[0]
    if not isinstance(first, dict):
        raise ValueError(f"Unexpected choice format: {first}")

    message: object = first.get("message")
    if not isinstance(message, dict):
        raise ValueError(f"Unexpected message format: {first}")

    content: object = message.get("content", "")
    if not isinstance(content, str):
        raise ValueError(f"Unexpected content type: {type(content)}")

    logger.info("✅ LLM response — %d chars", len(content))
    return content
