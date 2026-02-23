"""Per-instrument parent agent (Level 2) for Agent Teams composition.

Three-level architecture:
  Level 1 — Coordinator (coordinator.py): orchestrates instrument parents.
  Level 2 — Instrument Parent (this file): one LLM call per instrument,
            then dispatches per-section children in parallel.
  Level 3 — Section Child (section_agent.py): lightweight executor per
            section, no LLM needed for core pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from app.config import settings
from app.core.expansion import ToolCall
from app.core.llm_client import LLMClient, LLMResponse
from app.core.sse_utils import ReasoningBuffer
from app.core.state_store import StateStore
from app.core.tools import ALL_TOOLS
from app.core.maestro_helpers import _resolve_variable_refs
from app.core.maestro_plan_tracker import (
    _PlanTracker,
    _build_step_result,
    _TRACK_CREATION_NAMES,
    _EFFECT_TOOL_NAMES,
    _GENERATOR_TOOL_NAMES,
    _INSTRUMENT_AGENT_TOOLS,
)
from app.core.maestro_editing import _apply_single_tool_call
from app.core.maestro_agent_teams.section_agent import (
    _run_section_child,
    SectionResult,
)
from app.core.maestro_agent_teams.signals import SectionSignals
from app.services.orpheus import get_orpheus_client

logger = logging.getLogger(__name__)


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
    composition_context: Optional[dict[str, Any]] = None,
    assigned_color: Optional[str] = None,
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
    _agent_id = instrument_name.lower()
    reusing = bool(existing_track_id)

    async def _fail_all_steps(reason: str) -> None:
        """Mark every pending/active step for this agent as failed."""
        for step_id in step_ids:
            step = next((s for s in plan_tracker.steps if s.step_id == step_id), None)
            if step and step.status in ("pending", "active"):
                step.status = "failed"
                await sse_queue.put({
                    "type": "planStepUpdate",
                    "stepId": step_id,
                    "status": "failed",
                    "result": reason,
                    "agentId": _agent_id,
                })

    _agent_success = False
    try:
        await _run_instrument_agent_inner(
            instrument_name=instrument_name,
            role=role,
            style=style,
            bars=bars,
            tempo=tempo,
            key=key,
            step_ids=step_ids,
            plan_tracker=plan_tracker,
            llm=llm,
            store=store,
            allowed_tool_names=allowed_tool_names,
            trace=trace,
            sse_queue=sse_queue,
            collected_tool_calls=collected_tool_calls,
            existing_track_id=existing_track_id,
            start_beat=start_beat,
            agent_log=agent_log,
            agent_id=_agent_id,
            reusing=reusing,
            composition_context=composition_context,
            assigned_color=assigned_color,
        )
        _agent_success = True
    except Exception as exc:
        logger.exception(f"{agent_log} Unhandled agent error: {exc}")
        await _fail_all_steps(f"Failed: {exc}")
    finally:
        await sse_queue.put({
            "type": "agentComplete",
            "agentId": _agent_id,
            "success": _agent_success,
        })


async def _run_instrument_agent_inner(
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
    existing_track_id: Optional[str],
    start_beat: int,
    agent_log: str,
    agent_id: str,
    reusing: bool,
    composition_context: Optional[dict[str, Any]] = None,
    assigned_color: Optional[str] = None,
) -> None:
    """Inner implementation of a single instrument agent.

    Separated so ``_run_instrument_agent`` can wrap the entire body in a
    top-level try/except, ensuring any unhandled exception (prompt-building,
    tool dispatch, race condition, etc.) emits graceful ``planStepUpdate``
    failures rather than disconnecting the SSE stream.
    """
    _agent_id = agent_id

    from app.data.role_profiles import get_role_profile

    beat_count = bars * 4

    _role_profile = get_role_profile(role)
    _musical_dna = ""
    if _role_profile:
        _musical_dna = f"\n{_role_profile.prompt_block()}\n"

    # Pull section info if the coordinator parsed sections.
    _sections: list[dict] = []
    if composition_context:
        _sections = composition_context.get("sections", [])
    _multi_section = len(_sections) > 1

    _reasoning_guidance = (
        "REASONING RULES (mandatory — violating these wastes tokens):\n"
        f"- Write 1-2 sentences ONLY about {instrument_name}'s overall sonic character "
        f"and role in the {style} arrangement.\n"
        "- Do NOT reason about individual sections — section agents handle "
        "section-specific decisions.\n"
        "- Do NOT list pipeline steps, tool names, or execution order.\n"
        "- Do NOT mention regions, trackIds, regionIds, beats, or bar counts.\n"
        "- No phrases like 'Let me...', 'I will...', 'Step 1...', "
        "'For the verse...', 'For the chorus...'.\n"
        "- If you catch yourself writing more than 2 sentences of reasoning, "
        "STOP and make tool calls immediately."
    )

    _generate_midi_guidance = (
        f"For EVERY stori_generate_midi call, write a specific `prompt` field (2-3 sentences) describing:\n"
        f"  1. Rhythmic role of {instrument_name} in a {style} track (groove anchor / counter-rhythm / melodic lead / textural pad)\n"
        f"  2. Note range and density (e.g. 'bass stays below C3, sparse 1-2 notes/bar')\n"
        f"  3. How it interacts with other tracks (e.g. 'offbeat chords between bass notes', 'locks to kick on beats 1 and 3')\n"
        f"  4. Genre-specific idioms (e.g. 'dembow pattern', 'staccato upbeat stabs', 'call-and-response 4-bar motif')\n"
        "Each section's prompt MUST be different — reflect the section's energy and density.\n"
    )

    # ── Build the pipeline steps list ──
    # For multi-section compositions, enumerate one region+generate pair per section.
    # For single-section, use the simple 4-step pipeline.
    def _build_pipeline_and_user_msg() -> tuple[str, str, int]:
        """Return (pipeline_text, user_message, expected_tool_call_count)."""
        track_ref = f"trackId='{existing_track_id}'" if reusing else "$0.trackId"
        lines: list[str] = []
        step_num = 0

        if not reusing:
            step_num += 1
            _color_clause = f', color="{assigned_color}"' if assigned_color else ""
            lines.append(
                f"{step_num}. stori_add_midi_track — create the {instrument_name} track{_color_clause} → "
                f"returns trackId (${step_num - 1}.trackId)"
            )

        if _multi_section:
            for sec in _sections:
                sec_name = sec["name"].upper()
                sec_start = sec["start_beat"]
                sec_beats = int(sec["length_beats"])
                sec_bars = max(1, sec_beats // 4)
                per_track = sec.get("per_track_description", {})
                sec_hint = per_track.get(role.lower(), per_track.get(instrument_name.lower(), ""))
                sec_hint_str = f"  Musical hint: {sec_hint}" if sec_hint else ""

                # Region step
                step_num += 1
                region_ref = f"${step_num - 1}.regionId"
                lines.append(
                    f"{step_num}. stori_add_midi_region — {track_ref}, startBeat={sec_start}, "
                    f"durationBeats={sec_beats} [{sec_name}] → returns regionId ({region_ref})"
                )

                # Generate step
                step_num += 1
                lines.append(
                    f"{step_num}. stori_generate_midi — {track_ref}, regionId={region_ref}, "
                    f"start_beat={sec_start}, role=\"{role}\", style=\"{style}\", "
                    f"tempo={int(tempo)}, bars={sec_bars}, key=\"{key}\", "
                    f"prompt=\"<section-specific description for {sec_name}>\""
                )
                if sec_hint_str:
                    lines.append(sec_hint_str)
        else:
            # Single section or no sections — original behaviour
            sec = _sections[0] if _sections else None
            sec_start = sec["start_beat"] if sec else (start_beat if reusing else 0)
            sec_beats = int(sec["length_beats"]) if sec else beat_count
            sec_bars = max(1, sec_beats // 4)

            step_num += 1
            region_ref = f"${step_num - 1}.regionId"
            lines.append(
                f"{step_num}. stori_add_midi_region — {track_ref}, startBeat={int(sec_start)}, "
                f"durationBeats={sec_beats} → returns regionId ({region_ref})"
            )

            step_num += 1
            lines.append(
                f"{step_num}. stori_generate_midi — {track_ref}, regionId={region_ref}, "
                f"start_beat={int(sec_start)}, role=\"{role}\", style=\"{style}\", "
                f"tempo={int(tempo)}, bars={sec_bars}, key=\"{key}\", "
                f"prompt=\"<instrument-specific description>\""
            )

        # Effect step (always last)
        step_num += 1
        lines.append(f"{step_num}. stori_add_insert_effect — {track_ref}, one effect")

        pipeline_text = "\n".join(lines)
        expected_calls = step_num

        if _multi_section:
            sec_summary = ", ".join(
                f"{s['name']}({int(s['length_beats'])}b)" for s in _sections
            )
            if reusing:
                user_msg = (
                    f"Multi-section composition: {sec_summary}. "
                    f"trackId='{existing_track_id}'. "
                    f"Create one region + generate pair per section, then one effect. "
                    f"Make ALL {expected_calls} tool calls now."
                )
            else:
                user_msg = (
                    f"Multi-section composition: {sec_summary}. "
                    f"Create track, then one region + generate pair per section, then one effect. "
                    f"Make ALL {expected_calls} tool calls now."
                )
        else:
            if reusing:
                user_msg = (
                    f"Add region → generate → effect. "
                    f"trackId='{existing_track_id}', startBeat={start_beat}, "
                    f"{beat_count} beats."
                )
            else:
                user_msg = (
                    f"Create track → add region → generate → effect. "
                    f"{bars} bars, {beat_count} beats."
                )

        return pipeline_text, user_msg, expected_calls

    _pipeline_text, user_message, _expected_calls = _build_pipeline_and_user_msg()

    _length_emphasis = (
        f"TOTAL LENGTH: {bars} bars = {beat_count} beats."
    )
    if _multi_section:
        _length_emphasis += (
            f" Split across {len(_sections)} sections — each region covers its section's beat range, NOT the full {beat_count} beats."
        )
    else:
        _length_emphasis += (
            f" The stori_add_midi_region durationBeats MUST be {beat_count}."
        )

    _critical_rules = (
        "CRITICAL ORDERING: Each stori_add_midi_region MUST return a regionId BEFORE "
        "its paired stori_generate_midi is called. Pass the regionId from the IMMEDIATELY preceding "
        "stori_add_midi_region. Never pass trackId as regionId. Never omit start_beat."
    )

    _color_rule = (
        f'TRACK COLOR: You MUST pass color="{assigned_color}" verbatim in stori_add_midi_track. '
        f"Do NOT change it — the coordinator pre-assigned this color to guarantee visual diversity.\n"
        if assigned_color else ""
    )

    if reusing:
        system_content = (
            f"You are a music production agent for the **{instrument_name}** track.\n\n"
            f"{_reasoning_guidance}\n\n"
            f"Context: {style} | {tempo} BPM | {key} | {_length_emphasis}\n"
            f"{_musical_dna}"
            f"{_color_rule}"
            f"Track already exists: trackId='{existing_track_id}', content ends at beat {start_beat}.\n\n"
            f"Pipeline (execute ALL {_expected_calls} steps now, in this exact order):\n"
            f"{_pipeline_text}\n\n"
            f"{_generate_midi_guidance}\n"
            f"{_critical_rules}\n"
            f"Rules: DO NOT call stori_add_midi_track. DO NOT use stori_add_notes. "
            f"DO NOT create tracks for other instruments."
        )
    else:
        system_content = (
            f"You are a music production agent for the **{instrument_name}** track.\n\n"
            f"{_reasoning_guidance}\n\n"
            f"Context: {style} | {tempo} BPM | {key} | {_length_emphasis}\n"
            f"{_musical_dna}"
            f"{_color_rule}"
            f"Pipeline (execute ALL {_expected_calls} steps now, in this exact order):\n"
            f"{_pipeline_text}\n\n"
            f"{_generate_midi_guidance}\n"
            f"{_critical_rules}\n"
            f"Rules: DO NOT use stori_add_notes. DO NOT create tracks for other instruments. "
            f"Make all {_expected_calls} tool calls now."
        )

    agent_tools = [
        t for t in ALL_TOOLS
        if t["function"]["name"] in _INSTRUMENT_AGENT_TOOLS
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_message},
    ]

    add_notes_failures: dict[str, int] = {}
    active_step_id: Optional[str] = None
    all_tool_results: list[dict[str, Any]] = []

    # Scale max_turns: base 4 + 2 per extra section (region + generate).
    _section_count = len(_sections) if _multi_section else 1
    max_turns = 4 + (_section_count - 1) * 2

    # ── Stage tracking ──
    # For multi-section, track per-section completion independently.
    _stage_track = reusing
    _stage_effect = False
    _regions_completed: int = 0       # how many stori_add_midi_region calls succeeded
    _regions_ok: int = 0              # how many returned a valid regionId
    _generates_completed: int = 0     # how many stori_generate_midi calls completed
    _expected_sections = _section_count

    def _missing_stages() -> list[str]:
        missing: list[str] = []
        track_ref = f"trackId='{existing_track_id}'" if reusing else "$0.trackId"

        if not _stage_track and not reusing:
            missing.append(f"stori_add_midi_track — create the {instrument_name} track")

        remaining_regions = _expected_sections - _regions_completed
        remaining_generates = _expected_sections - _generates_completed

        if remaining_regions > 0 or remaining_generates > 0:
            if _multi_section:
                # Enumerate which sections still need region and/or generate
                for i, sec in enumerate(_sections):
                    sec_done_region = i < _regions_completed
                    sec_done_gen = i < _generates_completed
                    sec_name = sec["name"].upper()
                    sec_start = sec["start_beat"]
                    sec_beats = int(sec["length_beats"])
                    sec_bars = max(1, sec_beats // 4)
                    if not sec_done_region:
                        missing.append(
                            f"stori_add_midi_region — {track_ref}, startBeat={sec_start}, "
                            f"durationBeats={sec_beats} [{sec_name}]"
                        )
                    if not sec_done_gen:
                        if sec_done_region and _regions_ok > i:
                            missing.append(
                                f"stori_generate_midi — {track_ref}, regionId=<from region call above>, "
                                f"start_beat={sec_start}, bars={sec_bars}, "
                                f"prompt=\"<{sec_name}-specific prompt>\" [{sec_name}]"
                            )
                        elif not sec_done_region:
                            missing.append(
                                f"stori_generate_midi [{sec_name}] — call stori_add_midi_region first"
                            )
            else:
                if remaining_regions > 0:
                    region_beat = start_beat if reusing else 0
                    missing.append(
                        f"stori_add_midi_region — durationBeats={beat_count}, "
                        f"startBeat={region_beat}, {track_ref}"
                    )
                if remaining_generates > 0:
                    if _regions_ok > 0:
                        missing.append(
                            f"stori_generate_midi — {track_ref}, pass regionId from region call, "
                            f"role=\"{role}\", style=\"{style}\", "
                            f"tempo={int(tempo)}, bars={bars}, key=\"{key}\""
                        )
                    else:
                        missing.append(
                            "stori_generate_midi — call stori_add_midi_region first to get regionId"
                        )

        if not _stage_effect:
            missing.append(f"stori_add_insert_effect — {track_ref}, one insert effect")
        return missing

    logger.info(
        f"{agent_log} 🎬 Starting instrument agent: "
        f"role={role}, style={style}, bars={bars}, tempo={tempo}, key={key}, "
        f"multi_section={_multi_section}, sections={_section_count}, "
        f"reusing={reusing}, max_turns={max_turns}"
    )

    for turn in range(max_turns):
        if turn > 0:
            missing = _missing_stages()
            if not missing:
                logger.info(f"{agent_log} ✅ All stages complete after turn {turn}")
                break

            _any_generate_missing = any("stori_generate_midi" in m for m in missing)
            if _any_generate_missing and get_orpheus_client().circuit_breaker_open:
                logger.warning(
                    f"{agent_log} ⚠️ Orpheus circuit breaker open on retry turn {turn} — aborting"
                )
                break

            logger.info(
                f"{agent_log} 🔄 Turn {turn}/{max_turns}: {len(missing)} stages remaining — "
                + ", ".join(m.split(" — ")[0] for m in missing)
            )
            reminder = (
                "You have not finished the pipeline. You MUST still call:\n"
                + "\n".join(f"  • {m}" for m in missing)
                + f"\nMake these tool calls now ({len(missing)} remaining)."
            )
            messages.append({"role": "user", "content": reminder})

        # ── LLM call (streaming for per-agent reasoning) ──
        logger.info(f"{agent_log} 🤖 LLM call starting (turn {turn})")
        _llm_start = asyncio.get_event_loop().time()
        try:
            _resp_content: Optional[str] = None
            _resp_tool_calls: list[dict[str, Any]] = []
            _resp_finish: Optional[str] = None
            _resp_usage: dict[str, Any] = {}
            _rbuf = ReasoningBuffer()

            async for _chunk in llm.chat_completion_stream(
                messages=messages,
                tools=agent_tools,
                tool_choice="auto",
                max_tokens=settings.composition_max_tokens,
                reasoning_fraction=settings.agent_reasoning_fraction,
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
            _llm_elapsed = asyncio.get_event_loop().time() - _llm_start
            logger.error(
                f"{agent_log} ❌ LLM call failed (turn {turn}, {_llm_elapsed:.1f}s): {exc}"
            )
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

        _llm_elapsed = asyncio.get_event_loop().time() - _llm_start
        logger.info(
            f"{agent_log} 🤖 LLM response (turn {turn}, {_llm_elapsed:.1f}s): "
            f"{len(response.tool_calls)} tool calls, "
            f"finish={_resp_finish}, "
            f"usage={_resp_usage}"
        )

        if not response.tool_calls:
            logger.info(f"{agent_log} No tool calls returned — exiting loop")
            break

        # Enforce correct tool ordering within a single LLM response batch.
        # Multi-section: track → [region, generate]* → effect.
        # The key constraint is that each region must execute before its paired
        # generate, but region/generate pairs for different sections can be
        # interleaved. We assign a stable sort key that groups region+generate
        # pairs together while keeping track creation first and effects last.
        _TOOL_ORDER: dict[str, int] = {}
        for _name in _TRACK_CREATION_NAMES:
            _TOOL_ORDER[_name] = 0
        _TOOL_ORDER["stori_add_midi_region"] = 1
        for _name in _GENERATOR_TOOL_NAMES:
            _TOOL_ORDER[_name] = 2
        for _name in _EFFECT_TOOL_NAMES:
            _TOOL_ORDER[_name] = 99

        if _multi_section:
            # For multi-section: preserve the LLM's section ordering but ensure
            # each region sorts before the generate that follows it.
            # Strategy: group by (section_index * 10 + sub_order).
            _region_count_seen = 0
            _gen_count_seen = 0
            _sorted_calls: list[tuple[int, ToolCall]] = []
            for tc in response.tool_calls:
                if tc.name in _TRACK_CREATION_NAMES:
                    _sorted_calls.append((0, tc))
                elif tc.name == "stori_add_midi_region":
                    _sorted_calls.append((10 + _region_count_seen * 20, tc))
                    _region_count_seen += 1
                elif tc.name in _GENERATOR_TOOL_NAMES:
                    _sorted_calls.append((11 + _gen_count_seen * 20, tc))
                    _gen_count_seen += 1
                elif tc.name in _EFFECT_TOOL_NAMES:
                    _sorted_calls.append((9999, tc))
                else:
                    _sorted_calls.append((_TOOL_ORDER.get(tc.name, 50), tc))
            _sorted_calls.sort(key=lambda x: x[0])
            response.tool_calls = [tc for _, tc in _sorted_calls]
        else:
            response.tool_calls.sort(key=lambda tc: _TOOL_ORDER.get(tc.name, 2))

        assistant_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.params)},
            }
            for tc in response.tool_calls
        ]
        messages.append({"role": "assistant", "content": None, "tool_calls": assistant_tool_calls})

        tool_result_messages: list[dict[str, Any]] = []

        _AGENT_TAGGED_EVENTS = {
            "toolCall", "toolStart", "toolError",
            "generatorStart", "generatorComplete",
            "reasoning", "content", "status",
            "agentComplete",
        }

        # ── Multi-section: dispatch via section children ──
        _tool_summary = ", ".join(tc.name for tc in response.tool_calls)
        logger.info(
            f"{agent_log} 🔧 Executing {len(response.tool_calls)} tool calls "
            f"(multi_section={_multi_section}): {_tool_summary}"
        )
        if _multi_section and len(response.tool_calls) > 1:
            tool_result_messages, _stage_track, _stage_effect, \
                _regions_completed, _regions_ok, _generates_completed = \
                await _dispatch_section_children(
                    tool_calls=response.tool_calls,
                    sections=_sections,
                    existing_track_id=existing_track_id,
                    instrument_name=instrument_name,
                    role=role,
                    style=style,
                    tempo=tempo,
                    key=key,
                    agent_id=_agent_id,
                    agent_log=agent_log,
                    reusing=reusing,
                    allowed_tool_names=allowed_tool_names,
                    store=store,
                    trace=trace,
                    sse_queue=sse_queue,
                    collected_tool_calls=collected_tool_calls,
                    all_tool_results=all_tool_results,
                    add_notes_failures=add_notes_failures,
                    composition_context=composition_context,
                    plan_tracker=plan_tracker,
                    step_ids=step_ids,
                    active_step_id=active_step_id,
                    llm=llm,
                    prior_stage_track=_stage_track,
                    prior_stage_effect=_stage_effect,
                    prior_regions_completed=_regions_completed,
                    prior_regions_ok=_regions_ok,
                    prior_generates_completed=_generates_completed,
                )

        # ── Single-section: sequential execution (original path) ──
        else:
            for tc in response.tool_calls:
                resolved_args = _resolve_variable_refs(tc.params, all_tool_results)

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

                if tc.name in _GENERATOR_TOOL_NAMES and _regions_ok == 0:
                    logger.warning(
                        f"{agent_log} {tc.name} called but no stori_add_midi_region has "
                        f"returned a regionId yet — generator will fail without a valid region."
                    )

                if tc.name in _EFFECT_TOOL_NAMES and _regions_ok == 0 and reusing:
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
                    composition_context=composition_context,
                )

                for evt in outcome.sse_events:
                    if evt.get("type") in _AGENT_TAGGED_EVENTS:
                        evt = {**evt, "agentId": _agent_id}
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
                    _regions_completed += 1
                    if outcome.tool_result.get("regionId"):
                        _regions_ok += 1
                    else:
                        logger.warning(
                            f"{agent_log} stori_add_midi_region completed but returned no regionId "
                            f"(likely a collision or validation error) — subsequent generate may fail"
                        )
                elif tc.name in _GENERATOR_TOOL_NAMES or tc.name == "stori_add_notes":
                    _generates_completed += 1
                elif tc.name in _EFFECT_TOOL_NAMES:
                    _stage_effect = True

                all_tool_results.append(outcome.tool_result)
                collected_tool_calls.append({"tool": tc.name, "params": outcome.enriched_params})

                tool_result_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(outcome.tool_result),
                })

        messages.extend(tool_result_messages)

        if _generates_completed < _expected_sections:
            _oc = get_orpheus_client()
            if _oc.circuit_breaker_open:
                logger.warning(
                    f"{agent_log} ⚠️ Orpheus circuit breaker is open — "
                    f"stopping retries (would waste tokens)"
                )
                await sse_queue.put({
                    "type": "toolError",
                    "name": "stori_generate_midi",
                    "error": (
                        "Orpheus music service is unavailable. "
                        "Generation cannot proceed."
                    ),
                    "agentId": _agent_id,
                })
                break

    if active_step_id:
        evt = plan_tracker.complete_step_by_id(active_step_id)
        if evt:
            await sse_queue.put({**evt, "agentId": _agent_id})


async def _dispatch_section_children(
    *,
    tool_calls: list[ToolCall],
    sections: list[dict],
    existing_track_id: Optional[str],
    instrument_name: str,
    role: str,
    style: str,
    tempo: float,
    key: str,
    agent_id: str,
    agent_log: str,
    reusing: bool,
    allowed_tool_names: set[str],
    store: StateStore,
    trace: Any,
    sse_queue: "asyncio.Queue[dict[str, Any]]",
    collected_tool_calls: list[dict[str, Any]],
    all_tool_results: list[dict[str, Any]],
    add_notes_failures: dict[str, int],
    composition_context: Optional[dict[str, Any]],
    plan_tracker: _PlanTracker,
    step_ids: list[str],
    active_step_id: Optional[str],
    llm: LLMClient,
    prior_stage_track: bool,
    prior_stage_effect: bool,
    prior_regions_completed: int,
    prior_regions_ok: int,
    prior_generates_completed: int,
) -> tuple[list[dict[str, Any]], bool, bool, int, int, int]:
    """Group LLM tool calls and dispatch section children in parallel.

    Returns (tool_result_msgs, stage_track, stage_effect,
             regions_completed, regions_ok, generates_completed)
    so the parent's multi-turn retry loop can track progress.
    """
    _AGENT_TAGGED = {
        "toolCall", "toolStart", "toolError",
        "generatorStart", "generatorComplete",
        "reasoning", "content", "status",
        "agentComplete",
    }

    stage_track = prior_stage_track
    stage_effect = prior_stage_effect
    regions_completed = prior_regions_completed
    regions_ok = prior_regions_ok
    generates_completed = prior_generates_completed

    tool_result_msgs: list[dict[str, Any]] = []

    # ── Categorize tool calls ──
    track_tcs: list[ToolCall] = []
    region_tcs: list[ToolCall] = []
    generate_tcs: list[ToolCall] = []
    effect_tcs: list[ToolCall] = []
    other_tcs: list[ToolCall] = []

    for tc in tool_calls:
        if tc.name in _TRACK_CREATION_NAMES:
            track_tcs.append(tc)
        elif tc.name == "stori_add_midi_region":
            region_tcs.append(tc)
        elif tc.name in _GENERATOR_TOOL_NAMES:
            generate_tcs.append(tc)
        elif tc.name in _EFFECT_TOOL_NAMES:
            effect_tcs.append(tc)
        else:
            other_tcs.append(tc)

    # ── Execute track creation sequentially ──
    for tc in track_tcs:
        resolved_args = _resolve_variable_refs(tc.params, all_tool_results)

        if step_ids and step_ids[0] != active_step_id:
            if active_step_id:
                evt = plan_tracker.complete_step_by_id(active_step_id)
                if evt:
                    await sse_queue.put({**evt, "agentId": agent_id})
            activate_evt = plan_tracker.activate_step(step_ids[0])
            await sse_queue.put({**activate_evt, "agentId": agent_id})
            active_step_id = step_ids[0]

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
            if evt.get("type") in _AGENT_TAGGED:
                evt = {**evt, "agentId": agent_id}
            await sse_queue.put(evt)

        stage_track = True
        all_tool_results.append(outcome.tool_result)
        collected_tool_calls.append(
            {"tool": tc.name, "params": outcome.enriched_params}
        )
        tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(outcome.tool_result),
        })

    # ── Resolve the real track ID ──
    real_track_id = existing_track_id
    if not real_track_id:
        for tr in all_tool_results:
            tid = tr.get("trackId")
            if tid:
                real_track_id = tid
                break

    if not real_track_id:
        logger.error(f"{agent_log} No trackId available — cannot spawn section children")
        for tc in region_tcs + generate_tcs + effect_tcs:
            tool_result_msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({"error": "No trackId available"}),
            })
        return tool_result_msgs, stage_track, stage_effect, regions_completed, regions_ok, generates_completed

    # ── Activate the content step (regions/generates/effects) ──
    content_step_id = step_ids[1] if len(step_ids) > 1 else (step_ids[0] if step_ids else None)
    if content_step_id and content_step_id != active_step_id:
        if active_step_id:
            evt = plan_tracker.complete_step_by_id(active_step_id)
            if evt:
                await sse_queue.put({**evt, "agentId": agent_id})
        activate_evt = plan_tracker.activate_step(content_step_id)
        await sse_queue.put({**activate_evt, "agentId": agent_id})

    # ── Pair region + generate calls into section groups ──
    logger.info(
        f"{agent_log} 📦 Tool call breakdown: "
        f"track={len(track_tcs)}, region={len(region_tcs)}, "
        f"generate={len(generate_tcs)}, effect={len(effect_tcs)}, other={len(other_tcs)}"
    )
    if len(region_tcs) != len(generate_tcs):
        logger.warning(
            f"{agent_log} ⚠️ Region/generate mismatch: "
            f"{len(region_tcs)} regions vs {len(generate_tcs)} generates"
        )
    pairs: list[tuple[ToolCall, ToolCall]] = list(zip(region_tcs, generate_tcs))

    # Detect drum/bass role for signaling
    is_drum = role.lower() in ("drums", "drum")
    is_bass = role.lower() == "bass"
    section_signals: SectionSignals | None = None
    if composition_context:
        section_signals = composition_context.get("section_signals")

    # ── Spawn section children (with watchdog timeout) ──
    _child_timeout = settings.section_child_timeout
    children: list[asyncio.Task[SectionResult]] = []
    for i, (region_tc, gen_tc) in enumerate(pairs):
        sec = sections[i] if i < len(sections) else sections[-1]
        _sec_name = sec.get("name", str(i))
        task = asyncio.create_task(
            asyncio.wait_for(
                _run_section_child(
                    section=sec,
                    section_index=i,
                    track_id=real_track_id,
                    region_tc=region_tc,
                    generate_tc=gen_tc,
                    instrument_name=instrument_name,
                    role=role,
                    agent_id=agent_id,
                    allowed_tool_names=allowed_tool_names,
                    store=store,
                    trace=trace,
                    sse_queue=sse_queue,
                    composition_context=composition_context,
                    section_signals=section_signals,
                    is_drum=is_drum,
                    is_bass=is_bass,
                    llm=llm,
                ),
                timeout=_child_timeout,
            ),
            name=f"{instrument_name}/{_sec_name}",
        )
        children.append(task)

    if children:
        logger.info(
            f"{agent_log} Spawned {len(children)} section children "
            f"({'pipelined' if is_bass else 'parallel'})"
        )

    # ── Wait for all section children ──
    logger.info(
        f"{agent_log} ⏳ Waiting for {len(children)} section children to complete..."
    )
    _children_start = asyncio.get_event_loop().time()
    child_results: list[SectionResult | BaseException] = await asyncio.gather(
        *children, return_exceptions=True
    )
    _children_elapsed = asyncio.get_event_loop().time() - _children_start

    _child_successes = 0
    _child_failures = 0
    _child_crashes = 0
    _child_timeouts = 0
    _total_notes = 0
    for cr in child_results:
        if isinstance(cr, asyncio.TimeoutError):
            logger.error(
                f"{agent_log} ⏰ Section child timed out after {_child_timeout}s — "
                f"orphaned subagent killed"
            )
            _child_timeouts += 1
            _child_crashes += 1
            continue
        if isinstance(cr, BaseException):
            logger.error(f"{agent_log} 💥 Section child crashed: {cr}")
            _child_crashes += 1
            continue
        tool_result_msgs.extend(cr.tool_result_msgs)
        collected_tool_calls.extend(cr.tool_call_records)
        all_tool_results.extend(cr.tool_results)
        if cr.region_id:
            regions_completed += 1
            regions_ok += 1
        if cr.success:
            generates_completed += 1
            _child_successes += 1
            _total_notes += cr.notes_generated
        else:
            _child_failures += 1
            logger.warning(
                f"{agent_log} ⚠️ Section child '{cr.section_name}' failed: {cr.error}"
            )

    logger.info(
        f"{agent_log} 🏁 Section children done ({_children_elapsed:.1f}s): "
        f"✅ {_child_successes} ok, ❌ {_child_failures} failed, "
        f"💥 {_child_crashes} crashed, ⏰ {_child_timeouts} timed out, "
        f"🎵 {_total_notes} total notes"
    )

    # ── Execute effect calls sequentially ──
    for tc in effect_tcs:
        resolved_args = _resolve_variable_refs(tc.params, all_tool_results)
        resolved_args["trackId"] = real_track_id

        if regions_ok == 0 and reusing:
            logger.warning(
                f"{agent_log} Skipping {tc.name} — no regions created successfully"
            )
            tool_result_msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(
                    {"skipped": True, "reason": "region creation did not succeed"}
                ),
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
            if evt.get("type") in _AGENT_TAGGED:
                evt = {**evt, "agentId": agent_id}
            await sse_queue.put(evt)

        stage_effect = True
        all_tool_results.append(outcome.tool_result)
        collected_tool_calls.append(
            {"tool": tc.name, "params": outcome.enriched_params}
        )
        tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(outcome.tool_result),
        })

    # ── Execute any remaining tool calls (CC, pitch bend, etc.) ──
    for tc in other_tcs:
        resolved_args = _resolve_variable_refs(tc.params, all_tool_results)
        if "trackId" in resolved_args:
            resolved_args["trackId"] = real_track_id

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
            if evt.get("type") in _AGENT_TAGGED:
                evt = {**evt, "agentId": agent_id}
            await sse_queue.put(evt)

        all_tool_results.append(outcome.tool_result)
        collected_tool_calls.append(
            {"tool": tc.name, "params": outcome.enriched_params}
        )
        tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(outcome.tool_result),
        })

    # ── Emit planStepUpdate(completed) for the content step ──
    # content_step_id was activated above but the outer _run_instrument_agent_inner
    # loop never receives it (active_step_id is not updated from this function),
    # so without explicit completion here the macOS client sees the step stuck in
    # "active" indefinitely.
    if content_step_id:
        _content_step = plan_tracker.get_step(content_step_id)
        if _content_step and _content_step.status == "active":
            _n_total = max(len(children), 1) if children else 1
            _result_text = (
                f"{_child_successes}/{_n_total} sections completed, "
                f"{_total_notes} notes"
            )
            if _child_failures:
                _result_text += f" ({_child_failures} failed)"
            _done_evt = plan_tracker.complete_step_by_id(content_step_id, _result_text)
            await sse_queue.put({**_done_evt, "agentId": agent_id})

    return (
        tool_result_msgs,
        stage_track,
        stage_effect,
        regions_completed,
        regions_ok,
        generates_completed,
    )

