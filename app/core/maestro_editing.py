"""EDITING handler and tool execution core for Maestro.

Handles the EDITING SSE state: LLM tool calls with allowlist + validation,
multi-iteration composition continuation, and variation proposal mode.
Also provides routing helpers used by the orchestrator.
"""

from __future__ import annotations

import json
import logging
import time
import uuid as _uuid_mod
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, cast

from app.config import settings
from app.core.entity_context import build_entity_context_for_llm, format_project_context
from app.core.expansion import ToolCall
from app.core.gm_instruments import DRUM_ICON, icon_for_gm_program
from app.core.intent import Intent, IntentResult, SSEState
from app.core.intent_config import (
    _PRIMITIVES_FX,
    _PRIMITIVES_MIXING,
    _PRIMITIVES_REGION,
    _PRIMITIVES_TRACK,
)
from app.core.llm_client import LLMClient, LLMResponse, enforce_single_tool
from app.core.prompt_parser import ParsedPrompt
from app.core.prompts import (
    editing_composition_prompt,
    editing_prompt,
    resolve_position,
    sequential_context,
    structured_prompt_context,
    system_prompt_base,
    wrap_user_request,
)
from app.core.sse_utils import sse_event, strip_tool_echoes
from app.core.state_store import StateStore
from app.core.tool_validation import VALID_SF_SYMBOL_ICONS, validate_tool_call
from app.core.tools import ALL_TOOLS
from app.core.tracing import (
    log_llm_call,
    log_tool_call,
    log_validation_error,
    trace_span,
)
from app.core.maestro_helpers import (
    UsageTracker,
    StreamFinalResponse,
    _context_usage_fields,
    _enrich_params_with_track_context,
    _entity_manifest,
    _build_tool_result,
    _human_label_for_tool,
    _resolve_variable_refs,
    _stream_llm_response,
)
from app.core.maestro_plan_tracker import (
    _PlanTracker,
    _ToolCallOutcome,
    _build_step_result,
    _TRACK_CREATION_NAMES,
    _EFFECT_TOOL_NAMES,
    _GENERATOR_TOOL_NAMES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing helpers (used by orchestrate())
# ---------------------------------------------------------------------------

def _project_needs_structure(project_context: dict[str, Any]) -> bool:
    """Check if the project is empty and needs structural creation.

    Returns True when the project has no tracks, meaning composition
    requests should use EDITING mode (tool_call events) rather than
    COMPOSING mode (variation review) — you can't diff against nothing.
    """
    tracks = project_context.get("tracks", [])
    return len(tracks) == 0


def _is_additive_composition(
    parsed: Optional["ParsedPrompt"],
    project_context: dict[str, Any],
) -> bool:
    """Detect if a composition request creates a new section (EDITING, not COMPOSING).

    Returns True when the request appends new content (Position: after/last)
    or introduces roles that don't map to existing tracks. In these cases
    EDITING mode is preferred because the content is additive — there is
    nothing to diff against, and COMPOSING with phraseCount: 0 is always a bug.

    STORI PROMPTs with 2+ roles always return True: they spawn Agent Teams
    regardless of whether the named tracks already exist, because the prompt
    always places new timeline content (new regions at a later beat position).
    Routing confidence and existing-track state are both irrelevant here.
    """
    if not parsed:
        return False

    # A structured STORI PROMPT (2+ roles) always runs Agent Teams — even when
    # all tracks exist. The prompt creates new regions at later beat positions,
    # so it is always additive. This prevents the composing/variation pipeline
    # from intercepting STORI PROMPTs and producing clarification questions.
    if parsed.roles and len(parsed.roles) >= 2:
        return True

    if parsed.position and parsed.position.kind in ("after", "last"):
        return True

    existing_names = {
        t.get("name", "").lower()
        for t in project_context.get("tracks", [])
        if t.get("name")
    }
    if parsed.roles:
        for role in parsed.roles:
            if role.lower() not in existing_names:
                return True

    return False


def _get_incomplete_tracks(
    store: "StateStore",
    tool_calls_collected: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return names of tracks that are missing regions or notes.

    Checks two conditions:
    1. Track has no regions at all
    2. Track has regions but none of them have notes — either from the current
       iteration's tool calls OR persisted in the StateStore from a prior
       iteration. Checking both sources prevents false "still needs notes"
       continuations that cause the model to clear and re-add valid content.

    Used by the composition continuation loop to detect premature LLM stops.
    """
    regions_with_notes_this_iter: set[str] = set()
    if tool_calls_collected:
        for tc in tool_calls_collected:
            if tc["tool"] == "stori_add_notes":
                rid = tc["params"].get("regionId")
                if rid:
                    regions_with_notes_this_iter.add(rid)

    incomplete: list[str] = []
    for track in store.registry.list_tracks():
        regions = store.registry.get_track_regions(track.id)
        if not regions:
            incomplete.append(track.name)
        elif not any(
            r.id in regions_with_notes_this_iter or bool(store.get_region_notes(r.id))
            for r in regions
        ):
            incomplete.append(track.name)
    return incomplete


def _get_missing_expressive_steps(
    parsed: Optional["ParsedPrompt"],
    tool_calls_collected: list[dict[str, Any]],
) -> list[str]:
    """Return human-readable descriptions of expressive steps not yet executed.

    Checks Effects, MidiExpressiveness, and Automation blocks from the parsed
    STORI PROMPT against the tool calls already made this session. Returns an
    empty list when everything has been called (or when the parsed prompt has
    no expressive blocks).
    """
    if parsed is None:
        return []

    # Keys are lowercased by the parser (prompt_parser.py line 177)
    extensions: dict[str, Any] = parsed.extensions or {}
    called_tools = {tc["tool"] for tc in tool_calls_collected}

    missing: list[str] = []

    if extensions.get("effects") and "stori_add_insert_effect" not in called_tools:
        missing.append(
            "Effects block present but stori_add_insert_effect was never called. "
            "Call stori_add_insert_effect for each effects entry (compressor, reverb, eq, etc.)."
        )

    me = extensions.get("midiexpressiveness") or {}
    if me.get("cc_curves") and "stori_add_midi_cc" not in called_tools:
        missing.append(
            "MidiExpressiveness.cc_curves present but stori_add_midi_cc was never called. "
            "Call stori_add_midi_cc for each cc_curves entry."
        )

    if me.get("sustain_pedal") and "stori_add_midi_cc" not in called_tools:
        missing.append(
            "MidiExpressiveness.sustain_pedal present but stori_add_midi_cc (CC 64) was never called. "
            "Call stori_add_midi_cc with cc=64 on the target region."
        )

    if me.get("pitch_bend") and "stori_add_pitch_bend" not in called_tools:
        missing.append(
            "MidiExpressiveness.pitch_bend present but stori_add_pitch_bend was never called. "
            "Call stori_add_pitch_bend with slide events on the target region."
        )

    if extensions.get("automation") and "stori_add_automation" not in called_tools:
        missing.append(
            "Automation block present but stori_add_automation was never called. "
            "Call stori_add_automation(trackId=..., parameter='Volume', points=[...]) "
            "for each lane. Use trackId (NOT 'target'). parameter must be a canonical "
            "string like 'Volume', 'Pan', 'Synth Cutoff', 'Expression (CC11)', etc."
        )

    effects_data = extensions.get("effects") or {}
    if isinstance(effects_data, dict):
        tracks_needing_reverb = [
            k for k, v in effects_data.items()
            if isinstance(v, dict) and "reverb" in v
        ]
        if len(tracks_needing_reverb) >= 2 and "stori_ensure_bus" not in called_tools:
            missing.append(
                f"Multiple tracks ({', '.join(tracks_needing_reverb)}) need reverb — "
                "use a shared Reverb bus: call stori_ensure_bus(name='Reverb') once, "
                "then stori_add_send(trackId=X, busId=$N.busId, levelDb=-6) for each track."
            )

    return missing


def _create_editing_composition_route(route: "IntentResult") -> "IntentResult":
    """Build an EDITING IntentResult for composition on empty projects.

    When the project has no tracks, composition requests should use EDITING
    mode so structural changes (tracks, regions, instruments, notes) are
    emitted as tool_call events for real-time frontend rendering.
    """
    all_composition_tools = (
        set(_PRIMITIVES_TRACK) | set(_PRIMITIVES_REGION)
        | set(_PRIMITIVES_FX) | set(_PRIMITIVES_MIXING)
        | {"stori_set_tempo", "stori_set_key"}
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


# ---------------------------------------------------------------------------
# Core tool execution
# ---------------------------------------------------------------------------

async def _apply_single_tool_call(
    tc_id: str,
    tc_name: str,
    resolved_args: dict[str, Any],
    allowed_tool_names: set[str],
    store: StateStore,
    trace: Any,
    add_notes_failures: dict[str, int],
    emit_sse: bool = True,
) -> _ToolCallOutcome:
    """Validate, enrich, persist, and return results for one tool call.

    Handles entity creation (UUIDs), note persistence, icon synthesis, and
    tool result building. Returns SSE events, LLM message objects, and
    enriched params without yielding — the caller decides whether to yield
    events directly (editing path) or put them into an asyncio.Queue
    (agent-team path).

    Args:
        tc_id: Tool call ID (from LLM response or synthetic UUID).
        tc_name: Tool name (e.g. ``"stori_add_midi_track"``).
        resolved_args: Params after ``$N.field`` variable ref resolution.
        allowed_tool_names: Tool allowlist for validation.
        store: StateStore for entity creation and result building.
        trace: Trace context for logging and spans.
        add_notes_failures: Mutable circuit-breaker counter (modified in-place).
        emit_sse: When ``False``, sse_events is empty (variation/proposal mode).
    """
    sse_events: list[dict[str, Any]] = []

    # ── Circuit breaker: stori_add_notes infinite-retry guard ──
    if tc_name == "stori_add_notes":
        cb_region_id = resolved_args.get("regionId", "__unknown__")
        cb_failures = add_notes_failures.get(cb_region_id, 0)
        if cb_failures >= 3:
            cb_error = (
                f"stori_add_notes: regionId '{cb_region_id}' has failed {cb_failures} times "
                f"without valid notes being added. Stop retrying with shorthand params. "
                f"Provide a real 'notes' array: "
                f"[{{\"pitch\": 60, \"startBeat\": 0, \"durationBeats\": 1, \"velocity\": 80}}, ...]"
            )
            logger.error(f"[{trace.trace_id[:8]}] 🔴 Circuit breaker: {cb_error}")
            if emit_sse:
                sse_events.append({"type": "toolError", "name": tc_name, "error": cb_error})
            msg_call: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(resolved_args)}}],
            }
            msg_result: dict[str, Any] = {
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps({"error": cb_error}),
            }
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={"error": cb_error},
                sse_events=sse_events,
                msg_call=msg_call,
                msg_result=msg_result,
                skipped=True,
            )

    # ── Validation ──
    with trace_span(trace, f"validate:{tc_name}"):
        validation = validate_tool_call(tc_name, resolved_args, allowed_tool_names, store.registry)

    if not validation.valid:
        log_validation_error(trace.trace_id, tc_name, [str(e) for e in validation.errors])
        if tc_name == "stori_add_notes":
            cb_region_id = resolved_args.get("regionId", "__unknown__")
            add_notes_failures[cb_region_id] = add_notes_failures.get(cb_region_id, 0) + 1
        if emit_sse:
            sse_events.append({
                "type": "toolError",
                "name": tc_name,
                "error": validation.error_message,
                "errors": [str(e) for e in validation.errors],
            })
        error_result = {"error": validation.error_message}
        msg_call = {
            "role": "assistant",
            "tool_calls": [{"id": tc_id, "type": "function",
                            "function": {"name": tc_name, "arguments": json.dumps(resolved_args)}}],
        }
        msg_result = {
            "role": "tool", "tool_call_id": tc_id,
            "content": json.dumps(error_result),
        }
        return _ToolCallOutcome(
            enriched_params=resolved_args,
            tool_result=error_result,
            sse_events=sse_events,
            msg_call=msg_call,
            msg_result=msg_result,
            skipped=True,
        )

    enriched_params = validation.resolved_params

    # ── Entity creation ──
    if tc_name == "stori_add_midi_track":
        track_name = enriched_params.get("name", "Track")
        instrument = enriched_params.get("instrument")
        gm_program = enriched_params.get("gmProgram")
        if "trackId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided trackId '{enriched_params['trackId']}' for NEW track '{track_name}'. "
                f"Ignoring and generating fresh UUID to prevent duplicates."
            )
        track_id = store.create_track(track_name)
        enriched_params["trackId"] = track_id
        logger.debug(f"🔑 Generated trackId: {track_id[:8]} for '{track_name}'")
        if gm_program is None:
            from app.core.gm_instruments import infer_gm_program_with_context
            inference = infer_gm_program_with_context(track_name=track_name, instrument=instrument)
            enriched_params["_gmInstrumentName"] = inference.instrument_name
            enriched_params["_isDrums"] = inference.is_drums
            logger.info(
                f"🎵 GM inference for '{track_name}': "
                f"program={inference.program}, instrument={inference.instrument_name}, "
                f"is_drums={inference.is_drums}"
            )
            if inference.needs_program_change:
                enriched_params["gmProgram"] = inference.program

    elif tc_name == "stori_add_midi_region":
        midi_region_track_id: Optional[str] = enriched_params.get("trackId")
        region_name: str = str(enriched_params.get("name", "Region"))
        if "regionId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided regionId '{enriched_params['regionId']}' for NEW region '{region_name}'. "
                f"Ignoring and generating fresh UUID to prevent duplicates."
            )
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
                error_result = {"success": False, "error": f"Failed to create region: {e}"}
                msg_call = {
                    "role": "assistant",
                    "tool_calls": [{"id": tc_id, "type": "function",
                                    "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
                }
                msg_result = {
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps(error_result),
                }
                return _ToolCallOutcome(
                    enriched_params=enriched_params,
                    tool_result=error_result,
                    sse_events=sse_events,
                    msg_call=msg_call,
                    msg_result=msg_result,
                    skipped=True,
                )
        else:
            logger.error(
                f"⚠️ stori_add_midi_region called without trackId for region '{region_name}'"
            )
            error_result = {
                "success": False,
                "error": (
                    f"Cannot create region '{region_name}' — no trackId provided. "
                    "Use $N.trackId to reference a track created in a prior tool call, "
                    "or use trackName for name-based resolution."
                ),
            }
            msg_call = {
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
            }
            msg_result = {
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps(error_result),
            }
            return _ToolCallOutcome(
                enriched_params=enriched_params,
                tool_result=error_result,
                sse_events=sse_events,
                msg_call=msg_call,
                msg_result=msg_result,
                skipped=True,
            )

    elif tc_name == "stori_duplicate_region":
        source_region_id: str = enriched_params.get("regionId", "")
        source_entity = store.registry.get_region(source_region_id)
        if source_entity:
            copy_name = f"{source_entity.name} (copy)"
            parent_track_id = source_entity.parent_id or ""
            try:
                new_region_id = store.create_region(
                    copy_name, parent_track_id,
                    metadata={"startBeat": enriched_params.get("startBeat", 0)},
                )
                enriched_params["newRegionId"] = new_region_id
                logger.debug(
                    f"🔑 Generated newRegionId: {new_region_id[:8]} "
                    f"for duplicate of '{source_entity.name}'"
                )
            except ValueError as e:
                logger.error(f"Failed to register duplicate region: {e}")

    elif tc_name == "stori_ensure_bus":
        bus_name = enriched_params.get("name", "Bus")
        if "busId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided busId '{enriched_params['busId']}' for bus '{bus_name}'. "
                f"Ignoring to prevent duplicates."
            )
        bus_id = store.get_or_create_bus(bus_name)
        enriched_params["busId"] = bus_id

    # ── SSE events (toolStart + toolCall) ──
    extra_tool_calls: list[dict[str, Any]] = []
    if emit_sse:
        emit_params = _enrich_params_with_track_context(enriched_params, store)
        sse_events.append({
            "type": "toolStart",
            "name": tc_name,
            "label": _human_label_for_tool(tc_name, emit_params),
        })
        sse_events.append({
            "type": "toolCall",
            "id": tc_id,
            "name": tc_name,
            "params": emit_params,
        })

    log_tool_call(trace.trace_id, tc_name, enriched_params, True)

    # ── Synthetic stori_set_track_icon after stori_add_midi_track ──
    if tc_name == "stori_add_midi_track" and emit_sse:
        _icon_track_id = enriched_params.get("trackId", "")
        _drum_kit = enriched_params.get("drumKitId")
        _is_drums = enriched_params.get("_isDrums", False)
        _gm_program = enriched_params.get("gmProgram")
        if _drum_kit or _is_drums:
            _track_icon: Optional[str] = DRUM_ICON
        elif _gm_program is not None:
            _track_icon = icon_for_gm_program(int(_gm_program))
        else:
            _track_icon = None
        if _track_icon and _track_icon not in VALID_SF_SYMBOL_ICONS:
            logger.warning(
                f"⚠️ Icon '{_track_icon}' not in curated SF Symbols list — "
                f"omitting stori_set_track_icon (frontend will assign default)"
            )
            _track_icon = None
        if _track_icon and _icon_track_id:
            _icon_params: dict[str, Any] = {"trackId": _icon_track_id, "icon": _track_icon}
            sse_events.append({
                "type": "toolStart",
                "name": "stori_set_track_icon",
                "label": f"Setting icon for {enriched_params.get('name', 'track')}",
            })
            sse_events.append({
                "type": "toolCall",
                "id": f"{tc_id}-icon",
                "name": "stori_set_track_icon",
                "params": _icon_params,
            })
            extra_tool_calls.append({"tool": "stori_set_track_icon", "params": _icon_params})
            logger.debug(
                f"🎨 Synthetic icon '{_track_icon}' → trackId {_icon_track_id[:8]} "
                f"({'drum kit' if (_drum_kit or _is_drums) else f'GM {_gm_program}'})"
            )

    # ── Note persistence ──
    if tc_name == "stori_add_notes":
        _notes = enriched_params.get("notes", [])
        _rid = enriched_params.get("regionId", "")
        if _rid and _notes:
            store.add_notes(_rid, _notes)
            logger.debug(
                f"📝 Persisted {len(_notes)} notes for region {_rid[:8]} in StateStore"
            )
        add_notes_failures.pop(enriched_params.get("regionId", "__unknown__"), None)

    # ── Message objects for LLM conversation history ──
    if tc_name == "stori_add_notes":
        notes = enriched_params.get("notes", [])
        summary_params = {k: v for k, v in enriched_params.items() if k != "notes"}
        summary_params["_noteCount"] = len(notes)
        if notes:
            starts = [n["startBeat"] for n in notes]
            summary_params["_beatRange"] = [min(starts), max(starts)]
        msg_arguments = json.dumps(summary_params)
    else:
        msg_arguments = json.dumps(enriched_params)

    msg_call = {
        "role": "assistant",
        "tool_calls": [{"id": tc_id, "type": "function",
                        "function": {"name": tc_name, "arguments": msg_arguments}}],
    }

    # ── Tool result ──
    tool_result = _build_tool_result(tc_name, enriched_params, store)
    msg_result = {
        "role": "tool", "tool_call_id": tc_id,
        "content": json.dumps(tool_result),
    }

    return _ToolCallOutcome(
        enriched_params=enriched_params,
        tool_result=tool_result,
        sse_events=sse_events,
        msg_call=msg_call,
        msg_result=msg_result,
        skipped=False,
        extra_tool_calls=extra_tool_calls,
    )


# ---------------------------------------------------------------------------
# EDITING handler
# ---------------------------------------------------------------------------

async def _handle_editing(
    prompt: str,
    project_context: dict[str, Any],
    route: Any,
    llm: LLMClient,
    store: StateStore,
    trace: Any,
    usage_tracker: Optional[UsageTracker],
    conversation_history: list[dict[str, Any]],
    execution_mode: str = "apply",
    is_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """Handle EDITING state - LLM tool calls with allowlist + validation.

    Args:
        execution_mode: "apply" for immediate mutation, "variation" for proposal mode
        is_cancelled: async callback returning True if the client disconnected
    """
    status_msg = "Processing..." if execution_mode == "apply" else "Generating variation..."
    yield await sse_event({"type": "status", "message": status_msg})

    if route.intent == Intent.GENERATE_MUSIC:
        sys_prompt = system_prompt_base() + "\n" + editing_composition_prompt()
    else:
        required_single = bool(route.force_stop_after and route.tool_choice == "required")
        sys_prompt = system_prompt_base() + "\n" + editing_prompt(required_single)

    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: Optional[ParsedPrompt] = _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    if parsed is not None:
        sys_prompt += structured_prompt_context(parsed)
        if parsed.position is not None:
            start_beat = resolve_position(parsed.position, project_context or {})
            sys_prompt += sequential_context(start_beat, parsed.section, pos=parsed.position)

    allowed_tools = [t for t in ALL_TOOLS if t["function"]["name"] in route.allowed_tool_names]

    messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]

    if project_context:
        messages.append({"role": "system", "content": format_project_context(project_context)})
    else:
        messages.append({"role": "system", "content": build_entity_context_for_llm(store)})

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": wrap_user_request(prompt)})

    is_composition = route.intent == Intent.GENERATE_MUSIC
    llm_max_tokens: Optional[int] = settings.composition_max_tokens if is_composition else None
    reasoning_fraction: Optional[float] = settings.composition_reasoning_fraction if is_composition else None

    tool_calls_collected: list[dict[str, Any]] = []
    plan_tracker: Optional[_PlanTracker] = None
    iteration = 0
    _add_notes_failures: dict[str, int] = {}
    max_iterations = (
        settings.composition_max_iterations if is_composition
        else settings.orchestration_max_iterations
    )

    if is_composition and parsed is not None and execution_mode == "apply":
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(parsed, prompt, project_context or {})
        yield await sse_event(plan_tracker.to_plan_event())

    while iteration < max_iterations:
        iteration += 1

        if is_cancelled:
            try:
                if await is_cancelled():
                    logger.info(
                        f"[{trace.trace_id[:8]}] 🛑 Client disconnected, "
                        f"stopping at iteration {iteration}"
                    )
                    break
            except Exception:
                pass

        logger.info(
            f"[{trace.trace_id[:8]}] 🔄 Editing iteration {iteration}/{max_iterations} "
            f"(composition={is_composition})"
        )

        with trace_span(trace, f"llm_iteration_{iteration}"):
            start_time = time.time()

            if llm.supports_reasoning():
                response = None
                async for item in _stream_llm_response(
                    llm, messages, allowed_tools, route.tool_choice,
                    trace, lambda data: sse_event(data),
                    max_tokens=llm_max_tokens,
                    reasoning_fraction=reasoning_fraction,
                    suppress_content=True,
                ):
                    if isinstance(item, StreamFinalResponse):
                        response = item.response
                    else:
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

        if response is None:
            break
        if route.force_stop_after:
            response = enforce_single_tool(response)

        if response.content:
            clean_content = strip_tool_echoes(response.content)
            if clean_content:
                yield await sse_event({"type": "content", "content": clean_content})

        if not response.has_tool_calls:
            if not is_composition:
                break

        iter_tool_results: list[dict[str, Any]] = []

        if (
            plan_tracker is None
            and response is not None
            and response.has_tool_calls
            and execution_mode == "apply"
        ):
            _candidate = _PlanTracker()
            _candidate.build(
                response.tool_calls, prompt, project_context,
                is_composition, store,
            )
            if len(_candidate.steps) >= 2:
                plan_tracker = _candidate
                yield await sse_event(plan_tracker.to_plan_event())
        elif (
            plan_tracker is not None
            and response is not None
            and response.has_tool_calls
            and execution_mode == "apply"
        ):
            for tc in response.tool_calls:
                resolved = _resolve_variable_refs(tc.params, iter_tool_results)
                step = plan_tracker.find_step_for_tool(tc.name, resolved, store)
                if step and step.status == "pending":
                    yield await sse_event(plan_tracker.activate_step(step.step_id))

        for tc_idx, tc in enumerate(response.tool_calls):
            resolved_args = _resolve_variable_refs(tc.params, iter_tool_results)

            if plan_tracker and execution_mode == "apply":
                step = plan_tracker.step_for_tool_index(tc_idx)
                if step is None:
                    step = plan_tracker.find_step_for_tool(
                        tc.name, resolved_args, store,
                    )
                if step and step.step_id != plan_tracker._active_step_id:
                    if plan_tracker._active_step_id:
                        evt = plan_tracker.complete_active_step()
                        if evt:
                            yield await sse_event(evt)
                    yield await sse_event(
                        plan_tracker.activate_step(step.step_id)
                    )

            outcome = await _apply_single_tool_call(
                tc_id=tc.id,
                tc_name=tc.name,
                resolved_args=resolved_args,
                allowed_tool_names=route.allowed_tool_names,
                store=store,
                trace=trace,
                add_notes_failures=_add_notes_failures,
                emit_sse=(execution_mode == "apply"),
            )

            for evt in outcome.sse_events:
                yield await sse_event(evt)

            if not outcome.skipped:
                tool_calls_collected.append({"tool": tc.name, "params": outcome.enriched_params})
                tool_calls_collected.extend(outcome.extra_tool_calls)

                if plan_tracker and execution_mode == "apply":
                    _step = (
                        plan_tracker.step_for_tool_index(tc_idx)
                        or plan_tracker.find_step_for_tool(
                            tc.name, outcome.enriched_params, store
                        )
                    )
                    if _step:
                        _step.result = _build_step_result(
                            tc.name, outcome.enriched_params, _step.result,
                        )

            messages.append(outcome.msg_call)
            iter_tool_results.append(outcome.tool_result)
            messages.append(outcome.msg_result)

        if plan_tracker and plan_tracker._active_step_id and execution_mode == "apply":
            evt = plan_tracker.complete_active_step()
            if evt:
                yield await sse_event(evt)

        if response is not None and response.has_tool_calls:
            snapshot = _entity_manifest(store)
            messages.append({
                "role": "system",
                "content": (
                    "ENTITY STATE AFTER TOOL CALLS (authoritative — use these IDs):\n"
                    + json.dumps(snapshot, indent=None)
                    + "\nUse the IDs above for subsequent tool calls. "
                    "Do NOT re-add notes to regions that already have notes (check noteCount). "
                    "Do NOT call stori_clear_notes unless explicitly replacing content. "
                    "A successful stori_add_notes response means the notes were stored — "
                    "do not redo the call."
                ),
            })

        if route.force_stop_after and tool_calls_collected:
            logger.info(f"[{trace.trace_id[:8]}] ✅ Force stop after {len(tool_calls_collected)} tool(s)")
            break

        if is_composition and iteration < max_iterations:
            all_tracks = store.registry.list_tracks()
            incomplete = _get_incomplete_tracks(store, tool_calls_collected)

            if not all_tracks:
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
                if plan_tracker and execution_mode == "apply":
                    incomplete_set = set(incomplete)
                    existing_track_names = {
                        t.name for t in store.registry.list_tracks()
                    }
                    for _step in plan_tracker.steps:
                        if (
                            _step.track_name
                            and _step.status in ("active", "pending")
                            and _step.track_name in existing_track_names
                            and _step.track_name not in incomplete_set
                        ):
                            yield await sse_event(
                                plan_tracker.complete_step_by_id(
                                    _step.step_id,
                                    f"Created {_step.track_name}",
                                )
                            )
                    messages.append({
                        "role": "system",
                        "content": plan_tracker.progress_context(),
                    })

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
                missing_expressive = _get_missing_expressive_steps(
                    parsed, tool_calls_collected
                )
                if missing_expressive:
                    entity_snapshot = _entity_manifest(store)
                    messages.append({
                        "role": "system",
                        "content": (
                            "EXPRESSIVE PHASE LOCK: All tracks have been created and have notes. "
                            "You MUST NOT call stori_add_midi_track, stori_add_midi_region, "
                            "stori_add_notes, or any track/region creation tool. "
                            "Only call: stori_add_insert_effect, stori_add_midi_cc, "
                            "stori_add_pitch_bend, stori_add_automation, stori_ensure_bus, stori_add_send."
                        ),
                    })
                    expressive_msg = (
                        "⚠️ EXPRESSIVE PHASE — call ALL of these in ONE batch, then stop:\n"
                        + "\n".join(f"  {i+1}. {m}" for i, m in enumerate(missing_expressive))
                        + f"\n\nEntity IDs for your calls:\n{entity_snapshot}"
                        + "\n\nBatch ALL tool calls in a single response. No text. Just the tool calls."
                    )
                    messages.append({"role": "user", "content": expressive_msg})
                    logger.info(
                        f"[{trace.trace_id[:8]}] 🔄 Continuation: {len(missing_expressive)} "
                        f"expressive step(s) pending (iteration {iteration})"
                    )
                    continue
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ All tracks and expressive steps done "
                    f"after iteration {iteration}"
                )
                break

        if not is_composition:
            if response is not None and response.has_tool_calls:
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ Non-composition: executed "
                    f"{len(response.tool_calls)} tool(s), stopping after iteration {iteration}"
                )
                break
            break

    # =========================================================================
    # Variation Mode: Compute and emit variation per spec (meta/phrase/done)
    # =========================================================================
    if execution_mode == "variation" and tool_calls_collected:
        from app.core.executor import execute_plan_variation

        tool_call_objs = [
            ToolCall(name=cast(str, tc["tool"]), params=cast(dict[str, Any], tc["params"]))
            for tc in tool_calls_collected
        ]

        variation = await execute_plan_variation(
            tool_calls=tool_call_objs,
            project_state=project_context,
            intent=prompt,
            conversation_id=store.conversation_id,
            explanation=None,
            quality_preset=quality_preset,
        )

        from app.core.maestro_composing import _store_variation
        _store_variation(variation, project_context, store)

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

        logger.info(
            f"[{trace.trace_id[:8]}] EDITING variation streamed: "
            f"{variation.total_changes} changes in {len(variation.phrases)} phrases"
        )

        yield await sse_event({
            "type": "complete",
            "success": True,
            "variationId": variation.variation_id,
            "totalChanges": variation.total_changes,
            "phraseCount": len(variation.phrases),
            "traceId": trace.trace_id,
            **_context_usage_fields(usage_tracker, llm.model),
        })
        return

    # =========================================================================
    # Apply Mode: Standard completion
    # =========================================================================

    if plan_tracker:
        for skip_evt in plan_tracker.finalize_pending_as_skipped():
            yield await sse_event(skip_evt)

    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": tool_calls_collected,
        "stateVersion": store.version,
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    })
