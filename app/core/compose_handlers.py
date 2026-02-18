"""
Orchestration and request handlers for Composer (Cursor-of-DAWs).

This module contains the main orchestrate() flow and the three handlers
(REASONING, COMPOSING, EDITING). The API route layer imports orchestrate
and UsageTracker from here so the route file stays thin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, cast

from app.config import settings
from app.core.entity_context import build_entity_context_for_llm, format_project_context
from app.core.expansion import ToolCall
from app.core.intent import (
    Intent,
    IntentResult,
    SSEState,
    get_intent_result_with_llm,
)
from app.core.intent_config import (
    _PRIMITIVES_FX,
    _PRIMITIVES_MIXING,
    _PRIMITIVES_REGION,
    _PRIMITIVES_TRACK,
)
from app.core.llm_client import (
    LLMClient,
    LLMResponse,
    ToolCallData,
    enforce_single_tool,
)
from app.core.pipeline import run_pipeline
from app.core.prompt_parser import ParsedPrompt
from app.core.prompts import editing_composition_prompt, editing_prompt, structured_prompt_context, system_prompt_base
from app.core.sse_utils import ReasoningBuffer, sanitize_reasoning, sse_event, strip_tool_echoes
from app.core.state_store import StateStore, get_or_create_store
from app.core.tool_validation import validate_tool_call
from app.core.tools import ALL_TOOLS
from app.core.tracing import (
    clear_trace_context,
    create_trace_context,
    log_intent,
    log_llm_call,
    log_tool_call,
    log_validation_error,
    trace_span,
)
from app.services.budget import get_model_or_default

logger = logging.getLogger(__name__)


@dataclass
class StreamFinalResponse:
    """Sentinel yielded by _stream_llm_response when the LLM stream is done. Carry the final LLMResponse."""
    response: LLMResponse


@dataclass
class UsageTracker:
    """Tracks token usage across LLM calls."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    
    def add(self, prompt: int, completion: int):
        self.prompt_tokens += prompt
        self.completion_tokens += completion


# Entity ID fields to echo back to the LLM after entity-creating tool calls.
# The server replaces LLM-provided UUIDs with freshly generated ones; the LLM
# must receive the real IDs so it can reference them in subsequent calls.
_ENTITY_ID_ECHO: dict[str, list[str]] = {
    "stori_add_midi_track": ["trackId"],
    "stori_add_midi_region": ["regionId", "trackId"],
    "stori_ensure_bus": ["busId"],
}


def _project_needs_structure(project_context: dict[str, Any]) -> bool:
    """Check if the project is empty and needs structural creation.

    Returns True when the project has no tracks, meaning composition
    requests should use EDITING mode (tool_call events) rather than
    COMPOSING mode (variation review) — you can't diff against nothing.
    """
    tracks = project_context.get("tracks", [])
    return len(tracks) == 0


def _get_incomplete_tracks(
    store: "StateStore",
    tool_calls_collected: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return names of tracks that are missing regions or notes.

    Checks two conditions:
    1. Track has no regions at all
    2. Track has regions but none of them received stori_add_notes calls

    Used by the composition continuation loop to detect premature LLM stops.
    """
    # Build set of regionIds that received notes
    regions_with_notes: set[str] = set()
    if tool_calls_collected:
        for tc in tool_calls_collected:
            if tc["tool"] == "stori_add_notes":
                rid = tc["params"].get("regionId")
                if rid:
                    regions_with_notes.add(rid)

    incomplete: list[str] = []
    for track in store.registry.list_tracks():
        regions = store.registry.get_track_regions(track.id)
        if not regions:
            incomplete.append(track.name)
        elif not any(r.id in regions_with_notes for r in regions):
            incomplete.append(track.name)
    return incomplete


def _create_editing_composition_route(route: "IntentResult") -> "IntentResult":
    """Build an EDITING IntentResult for composition on empty projects.

    When the project has no tracks, composition requests should use EDITING
    mode so structural changes (tracks, regions, instruments, notes) are
    emitted as tool_call events for real-time frontend rendering.
    """
    all_composition_tools = (
        set(_PRIMITIVES_TRACK) | set(_PRIMITIVES_REGION)
        | set(_PRIMITIVES_FX) | set(_PRIMITIVES_MIXING)
        | {"stori_set_tempo", "stori_set_key_signature"}
    )
    return IntentResult(
        intent=route.intent,
        sse_state=SSEState.EDITING,
        confidence=route.confidence,
        slots=route.slots,
        tools=ALL_TOOLS,
        allowed_tool_names=all_composition_tools,
        tool_choice="auto",
        force_stop_after=False,
        requires_planner=False,
        reasons=route.reasons + ("empty_project_override",),
    )


def _store_variation(
    variation,
    project_context: dict[str, Any],
    store: "StateStore",
) -> None:
    """Persist a Variation to the VariationStore so commit/discard can find it.

    Called from the compose/stream path after ``execute_plan_variation`` returns.
    Mirrors the storage logic in the ``/variation/propose`` background task.
    """
    from app.variation.storage.variation_store import (
        get_variation_store,
        PhraseRecord,
    )
    from app.variation.core.state_machine import VariationStatus

    project_id = project_context.get("projectId", "")
    base_state_id = store.get_state_id()

    vstore = get_variation_store()
    record = vstore.create(
        project_id=project_id,
        base_state_id=base_state_id,
        intent=variation.intent,
        variation_id=variation.variation_id,
    )

    # CREATED → STREAMING → READY (fast-forward since generation is already done)
    record.transition_to(VariationStatus.STREAMING)
    record.ai_explanation = variation.ai_explanation
    record.affected_tracks = variation.affected_tracks
    record.affected_regions = variation.affected_regions

    for phrase in variation.phrases:
        seq = record.next_sequence()
        record.add_phrase(PhraseRecord(
            phrase_id=phrase.phrase_id,
            variation_id=variation.variation_id,
            sequence=seq,
            track_id=phrase.track_id,
            region_id=phrase.region_id,
            beat_start=phrase.start_beat,
            beat_end=phrase.end_beat,
            label=phrase.label,
            diff_json={
                "phrase_id": phrase.phrase_id,
                "track_id": phrase.track_id,
                "region_id": phrase.region_id,
                "start_beat": phrase.start_beat,
                "end_beat": phrase.end_beat,
                "label": phrase.label,
                "tags": phrase.tags,
                "explanation": phrase.explanation,
                "note_changes": [nc.model_dump() for nc in phrase.note_changes],
                "controller_changes": phrase.controller_changes,
            },
            ai_explanation=phrase.explanation,
            tags=phrase.tags,
        ))

    record.transition_to(VariationStatus.READY)
    logger.info(
        f"Variation stored: {variation.variation_id[:8]} "
        f"({len(variation.phrases)} phrases, status=READY)"
    )


async def orchestrate(
    prompt: str,
    project_context: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
    usage_tracker: Optional[UsageTracker] = None,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    conversation_history: Optional[list[dict[str, Any]]] = None,
    is_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
) -> AsyncIterator[str]:
    """
    Main orchestration using Cursor-of-DAWs architecture.
    
    Flow:
    1. Create trace context for request
    2. Intent Router classifies prompt → route + allowlist
    3. Backend determines execution_mode from intent:
       COMPOSING → variation (human review), EDITING → apply (immediate)
    4. Route to REASONING / EDITING / COMPOSING
    5. Execute with strict tool gating + entity validation
    """
    project_context = project_context or {}
    conversation_history = conversation_history or []
    selected_model = get_model_or_default(model)
    
    # Create trace context for this request
    trace = create_trace_context(
        conversation_id=conversation_id,
        user_id=user_id,
    )
    
    llm = LLMClient(model=selected_model)
    
    # Get or create StateStore — use project_id as primary key so the
    # variation commit endpoint can find the same store instance.
    _project_id = project_context.get("projectId") or ""
    store = get_or_create_store(
        conversation_id=_project_id or conversation_id or "default",
        project_id=_project_id,
    )
    store.sync_from_client(project_context)
    
    try:
        with trace_span(trace, "orchestrate", {"prompt_length": len(prompt)}):
            
            # =================================================================
            # Step 1: Intent Classification
            # =================================================================
            
            with trace_span(trace, "intent_classification"):
                route = await get_intent_result_with_llm(prompt, project_context, llm, conversation_history)
                
                # Backend-owned execution mode policy:
                # COMPOSING → variation (music generation requires human review)
                #   EXCEPT: empty project → override to EDITING (can't diff against nothing)
                # EDITING   → apply (structural ops execute directly)
                # REASONING → n/a (no tools)
                if route.sse_state == SSEState.COMPOSING:
                    if _project_needs_structure(project_context):
                        # Empty project: structural changes need tool_call events,
                        # not variation review — you can't diff against nothing.
                        route = _create_editing_composition_route(route)
                        execution_mode = "apply"
                        logger.info(
                            f"🔄 Empty project: overriding {route.intent.value} → EDITING "
                            f"for structural creation with tool_call events"
                        )
                    else:
                        execution_mode = "variation"
                        logger.info(f"Intent {route.intent.value} → COMPOSING, execution_mode='variation'")
                else:
                    execution_mode = "apply"
                    logger.info(f"Intent {route.intent.value} → {route.sse_state.value}, execution_mode='apply'")
                
                log_intent(
                    trace.trace_id,
                    prompt,
                    route.intent.value,
                    route.confidence,
                    route.sse_state.value,
                    route.reasons,
                )
            
            # Emit SSE state for frontend
            yield await sse_event({
                "type": "state",
                "state": route.sse_state.value,
                "intent": route.intent.value,
                "confidence": route.confidence,
                "trace_id": trace.trace_id,
            })
            
            logger.info(f"[{trace.trace_id[:8]}] 🎯 {route.intent.value} → {route.sse_state.value}")
            
            # =================================================================
            # Step 2: Handle REASONING (questions - no tools)
            # =================================================================
            
            if route.sse_state == SSEState.REASONING:
                async for event in _handle_reasoning(
                    prompt, project_context, route, llm, trace, 
                    usage_tracker, conversation_history
                ):
                    yield event
                return
            
            # =================================================================
            # Step 3: Handle COMPOSING (planner path)
            # =================================================================
            
            if route.sse_state == SSEState.COMPOSING:
                async for event in _handle_composing(
                    prompt, project_context, route, llm, store, trace, 
                    usage_tracker, conversation_id,
                ):
                    yield event
                return
            
            # =================================================================
            # Step 4: Handle EDITING (LLM tool calls with allowlist)
            # =================================================================
            
            async for event in _handle_editing(
                prompt, project_context, route, llm, store, trace,
                usage_tracker, conversation_history, execution_mode,
                is_cancelled=is_cancelled,
            ):
                yield event
    
    except Exception as e:
        logger.exception(f"[{trace.trace_id[:8]}] Orchestration error: {e}")
        yield await sse_event({
            "type": "error",
            "message": str(e),
            "trace_id": trace.trace_id,
        })
    
    finally:
        await llm.close()
        clear_trace_context()


async def _handle_reasoning(
    prompt: str,
    project_context: dict[str, Any],
    route,
    llm: LLMClient,
    trace,
    usage_tracker: Optional[UsageTracker],
    conversation_history: list[dict[str, Any]],
) -> AsyncIterator[str]:
    """Handle REASONING state - answer questions without tools."""
    yield await sse_event({"type": "status", "message": "Reasoning..."})
    
    # Check for Stori docs questions → RAG
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
                    "tool_calls": [],
                    "trace_id": trace.trace_id,
                })
                return
        except Exception as e:
            logger.warning(f"[{trace.trace_id[:8]}] RAG failed: {e}")
    
    # General question → LLM without tools
    with trace_span(trace, "llm_thinking"):
        messages = [{"role": "system", "content": system_prompt_base()}]

        if project_context:
            messages.append({"role": "system", "content": format_project_context(project_context)})

        if conversation_history:
            messages.extend(conversation_history)
        
        messages.append({"role": "user", "content": prompt})
        
        start_time = time.time()
        response = None

        # Use streaming for reasoning models
        logger.info(f"🎯 REASONING handler: supports_reasoning={llm.supports_reasoning()}, model={llm.model}")
        if llm.supports_reasoning():
            logger.info("🌊 Using streaming path for reasoning model")
            response_text = ""
            async for raw in llm.chat_completion_stream(
                messages=messages,
                tools=[],
                tool_choice="none",
            ):
                event = cast(dict[str, Any], raw)
                if event.get("type") == "reasoning_delta":
                    # Chain of Thought reasoning (extended reasoning from OpenRouter)
                    reasoning_text = event.get("text", "")
                    if reasoning_text:
                        # Sanitize reasoning to remove internal implementation details
                        sanitized = sanitize_reasoning(reasoning_text)
                        if sanitized:  # Only emit if there's content after sanitization
                            yield await sse_event({
                                "type": "reasoning",
                                "content": sanitized,
                            })
                elif event.get("type") == "content_delta":
                    # User-facing response
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
            # Non-thinking models use regular completion
            response = await llm.chat_completion(
                messages=messages,
                tools=[],
                tool_choice="none",
            )
            duration_ms = (time.time() - start_time) * 1000
            
            # Stream the response content
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
        "tool_calls": [],
        "trace_id": trace.trace_id,
    })


def _create_editing_fallback_route(route) -> IntentResult:
    """
    Build an IntentResult for EDITING when the COMPOSING planner fails with function-call-like output.

    The planner is supposed to return JSON; sometimes the LLM returns tool-call syntax instead.
    This creates a one-off EDITING route with primitives so we can still produce tool calls.
    See docs/COMPOSER_ARCHITECTURE.md.
    """
    return IntentResult(
        intent=Intent.NOTES_ADD,
        sse_state=SSEState.EDITING,
        confidence=0.7,
        slots=route.slots,
        tools=ALL_TOOLS,
        allowed_tool_names=set(_PRIMITIVES_REGION) | set(_PRIMITIVES_TRACK),
        tool_choice="auto",
        force_stop_after=False,
        requires_planner=False,
        reasons=("Fallback from planner failure",),
    )


async def _retry_composing_as_editing(
    prompt: str,
    project_context: dict[str, Any],
    route,
    llm: LLMClient,
    store: StateStore,
    trace,
    usage_tracker: Optional[UsageTracker],
) -> AsyncIterator[str]:
    """When planner output looks like function calls instead of JSON, retry as EDITING with primitives."""
    logger.warning(
        f"[{trace.trace_id[:8]}] Planner output looks like function calls, "
        "falling back to EDITING mode with tools"
    )
    yield await sse_event({"type": "status", "message": "Retrying with different approach..."})
    editing_route = _create_editing_fallback_route(route)
    async for event in _handle_editing(
        prompt, project_context, editing_route, llm, store,
        trace, usage_tracker, [], "variation"
    ):
        yield event


async def _handle_composing(
    prompt: str,
    project_context: dict[str, Any],
    route,
    llm: LLMClient,
    store: StateStore,
    trace,
    usage_tracker: Optional[UsageTracker],
    conversation_id: Optional[str],
) -> AsyncIterator[str]:
    """Handle COMPOSING state - generate music via planner.
    
    All COMPOSING intents produce a Variation for human review.
    The planner generates a tool-call plan, the executor simulates it
    in variation mode, and the result is streamed as meta/phrase/done events.
    """
    yield await sse_event({"type": "status", "message": "Generating variation..."})
    
    with trace_span(trace, "planner"):
        output = await run_pipeline(prompt, project_context, llm)
    
    if output.plan and output.plan.tool_calls:
        yield await sse_event({
            "type": "plan_summary",
            "total_steps": len(output.plan.tool_calls),
            "generations": output.plan.generation_count,
            "edits": output.plan.edit_count,
        })
        
        # =====================================================================
        # Variation Mode: Generate proposal without mutation
        # =====================================================================
        try:
            with trace_span(trace, "variation_generation", {"steps": len(output.plan.tool_calls)}):
                from app.core.executor import execute_plan_variation

                logger.info(
                    f"[{trace.trace_id[:8]}] Starting variation generation: "
                    f"{len(output.plan.tool_calls)} tool calls"
                )

                # Progress queue so we can emit step_progress SSE while executor runs
                progress_queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()

                async def on_progress(current: int, total: int) -> None:
                    await progress_queue.put((current, total))

                # Timeout: prevent infinite hangs from backend calls
                _VARIATION_TIMEOUT = 90  # seconds
                task = asyncio.create_task(
                    execute_plan_variation(
                        tool_calls=output.plan.tool_calls,
                        project_state=project_context,
                        intent=prompt,
                        conversation_id=conversation_id,
                        explanation=output.plan.llm_response_text,
                        progress_callback=on_progress,
                    )
                )
                # Drain progress queue and yield progress SSE; enforce 90s timeout
                variation = None
                start_wall = time.time()
                try:
                    while True:
                        if time.time() - start_wall > _VARIATION_TIMEOUT:
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                            raise asyncio.TimeoutError()
                        try:
                            current, total = await asyncio.wait_for(
                                progress_queue.get(), timeout=0.05
                            )
                            yield await sse_event({
                                "type": "progress",
                                "current_step": current,
                                "total_steps": total,
                                "message": f"Step {current}/{total}...",
                            })
                        except asyncio.TimeoutError:
                            if task.done():
                                break
                        await asyncio.sleep(0)
                    variation = await task
                except asyncio.TimeoutError:
                    logger.error(
                        f"[{trace.trace_id[:8]}] Variation generation timed out "
                        f"after {_VARIATION_TIMEOUT}s"
                    )
                    yield await sse_event({
                        "type": "error",
                        "message": f"Generation timed out after {_VARIATION_TIMEOUT}s",
                        "trace_id": trace.trace_id,
                    })
                    yield await sse_event({
                        "type": "done",
                        "variation_id": "",
                        "phrase_count": 0,
                        "status": "failed",
                    })
                    yield await sse_event({
                        "type": "complete",
                        "success": False,
                        "error": "timeout",
                        "trace_id": trace.trace_id,
                    })
                    return

                logger.info(
                    f"[{trace.trace_id[:8]}] Variation computed: "
                    f"{variation.total_changes} changes, {len(variation.phrases)} phrases"
                )

                # Persist to VariationStore so commit/discard can find it
                _store_variation(variation, project_context, store)

                # Emit meta event
                note_counts = variation.note_counts
                yield await sse_event({
                    "type": "meta",
                    "variation_id": variation.variation_id,
                    "intent": variation.intent,
                    "ai_explanation": variation.ai_explanation,
                    "affected_tracks": variation.affected_tracks,
                    "affected_regions": variation.affected_regions,
                    "note_counts": note_counts,
                })

                # Emit individual phrase events
                for i, phrase in enumerate(variation.phrases):
                    logger.debug(
                        f"[{trace.trace_id[:8]}] Emitting phrase {i + 1}/{len(variation.phrases)}: "
                        f"{len(phrase.note_changes)} note changes"
                    )
                    yield await sse_event({
                        "type": "phrase",
                        "phrase_id": phrase.phrase_id,
                        "track_id": phrase.track_id,
                        "region_id": phrase.region_id,
                        "start_beat": phrase.start_beat,
                        "end_beat": phrase.end_beat,
                        "label": phrase.label,
                        "tags": phrase.tags,
                        "explanation": phrase.explanation,
                        "note_changes": [nc.model_dump() for nc in phrase.note_changes],
                        "controller_changes": phrase.controller_changes,
                    })

                # Emit done event
                yield await sse_event({
                    "type": "done",
                    "variation_id": variation.variation_id,
                    "phrase_count": len(variation.phrases),
                })

                logger.info(
                    f"[{trace.trace_id[:8]}] Variation streamed: "
                    f"{variation.total_changes} changes in {len(variation.phrases)} phrases"
                )

                # Complete event
                yield await sse_event({
                    "type": "complete",
                    "success": True,
                    "variation_id": variation.variation_id,
                    "total_changes": variation.total_changes,
                    "phrase_count": len(variation.phrases),
                    "trace_id": trace.trace_id,
                })

        except Exception as e:
            logger.exception(
                f"[{trace.trace_id[:8]}] Variation generation failed: {e}"
            )
            yield await sse_event({
                "type": "error",
                "message": f"Generation failed: {e}",
                "trace_id": trace.trace_id,
            })
            yield await sse_event({
                "type": "done",
                "variation_id": "",
                "phrase_count": 0,
                "status": "failed",
            })
            yield await sse_event({
                "type": "complete",
                "success": False,
                "error": str(e),
                "trace_id": trace.trace_id,
            })
        return
    else:
        # Planner couldn't generate a valid JSON plan
        # Check if the LLM output looks like function calls (common failure mode)
        response_text = None
        if output.plan and output.plan.llm_response_text:
            response_text = output.plan.llm_response_text
        elif output.llm_response and output.llm_response.content:
            response_text = output.llm_response.content
        
        # Detect if LLM output looks like function call syntax (not JSON)
        looks_like_function_calls = (
            response_text and 
            ("stori_" in response_text or 
             "add_midi_track(" in response_text or
             "add_notes(" in response_text or
             "add_region(" in response_text)
        )
        
        if looks_like_function_calls:
            # Explicit fallback: planner returned function-call-like text instead of JSON.
            # Re-route as EDITING with primitives so we still get tool calls. See docs/COMPOSER_ARCHITECTURE.md.
            async for event in _retry_composing_as_editing(
                prompt, project_context, route, llm, store,
                trace, usage_tracker,
            ):
                yield event
            return
        
        # Otherwise, provide guidance to the user
        if response_text:
            # Don't stream raw LLM output that looks malformed
            # Instead, ask for clarification
            yield await sse_event({
                "type": "content",
                "content": "I understand you want to generate music. To help me create exactly what you're looking for, "
                           "could you tell me:\n"
                           "- What style or genre? (e.g., 'lofi', 'jazz', 'electronic')\n"
                           "- What tempo? (e.g., 90 BPM)\n"
                           "- How many bars? (e.g., 8 bars)\n\n"
                           "Example: 'Create an exotic melody at 100 BPM for 8 bars in C minor'",
            })
        else:
            yield await sse_event({
                "type": "content",
                "content": "I need more information to generate music. Please specify:\n"
                           "- Style/genre (e.g., 'boom bap', 'lofi', 'trap')\n"
                           "- Tempo (e.g., 90 BPM)\n"
                           "- Number of bars (e.g., 8 bars)\n\n"
                           "Example: 'Make a boom bap beat at 90 BPM with drums and bass for 8 bars'",
            })
        
        yield await sse_event({
            "type": "complete",
            "success": True,
            "tool_calls": [],
            "trace_id": trace.trace_id,
        })


async def _handle_editing(
    prompt: str,
    project_context: dict[str, Any],
    route,
    llm: LLMClient,
    store: StateStore,
    trace,
    usage_tracker: Optional[UsageTracker],
    conversation_history: list[dict[str, Any]],
    execution_mode: str = "apply",
    is_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
) -> AsyncIterator[str]:
    """Handle EDITING state - LLM tool calls with allowlist + validation.
    
    Args:
        execution_mode: "apply" for immediate mutation, "variation" for proposal mode
        is_cancelled: async callback returning True if the client disconnected
    """
    status_msg = "Processing..." if execution_mode == "apply" else "Generating variation..."
    yield await sse_event({"type": "status", "message": status_msg})
    
    # Use composition-specific prompt when GENERATE_MUSIC was re-routed to EDITING
    if route.intent == Intent.GENERATE_MUSIC:
        sys_prompt = system_prompt_base() + "\n" + editing_composition_prompt()
    else:
        required_single = bool(route.force_stop_after and route.tool_choice == "required")
        sys_prompt = system_prompt_base() + "\n" + editing_prompt(required_single)

    # Inject structured context from structured prompt if present
    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: Optional[ParsedPrompt] = _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    if parsed is not None:
        sys_prompt += structured_prompt_context(parsed)
    
    # Build allowed tools only (Cursor-style action space shaping)
    allowed_tools = [t for t in ALL_TOOLS if t["function"]["name"] in route.allowed_tool_names]
    
    messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]

    # Inject project context — prefer the request-body snapshot (authoritative),
    # fall back to the entity registry for sessions that don't send project.
    if project_context:
        messages.append({"role": "system", "content": format_project_context(project_context)})
    else:
        messages.append({"role": "system", "content": build_entity_context_for_llm(store)})

    if conversation_history:
        messages.extend(conversation_history)
    
    messages.append({"role": "user", "content": prompt})
    
    # Use higher token budget for composition (multi-track MIDI data is token-heavy)
    is_composition = route.intent == Intent.GENERATE_MUSIC
    llm_max_tokens: Optional[int] = settings.composition_max_tokens if is_composition else None
    reasoning_fraction: Optional[float] = settings.composition_reasoning_fraction if is_composition else None
    
    tool_calls_collected: list[dict[str, Any]] = []
    iteration = 0
    max_iterations = (
        settings.composition_max_iterations if is_composition
        else settings.orchestration_max_iterations
    )
    
    while iteration < max_iterations:
        iteration += 1

        # Check for client disconnect before each LLM call
        if is_cancelled:
            try:
                if await is_cancelled():
                    logger.info(
                        f"[{trace.trace_id[:8]}] 🛑 Client disconnected, "
                        f"stopping at iteration {iteration}"
                    )
                    break
            except Exception:
                pass  # Swallow errors from disconnect check

        logger.info(
            f"[{trace.trace_id[:8]}] 🔄 Editing iteration {iteration}/{max_iterations} "
            f"(composition={is_composition})"
        )

        with trace_span(trace, f"llm_iteration_{iteration}"):
            start_time = time.time()
            
            # Use streaming for reasoning models
            if llm.supports_reasoning():
                response = None
                async for item in _stream_llm_response(
                    llm, messages, allowed_tools, route.tool_choice,
                    trace, lambda data: sse_event(data),
                    max_tokens=llm_max_tokens,
                    reasoning_fraction=reasoning_fraction,
                    suppress_content=True,
                ):
                    # Check if this is the final response marker
                    if isinstance(item, StreamFinalResponse):
                        response = item.response
                    else:
                        # Reasoning events — forward to client
                        yield item
            else:
                response = await llm.chat_completion(
                    messages=messages,
                    tools=allowed_tools,
                    tool_choice=route.tool_choice,
                    temperature=settings.orchestration_temperature,
                    max_tokens=llm_max_tokens,
                )
            
            duration_ms = (time.time() - start_time) * 1000
            
            if response is None:
                break
            if response.usage:
                log_llm_call(
                    trace.trace_id,
                    llm.model,
                    response.usage.get("prompt_tokens", 0),
                    response.usage.get("completion_tokens", 0),
                    duration_ms,
                    response.has_tool_calls,
                )
                if usage_tracker:
                    usage_tracker.add(
                        response.usage.get("prompt_tokens", 0),
                        response.usage.get("completion_tokens", 0),
                    )
        
        # Enforce single tool for force_stop_after
        if response is None:
            break
        if route.force_stop_after:
            response = enforce_single_tool(response)

        # Emit filtered content — strip leaked tool-call syntax
        # (e.g. "(key=\"G major\")", "(,, )") while keeping
        # natural-language text the user should see.
        if response.content:
            clean_content = strip_tool_echoes(response.content)
            if clean_content:
                yield await sse_event({"type": "content", "content": clean_content})

        # No more tool calls — fall through to continuation check
        if not response.has_tool_calls:
            # For non-composition, no tool calls means we're done
            if not is_composition:
                break
            # For composition, fall through to the unified continuation
            # check after the tool-call processing block
        
        # Process tool calls with validation
        for tc in response.tool_calls:
            with trace_span(trace, f"validate:{tc.name}"):
                validation = validate_tool_call(
                    tc.name, tc.arguments, route.allowed_tool_names, store.registry
                )
            
            if not validation.valid:
                log_validation_error(
                    trace.trace_id,
                    tc.name,
                    [str(e) for e in validation.errors],
                )
                
                yield await sse_event({
                    "type": "tool_error",
                    "name": tc.name,
                    "error": validation.error_message,
                    "errors": [str(e) for e in validation.errors],
                })
                
                # Add error to messages so LLM knows
                messages.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}
                    }]
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": validation.error_message}),
                })
                continue
            
            enriched_params = validation.resolved_params
            
            # Register entities for entity-creating tools via StateStore
            if tc.name == "stori_add_midi_track":
                track_name = enriched_params.get("name", "Track")
                instrument = enriched_params.get("instrument")
                gm_program = enriched_params.get("gmProgram")
                
                # CRITICAL: Always generate new UUID for entity-creating tools
                # LLMs sometimes hallucinate duplicate UUIDs, causing frontend crashes
                # System prompt instructs LLM NOT to provide IDs for new entities,
                # but we enforce this server-side as a safety measure
                if "trackId" in enriched_params:
                    logger.warning(
                        f"⚠️ LLM provided trackId '{enriched_params['trackId']}' for NEW track '{track_name}'. "
                        f"Ignoring and generating fresh UUID to prevent duplicates."
                    )
                
                # Always generate fresh UUID via StateStore
                track_id = store.create_track(track_name)
                enriched_params["trackId"] = track_id
                logger.debug(f"🔑 Generated trackId: {track_id[:8]} for '{track_name}'")
                
                # Auto-infer GM program if not specified
                if gm_program is None:
                    from app.core.gm_instruments import infer_gm_program_with_context
                    inference = infer_gm_program_with_context(
                        track_name=track_name,
                        instrument=instrument,
                    )
                    # Always provide instrument metadata
                    enriched_params["_gmInstrumentName"] = inference.instrument_name
                    enriched_params["_isDrums"] = inference.is_drums
                    
                    logger.info(
                        f"🎵 [EDITING] GM inference for '{track_name}': "
                        f"program={inference.program}, instrument={inference.instrument_name}, is_drums={inference.is_drums}"
                    )
                    
                    # Add GM program if not drums
                    if inference.needs_program_change:
                        enriched_params["gmProgram"] = inference.program
            
            elif tc.name == "stori_add_midi_region":
                midi_region_track_id: Optional[str] = enriched_params.get("trackId")
                region_name: str = str(enriched_params.get("name", "Region"))
                
                # CRITICAL: Always generate new UUID for entity-creating tools
                # LLMs sometimes hallucinate duplicate UUIDs, causing frontend crashes
                if "regionId" in enriched_params:
                    logger.warning(
                        f"⚠️ LLM provided regionId '{enriched_params['regionId']}' for NEW region '{region_name}'. "
                        f"Ignoring and generating fresh UUID to prevent duplicates."
                    )
                
                # Always generate fresh UUID via StateStore
                if midi_region_track_id:
                    try:
                        region_id = store.create_region(
                            region_name, midi_region_track_id,
                            metadata={
                                "startBeat": enriched_params.get("startBeat", 0),
                                "durationBeats": enriched_params.get("durationBeats", 16),
                            }
                        )
                        enriched_params["regionId"] = region_id
                        logger.debug(f"🔑 Generated regionId: {region_id[:8]} for '{region_name}'")
                    except ValueError as e:
                        logger.error(f"Failed to create region: {e}")
            
            elif tc.name == "stori_ensure_bus":
                bus_name = enriched_params.get("name", "Bus")
                
                # CRITICAL: Always let server manage bus IDs
                if "busId" in enriched_params:
                    logger.warning(
                        f"⚠️ LLM provided busId '{enriched_params['busId']}' for bus '{bus_name}'. "
                        f"Ignoring to prevent duplicates."
                    )
                
                bus_id = store.get_or_create_bus(bus_name)
                enriched_params["busId"] = bus_id
            
            # Emit tool call to client (only in apply mode)
            if execution_mode == "apply":
                yield await sse_event({
                    "type": "tool_call",
                    "id": tc.id,
                    "name": tc.name,
                    "params": enriched_params,
                })
            
            log_tool_call(trace.trace_id, tc.name, enriched_params, True)
            
            tool_calls_collected.append({
                "tool": tc.name,
                "params": enriched_params,
            })
            
            # Add to messages — summarize stori_add_notes to avoid
            # bloating context with hundreds of note objects
            if tc.name == "stori_add_notes":
                notes = enriched_params.get("notes", [])
                summary_params = {
                    k: v for k, v in enriched_params.items() if k != "notes"
                }
                summary_params["_noteCount"] = len(notes)
                if notes:
                    starts = [n["startBeat"] for n in notes]
                    summary_params["_beatRange"] = [min(starts), max(starts)]
                msg_arguments = json.dumps(summary_params)
            else:
                msg_arguments = json.dumps(enriched_params)

            messages.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": msg_arguments}
                }]
            })
            # Echo server-assigned entity IDs back to the LLM.
            # For entity-creating tools the server generates fresh UUIDs and
            # replaces whatever the LLM provided — the LLM must know the real
            # IDs so it can reference them correctly in subsequent tool calls.
            tool_result: dict = {"status": "success"}
            for _field in _ENTITY_ID_ECHO.get(tc.name, []):
                if _field in enriched_params:
                    tool_result[_field] = enriched_params[_field]

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_result),
            })
        
        # Force stop after first tool execution
        if route.force_stop_after and tool_calls_collected:
            logger.info(f"[{trace.trace_id[:8]}] ✅ Force stop after {len(tool_calls_collected)} tool(s)")
            break

        # ── Composition continuation: always check after tool calls ──
        # The LLM may return tool calls every iteration but never finish
        # all tracks. We must check and re-prompt regardless of whether
        # tool calls were present or what finish_reason says.
        if is_composition and iteration < max_iterations:
            all_tracks = store.registry.list_tracks()
            incomplete = _get_incomplete_tracks(store, tool_calls_collected)

            if not all_tracks:
                # No tracks created yet — the composition hasn't started
                continuation = (
                    "You haven't created any tracks yet. "
                    "Use stori_add_midi_track to create the instruments, "
                    "then stori_add_midi_region and stori_add_notes for each."
                )
                messages.append({"role": "user", "content": continuation})
                logger.info(
                    f"[{trace.trace_id[:8]}] 🔄 Continuation: no tracks yet "
                    f"(iteration {iteration})"
                )
                continue
            elif incomplete:
                continuation = (
                    f"Continue — these tracks still need regions and notes: "
                    f"{', '.join(incomplete)}. "
                    f"Call stori_add_midi_region AND stori_add_notes together for each track. "
                    f"Use multiple tool calls in one response."
                )
                messages.append({"role": "user", "content": continuation})
                logger.info(
                    f"[{trace.trace_id[:8]}] 🔄 Continuation: {len(incomplete)} tracks still need content "
                    f"(iteration {iteration})"
                )
                continue
            else:
                # All tracks have content — composition is complete
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ All tracks have content after iteration {iteration}"
                )
                break

        # ── Non-composition: stop after executing tool calls ──
        # For non-composition editing, the LLM should batch everything it
        # needs in one response.  Don't re-prompt — that causes runaway loops
        # where the LLM keeps adding notes indefinitely.
        # Only continue if there were NO tool calls (content-only response).
        if not is_composition:
            if response is not None and response.has_tool_calls:
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ Non-composition: executed "
                    f"{len(response.tool_calls)} tool(s), stopping after iteration {iteration}"
                )
                break
            # No tool calls — LLM is done (emitted content-only response)
            break
    
    # =========================================================================
    # Variation Mode: Compute and emit variation per spec (meta/phrase/done)
    # =========================================================================
    if execution_mode == "variation" and tool_calls_collected:
        from app.core.executor import execute_plan_variation
        from app.core.expansion import ToolCall
        
        # Convert collected tool calls to ToolCall objects
        tool_call_objs = [
            ToolCall(name=cast(str, tc["tool"]), params=cast(dict[str, Any], tc["params"]))
            for tc in tool_calls_collected
        ]
        
        variation = await execute_plan_variation(
            tool_calls=tool_call_objs,
            project_state=project_context,
            intent=prompt,
            conversation_id=None,  # EDITING doesn't use conversation_id here
            explanation=None,
        )

        # Persist to VariationStore so commit/discard can find it
        _store_variation(variation, project_context, store)

        # Emit meta event (overall summary per spec)
        note_counts = variation.note_counts
        yield await sse_event({
            "type": "meta",
            "variation_id": variation.variation_id,
            "intent": variation.intent,
            "ai_explanation": variation.ai_explanation,
            "affected_tracks": variation.affected_tracks,
            "affected_regions": variation.affected_regions,
            "note_counts": note_counts,
        })
        
        # Emit individual phrase events (per spec)
        for phrase in variation.phrases:
            yield await sse_event({
                "type": "phrase",
                "phrase_id": phrase.phrase_id,
                "track_id": phrase.track_id,
                "region_id": phrase.region_id,
                "start_beat": phrase.start_beat,
                "end_beat": phrase.end_beat,
                "label": phrase.label,
                "tags": phrase.tags,
                "explanation": phrase.explanation,
                "note_changes": [nc.model_dump() for nc in phrase.note_changes],
                "controller_changes": phrase.controller_changes,
            })
        
        # Emit done event (per spec — includes phrase_count)
        yield await sse_event({
            "type": "done",
            "variation_id": variation.variation_id,
            "phrase_count": len(variation.phrases),
        })
        
        logger.info(
            f"[{trace.trace_id[:8]}] EDITING variation streamed: "
            f"{variation.total_changes} changes in {len(variation.phrases)} phrases"
        )
        
        # Complete event
        yield await sse_event({
            "type": "complete",
            "success": True,
            "variation_id": variation.variation_id,
            "total_changes": variation.total_changes,
            "phrase_count": len(variation.phrases),
            "trace_id": trace.trace_id,
        })
        return
    
    # =========================================================================
    # Apply Mode: Standard completion (existing behavior)
    # =========================================================================
    yield await sse_event({
        "type": "complete",
        "success": True,
        "tool_calls": tool_calls_collected,
        "state_version": store.version,
        "trace_id": trace.trace_id,
    })


async def _stream_llm_response(
    llm: LLMClient,
    messages: list[dict],
    tools: list[dict],
    tool_choice: str,
    trace,
    emit_sse,
    max_tokens: Optional[int] = None,
    reasoning_fraction: Optional[float] = None,
    suppress_content: bool = False,
):
    """Stream LLM response with thinking deltas. Yields SSE events and final response.

    Reasoning tokens are buffered via ReasoningBuffer so BPE sub-word pieces
    are merged into complete words before sanitization and SSE emission.

    Args:
        suppress_content: When True, content deltas are accumulated on the
            response but NOT emitted as SSE events.  Used by the EDITING
            handler because the LLM often interleaves tool-call argument
            syntax (e.g. ``(key="G major")``) into the content stream,
            which is meaningless to the user.  The caller decides whether
            to emit ``response.content`` after the stream ends.
    """
    response_content = None
    response_tool_calls: list[dict[str, Any]] = []
    finish_reason: Optional[str] = None
    usage: dict[str, Any] = {}
    reasoning_buf = ReasoningBuffer()
    
    async for chunk in llm.chat_completion_stream(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=settings.orchestration_temperature,
        max_tokens=max_tokens,
        reasoning_fraction=reasoning_fraction,
    ):
        if chunk.get("type") == "reasoning_delta":
            reasoning_text = chunk.get("text", "")
            if reasoning_text:
                to_emit = reasoning_buf.add(reasoning_text)
                if to_emit and emit_sse:
                    yield await emit_sse({
                        "type": "reasoning",
                        "content": to_emit,
                    })
        elif chunk.get("type") == "content_delta":
            # Flush any remaining reasoning before content starts
            flushed = reasoning_buf.flush()
            if flushed and emit_sse:
                yield await emit_sse({
                    "type": "reasoning",
                    "content": flushed,
                })
            content_text = chunk.get("text", "")
            if content_text and emit_sse and not suppress_content:
                yield await emit_sse({
                    "type": "content",
                    "content": content_text,
                })
        elif chunk.get("type") == "done":
            # Flush remaining reasoning buffer
            flushed = reasoning_buf.flush()
            if flushed and emit_sse:
                yield await emit_sse({
                    "type": "reasoning",
                    "content": flushed,
                })
            response_content = chunk.get("content")
            response_tool_calls = chunk.get("tool_calls", [])
            finish_reason = chunk.get("finish_reason")
            usage = chunk.get("usage", {})
    
    response = LLMResponse(
        content=response_content,
        finish_reason=finish_reason,
        usage=usage,
    )
    for tc in response_tool_calls:
        try:
            args = tc.get("function", {}).get("arguments", "{}")
            if isinstance(args, str):
                args = json.loads(args) if args else {}
            response.tool_calls.append(ToolCallData(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                arguments=args,
            ))
        except Exception as e:
            logger.error(f"Error parsing tool call: {e}")
    
    # Yield sentinel so caller can consume final LLMResponse (see StreamFinalResponse)
    yield StreamFinalResponse(response=response)
