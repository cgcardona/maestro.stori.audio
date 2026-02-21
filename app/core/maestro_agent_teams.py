"""Agent Teams — parallel instrument execution for Maestro.

Implements multi-instrument STORI PROMPT compositions via independent
per-instrument LLM sessions running concurrently via asyncio.gather.
SSE events from all agents are multiplexed through a shared queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid_mod
from typing import Any, AsyncIterator, Optional

from app.config import settings
from app.core.expansion import ToolCall
from app.core.llm_client import LLMClient, LLMResponse
from app.core.prompts import system_prompt_base
from app.core.sse_utils import ReasoningBuffer, sse_event
from app.core.state_store import StateStore
from app.core.tools import ALL_TOOLS
from app.core.maestro_helpers import (
    UsageTracker,
    _context_usage_fields,
    _entity_manifest,
    _human_label_for_tool,
    _resolve_variable_refs,
)
from app.core.maestro_plan_tracker import (
    _PlanTracker,
    _build_step_result,
    _TRACK_CREATION_NAMES,
    _EFFECT_TOOL_NAMES,
    _GENERATOR_TOOL_NAMES,
    _INSTRUMENT_AGENT_TOOLS,
    _AGENT_TEAM_PHASE3_TOOLS,
)
from app.core.maestro_editing import _apply_single_tool_call

logger = logging.getLogger(__name__)

_CC_NAMES: dict[int, str] = {
    1: "Mod Wheel", 7: "Volume", 10: "Pan", 11: "Expression",
    64: "Sustain Pedal", 74: "Filter Cutoff", 91: "Reverb Send", 93: "Chorus",
}


# ---------------------------------------------------------------------------
# Per-instrument agent
# ---------------------------------------------------------------------------

async def _run_instrument_agent(
    instrument_name: str,
    role: str,
    style: str,
    bars: int,
    tempo: float,
    key: str,
    step_ids: list[str],
    plan_tracker: _PlanTracker,
    llm: LLMClient,
    store: StateStore,
    allowed_tool_names: set[str],
    trace: Any,
    sse_queue: "asyncio.Queue[dict[str, Any]]",
    collected_tool_calls: list[dict[str, Any]],
    existing_track_id: Optional[str] = None,
    start_beat: int = 0,
) -> None:
    """Independent instrument agent: dedicated multi-turn LLM session per instrument.

    Each invocation is a genuinely concurrent HTTP session running simultaneously
    with sibling agents via ``asyncio.gather``. The agent loops over LLM turns
    until all tool calls complete (create track → region → notes → effect).

    When ``existing_track_id`` is provided the track already exists in the
    project; the agent skips ``stori_add_midi_track`` and places new regions
    starting at ``start_beat`` (the beat immediately after the last existing
    region on that track).

    SSE events are written to ``sse_queue`` (forwarded to client by coordinator).
    All executed tool calls are appended to ``collected_tool_calls`` for the
    summary.final event.

    Failure is isolated: an exception marks only this agent's plan steps as
    failed and does not propagate to sibling agents.
    """
    agent_log = f"[{trace.trace_id[:8]}][{instrument_name}Agent]"
    reusing = bool(existing_track_id)
    logger.info(
        f"{agent_log} Starting — style={style}, bars={bars}, tempo={tempo}, key={key}"
        + (f", reusing trackId={existing_track_id}, startBeat={start_beat}" if reusing else "")
    )

    beat_count = bars * 4
    if reusing:
        system_content = (
            f"You are a music production agent. Your ONLY job is to add new content to the "
            f"existing **{instrument_name}** track for this {style} composition. "
            f"You must complete ALL steps before stopping.\n\n"
            f"Project context:\n"
            f"- Tempo: {tempo} BPM | Key: {key} | Style: {style} | Length: {bars} bars ({beat_count} beats)\n\n"
            f"IMPORTANT — track already exists:\n"
            f"- The {instrument_name} track already exists with trackId='{existing_track_id}'.\n"
            f"- DO NOT call stori_add_midi_track. Use '{existing_track_id}' directly as trackId.\n"
            f"- Existing content ends at beat {start_beat}. Start all new regions at beat {start_beat}.\n\n"
            f"Required pipeline — execute ALL steps in order:\n"
            f"1. stori_add_midi_region — add a {beat_count}-beat region starting at beat {start_beat} "
            f"on trackId='{existing_track_id}'\n"
            f"2. Add content — use stori_add_notes with specific pitches and rhythms\n"
            f"   Use $0.regionId for regionId, trackId='{existing_track_id}'\n"
            f"3. stori_add_insert_effect — add one appropriate effect to trackId='{existing_track_id}'\n\n"
            f"IMPORTANT:\n"
            f"- Do NOT call stori_add_midi_track — the track already exists.\n"
            f"- Start the region at beat {start_beat}, NOT beat 0.\n"
            f"- Do NOT create tracks for other instruments.\n"
            f"- Make all tool calls now — the pipeline is not complete until step 3.\n"
            f"- Do not add any text response — only tool calls."
        )
    else:
        system_content = (
            f"You are a music production agent. Your ONLY job is to fully build the "
            f"**{instrument_name}** track for this {style} composition. "
            f"You must complete ALL steps before stopping.\n\n"
            f"Project context:\n"
            f"- Tempo: {tempo} BPM | Key: {key} | Style: {style} | Length: {bars} bars ({beat_count} beats)\n\n"
            f"Required pipeline — execute ALL steps in order:\n"
            f"1. stori_add_midi_track — create the {instrument_name} track\n"
            f"2. stori_add_midi_region — add a {beat_count}-beat region at beat 0 "
            f"(use $0.trackId for trackId)\n"
            f"3. Add content — use stori_add_notes with specific pitches and rhythms\n"
            f"   Use $1.regionId for regionId, $0.trackId for trackId\n"
            f"4. stori_add_insert_effect — add one appropriate effect to the track\n\n"
            f"IMPORTANT:\n"
            f"- Do NOT stop after step 1. You MUST complete all 4 steps.\n"
            f"- Do NOT create tracks for other instruments.\n"
            f"- Make all tool calls now — the pipeline is not complete until step 4.\n"
            f"- Do not add any text response — only tool calls."
        )

    agent_tools = [
        t for t in ALL_TOOLS
        if t["function"]["name"] in _INSTRUMENT_AGENT_TOOLS
    ]
    if reusing:
        user_message = (
            f"Add a new {style} section to the existing {instrument_name} track "
            f"(trackId='{existing_track_id}') starting at beat {start_beat}. "
            f"Execute all 3 steps: add region at beat {start_beat}, add notes, add effect. "
            f"Do NOT create a new track. Make all tool calls in this response."
        )
    else:
        user_message = (
            f"Build the complete {instrument_name} track now. "
            f"Execute all 4 steps: create track, add region, add notes, add effect. "
            f"Make all tool calls in this response."
        )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_message},
    ]

    _agent_id = instrument_name.lower()
    add_notes_failures: dict[str, int] = {}
    active_step_id: Optional[str] = None
    all_tool_results: list[dict[str, Any]] = []
    max_turns = 4

    _stage_track = reusing
    _stage_region = False
    _stage_region_ok = False
    _stage_content = False
    _stage_effect = False

    def _missing_stages() -> list[str]:
        missing = []
        if not _stage_region:
            region_beat = start_beat if reusing else 0
            track_ref = f"trackId='{existing_track_id}'" if reusing else "$0.trackId"
            missing.append(
                f"stori_add_midi_region (add a {bars * 4}-beat region at beat {region_beat} "
                f"on {track_ref})"
            )
        if not _stage_content:
            missing.append("stori_add_notes (add musical content with specific pitches)")
        if not _stage_effect:
            missing.append("stori_add_insert_effect (add one insert effect)")
        return missing

    for turn in range(max_turns):
        if turn > 0:
            missing = _missing_stages()
            if not missing:
                break
            reminder = (
                "You have not finished the pipeline. You MUST still call:\n"
                + "\n".join(f"  • {m}" for m in missing)
                + "\nMake these tool calls now."
            )
            messages.append({"role": "user", "content": reminder})

        # ── LLM call (streaming for per-agent reasoning) ──
        try:
            _resp_content: Optional[str] = None
            _resp_tool_calls: list[dict[str, Any]] = []
            _resp_finish: Optional[str] = None
            _resp_usage: dict[str, Any] = {}
            _rbuf = ReasoningBuffer()

            async for _chunk in llm.chat_completion_stream(
                messages=messages,
                tools=agent_tools,
                tool_choice="required",
                max_tokens=settings.composition_max_tokens,
                reasoning_fraction=settings.composition_reasoning_fraction,
            ):
                _ct = _chunk.get("type")
                if _ct == "reasoning_delta":
                    _text = _chunk.get("text", "")
                    if _text:
                        _word = _rbuf.add(_text)
                        if _word:
                            await sse_queue.put({
                                "type": "reasoning",
                                "content": _word,
                                "agentId": _agent_id,
                            })
                elif _ct == "content_delta":
                    _flush = _rbuf.flush()
                    if _flush:
                        await sse_queue.put({
                            "type": "reasoning",
                            "content": _flush,
                            "agentId": _agent_id,
                        })
                elif _ct == "done":
                    _flush = _rbuf.flush()
                    if _flush:
                        await sse_queue.put({
                            "type": "reasoning",
                            "content": _flush,
                            "agentId": _agent_id,
                        })
                    _resp_content = _chunk.get("content")
                    _resp_tool_calls = _chunk.get("tool_calls", [])
                    _resp_finish = _chunk.get("finish_reason")
                    _resp_usage = _chunk.get("usage", {})

            response = LLMResponse(
                content=_resp_content,
                finish_reason=_resp_finish,
                usage=_resp_usage,
            )
            for _tc in _resp_tool_calls:
                try:
                    _args = _tc.get("function", {}).get("arguments", "{}")
                    if isinstance(_args, str):
                        _args = json.loads(_args) if _args else {}
                    response.tool_calls.append(ToolCall(
                        id=_tc.get("id", ""),
                        name=_tc.get("function", {}).get("name", ""),
                        params=_args,
                    ))
                except Exception as _parse_err:
                    logger.error(f"{agent_log} Error parsing tool call: {_parse_err}")
        except Exception as exc:
            logger.error(f"{agent_log} LLM call failed (turn {turn}): {exc}")
            for step_id in step_ids:
                step = next((s for s in plan_tracker.steps if s.step_id == step_id), None)
                if step and step.status in ("pending", "active"):
                    step.status = "failed"
                    await sse_queue.put({
                        "type": "planStepUpdate",
                        "stepId": step_id,
                        "status": "failed",
                        "result": f"Failed: {exc}",
                        "agentId": _agent_id,
                    })
            return

        logger.info(f"{agent_log} Turn {turn}: {len(response.tool_calls)} tool call(s)")

        if not response.tool_calls:
            break

        assistant_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.params)},
            }
            for tc in response.tool_calls
        ]
        messages.append({"role": "assistant", "content": None, "tool_calls": assistant_tool_calls})

        turn_tool_results: list[dict[str, Any]] = []
        tool_result_messages: list[dict[str, Any]] = []

        for tc in response.tool_calls:
            resolved_args = _resolve_variable_refs(tc.params, all_tool_results)

            # Index-based step progression: track creation tools map to the first
            # step; all content/generator/effect tools map to the second step.
            if tc.name in _TRACK_CREATION_NAMES:
                desired_step_id = step_ids[0] if step_ids else None
            elif step_ids:
                desired_step_id = step_ids[1] if len(step_ids) > 1 else step_ids[0]
            else:
                desired_step_id = None

            if desired_step_id and desired_step_id != active_step_id:
                if active_step_id:
                    evt = plan_tracker.complete_step_by_id(active_step_id)
                    if evt:
                        await sse_queue.put({**evt, "agentId": _agent_id})
                activate_evt = plan_tracker.activate_step(desired_step_id)
                await sse_queue.put({**activate_evt, "agentId": _agent_id})
                active_step_id = desired_step_id

            # Guard: skip effect tools when no region was successfully created.
            if tc.name in _EFFECT_TOOL_NAMES and not _stage_region_ok and reusing:
                logger.warning(
                    f"{agent_log} Skipping {tc.name} — region was not created successfully. "
                    f"This prevents adding effects to the wrong track."
                )
                tool_result_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"skipped": True, "reason": "region creation did not succeed"}),
                })
                continue

            outcome = await _apply_single_tool_call(
                tc_id=tc.id,
                tc_name=tc.name,
                resolved_args=resolved_args,
                allowed_tool_names=allowed_tool_names,
                store=store,
                trace=trace,
                add_notes_failures=add_notes_failures,
                emit_sse=True,
            )

            for evt in outcome.sse_events:
                if evt.get("type") in ("toolCall", "toolStart", "toolError"):
                    evt = {**evt, "agentId": instrument_name.lower()}
                await sse_queue.put(evt)

            if not outcome.skipped and active_step_id:
                active_step = next(
                    (s for s in plan_tracker.steps if s.step_id == active_step_id), None
                )
                if active_step:
                    active_step.result = _build_step_result(
                        tc.name, outcome.enriched_params, active_step.result
                    )

            if tc.name in _TRACK_CREATION_NAMES:
                _stage_track = True
            elif tc.name in {"stori_add_midi_region"}:
                _stage_region = True
                if outcome.tool_result.get("regionId"):
                    _stage_region_ok = True
                else:
                    logger.warning(
                        f"{agent_log} stori_add_midi_region completed but returned no regionId "
                        f"(likely a collision or validation error) — effects will be skipped"
                    )
            elif tc.name in _GENERATOR_TOOL_NAMES or tc.name == "stori_add_notes":
                _stage_content = True
            elif tc.name in _EFFECT_TOOL_NAMES:
                _stage_effect = True

            all_tool_results.append(outcome.tool_result)
            turn_tool_results.append(outcome.tool_result)
            collected_tool_calls.append({"tool": tc.name, "params": outcome.enriched_params})

            tool_result_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(outcome.tool_result),
            })

            logger.debug(f"{agent_log} {tc.name} executed (skipped={outcome.skipped})")

        messages.extend(tool_result_messages)

    if active_step_id:
        evt = plan_tracker.complete_step_by_id(active_step_id)
        if evt:
            await sse_queue.put({**evt, "agentId": _agent_id})

    logger.info(f"{agent_log} Complete ({len(all_tool_results)} tool calls, {turn + 1} turn(s))")


# ---------------------------------------------------------------------------
# Composition summary helpers
# ---------------------------------------------------------------------------

def _build_composition_summary(
    tool_calls_collected: list[dict[str, Any]],
    tempo: Optional[float] = None,
    key: Optional[str] = None,
    style: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregate composition metadata for the summary.final SSE event.

    Recognises the synthetic ``_reused_track`` tool name injected by the
    coordinator for tracks that already existed so the frontend can display
    "reused" vs "created" labels correctly.

    When ``tempo``, ``key``, or ``style`` are provided, a human-readable
    ``text`` field is included so the frontend can display a completion
    paragraph below the agent execution feed.
    """
    tracks_created: list[dict[str, Any]] = []
    tracks_reused: list[dict[str, Any]] = []
    regions_created = 0
    notes_generated = 0
    effects_added: list[dict[str, str]] = []
    sends_created = 0
    cc_counts: dict[int, str] = {}
    automation_lanes = 0

    for tc in tool_calls_collected:
        name = tc.get("tool", "")
        params = tc.get("params", {})
        if name == "stori_add_midi_track":
            tracks_created.append({
                "name": params.get("name", ""),
                "instrument": params.get("_gmInstrumentName") or params.get("drumKitId") or "Unknown",
                "trackId": params.get("trackId", ""),
            })
        elif name == "_reused_track":
            tracks_reused.append({
                "name": params.get("name", ""),
                "trackId": params.get("trackId", ""),
            })
        elif name == "stori_add_midi_region":
            regions_created += 1
        elif name == "stori_add_notes":
            notes_generated += len(params.get("notes", []))
        elif name == "stori_add_insert_effect":
            effects_added.append({
                "trackId": params.get("trackId", ""),
                "type": params.get("effectType") or params.get("type", ""),
            })
        elif name == "stori_add_send":
            sends_created += 1
        elif name == "stori_add_midi_cc":
            cc_num = int(params.get("cc", 0))
            cc_counts[cc_num] = _CC_NAMES.get(cc_num, f"CC {cc_num}")
        elif name == "stori_add_automation":
            automation_lanes += 1

    result: dict[str, Any] = {
        "tracksCreated": tracks_created,
        "tracksReused": tracks_reused,
        "trackCount": len(tracks_created) + len(tracks_reused),
        "regionsCreated": regions_created,
        "notesGenerated": notes_generated,
        "effectsAdded": effects_added,
        "effectCount": len(effects_added),
        "sendsCreated": sends_created,
        "ccEnvelopes": [{"cc": k, "name": v} for k, v in sorted(cc_counts.items())],
        "automationLanes": automation_lanes,
    }
    result["text"] = _compose_summary_text(
        result, tempo=tempo, key=key, style=style,
    )
    return result


def _compose_summary_text(
    summary: dict[str, Any],
    tempo: Optional[float] = None,
    key: Optional[str] = None,
    style: Optional[str] = None,
) -> str:
    """Build a concise natural-language summary of a completed composition."""
    all_tracks = summary.get("tracksCreated", []) + summary.get("tracksReused", [])
    track_count = len(all_tracks)
    track_names = [t.get("name", "") for t in all_tracks if t.get("name")]
    notes = summary.get("notesGenerated", 0)
    regions = summary.get("regionsCreated", 0)
    effects = summary.get("effectCount", 0)

    parts: list[str] = []
    verb = "Created"
    if summary.get("tracksReused"):
        verb = "Extended"

    desc = f"{verb} a"
    if style and style != "default":
        desc += f" {style}"
    desc += " composition"
    if key:
        desc += f" in {key}"
    if tempo:
        bpm = int(tempo) if tempo == int(tempo) else tempo
        desc += f" at {bpm} BPM"
    parts.append(desc)

    if track_count and track_names:
        if len(track_names) <= 4:
            if len(track_names) == 1:
                names_str = track_names[0]
            elif len(track_names) == 2:
                names_str = f"{track_names[0]} and {track_names[1]}"
            else:
                names_str = ", ".join(track_names[:-1]) + f", and {track_names[-1]}"
            parts.append(f"with {track_count} tracks \u2014 {names_str}")
        else:
            parts.append(f"with {track_count} tracks")

    stats: list[str] = []
    if notes:
        stats.append(f"{notes} notes")
    if regions:
        stats.append(f"{regions} {'region' if regions == 1 else 'regions'}")
    if effects:
        stats.append(f"{effects} {'effect' if effects == 1 else 'effects'}")
    if stats:
        parts.append("totaling " + " across ".join(stats[:2]))
        if len(stats) > 2:
            parts[-1] += f" with {stats[2]}"

    return " ".join(parts) + "."


# ---------------------------------------------------------------------------
# Agent Teams coordinator
# ---------------------------------------------------------------------------

async def _handle_composition_agent_team(
    prompt: str,
    project_context: dict[str, Any],
    parsed: Any,  # ParsedPrompt — avoids circular import at module level
    route: Any,
    llm: LLMClient,
    store: StateStore,
    trace: Any,
    usage_tracker: Optional["UsageTracker"],
) -> AsyncIterator[str]:
    """Agent Teams coordinator for multi-instrument STORI PROMPT compositions.

    Three-phase execution:

    - **Phase 1** (sequential): tempo and key applied deterministically from
      the parsed prompt — no LLM call needed.
    - **Phase 2** (parallel): one independent ``_run_instrument_agent`` task
      per role, all launched simultaneously via ``asyncio.gather``. SSE events
      from all agents are multiplexed through a shared queue and forwarded to
      the client as they arrive.
    - **Phase 3** (sequential): optional mixing coordinator LLM call for
      shared buses, sends, and volume adjustments.
    """
    # Coordinator reasoning is suppressed for STORI PROMPT requests.
    # The structured prompt already encodes full intent; emitting reasoning
    # tokens would add latency and cost with no user value.
    # Per-agent reasoning is emitted with agentId during Phase 2 instead.

    yield await sse_event({"type": "status", "message": "Preparing composition..."})

    plan_tracker = _PlanTracker()
    plan_tracker.build_from_prompt(parsed, prompt, project_context or {})
    if plan_tracker.steps:
        yield await sse_event(plan_tracker.to_plan_event())

    tool_calls_collected: list[dict[str, Any]] = []
    add_notes_failures: dict[str, int] = {}

    # ── Phase 1: Deterministic setup ──
    current_tempo = project_context.get("tempo")
    current_key = (project_context.get("key") or "").strip().lower()

    if parsed.tempo and parsed.tempo != current_tempo:
        tempo_step = next(
            (s for s in plan_tracker.steps if s.tool_name == "stori_set_tempo"), None
        )
        if tempo_step:
            yield await sse_event(plan_tracker.activate_step(tempo_step.step_id))

        outcome = await _apply_single_tool_call(
            tc_id=str(_uuid_mod.uuid4()),
            tc_name="stori_set_tempo",
            resolved_args={"tempo": parsed.tempo},
            allowed_tool_names=route.allowed_tool_names,
            store=store,
            trace=trace,
            add_notes_failures=add_notes_failures,
            emit_sse=True,
        )
        for evt in outcome.sse_events:
            yield await sse_event(evt)
        if not outcome.skipped:
            tool_calls_collected.append({"tool": "stori_set_tempo", "params": outcome.enriched_params})
            if tempo_step:
                yield await sse_event(
                    plan_tracker.complete_step_by_id(
                        tempo_step.step_id, f"Set tempo to {parsed.tempo} BPM"
                    )
                )

    if parsed.key and parsed.key.strip().lower() != current_key:
        key_step = next(
            (s for s in plan_tracker.steps if s.tool_name == "stori_set_key"), None
        )
        if key_step:
            yield await sse_event(plan_tracker.activate_step(key_step.step_id))

        outcome = await _apply_single_tool_call(
            tc_id=str(_uuid_mod.uuid4()),
            tc_name="stori_set_key",
            resolved_args={"key": parsed.key},
            allowed_tool_names=route.allowed_tool_names,
            store=store,
            trace=trace,
            add_notes_failures=add_notes_failures,
            emit_sse=True,
        )
        for evt in outcome.sse_events:
            yield await sse_event(evt)
        if not outcome.skipped:
            tool_calls_collected.append({"tool": "stori_set_key", "params": outcome.enriched_params})
            if key_step:
                yield await sse_event(
                    plan_tracker.complete_step_by_id(
                        key_step.step_id, f"Set key to {parsed.key}"
                    )
                )

    # ── Phase 2: Spawn instrument agents ──
    _ROLE_LABELS: dict[str, str] = {
        "drums": "Drums", "drum": "Drums",
        "bass": "Bass",
        "chords": "Chords", "chord": "Chords",
        "melody": "Melody",
        "lead": "Lead",
        "arp": "Arp",
        "pads": "Pads", "pad": "Pads",
        "fx": "FX",
    }

    instrument_step_ids: dict[str, list[str]] = {}
    for step in plan_tracker.steps:
        if step.parallel_group == "instruments" and step.track_name:
            key_label = step.track_name.lower()
            instrument_step_ids.setdefault(key_label, []).append(step.step_id)

    style = parsed.style or "default"
    ext = getattr(parsed, "extensions", {}) or {}
    bars = int(ext.get("bars") or ext.get("Bars") or 4)
    tempo = float(parsed.tempo or project_context.get("tempo") or 120)
    key = parsed.key or project_context.get("key") or "C"

    # ── Detect existing tracks to avoid creating duplicates ──
    _existing_track_info: dict[str, dict[str, Any]] = {}
    for pc_track in project_context.get("tracks", []):
        track_name_lower = (pc_track.get("name") or "").lower()
        if not track_name_lower:
            continue
        track_id: str = (
            pc_track.get("trackId")
            or pc_track.get("id")
            or ""
        )
        regions = pc_track.get("regions", [])
        next_beat: int = 0
        if regions:
            next_beat = int(max(
                r.get("startBeat", 0) + r.get("durationBeats", 0)
                for r in regions
            ))
        if track_name_lower not in _existing_track_info:
            _existing_track_info[track_name_lower] = {
                "trackId": track_id,
                "next_beat": next_beat,
            }
    logger.debug(
        f"[{trace.trace_id[:8]}] Existing track map: "
        + ", ".join(f"{k}={v['trackId'][:8]}" for k, v in _existing_track_info.items() if v["trackId"])
    )

    # ── Preflight events — latency masking (emit before agents start) ──
    # Lets the frontend pre-allocate timeline rows and show "incoming" states
    # for every predicted instrument step. Derived from the plan, no LLM needed.
    for role in parsed.roles:
        instrument_name = _ROLE_LABELS.get(role.lower(), role.title())
        step_ids_for_role = instrument_step_ids.get(instrument_name.lower(), [])
        steps_for_role = [s for s in plan_tracker.steps if s.step_id in step_ids_for_role]
        for step in steps_for_role:
            yield await sse_event({
                "type": "preflight",
                "stepId": step.step_id,
                "agentId": instrument_name.lower(),
                "agentRole": role,
                "label": step.label,
                "toolName": step.tool_name,
                "parallelGroup": step.parallel_group,
                "confidence": 0.9,
            })

    sse_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    agent_tool_calls: list[dict[str, Any]] = []
    tasks: list[asyncio.Task] = []

    _role_track_info: dict[str, dict[str, Any]] = {}
    for role in parsed.roles:
        instrument_name = _ROLE_LABELS.get(role.lower(), role.title())
        existing_info = _existing_track_info.get(instrument_name.lower())
        if not existing_info:
            for ekey, info in _existing_track_info.items():
                if instrument_name.lower() in ekey or ekey in instrument_name.lower():
                    existing_info = info
                    break
        track_id = existing_info["trackId"] if existing_info else None
        _role_track_info[role] = {
            "instrument_name": instrument_name,
            "existing_track_id": track_id if track_id else None,
            "start_beat": existing_info["next_beat"] if existing_info and track_id else 0,
        }
        logger.debug(
            f"[{trace.trace_id[:8]}] Role '{role}' → instrument='{instrument_name}' "
            f"existing_track_id={track_id or 'None (will create)'}"
        )

    _reused_ids = [
        info["existing_track_id"]
        for info in _role_track_info.values()
        if info["existing_track_id"]
    ]
    if len(_reused_ids) != len(set(_reused_ids)):
        logger.error(
            f"[{trace.trace_id[:8]}] ❌ DUPLICATE trackId in role→track mapping! "
            f"ids={_reused_ids} — check project_context track names vs role names"
        )

    for role in parsed.roles:
        role_info = _role_track_info[role]
        instrument_name = role_info["instrument_name"]
        step_ids_for_role = instrument_step_ids.get(instrument_name.lower(), [])
        existing_track_id = role_info["existing_track_id"]
        agent_start_beat = role_info["start_beat"]
        if existing_track_id:
            agent_tool_calls.append({
                "tool": "_reused_track",
                "params": {"name": instrument_name, "trackId": existing_track_id},
            })
        task = asyncio.create_task(
            _run_instrument_agent(
                instrument_name=instrument_name,
                role=role,
                style=style,
                bars=bars,
                tempo=tempo,
                key=key,
                step_ids=step_ids_for_role,
                plan_tracker=plan_tracker,
                llm=llm,
                store=store,
                allowed_tool_names=_INSTRUMENT_AGENT_TOOLS,
                trace=trace,
                sse_queue=sse_queue,
                collected_tool_calls=agent_tool_calls,
                existing_track_id=existing_track_id,
                start_beat=agent_start_beat,
            )
        )
        tasks.append(task)
        logger.info(
            f"[{trace.trace_id[:8]}] 🚀 Spawned {instrument_name} agent "
            f"(step_ids={step_ids_for_role}"
            + (f", reusing trackId={existing_track_id}, startBeat={agent_start_beat}" if existing_track_id else "")
            + ")"
        )

    # Drain queue while agents run — forward events to client as they arrive
    pending: set[asyncio.Task] = set(tasks)
    while pending:
        done, pending = await asyncio.wait(pending, timeout=0.05)
        while not sse_queue.empty():
            yield await sse_event(sse_queue.get_nowait())
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                logger.error(
                    f"[{trace.trace_id[:8]}] ❌ Instrument agent failed: {task.exception()}"
                )
    while not sse_queue.empty():
        yield await sse_event(sse_queue.get_nowait())

    logger.info(f"[{trace.trace_id[:8]}] ✅ All instrument agents complete")

    # ── Phase 3: Mixing coordinator (optional, one LLM call) ──
    phase3_steps = [
        s for s in plan_tracker.steps
        if s.status == "pending" and s.parallel_group is None
        and s.tool_name in _AGENT_TEAM_PHASE3_TOOLS
    ]
    if phase3_steps:
        entity_snapshot = _entity_manifest(store)
        phase3_tools = [
            t for t in ALL_TOOLS
            if t["function"]["name"] in _AGENT_TEAM_PHASE3_TOOLS
        ]
        mixing_prompt = (
            "All instrument tracks have been created. Apply final mixing:\n"
            + "\n".join(f"- {s.label}" for s in phase3_steps)
            + f"\n\nCurrent entity IDs:\n{json.dumps(entity_snapshot)}\n\n"
            "Batch ALL mixing tool calls in a single response. No text."
        )
        try:
            phase3_response = await llm.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt_base()},
                    {"role": "user", "content": mixing_prompt},
                ],
                tools=phase3_tools,
                tool_choice="auto",
                max_tokens=2000,
            )
            phase3_iter_results: list[dict[str, Any]] = []
            phase3_failures: dict[str, int] = {}
            for tc in phase3_response.tool_calls:
                p3_resolved = _resolve_variable_refs(tc.params, phase3_iter_results)
                p3_step = plan_tracker.find_step_for_tool(tc.name, p3_resolved, store)
                if p3_step:
                    yield await sse_event(plan_tracker.activate_step(p3_step.step_id))
                p3_outcome = await _apply_single_tool_call(
                    tc_id=tc.id,
                    tc_name=tc.name,
                    resolved_args=p3_resolved,
                    allowed_tool_names=_AGENT_TEAM_PHASE3_TOOLS,
                    store=store,
                    trace=trace,
                    add_notes_failures=phase3_failures,
                    emit_sse=True,
                )
                for evt in p3_outcome.sse_events:
                    yield await sse_event(evt)
                if not p3_outcome.skipped:
                    tool_calls_collected.append({"tool": tc.name, "params": p3_outcome.enriched_params})
                    tool_calls_collected.extend(p3_outcome.extra_tool_calls)
                    if p3_step:
                        yield await sse_event(
                            plan_tracker.complete_step_by_id(p3_step.step_id)
                        )
                phase3_iter_results.append(p3_outcome.tool_result)
        except Exception as exc:
            logger.error(f"[{trace.trace_id[:8]}] Phase 3 coordinator failed: {exc}")

    # ── Finalize ──
    for skip_evt in plan_tracker.finalize_pending_as_skipped():
        yield await sse_event(skip_evt)

    all_collected = tool_calls_collected + agent_tool_calls
    summary = _build_composition_summary(
        all_collected, tempo=tempo, key=key, style=style,
    )
    yield await sse_event({
        "type": "summary.final",
        "traceId": trace.trace_id,
        **summary,
    })

    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": all_collected,
        "stateVersion": store.version,
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    })
