"""COMPOSING handler — generate music via planner → executor → variation."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as _uuid_mod
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from app.core.entity_context import format_project_context
from app.core.llm_client import LLMClient
from app.core.planner import build_execution_plan_stream, ExecutionPlan
from app.core.prompt_parser import ParsedPrompt
from app.core.sse_utils import sse_event
from app.core.state_store import StateStore
from app.core.tracing import trace_span
from app.core.maestro_editing.tool_execution import phase_for_tool
from app.core.maestro_helpers import (
    UsageTracker,
    _context_usage_fields,
    _enrich_params_with_track_context,
    _human_label_for_tool,
)
from app.core.maestro_plan_tracker import _PlanTracker
from app.core.maestro_composing.storage import _store_variation
from app.core.maestro_composing.fallback import _retry_composing_as_editing

logger = logging.getLogger(__name__)


async def _handle_composing(
    prompt: str,
    project_context: dict[str, Any],
    route: Any,
    llm: LLMClient,
    store: StateStore,
    trace: Any,
    usage_tracker: Optional[UsageTracker],
    conversation_id: Optional[str],
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """Handle COMPOSING state - generate music via planner.

    All COMPOSING intents produce a Variation for human review.
    The planner generates a tool-call plan, the executor simulates it
    in variation mode, and the result is streamed as meta/phrase/done events.

    Phase 1 (Unified SSE UX): reasoning events are streamed during the
    planner's LLM call so the user sees the agent thinking — same UX as
    EDITING mode.
    """
    yield await sse_event({"type": "status", "message": "Thinking..."})

    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: Optional[ParsedPrompt] = (
        _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    )

    # ── Streaming planner: yields reasoning SSE events, then the plan ──
    plan: Optional[ExecutionPlan] = None
    with trace_span(trace, "planner"):
        async for item in build_execution_plan_stream(
            user_prompt=prompt,
            project_state=project_context,
            route=route,
            llm=llm,
            parsed=parsed,
            usage_tracker=usage_tracker,
            emit_sse=lambda data: sse_event(data),
        ):
            if isinstance(item, ExecutionPlan):
                plan = item
            else:
                yield item

    if plan and plan.tool_calls:
        composing_plan_tracker = _PlanTracker()
        composing_plan_tracker.build(
            plan.tool_calls, prompt, project_context,
            is_composition=True, store=store,
        )
        if len(composing_plan_tracker.steps) >= 1:
            yield await sse_event(composing_plan_tracker.to_plan_event())

        # PROPOSAL PHASE
        for tc in plan.tool_calls:
            yield await sse_event({
                "type": "toolCall",
                "id": "",
                "name": tc.name,
                "label": _human_label_for_tool(tc.name, tc.params),
                "phase": phase_for_tool(tc.name),
                "params": tc.params,
                "proposal": True,
            })

        # EXECUTION PHASE
        try:
            with trace_span(trace, "variation_generation", {"steps": len(plan.tool_calls)}):
                from app.core.executor import execute_plan_variation

                _event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                _active_step_by_track: dict[str, str] = {}

                async def _on_pre_tool(
                    tool_name: str, params: dict[str, Any],
                ) -> None:
                    """Execution phase: planStepUpdate:active + toolStart."""
                    step = composing_plan_tracker.find_step_for_tool(
                        tool_name, params, store,
                    )
                    if step and step.status != "active":
                        track_key = (step.track_name or "").lower()
                        prev_step_id = _active_step_by_track.get(track_key)
                        if prev_step_id and prev_step_id != step.step_id:
                            await _event_queue.put(
                                composing_plan_tracker.complete_step_by_id(prev_step_id)
                            )
                        await _event_queue.put(
                            composing_plan_tracker.activate_step(step.step_id)
                        )
                        if track_key:
                            _active_step_by_track[track_key] = step.step_id

                    label = _human_label_for_tool(tool_name, params)
                    await _event_queue.put({
                        "type": "toolStart",
                        "name": tool_name,
                        "label": label,
                        "phase": phase_for_tool(tool_name),
                    })

                async def _on_post_tool(
                    tool_name: str, resolved_params: dict[str, Any],
                ) -> None:
                    """Execution phase: toolCall with real UUID after success.

                    Generator tools (stori_generate_midi etc.) are backend-internal;
                    their output reaches the frontend via phrase events, not toolCall.
                    """
                    from app.core.maestro_plan_tracker.constants import _GENERATOR_TOOL_NAMES
                    if tool_name in _GENERATOR_TOOL_NAMES:
                        return
                    call_id = str(_uuid_mod.uuid4())
                    emit_params = _enrich_params_with_track_context(resolved_params, store)
                    label = _human_label_for_tool(tool_name, emit_params)
                    await _event_queue.put({
                        "type": "toolCall",
                        "id": call_id,
                        "name": tool_name,
                        "label": label,
                        "phase": phase_for_tool(tool_name),
                        "params": emit_params,
                        "proposal": False,
                    })

                _VARIATION_TIMEOUT = 300
                task = asyncio.create_task(
                    execute_plan_variation(
                        tool_calls=plan.tool_calls,
                        project_state=project_context,
                        intent=prompt,
                        conversation_id=conversation_id,
                        explanation=plan.llm_response_text,
                        pre_tool_callback=_on_pre_tool,
                        post_tool_callback=_on_post_tool,
                        quality_preset=quality_preset,
                    )
                )

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
                            event_data = await asyncio.wait_for(
                                _event_queue.get(), timeout=0.05,
                            )
                            yield await sse_event(event_data)
                        except asyncio.TimeoutError:
                            if task.done():
                                break
                        await asyncio.sleep(0)

                    while not _event_queue.empty():
                        yield await sse_event(await _event_queue.get())

                    variation = await task

                    for final_evt in composing_plan_tracker.complete_all_active_steps():
                        yield await sse_event(final_evt)

                    for skip_evt in composing_plan_tracker.finalize_pending_as_skipped():
                        yield await sse_event(skip_evt)

                except asyncio.TimeoutError:
                    logger.error(
                        f"[{trace.trace_id[:8]}] Variation generation timed out "
                        f"after {_VARIATION_TIMEOUT}s"
                    )
                    _fail_variation_id = str(_uuid_mod.uuid4())
                    yield await sse_event({
                        "type": "error",
                        "message": f"Generation timed out after {_VARIATION_TIMEOUT}s",
                        "traceId": trace.trace_id,
                    })
                    yield await sse_event({
                        "type": "done",
                        "variationId": _fail_variation_id,
                        "phraseCount": 0,
                        "status": "failed",
                    })
                    yield await sse_event({
                        "type": "complete",
                        "success": False,
                        "error": "timeout",
                        "traceId": trace.trace_id,
                        **_context_usage_fields(usage_tracker, llm.model),
                    })
                    return

                if len(variation.phrases) == 0:
                    logger.error(
                        f"[{trace.trace_id[:8]}] COMPOSING produced 0 phrases "
                        f"despite {len(plan.tool_calls)} tool calls — "
                        f"this indicates a generation or entity resolution failure. "
                        f"Proposed notes captured: {sum(len(n) for n in getattr(variation, '_proposed_notes', {}).values()) if hasattr(variation, '_proposed_notes') else 'N/A'}"
                    )

                _region_metadata: dict[str, dict] = {}
                for _re in store.registry.list_regions():
                    _rmeta: dict[str, Any] = {}
                    if _re.metadata:
                        _rmeta["startBeat"] = _re.metadata.get("startBeat")
                        _rmeta["durationBeats"] = _re.metadata.get("durationBeats")
                    _rmeta["name"] = _re.name
                    _region_metadata[_re.id] = _rmeta

                await _store_variation(
                    variation, project_context,
                    base_state_id=store.get_state_id(),
                    conversation_id=store.conversation_id,
                    region_metadata=_region_metadata,
                )

                note_counts = variation.note_counts
                yield await sse_event({
                    "type": "meta",
                    "variationId": variation.variation_id,
                    "baseStateId": store.get_state_id(),
                    "intent": variation.intent,
                    "aiExplanation": variation.ai_explanation,
                    "affectedTracks": variation.affected_tracks,
                    "affectedRegions": variation.affected_regions,
                    "noteCounts": note_counts,
                })

                for phrase in variation.phrases:
                    yield await sse_event({
                        "type": "phrase",
                        "phraseId": phrase.phrase_id,
                        "trackId": phrase.track_id,
                        "regionId": phrase.region_id,
                        "startBeat": phrase.start_beat,
                        "endBeat": phrase.end_beat,
                        "label": phrase.label,
                        "tags": phrase.tags,
                        "explanation": phrase.explanation,
                        "noteChanges": [nc.model_dump(by_alias=True) for nc in phrase.note_changes],
                        "controllerChanges": phrase.controller_changes,
                    })

                yield await sse_event({
                    "type": "done",
                    "variationId": variation.variation_id,
                    "phraseCount": len(variation.phrases),
                })

                yield await sse_event({
                    "type": "complete",
                    "success": True,
                    "variationId": variation.variation_id,
                    "totalChanges": variation.total_changes,
                    "phraseCount": len(variation.phrases),
                    "traceId": trace.trace_id,
                    **_context_usage_fields(usage_tracker, llm.model),
                })

        except BaseException as e:
            logger.exception(
                f"[{trace.trace_id[:8]}] Variation generation failed: {e}"
            )
            _fail_variation_id = str(_uuid_mod.uuid4())
            yield await sse_event({
                "type": "error",
                "message": f"Generation failed: {e}",
                "traceId": trace.trace_id,
            })
            yield await sse_event({
                "type": "done",
                "variationId": _fail_variation_id,
                "phraseCount": 0,
                "status": "failed",
            })
            yield await sse_event({
                "type": "complete",
                "success": False,
                "error": str(e),
                "traceId": trace.trace_id,
                **_context_usage_fields(usage_tracker, llm.model),
            })
        return
    else:
        response_text = plan.llm_response_text if plan else None

        looks_like_function_calls = (
            response_text and
            ("stori_" in response_text or
             "add_midi_track(" in response_text or
             "add_notes(" in response_text or
             "add_region(" in response_text)
        )

        if looks_like_function_calls:
            async for event in _retry_composing_as_editing(
                prompt, project_context, route, llm, store,
                trace, usage_tracker,
                quality_preset=quality_preset,
            ):
                yield event
            return

        if response_text:
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
            "toolCalls": [],
            "traceId": trace.trace_id,
            **_context_usage_fields(usage_tracker, llm.model),
        })


# ---------------------------------------------------------------------------
# Agent Teams + Variation capture
# ---------------------------------------------------------------------------


async def _handle_composing_with_agent_teams(
    prompt: str,
    project_context: dict[str, Any],
    parsed: ParsedPrompt,
    route: Any,
    llm: LLMClient,
    store: StateStore,
    trace: Any,
    usage_tracker: Optional[UsageTracker],
    is_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """Agent Teams composition with Variation capture.

    Combines streaming per-agent reasoning from Agent Teams with
    Muse's Variation system for human-reviewable commit/discard.

    ``Mode: compose`` is the directive — any compose request with parsed
    roles (1+) uses this path.  The instrument count determines
    parallelism (1 agent vs N parallel agents), not the execution path.

    Flow:
      1. Snapshot base notes from project state
      2. Run Agent Teams (streams reasoning, tool events, heartbeats)
      3. Collect generated notes from StateStore
      4. Compute Variation via VariationService
      5. Store variation for commit/discard
      6. Emit meta/phrase/done/complete events
    """
    from app.core.maestro_agent_teams import _handle_composition_agent_team
    from app.core.maestro_editing import _create_editing_composition_route
    from app.core.executor.snapshots import capture_base_snapshot, capture_proposed_snapshot
    from app.models.variation import Variation
    from app.services.variation import get_variation_service

    # ── 1. Snapshot base notes before Agent Teams runs ──
    _base_snapshot = capture_base_snapshot(store)
    _base_notes: dict[str, list[dict]] = {}
    _track_regions: dict[str, str] = {}
    for track in project_context.get("tracks", []):
        track_id = track.get("id", "")
        for region in track.get("regions", []):
            rid = region.get("id", "")
            notes = region.get("notes", []) or _base_snapshot.notes.get(rid, [])
            if rid and notes:
                _base_notes[rid] = notes
                _track_regions[rid] = track_id

    # Agent Teams needs an editing-level tool allowlist (tempo, key, tracks,
    # regions, generators, effects).  The COMPOSING route may have a narrower
    # set, so we create an editing composition route for the coordinator.
    _at_route = _create_editing_composition_route(route)

    # ── 2. Run Agent Teams — intercept the ``complete`` event ──
    _agent_complete: dict[str, Any] | None = None
    _SSE_PREFIX = "data: "

    async for event_str in _handle_composition_agent_team(
        prompt, project_context, parsed, _at_route, llm, store,
        trace, usage_tracker, is_cancelled=is_cancelled,
    ):
        if event_str.startswith(_SSE_PREFIX):
            try:
                _payload = json.loads(event_str[len(_SSE_PREFIX):].rstrip())
                if _payload.get("type") == "complete":
                    _agent_complete = _payload
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
        yield event_str

    # ── 3. Collect proposed notes via snapshot (never read live StateStore) ──
    _proposed_snapshot = capture_proposed_snapshot(store)
    _proposed_notes: dict[str, list[dict]] = {}
    _region_start_beats: dict[str, float] = {}

    for region_entity in store.registry.list_regions():
        rid = region_entity.id
        notes = _proposed_snapshot.notes.get(rid, [])
        if notes:
            _proposed_notes[rid] = notes
            _track_regions[rid] = region_entity.parent_id or ""
            if rid not in _base_notes:
                _base_notes[rid] = []
            if region_entity.metadata:
                _region_start_beats[rid] = float(
                    region_entity.metadata.get("startBeat", 0)
                )

    # ── 4. Compute Variation ──
    _vs = get_variation_service()

    if _proposed_notes:
        if len(_proposed_notes) > 1:
            variation = _vs.compute_multi_region_variation(
                base_regions=_base_notes,
                proposed_regions=_proposed_notes,
                track_regions=_track_regions,
                intent=prompt,
                explanation=(
                    f"Agent Teams composition: "
                    f"{len(parsed.roles)} instrument(s)"
                ),
                region_start_beats=_region_start_beats,
            )
        else:
            _rid = next(iter(_proposed_notes))
            _tid = _track_regions.get(_rid, "")
            variation = _vs.compute_variation(
                base_notes=_base_notes.get(_rid, []),
                proposed_notes=_proposed_notes[_rid],
                region_id=_rid,
                track_id=_tid,
                intent=prompt,
                explanation=(
                    f"Agent Teams composition: "
                    f"{len(parsed.roles)} instrument(s)"
                ),
                region_start_beat=_region_start_beats.get(_rid, 0.0),
            )
    else:
        variation = Variation(
            variation_id=str(_uuid_mod.uuid4()),
            intent=prompt,
            ai_explanation="No notes generated",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 0.0),
            phrases=[],
        )

    logger.info(
        f"[{trace.trace_id[:8]}] 🎼 Variation computed from Agent Teams: "
        f"{variation.total_changes} changes in "
        f"{len(variation.phrases)} phrases"
    )

    # ── 5. Store variation for commit/discard ──
    _at_region_metadata: dict[str, dict] = {}
    for _re in store.registry.list_regions():
        _rmeta_at: dict[str, Any] = {}
        if _re.metadata:
            _rmeta_at["startBeat"] = _re.metadata.get("startBeat")
            _rmeta_at["durationBeats"] = _re.metadata.get("durationBeats")
        _rmeta_at["name"] = _re.name
        _at_region_metadata[_re.id] = _rmeta_at

    await _store_variation(
        variation, project_context,
        base_state_id=store.get_state_id(),
        conversation_id=store.conversation_id,
        region_metadata=_at_region_metadata,
    )

    # ── 6. Emit variation events ──
    yield await sse_event({
        "type": "meta",
        "variationId": variation.variation_id,
        "baseStateId": store.get_state_id(),
        "intent": variation.intent,
        "aiExplanation": variation.ai_explanation,
        "affectedTracks": variation.affected_tracks,
        "affectedRegions": variation.affected_regions,
        "noteCounts": variation.note_counts,
    })

    for phrase in variation.phrases:
        yield await sse_event({
            "type": "phrase",
            "phraseId": phrase.phrase_id,
            "trackId": phrase.track_id,
            "regionId": phrase.region_id,
            "startBeat": phrase.start_beat,
            "endBeat": phrase.end_beat,
            "label": phrase.label,
            "tags": phrase.tags,
            "explanation": phrase.explanation,
            "noteChanges": [
                nc.model_dump(by_alias=True)
                for nc in phrase.note_changes
            ],
            "controllerChanges": phrase.controller_changes,
        })

    yield await sse_event({
        "type": "done",
        "variationId": variation.variation_id,
        "phraseCount": len(variation.phrases),
    })

    # Merge Agent Teams success/warnings with variation metadata
    _success = (
        _agent_complete.get("success", True)
        if _agent_complete else True
    )
    _complete: dict[str, Any] = {
        "type": "complete",
        "success": _success,
        "variationId": variation.variation_id,
        "totalChanges": variation.total_changes,
        "phraseCount": len(variation.phrases),
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    }
    if _agent_complete:
        if "warnings" in _agent_complete:
            _complete["warnings"] = _agent_complete["warnings"]
        if "stateVersion" in _agent_complete:
            _complete["stateVersion"] = _agent_complete["stateVersion"]

    yield await sse_event(_complete)
