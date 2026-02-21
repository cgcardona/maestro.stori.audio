"""REASONING handler — answer questions without tools."""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Optional

from app.core.entity_context import format_project_context
from app.core.intent import Intent
from app.core.llm_client import LLMClient, LLMResponse
from app.core.prompts import system_prompt_base, wrap_user_request
from app.core.sse_utils import sanitize_reasoning, sse_event
from app.core.tracing import log_llm_call, trace_span
from app.core.maestro_helpers import UsageTracker, _context_usage_fields

logger = logging.getLogger(__name__)


async def _handle_reasoning(
    prompt: str,
    project_context: dict[str, Any],
    route: Any,
    llm: LLMClient,
    trace: Any,
    usage_tracker: Optional[UsageTracker],
    conversation_history: list[dict[str, Any]],
) -> AsyncIterator[str]:
    """Handle REASONING state - answer questions without tools."""
    yield await sse_event({"type": "status", "message": "Reasoning..."})

    if route.intent == Intent.ASK_STORI_DOCS:
        try:
            from app.services.rag import get_rag_service
            rag = get_rag_service(llm_client=llm)

            if rag.collection_exists():
                async for chunk in rag.answer(prompt, model=llm.model):
                    yield await sse_event({"type": "content", "content": chunk})

                yield await sse_event({
                    "type": "complete",
                    "success": True,
                    "toolCalls": [],
                    "traceId": trace.trace_id,
                    **_context_usage_fields(usage_tracker, llm.model),
                })
                return
        except Exception as e:
            logger.warning(f"[{trace.trace_id[:8]}] RAG failed: {e}")

    with trace_span(trace, "llm_thinking"):
        messages = [{"role": "system", "content": system_prompt_base()}]

        if project_context:
            messages.append({"role": "system", "content": format_project_context(project_context)})

        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": wrap_user_request(prompt)})

        start_time = time.time()
        response = None

        logger.info(f"🎯 REASONING handler: supports_reasoning={llm.supports_reasoning()}, model={llm.model}")
        if llm.supports_reasoning():
            logger.info("🌊 Using streaming path for reasoning model")
            response_text = ""
            async for raw in llm.chat_completion_stream(
                messages=messages,
                tools=[],
                tool_choice="none",
            ):
                event = raw
                if event.get("type") == "reasoning_delta":
                    reasoning_text = event.get("text", "")
                    if reasoning_text:
                        sanitized = sanitize_reasoning(reasoning_text)
                        if sanitized:
                            yield await sse_event({
                                "type": "reasoning",
                                "content": sanitized,
                            })
                elif event.get("type") == "content_delta":
                    content_text = event.get("text", "")
                    if content_text:
                        response_text += content_text
                        yield await sse_event({"type": "content", "content": content_text})
                elif event.get("type") == "done":
                    response = LLMResponse(
                        content=response_text or event.get("content"),
                        usage=event.get("usage", {})
                    )
            duration_ms = (time.time() - start_time) * 1000
        else:
            response = await llm.chat_completion(
                messages=messages,
                tools=[],
                tool_choice="none",
            )
            duration_ms = (time.time() - start_time) * 1000

            if response.content:
                yield await sse_event({"type": "content", "content": response.content})

        if response and response.usage:
            log_llm_call(
                trace.trace_id,
                llm.model,
                response.usage.get("prompt_tokens", 0),
                response.usage.get("completion_tokens", 0),
                duration_ms,
                False,
            )
            if usage_tracker:
                usage_tracker.add(
                    response.usage.get("prompt_tokens", 0),
                    response.usage.get("completion_tokens", 0),
                )

    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": [],
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    })
