"""REASONING handler — answer questions without tools."""

from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from maestro.contracts.llm_types import ChatMessage
from maestro.contracts.project_types import ProjectContext
from maestro.core.entity_context import format_project_context
from maestro.core.intent import Intent, IntentResult
from maestro.core.llm_client import LLMClient, LLMResponse
from maestro.core.prompts import system_prompt_base, wrap_user_request
from maestro.core.stream_utils import sanitize_reasoning
from maestro.core.tracing import TraceContext, log_llm_call, trace_span
from maestro.core.maestro_helpers import UsageTracker, _context_usage_fields
from maestro.protocol.emitter import emit
from maestro.protocol.events import CompleteEvent, ContentEvent, ReasoningEvent, StatusEvent

logger = logging.getLogger(__name__)


async def _handle_reasoning(
    prompt: str,
    project_context: ProjectContext,
    route: IntentResult,
    llm: LLMClient,
    trace: TraceContext,
    usage_tracker: UsageTracker | None,
    conversation_history: list[ChatMessage],
) -> AsyncIterator[str]:
    """Handle REASONING state - answer questions without tools."""
    yield emit(StatusEvent(message="Reasoning..."))

    if route.intent == Intent.ASK_STORI_DOCS:
        try:
            from maestro.services.rag import get_rag_service
            rag = get_rag_service(llm_client=llm)

            if rag.collection_exists():
                async for chunk in rag.answer(prompt, model=llm.model):
                    yield emit(ContentEvent(content=chunk))

                yield emit(CompleteEvent(
                    success=True,
                    trace_id=trace.trace_id,
                    **_context_usage_fields(usage_tracker, llm.model),
                ))
                return
        except Exception as e:
            logger.warning(f"[{trace.trace_id[:8]}] RAG failed: {e}")

    with trace_span(trace, "llm_thinking"):
        messages: list[ChatMessage] = [{"role": "system", "content": system_prompt_base()}]

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
                if event["type"] == "reasoning_delta":
                    reasoning_text = event["text"]
                    if reasoning_text:
                        sanitized = sanitize_reasoning(reasoning_text)
                        if sanitized:
                            yield emit(ReasoningEvent(content=sanitized))
                elif event["type"] == "content_delta":
                    content_text = event["text"]
                    if content_text:
                        response_text += content_text
                        yield emit(ContentEvent(content=content_text))
                elif event["type"] == "done":
                    response = LLMResponse(
                        content=response_text or event["content"],
                        usage=event["usage"],
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
                yield emit(ContentEvent(content=response.content))

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

    yield emit(CompleteEvent(
        success=True,
        trace_id=trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    ))
