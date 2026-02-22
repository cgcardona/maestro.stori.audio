"""Per-section child agent — Level 3 of the three-level agent architecture.

Each section child executes a pre-planned (region + generate) pair for one
musical section of one instrument.  No LLM call is needed for the core
pipeline; the parent agent already wrote the section-specific prompt.

Optional refinement: when the STORI PROMPT specifies expressive tools
(CC curves, pitch bend, automation), a small focused LLM call adds them
after generation completes.

For drums, the child signals completion via ``SectionSignals`` so the
matching bass section child can start with the drum RhythmSpine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid as _uuid_mod
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.expansion import ToolCall
from app.core.llm_client import LLMClient
from app.core.sse_utils import ReasoningBuffer
from app.core.state_store import StateStore
from app.core.maestro_plan_tracker import (
    _ToolCallOutcome,
    _GENERATOR_TOOL_NAMES,
    _INSTRUMENT_AGENT_TOOLS,
)
from app.core.maestro_editing import _apply_single_tool_call
from app.core.maestro_agent_teams.signals import SectionSignals, SectionState, _state_key
from app.core.telemetry import compute_section_telemetry

logger = logging.getLogger(__name__)

_AGENT_TAGGED_EVENTS = frozenset({
    "toolCall", "toolStart", "toolError",
    "generatorStart", "generatorComplete",
    "reasoning", "content", "status",
    "agentComplete",
})

_EXPRESSIVENESS_TOOLS = frozenset({
    "stori_add_midi_cc",
    "stori_add_pitch_bend",
})


@dataclass
class SectionResult:
    """Outcome of a section child's execution."""

    success: bool
    section_name: str
    region_id: str | None = None
    notes_generated: int = 0
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    tool_call_records: list[dict[str, Any]] = field(default_factory=list)
    tool_result_msgs: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


async def _run_section_child(
    section: dict[str, Any],
    section_index: int,
    track_id: str,
    region_tc: ToolCall,
    generate_tc: ToolCall,
    instrument_name: str,
    role: str,
    agent_id: str,
    allowed_tool_names: set[str],
    store: StateStore,
    trace: Any,
    sse_queue: asyncio.Queue[dict[str, Any]],
    composition_context: dict[str, Any] | None,
    section_signals: SectionSignals | None = None,
    is_drum: bool = False,
    is_bass: bool = False,
    llm: Optional[LLMClient] = None,
) -> SectionResult:
    """Execute one section's region + generate pipeline.

    This is a lightweight executor — the parent LLM already planned the
    tool calls and wrote the section-specific prompt.  The child resolves
    the trackId and regionId, executes the two tool calls, and optionally
    runs a refinement LLM call for expressive tools.
    """
    sec_name = section.get("name", f"section_{section_index}")
    child_log = f"[{trace.trace_id[:8]}][{instrument_name}/{sec_name}]"
    _child_start = asyncio.get_event_loop().time()

    logger.info(
        f"{child_log} 🎬 Section child starting: "
        f"is_drum={is_drum}, is_bass={is_bass}, "
        f"beats={section.get('length_beats', '?')}, "
        f"start_beat={section.get('start_beat', '?')}"
    )

    result = SectionResult(success=False, section_name=sec_name)
    add_notes_failures: dict[str, int] = {}

    async def _emit(outcome: _ToolCallOutcome) -> None:
        for evt in outcome.sse_events:
            if evt.get("type") in _AGENT_TAGGED_EVENTS:
                evt = {**evt, "agentId": agent_id, "sectionName": sec_name}
            await sse_queue.put(evt)

    section_state: SectionState | None = None
    if composition_context:
        section_state = composition_context.get("section_state")
    sec_beats = float(section.get("length_beats", 16))
    sec_tempo = float(composition_context.get("tempo", 120)) if composition_context else 120.0

    try:
        await sse_queue.put({
            "type": "status",
            "message": f"Starting {instrument_name} / {sec_name}",
            "agentId": agent_id,
            "sectionName": sec_name,
        })

        # ── If bass, wait for the corresponding drum section (with timeout) ──
        if is_bass and section_signals:
            from app.config import settings as _cfg
            _bass_timeout = _cfg.bass_signal_wait_timeout
            logger.info(
                f"{child_log} ⏳ Waiting for drum section '{sec_name}' "
                f"(timeout={_bass_timeout}s)..."
            )
            _wait_start = asyncio.get_event_loop().time()
            try:
                drum_data = await asyncio.wait_for(
                    section_signals.wait_for(sec_name),
                    timeout=_bass_timeout,
                )
            except asyncio.TimeoutError:
                _wait_elapsed = asyncio.get_event_loop().time() - _wait_start
                logger.error(
                    f"{child_log} ⏰ Bass wait TIMED OUT after {_wait_elapsed:.1f}s — "
                    f"drum section '{sec_name}' never signaled. "
                    f"Proceeding without drum spine."
                )
                drum_data = None
            else:
                _wait_elapsed = asyncio.get_event_loop().time() - _wait_start
                if drum_data:
                    logger.info(
                        f"{child_log} ✅ Drum section '{sec_name}' ready after "
                        f"{_wait_elapsed:.1f}s "
                        f"({len(drum_data.get('drum_notes', []))} drum notes)"
                    )
                else:
                    logger.warning(
                        f"{child_log} ⚠️ Drum wait returned no data after "
                        f"{_wait_elapsed:.1f}s"
                    )

        # ── Bass: read drum telemetry for cross-instrument awareness ──
        if is_bass and section_state:
            drum_key = _state_key("Drums", sec_name)
            drum_telemetry = await section_state.get(drum_key)
            if drum_telemetry:
                composition_context = {
                    **(composition_context or {}),
                    "drum_telemetry": {
                        "energy_level": drum_telemetry.energy_level,
                        "density_score": drum_telemetry.density_score,
                        "groove_vector": drum_telemetry.groove_vector,
                        "kick_pattern_hash": drum_telemetry.kick_pattern_hash,
                        "rhythmic_complexity": drum_telemetry.rhythmic_complexity,
                    },
                }
                logger.info(
                    f"{child_log} 🎯 Drum telemetry injected: "
                    f"energy={drum_telemetry.energy_level:.2f} "
                    f"density={drum_telemetry.density_score:.2f}"
                )

        # ── Execute stori_add_midi_region ──
        logger.info(
            f"{child_log} 📌 Creating region: "
            f"startBeat={region_tc.params.get('startBeat')}, "
            f"durationBeats={region_tc.params.get('durationBeats')}, "
            f"name={region_tc.params.get('name')}"
        )
        region_params = dict(region_tc.params)
        region_params["trackId"] = track_id

        region_outcome = await _apply_single_tool_call(
            tc_id=region_tc.id or str(_uuid_mod.uuid4()),
            tc_name=region_tc.name,
            resolved_args=region_params,
            allowed_tool_names=allowed_tool_names,
            store=store,
            trace=trace,
            add_notes_failures=add_notes_failures,
            emit_sse=True,
        )
        await _emit(region_outcome)

        result.tool_results.append(region_outcome.tool_result)
        result.tool_call_records.append(
            {"tool": region_tc.name, "params": region_outcome.enriched_params}
        )
        result.tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": region_tc.id,
            "content": json.dumps(region_outcome.tool_result),
        })

        region_id = region_outcome.tool_result.get("regionId")
        if not region_id:
            result.error = f"Region creation failed for {sec_name}"
            logger.warning(f"⚠️ {child_log} {result.error}")
            if is_drum and section_signals:
                section_signals.signal_complete(sec_name)
            return result

        result.region_id = region_id

        # ── Execute stori_generate_midi ──
        logger.info(
            f"{child_log} 🎵 Generating MIDI: regionId={region_id}, "
            f"prompt={str(generate_tc.params.get('prompt', ''))[:80]}..."
        )
        _gen_start = asyncio.get_event_loop().time()
        gen_params = dict(generate_tc.params)
        gen_params["trackId"] = track_id
        gen_params["regionId"] = region_id

        gen_outcome = await _apply_single_tool_call(
            tc_id=generate_tc.id or str(_uuid_mod.uuid4()),
            tc_name=generate_tc.name,
            resolved_args=gen_params,
            allowed_tool_names=allowed_tool_names,
            store=store,
            trace=trace,
            add_notes_failures=add_notes_failures,
            emit_sse=True,
            composition_context=composition_context,
        )
        await _emit(gen_outcome)

        result.tool_results.append(gen_outcome.tool_result)
        result.tool_call_records.append(
            {"tool": generate_tc.name, "params": gen_outcome.enriched_params}
        )
        result.tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": generate_tc.id,
            "content": json.dumps(gen_outcome.tool_result),
        })

        _gen_elapsed = asyncio.get_event_loop().time() - _gen_start

        if gen_outcome.skipped:
            result.error = gen_outcome.tool_result.get("error", "Generation failed")
            logger.warning(
                f"⚠️ {child_log} Generate failed after {_gen_elapsed:.1f}s: {result.error}"
            )
            if is_drum and section_signals:
                section_signals.signal_complete(sec_name)
            return result

        result.notes_generated = gen_outcome.tool_result.get("notesAdded", 0)
        result.success = True
        logger.info(
            f"{child_log} ✅ Generated {result.notes_generated} notes in {_gen_elapsed:.1f}s"
        )

        # ── Extract generated notes from SSE events ──
        generated_notes: list[dict] = []
        for evt in gen_outcome.sse_events:
            if evt.get("name") == "stori_add_notes":
                generated_notes = evt.get("params", {}).get("notes", [])
                break

        # ── Drum signaling — signal bass with drum notes ──
        if is_drum and section_signals:
            section_signals.signal_complete(sec_name, drum_notes=generated_notes)
            logger.info(
                f"{child_log} 🥁 Signaled bass: {len(generated_notes)} drum notes ready"
            )

        # ── Compute and store musical telemetry ──
        if section_state and generated_notes:
            telemetry = compute_section_telemetry(
                notes=generated_notes,
                tempo=sec_tempo,
                instrument=instrument_name,
                section_name=sec_name,
                section_beats=sec_beats,
            )
            state_key = _state_key(instrument_name, sec_name)
            await section_state.set(state_key, telemetry)

        await sse_queue.put({
            "type": "status",
            "message": (
                f"{instrument_name} / {sec_name}: "
                f"{result.notes_generated} notes generated"
            ),
            "agentId": agent_id,
            "sectionName": sec_name,
        })

        # ── Optional refinement LLM call for expressive tools ──
        if result.success and llm and composition_context:
            logger.info(f"{child_log} 🎨 Checking expression refinement...")
            await _maybe_refine_expression(
                section=section,
                track_id=track_id,
                region_id=region_id,
                instrument_name=instrument_name,
                role=role,
                agent_id=agent_id,
                sec_name=sec_name,
                notes_generated=result.notes_generated,
                llm=llm,
                store=store,
                trace=trace,
                sse_queue=sse_queue,
                allowed_tool_names=allowed_tool_names,
                composition_context=composition_context,
                result=result,
                child_log=child_log,
            )

        _child_elapsed = asyncio.get_event_loop().time() - _child_start
        logger.info(
            f"{child_log} 🏁 Section child complete ({_child_elapsed:.1f}s): "
            f"success={result.success}, notes={result.notes_generated}"
        )
        return result

    except Exception as exc:
        _child_elapsed = asyncio.get_event_loop().time() - _child_start
        logger.exception(
            f"{child_log} 💥 Unhandled section error after {_child_elapsed:.1f}s: {exc}"
        )
        result.error = str(exc)
        if is_drum and section_signals:
            section_signals.signal_complete(sec_name)
        return result


_EXPR_BLOCK_RE = re.compile(
    r"^(MidiExpressiveness|Automation):.*?(?=\n\S|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def _extract_expressiveness_blocks(raw_prompt: str) -> str:
    """Pull MidiExpressiveness: and Automation: YAML blocks from the raw prompt."""
    matches = list(_EXPR_BLOCK_RE.finditer(raw_prompt))
    if not matches:
        return ""
    return "\n\n".join(m.group(0) for m in matches)


async def _maybe_refine_expression(
    section: dict[str, Any],
    track_id: str,
    region_id: str,
    instrument_name: str,
    role: str,
    agent_id: str,
    sec_name: str,
    notes_generated: int,
    llm: LLMClient,
    store: StateStore,
    trace: Any,
    sse_queue: asyncio.Queue[dict[str, Any]],
    allowed_tool_names: set[str],
    composition_context: dict[str, Any],
    result: SectionResult,
    child_log: str,
) -> None:
    """Add CC curves and pitch bends via a streamed LLM call.

    Only triggered when the STORI PROMPT includes MidiExpressiveness or
    Automation blocks.  Streams reasoning (CoT) as SSE events tagged with
    ``agentId`` + ``sectionName`` so the GUI can display per-section
    musical thinking in real time.
    """
    from app.core.tools import ALL_TOOLS

    style = composition_context.get("style", "")
    tempo = composition_context.get("tempo", 120)
    key = composition_context.get("key", "C")
    sec_bars = max(1, int(section.get("length_beats", 16)) // 4)
    sec_start = section.get("start_beat", 0)

    prompt_text = composition_context.get("_raw_prompt", "")
    has_expressiveness = any(
        kw in prompt_text.lower()
        for kw in ("midiexpressiveness:", "automation:", "cc_curves:", "pitch_bend:")
    )
    if not has_expressiveness:
        return

    expr_tools = [
        t for t in ALL_TOOLS
        if t["function"]["name"] in _EXPRESSIVENESS_TOOLS
    ]
    if not expr_tools:
        return

    expr_blocks = _extract_expressiveness_blocks(prompt_text)

    refine_prompt = (
        f"You are a MIDI expression agent for the {sec_name.upper()} section "
        f"of the {instrument_name} track.\n\n"
        f"Context: {style} | {tempo} BPM | {key}\n"
        f"Section: {sec_bars} bars starting at beat {sec_start}, "
        f"{notes_generated} notes generated.\n"
        f"trackId='{track_id}', regionId='{region_id}'.\n\n"
    )
    if expr_blocks:
        refine_prompt += (
            f"The composer specified these expressiveness instructions:\n"
            f"```\n{expr_blocks}\n```\n\n"
        )
    refine_prompt += (
        "REASONING: Briefly explain (1-2 sentences) what expression you'll add "
        "and why it fits this section's energy.\n"
        "Then make 1-3 tool calls for CC curves and/or pitch bends that match "
        "the instructions above."
    )

    await sse_queue.put({
        "type": "status",
        "message": f"Adding expression to {instrument_name} / {sec_name}",
        "agentId": agent_id,
        "sectionName": sec_name,
    })

    try:
        from app.config import settings

        resp_tool_calls: list[dict[str, Any]] = []
        rbuf = ReasoningBuffer()

        async for chunk in llm.chat_completion_stream(
            messages=[
                {"role": "system", "content": refine_prompt},
                {"role": "user", "content": "Add expression now."},
            ],
            tools=expr_tools,
            tool_choice="auto",
            max_tokens=1000,
            reasoning_fraction=settings.agent_reasoning_fraction,
        ):
            ct = chunk.get("type")
            if ct == "reasoning_delta":
                text = chunk.get("text", "")
                if text:
                    word = rbuf.add(text)
                    if word:
                        await sse_queue.put({
                            "type": "reasoning",
                            "content": word,
                            "agentId": agent_id,
                            "sectionName": sec_name,
                        })
            elif ct == "content_delta":
                flush = rbuf.flush()
                if flush:
                    await sse_queue.put({
                        "type": "reasoning",
                        "content": flush,
                        "agentId": agent_id,
                        "sectionName": sec_name,
                    })
            elif ct == "done":
                flush = rbuf.flush()
                if flush:
                    await sse_queue.put({
                        "type": "reasoning",
                        "content": flush,
                        "agentId": agent_id,
                        "sectionName": sec_name,
                    })
                resp_tool_calls = chunk.get("tool_calls", [])

        tool_calls: list[ToolCall] = []
        for tc_raw in resp_tool_calls:
            try:
                args = tc_raw.get("function", {}).get("arguments", "{}")
                if isinstance(args, str):
                    args = json.loads(args) if args else {}
                tool_calls.append(ToolCall(
                    id=tc_raw.get("id", ""),
                    name=tc_raw.get("function", {}).get("name", ""),
                    params=args,
                ))
            except Exception as parse_err:
                logger.error(f"{child_log} Error parsing expression tool call: {parse_err}")

        add_notes_failures: dict[str, int] = {}
        for tc in tool_calls:
            params = dict(tc.params)
            params["trackId"] = track_id
            params["regionId"] = region_id

            outcome = await _apply_single_tool_call(
                tc_id=tc.id or str(_uuid_mod.uuid4()),
                tc_name=tc.name,
                resolved_args=params,
                allowed_tool_names=allowed_tool_names,
                store=store,
                trace=trace,
                add_notes_failures=add_notes_failures,
                emit_sse=True,
            )
            for evt in outcome.sse_events:
                if evt.get("type") in _AGENT_TAGGED_EVENTS:
                    evt = {**evt, "agentId": agent_id, "sectionName": sec_name}
                await sse_queue.put(evt)

            if not outcome.skipped:
                result.tool_results.append(outcome.tool_result)
                result.tool_call_records.append(
                    {"tool": tc.name, "params": outcome.enriched_params}
                )

        if tool_calls:
            logger.info(
                f"{child_log} ✨ Expression refinement: {len(tool_calls)} tool calls applied"
            )
    except Exception as exc:
        logger.warning(
            f"⚠️ {child_log} Expression refinement failed (non-fatal): {exc}"
        )
