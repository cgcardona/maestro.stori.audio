"""
Orchestration and request handlers for Composer (Cursor-of-DAWs).

This module contains the main orchestrate() flow and the three handlers
(REASONING, COMPOSING, EDITING). The API route layer imports orchestrate
and UsageTracker from here so the route file stays thin.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from app.config import settings
from app.core.entity_context import build_entity_context_for_llm
from app.core.executor import execute_plan_streaming
from app.core.expansion import ToolCall
from app.core.intent import (
    Intent,
    IntentResult,
    SSEState,
    get_intent_result_with_llm,
)
from app.core.intent_config import _PRIMITIVES_REGION, _PRIMITIVES_TRACK
from app.core.llm_client import (
    LLMClient,
    LLMResponse,
    ToolCallData,
    enforce_single_tool,
)
from app.core.pipeline import run_pipeline
from app.core.prompts import editing_prompt, system_prompt_base
from app.core.sse_utils import sanitize_reasoning, sse_event
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


async def orchestrate(
    prompt: str,
    project_context: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
    usage_tracker: Optional[UsageTracker] = None,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    conversation_history: Optional[list[dict[str, Any]]] = None,
    execution_mode: str = "apply",
) -> AsyncIterator[str]:
    """
    Main orchestration using Cursor-of-DAWs architecture.
    
    Flow:
    1. Create trace context for request
    2. Intent Router classifies prompt → route + allowlist
    3. LLM fallback for UNKNOWN intents
    4. Route to THINKING / EDITING / COMPOSING
    5. Execute with strict tool gating + entity validation
    
    Args:
        execution_mode: "apply" for immediate mutation, "variation" for proposal mode
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
    
    # Get or create StateStore for this conversation
    store = get_or_create_store(
        conversation_id=conversation_id or "default",
        project_id=project_context.get("projectId"),
    )
    store.sync_from_client(project_context)
    
    try:
        with trace_span(trace, "orchestrate", {"prompt_length": len(prompt)}):
            
            # =================================================================
            # Step 1: Intent Classification
            # =================================================================
            
            with trace_span(trace, "intent_classification"):
                route = await get_intent_result_with_llm(prompt, project_context, llm, conversation_history)
                
                # Force execution_mode to "apply" for EDITING state
                # Structural operations (add/delete tracks, regions, etc.) should execute directly
                # NOT go through variation mode which is for transformative MIDI edits
                if route.sse_state == SSEState.EDITING:
                    execution_mode = "apply"
                    logger.info(f"Intent {route.intent.value} → EDITING state, forcing execution_mode='apply'")
                
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
                    usage_tracker, conversation_id, execution_mode
                ):
                    yield event
                return
            
            # =================================================================
            # Step 4: Handle EDITING (LLM tool calls with allowlist)
            # =================================================================
            
            async for event in _handle_editing(
                prompt, project_context, route, llm, store, trace,
                usage_tracker, conversation_history, execution_mode
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
            messages.append({
                "role": "system",
                "content": f"Project state:\n{json.dumps(project_context, indent=2)}"
            })
        
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
            async for chunk in llm.chat_completion_stream(
                messages=messages,
                tools=[],
                tool_choice="none",
            ):
                if chunk.get("type") == "reasoning_delta":
                    # Chain of Thought reasoning (extended reasoning from OpenRouter)
                    reasoning_text = chunk.get("text", "")
                    if reasoning_text:
                        # Sanitize reasoning to remove internal implementation details
                        sanitized = sanitize_reasoning(reasoning_text)
                        if sanitized:  # Only emit if there's content after sanitization
                            yield await sse_event({
                                "type": "reasoning",
                                "content": sanitized,
                            })
                elif chunk.get("type") == "content_delta":
                    # User-facing response
                    content_text = chunk.get("text", "")
                    if content_text:
                        response_text += content_text
                        yield await sse_event({"type": "content", "content": content_text})
                elif chunk.get("type") == "done":
                    response = LLMResponse(
                        content=response_text or chunk.get("content"),
                        usage=chunk.get("usage", {})
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
    execution_mode: str,
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
        trace, usage_tracker, [], execution_mode
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
    execution_mode: str = "apply",
) -> AsyncIterator[str]:
    """Handle COMPOSING state - generate music via planner.
    
    Args:
        execution_mode: "apply" for immediate mutation, "variation" for proposal mode
    """
    status_msg = "Composing..." if execution_mode == "apply" else "Generating variation..."
    yield await sse_event({"type": "status", "message": status_msg})
    
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
        if execution_mode == "variation":
            with trace_span(trace, "variation_generation", {"steps": len(output.plan.tool_calls)}):
                from app.core.executor import execute_plan_variation
                
                variation = await execute_plan_variation(
                    tool_calls=output.plan.tool_calls,
                    project_state=project_context,
                    intent=prompt,
                    conversation_id=conversation_id,
                    explanation=output.plan.llm_response_text,
                )
                
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
                
                # Emit done event (per spec)
                yield await sse_event({
                    "type": "done",
                    "variation_id": variation.variation_id,
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
            return
        
        # =====================================================================
        # Apply Mode: Execute with transactions (existing behavior)
        # =====================================================================
        with trace_span(trace, "plan_execution", {"steps": len(output.plan.tool_calls)}):
            async for event in execute_plan_streaming(
                output.plan.tool_calls,
                project_context,
                conversation_id=conversation_id,
                store=store,
            ):
                if event.get("type") == "tool_call":
                    yield await sse_event(event)
                elif event.get("type") == "plan_progress":
                    yield await sse_event({
                        "type": "progress",
                        "step": event.get("step"),
                        "total": event.get("total"),
                        "tool": event.get("tool"),
                    })
                elif event.get("type") == "tool_error":
                    yield await sse_event(event)
                elif event.get("type") == "plan_complete":
                    yield await sse_event({
                        "type": "complete",
                        "success": event.get("success", True),
                        "tool_calls": [],
                        "failed_tools": event.get("failed_tools", []),
                        "state_version": event.get("state_version"),
                        "trace_id": trace.trace_id,
                    })
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
                trace, usage_tracker, execution_mode
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
) -> AsyncIterator[str]:
    """Handle EDITING state - LLM tool calls with allowlist + validation.
    
    Args:
        execution_mode: "apply" for immediate mutation, "variation" for proposal mode
    """
    status_msg = "Processing..." if execution_mode == "apply" else "Generating variation..."
    yield await sse_event({"type": "status", "message": status_msg})
    
    required_single = bool(route.force_stop_after and route.tool_choice == "required")
    sys_prompt = system_prompt_base() + "\n" + editing_prompt(required_single)
    
    # Build allowed tools only (Cursor-style action space shaping)
    allowed_tools = [t for t in ALL_TOOLS if t["function"]["name"] in route.allowed_tool_names]
    
    messages = [{"role": "system", "content": sys_prompt}]
    messages.append({"role": "system", "content": build_entity_context_for_llm(store)})
    
    if project_context:
        messages.append({
            "role": "system",
            "content": f"Project state:\n{json.dumps(project_context, indent=2)}"
        })
    
    if conversation_history:
        messages.extend(conversation_history)
    
    messages.append({"role": "user", "content": prompt})
    
    tool_calls_collected = []
    iteration = 0
    
    while iteration < settings.orchestration_max_iterations:
        iteration += 1
        
        with trace_span(trace, f"llm_iteration_{iteration}"):
            start_time = time.time()
            
            # Use streaming for reasoning models
            if llm.supports_reasoning():
                response = None
                async for item in _stream_llm_response(
                    llm, messages, allowed_tools, route.tool_choice,
                    trace, lambda data: sse_event(data),
                ):
                    # Check if this is the final response marker
                    if isinstance(item, StreamFinalResponse):
                        response = item.response
                    else:
                        # It's an SSE event string, yield it
                        yield item
            else:
                response = await llm.chat_completion(
                    messages=messages,
                    tools=allowed_tools,
                    tool_choice=route.tool_choice,
                    temperature=settings.orchestration_temperature,
                )
            
            duration_ms = (time.time() - start_time) * 1000
            
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
        if route.force_stop_after:
            response = enforce_single_tool(response)
        
        # No more tool calls → done
        if not response.has_tool_calls:
            if response.content:
                yield await sse_event({"type": "content", "content": response.content})
            break
        
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
                track_id = enriched_params.get("trackId")
                region_name = enriched_params.get("name", "Region")
                
                # CRITICAL: Always generate new UUID for entity-creating tools
                # LLMs sometimes hallucinate duplicate UUIDs, causing frontend crashes
                if "regionId" in enriched_params:
                    logger.warning(
                        f"⚠️ LLM provided regionId '{enriched_params['regionId']}' for NEW region '{region_name}'. "
                        f"Ignoring and generating fresh UUID to prevent duplicates."
                    )
                
                # Always generate fresh UUID via StateStore
                if track_id:
                    try:
                        region_id = store.create_region(
                            region_name, track_id,
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
            
            # Add to messages
            messages.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(enriched_params)}
                }]
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({"status": "success"}),
            })
        
        # Force stop after first tool execution
        if route.force_stop_after and tool_calls_collected:
            logger.info(f"[{trace.trace_id[:8]}] ✅ Force stop after {len(tool_calls_collected)} tool(s)")
            break
        
        if response.finish_reason == "stop":
            break
    
    # =========================================================================
    # Variation Mode: Compute and emit variation per spec (meta/phrase/done)
    # =========================================================================
    if execution_mode == "variation" and tool_calls_collected:
        from app.core.executor import execute_plan_variation
        from app.core.expansion import ToolCall
        
        # Convert collected tool calls to ToolCall objects
        tool_call_objs = [
            ToolCall(name=tc["tool"], params=tc["params"])
            for tc in tool_calls_collected
        ]
        
        variation = await execute_plan_variation(
            tool_calls=tool_call_objs,
            project_state=project_context,
            intent=prompt,
            conversation_id=None,  # EDITING doesn't use conversation_id here
            explanation=None,
        )
        
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
        
        # Emit done event (per spec)
        yield await sse_event({
            "type": "done",
            "variation_id": variation.variation_id,
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
):
    """Stream LLM response with thinking deltas. Yields SSE events and final response."""
    response_content = None
    response_tool_calls = []
    usage = {}
    
    async for chunk in llm.chat_completion_stream(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=settings.orchestration_temperature,
    ):
        if chunk.get("type") == "reasoning_delta":
            # Emit reasoning blocks to client for real-time CoT display
            reasoning_text = chunk.get("text", "")
            if reasoning_text and emit_sse:
                # Sanitize reasoning to remove internal implementation details
                sanitized = sanitize_reasoning(reasoning_text)
                if sanitized:  # Only emit if there's content after sanitization
                    yield await emit_sse({
                        "type": "reasoning",
                        "content": sanitized,
                    })
        elif chunk.get("type") == "content_delta":
            # Emit content blocks to client for user-facing response
            content_text = chunk.get("text", "")
            if content_text and emit_sse:
                yield await emit_sse({
                    "type": "content",
                    "content": content_text,
                })
        elif chunk.get("type") == "done":
            response_content = chunk.get("content")
            response_tool_calls = chunk.get("tool_calls", [])
            usage = chunk.get("usage", {})
    
    response = LLMResponse(content=response_content, usage=usage)
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
