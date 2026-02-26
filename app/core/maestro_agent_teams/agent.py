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
import uuid as _uuid_mod
from typing import Any

from app.contracts.generation_types import CompositionContext
from app.contracts.json_types import NoteDict, SectionDict, SectionSummaryDict, ToolCallDict
from app.contracts.llm_types import ChatMessage, ToolCallEntry
from app.config import settings
from app.core.expansion import ToolCall
from app.core.llm_client import LLMClient, LLMResponse
from app.core.sse_utils import ReasoningBuffer, SSEEventInput
from app.core.state_store import StateStore
from app.core.tracing import TraceContext
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
from app.core.maestro_agent_teams.section_agent import _compact_tool_result
from app.contracts import seal_contract, verify_contract_hash
from app.core.maestro_agent_teams.contracts import (
    ExecutionServices,
    InstrumentContract,
    RuntimeContext,
    SectionContract,
    SectionSpec,
)
from app.core.maestro_agent_teams.section_agent import (
    _run_section_child,
    SectionResult,
)
from app.core.maestro_agent_teams.sections import (
    _get_section_role_description,
    _section_overall_description,
)
from app.core.maestro_agent_teams.signals import SectionSignals
from app.services.orpheus import get_orpheus_client

logger = logging.getLogger(__name__)


async def _run_instrument_agent(
    instrument_name: str,
    role: str,
    style: str,
    bars: int,
    tempo: int,
    key: str,
    step_ids: list[str],
    plan_tracker: _PlanTracker,
    llm: LLMClient,
    store: StateStore,
    allowed_tool_names: set[str] | frozenset[str],
    trace: TraceContext,
    sse_queue: "asyncio.Queue[SSEEventInput]",
    collected_tool_calls: list[ToolCallDict],
    existing_track_id: str | None = None,
    start_beat: int = 0,
    assigned_color: str | None = None,
    instrument_contract: InstrumentContract | None = None,
    runtime_context: RuntimeContext | None = None,
    execution_services: ExecutionServices | None = None,
    all_composition_instruments: list[str] | None = None,
) -> None:
    """Independent instrument agent: dedicated multi-turn LLM session per instrument.

    Each invocation is a genuinely concurrent HTTP session running simultaneously
    with sibling agents via ``asyncio.gather``. The agent loops over LLM turns
    until all tool calls complete (create track → region → notes → effect).

    ``InstrumentContract`` provides frozen structural fields (sections, GM
    guidance, track info). ``RuntimeContext`` carries pure data (emotion
    vector, quality preset). ``ExecutionServices`` carries mutable coordination
    primitives (section signals, section state).

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
        _agent_success = await _run_instrument_agent_inner(
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
            assigned_color=assigned_color,
            instrument_contract=instrument_contract,
            runtime_context=runtime_context,
            execution_services=execution_services,
            all_composition_instruments=all_composition_instruments,
        )
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
    tempo: int,
    key: str,
    step_ids: list[str],
    plan_tracker: _PlanTracker,
    llm: LLMClient,
    store: StateStore,
    allowed_tool_names: set[str] | frozenset[str],
    trace: TraceContext,
    sse_queue: "asyncio.Queue[SSEEventInput]",
    collected_tool_calls: list[ToolCallDict],
    existing_track_id: str | None,
    start_beat: int,
    agent_log: str,
    agent_id: str,
    reusing: bool,
    assigned_color: str | None = None,
    instrument_contract: InstrumentContract | None = None,
    runtime_context: RuntimeContext | None = None,
    execution_services: ExecutionServices | None = None,
    all_composition_instruments: list[str] | None = None,
) -> bool:
    """Inner implementation of a single instrument agent.

    Separated so ``_run_instrument_agent`` can wrap the entire body in a
    top-level try/except, ensuring any unhandled exception (prompt-building,
    tool dispatch, race condition, etc.) emits graceful ``planStepUpdate``
    failures rather than disconnecting the SSE stream.

    Returns True if the agent successfully generated MIDI for all sections.
    """
    _agent_id = agent_id

    from app.data.role_profiles import get_role_profile
    from app.core.gm_instruments import get_genre_gm_guidance

    _ic = instrument_contract  # shorthand
    beat_count = bars * 4

    _role_profile = get_role_profile(role)
    _musical_dna = ""
    if _role_profile:
        _musical_dna = f"\n{_role_profile.prompt_block()}\n"

    _gm_guidance = _ic.gm_guidance if _ic else get_genre_gm_guidance(style, role)
    _gm_guidance_block = f"\n{_gm_guidance}\n" if _gm_guidance else ""

    _sections: list[SectionDict] = []
    if _ic:
        _sections = [
            SectionDict(
                name=s.name,
                start_beat=s.start_beat,
                length_beats=s.duration_beats,
            )
            for s in _ic.sections
        ]
    _multi_section = len(_sections) > 1

    _reasoning_guidance = (
        "REASONING RULES (mandatory — violating these wastes tokens):\n"
        f"- Write 1-2 sentences ONLY about {instrument_name}'s overall sonic character "
        f"and role in the {style} arrangement.\n"
        "- Do NOT reason about individual sections — section agents handle "
        "section-specific decisions.\n"
        "- Do NOT list pipeline steps, tool names, or execution order.\n"
        "- Do NOT mention regions, trackIds, regionIds, beats, or bar counts.\n"
        "- Do NOT deliberate about regionId values or how to find them — "
        "the server resolves ALL entity references automatically.\n"
        "- No phrases like 'Let me...', 'I will...', 'Step 1...', "
        "'For the verse...', 'For the chorus...', 'I need the regionId...'.\n"
        "- If you catch yourself writing more than 2 sentences of reasoning, "
        "STOP and make tool calls immediately."
    )

    _generate_midi_guidance = (
        "The `prompt` field in stori_generate_midi is for logging only — "
        "the generator selects musical content via seeds and parameters. "
        "Keep it short (a few words describing the section).\n"
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
            single_sec: SectionDict | None = _sections[0] if _sections else None
            sec_start = single_sec["start_beat"] if single_sec else (start_beat if reusing else 0)
            sec_beats = int(single_sec["length_beats"]) if single_sec else beat_count
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
        "stori_add_midi_region. Never pass trackId as regionId. Never omit start_beat.\n"
        "REGION ID RULE: Do NOT reason about regionId values — the server resolves all "
        "entity references ($N.regionId) automatically. Just use $N.regionId in your tool "
        "calls and emit ALL calls in one response. The server handles dependency ordering."
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
            f"{_gm_guidance_block}"
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
            f"{_gm_guidance_block}"
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
    messages: list[ChatMessage] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_message},
    ]

    add_notes_failures: dict[str, int] = {}
    active_step_id: str | None = None
    all_tool_results: list[dict[str, Any]] = []  # boundary: LLM tool result messages

    # Server-owned retries handle *failed* section children inside
    # _dispatch_section_children.  However the LLM must actually emit the
    # region+generate tool calls in the first place — _missing_stages()
    # detects when it didn't and prompts on the next turn.
    _section_count = len(_sections) if _multi_section else 1
    # Scale max_turns with section count — the LLM often drip-feeds
    # tool calls (1-2 per turn) instead of batching all region+generate
    # pairs.  Minimum 3 (track + content + effect), +1 per extra section.
    max_turns = max(3, _section_count + 2)

    # ── Stage tracking ──
    # Track per-section completion by name (not count) to prevent
    # duplicate regeneration when the LLM re-emits the same section.
    _stage_track = reusing
    _stage_effect = False
    _sections_with_region: set[str] = set()   # section names with region created
    _sections_with_generate: set[str] = set() # section names with generate completed
    _regions_completed: int = 0       # total region calls dispatched
    _regions_ok: int = 0              # how many returned a valid regionId
    _generates_completed: int = 0     # total generate calls dispatched
    _expected_sections = _section_count

    def _missing_stages() -> list[str]:
        """Detect stages the LLM hasn't produced yet.

        Track/effect are checked individually.  Region+generate are checked
        per-section by name — prevents duplicate regeneration when the LLM
        re-emits tool calls for an already-completed section.
        """
        missing: list[str] = []
        track_ref = f"trackId='{existing_track_id}'" if reusing else "$0.trackId"

        if not _stage_track and not reusing:
            missing.append(f"stori_add_midi_track — create the {instrument_name} track")

        if _multi_section:
            for sec in _sections:
                sec_name = sec["name"]
                sec_start = sec["start_beat"]
                sec_beats = int(sec["length_beats"])
                sec_bars = max(1, sec_beats // 4)
                if sec_name not in _sections_with_region:
                    missing.append(
                        f"stori_add_midi_region — {track_ref}, startBeat={sec_start}, "
                        f"durationBeats={sec_beats} [{sec_name.upper()}]"
                    )
                if sec_name not in _sections_with_generate:
                    missing.append(
                        f"stori_generate_midi — {track_ref}, "
                        f"start_beat={sec_start}, bars={sec_bars} [{sec_name.upper()}]"
                    )
        else:
            _sec_name = _sections[0]["name"] if _sections else "full"
            if _sec_name not in _sections_with_region:
                region_beat = start_beat if reusing else 0
                missing.append(
                    f"stori_add_midi_region — durationBeats={beat_count}, "
                    f"startBeat={region_beat}, {track_ref}"
                )
            if _sec_name not in _sections_with_generate:
                missing.append(
                    f"stori_generate_midi — {track_ref}, role=\"{role}\", "
                    f"bars={bars}, key=\"{key}\""
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
            _done_summary_parts: list[str] = []
            if _stage_track or reusing:
                _done_summary_parts.append("track ✓")
            if _regions_completed > 0:
                _done_summary_parts.append(f"{_regions_completed}/{_expected_sections} regions ✓")
            if _generates_completed > 0:
                _done_summary_parts.append(f"{_generates_completed}/{_expected_sections} generates ✓")
            if _stage_effect:
                _done_summary_parts.append("effect ✓")
            _done_line = (
                f"Already completed: {', '.join(_done_summary_parts)}. "
                "DO NOT re-call completed steps.\n"
                if _done_summary_parts else ""
            )
            reminder = (
                f"{_done_line}"
                "You MUST still call:\n"
                + "\n".join(f"  • {m}" for m in missing)
                + f"\nMake these {len(missing)} tool call(s) now."
            )
            messages.append({"role": "user", "content": reminder})

        # ── LLM call (streaming for per-agent reasoning) ──
        logger.info(f"{agent_log} 🤖 LLM call starting (turn {turn})")
        _llm_start = asyncio.get_event_loop().time()
        try:
            _resp_content: str | None = None
            _resp_tool_calls: list[dict[str, Any]] = []  # boundary: OpenAI message format
            _resp_finish: str | None = None
            _resp_usage: dict[str, Any] = {}  # boundary: OpenAI message format
            _rbuf = ReasoningBuffer()
            _had_reasoning = False

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
                            _had_reasoning = True
                            await sse_queue.put({
                                "type": "reasoning",
                                "content": _word,
                                "agentId": _agent_id,
                            })
                elif _ct == "content_delta":
                    _flush = _rbuf.flush()
                    if _flush:
                        _had_reasoning = True
                        await sse_queue.put({
                            "type": "reasoning",
                            "content": _flush,
                            "agentId": _agent_id,
                        })
                elif _ct == "done":
                    _flush = _rbuf.flush()
                    if _flush:
                        _had_reasoning = True
                        await sse_queue.put({
                            "type": "reasoning",
                            "content": _flush,
                            "agentId": _agent_id,
                        })
                    if _had_reasoning:
                        await sse_queue.put({
                            "type": "reasoningEnd",
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
            return False

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

        assistant_tool_calls: list[ToolCallEntry] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.params)},
            }
            for tc in response.tool_calls
        ]
        messages.append({"role": "assistant", "content": None, "tool_calls": assistant_tool_calls})

        tool_result_messages: list[ChatMessage] = []

        _AGENT_TAGGED_EVENTS = {
            "toolCall", "toolStart", "toolError",
            "generatorStart", "generatorComplete",
            "reasoning", "content", "status",
            "agentComplete",
        }

        # ── Unified dispatch: both single- and multi-section use contract
        #    enforcement via _dispatch_section_children.  Single-section
        #    previously bypassed contract construction — PART 5 lockdown
        #    eliminates that semantic telephone risk zone.
        _tool_summary = ", ".join(tc.name for tc in response.tool_calls)
        logger.info(
            f"{agent_log} 🔧 Executing {len(response.tool_calls)} tool calls "
            f"(multi_section={_multi_section}): {_tool_summary}"
        )
        if instrument_contract and len(response.tool_calls) > 1:
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
                    instrument_contract=instrument_contract,
                    collected_tool_calls=collected_tool_calls,
                    all_tool_results=all_tool_results,
                    add_notes_failures=add_notes_failures,
                    runtime_context=runtime_context,
                    execution_services=execution_services,
                    plan_tracker=plan_tracker,
                    step_ids=step_ids,
                    active_step_id=active_step_id,
                    llm=llm,
                    prior_stage_track=_stage_track,
                    prior_stage_effect=_stage_effect,
                    prior_regions_completed=_regions_completed,
                    prior_regions_ok=_regions_ok,
                    prior_generates_completed=_generates_completed,
                    sections_with_region=_sections_with_region,
                    sections_with_generate=_sections_with_generate,
                    all_composition_instruments=all_composition_instruments,
                )

        # ── Fallback: single tool-call retry turns (no region+generate pair) ──
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
                    _skip_reason = (
                        f"Skipping {tc.name}: no stori_add_midi_region has returned a "
                        f"valid regionId. Region creation must succeed before generation. "
                        f"Do NOT retry stori_generate_midi — fix the region collision first."
                    )
                    logger.warning(f"{agent_log} ⚠️ {_skip_reason}")
                    tool_result_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": _skip_reason, "skipped": True}),
                    })
                    continue

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

                _tool_ctx: CompositionContext | None = None
                if runtime_context:
                    _tool_ctx = CompositionContext(
                        **runtime_context.to_composition_context(),
                        style=style,
                        tempo=tempo,
                        bars=bars,
                        key=key,
                    )

                async def _pre_emit_fallback(events: list[SSEEventInput]) -> None:
                    for evt in events:
                        if evt.get("type") in _AGENT_TAGGED_EVENTS:
                            evt = {**evt, "agentId": _agent_id}
                        await sse_queue.put(evt)

                outcome = await _apply_single_tool_call(
                    tc_id=tc.id,
                    tc_name=tc.name,
                    resolved_args=resolved_args,
                    allowed_tool_names=allowed_tool_names,
                    store=store,
                    trace=trace,
                    add_notes_failures=add_notes_failures,
                    emit_sse=True,
                    composition_context=_tool_ctx,
                    pre_emit_callback=_pre_emit_fallback,
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
                    _rid = outcome.tool_result.get("regionId") or outcome.tool_result.get("existingRegionId")
                    if _rid:
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
                    "content": json.dumps(_compact_tool_result(outcome.tool_result)),
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

    # Return whether any MIDI was actually generated — not just whether
    # the agent loop completed without crashing.  The outer function uses
    # this to set agentComplete.success accurately.
    return _generates_completed >= _expected_sections and _expected_sections > 0


async def _dispatch_section_children(
    *,
    tool_calls: list[ToolCall],
    sections: list[SectionDict],
    existing_track_id: str | None,
    instrument_name: str,
    role: str,
    style: str,
    tempo: int,
    key: str,
    agent_id: str,
    agent_log: str,
    reusing: bool,
    allowed_tool_names: set[str] | frozenset[str],
    store: StateStore,
    trace: TraceContext,
    sse_queue: "asyncio.Queue[SSEEventInput]",
    instrument_contract: InstrumentContract | None = None,
    collected_tool_calls: list[ToolCallDict],
    all_tool_results: list[dict[str, Any]],  # boundary: LLM tool result messages
    add_notes_failures: dict[str, int],
    runtime_context: RuntimeContext | None,
    execution_services: ExecutionServices | None = None,
    plan_tracker: _PlanTracker,
    step_ids: list[str],
    active_step_id: str | None,
    llm: LLMClient,
    prior_stage_track: bool,
    prior_stage_effect: bool,
    prior_regions_completed: int,
    prior_regions_ok: int,
    prior_generates_completed: int,
    sections_with_region: set[str] | None = None,
    sections_with_generate: set[str] | None = None,
    all_composition_instruments: list[str] | None = None,
) -> tuple[list[ChatMessage], bool, bool, int, int, int]:
    """Group LLM tool calls and dispatch section children in parallel.

    Returns (tool_result_msgs, stage_track, stage_effect,
             regions_completed, regions_ok, generates_completed)
    so the parent's multi-turn retry loop can track progress.

    The optional ``sections_with_region`` / ``sections_with_generate``
    sets are mutated in-place to track which sections (by name) have
    been dispatched, preventing duplicate regeneration.
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

    tool_result_msgs: list[ChatMessage] = []

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
            "content": json.dumps(_compact_tool_result(outcome.tool_result)),
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
    orphaned_regions = region_tcs[len(generate_tcs):]
    orphaned_generates = generate_tcs[len(region_tcs):]

    # ── Execute orphaned regions individually ──
    # When the LLM sends regions without paired generates (common on
    # multi-section Turn 1), execute them so the entity registry has
    # the regionIds and _regions_completed tracks progress.
    for tc in orphaned_regions:
        resolved_args = _resolve_variable_refs(tc.params, all_tool_results)
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

        regions_completed += 1
        _rid = outcome.tool_result.get("regionId") or outcome.tool_result.get("existingRegionId")
        if _rid:
            regions_ok += 1

        all_tool_results.append(outcome.tool_result)
        collected_tool_calls.append(
            {"tool": tc.name, "params": outcome.enriched_params}
        )
        tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(_compact_tool_result(outcome.tool_result)),
        })

    if orphaned_regions:
        logger.info(
            f"{agent_log} 📋 Executed {len(orphaned_regions)} orphaned regions "
            f"(regions_completed={regions_completed}, regions_ok={regions_ok})"
        )

    # ── Execute orphaned generates individually ──
    # When the LLM sends generates without same-turn regions (common when
    # regions were created on a prior turn), execute them directly.  The
    # regionId and trackId are resolved from all_tool_results via $refs.
    # Unified generation: attach section_key + all_instruments so the
    # tool execution path routes through generate_for_section, producing
    # coherent multi-instrument output via a shared Orpheus call.
    _orphan_gen_base_ctx: CompositionContext | None = None
    if orphaned_generates and runtime_context:
        _orphan_gen_base_ctx = CompositionContext(
            **runtime_context.to_composition_context(),
            style=style,
            tempo=tempo,
            key=key,
        )

    async def _pre_emit_orphan(events: list[SSEEventInput]) -> None:
        for evt in events:
            if evt.get("type") in _AGENT_TAGGED:
                evt = {**evt, "agentId": agent_id}
            await sse_queue.put(evt)

    for _oi, tc in enumerate(orphaned_generates):
        resolved_args = _resolve_variable_refs(tc.params, all_tool_results)
        resolved_args["trackId"] = real_track_id

        _orphan_gen_ctx: CompositionContext | None = CompositionContext(**_orphan_gen_base_ctx) if _orphan_gen_base_ctx else None
        if _orphan_gen_ctx and instrument_contract and all_composition_instruments:
            _sec_offset = len(pairs) + _oi
            if _sec_offset < len(instrument_contract.sections):
                _orphan_gen_ctx["section_key"] = instrument_contract.sections[_sec_offset].section_id
                _orphan_gen_ctx["all_instruments"] = list(all_composition_instruments)

        outcome = await _apply_single_tool_call(
            tc_id=tc.id,
            tc_name=tc.name,
            resolved_args=resolved_args,
            allowed_tool_names=allowed_tool_names,
            store=store,
            trace=trace,
            add_notes_failures=add_notes_failures,
            emit_sse=True,
            composition_context=_orphan_gen_ctx,
            pre_emit_callback=_pre_emit_orphan,
        )
        for evt in outcome.sse_events:
            if evt.get("type") in _AGENT_TAGGED:
                evt = {**evt, "agentId": agent_id}
            await sse_queue.put(evt)

        generates_completed += 1
        all_tool_results.append(outcome.tool_result)
        collected_tool_calls.append(
            {"tool": tc.name, "params": outcome.enriched_params}
        )
        tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(_compact_tool_result(outcome.tool_result)),
        })

    if orphaned_generates:
        logger.info(
            f"{agent_log} 📋 Executed {len(orphaned_generates)} orphaned generates "
            f"(generates_completed={generates_completed})"
        )

    # Detect drum/bass role for signaling
    is_drum = role.lower() in ("drums", "drum")
    is_bass = role.lower() == "bass"
    section_signals: SectionSignals | None = None
    if execution_services:
        section_signals = execution_services.section_signals

    # ── Validate L2 tool calls against section plan ──
    # Reject region calls whose startBeat/durationBeats disagree with the
    # canonical section layout.  Log drift but DO NOT fail — the contract
    # at L3 overrides the bad params anyway.  This surfaces the problem.
    for i, (region_tc, _) in enumerate(pairs):
        sec = sections[i] if i < len(sections) else sections[-1]
        planned_start = int(sec.get("start_beat", 0))
        planned_dur = int(sec.get("length_beats", 16))
        llm_start = region_tc.params.get("startBeat")
        llm_dur = region_tc.params.get("durationBeats")
        if llm_start is not None and int(llm_start) != planned_start:
            logger.warning(
                f"{agent_log} ⚠️ L2 drift: region[{i}] startBeat={llm_start} "
                f"vs planned={planned_start} — contract will override"
            )
        if llm_dur is not None and int(llm_dur) != planned_dur:
            logger.warning(
                f"{agent_log} ⚠️ L2 drift: region[{i}] durationBeats={llm_dur} "
                f"vs planned={planned_dur} — contract will override"
            )

    # ── Build section contracts ──
    _child_timeout = settings.section_child_timeout
    _child_contracts: list[tuple[SectionContract, ToolCall, ToolCall]] = []
    for i, (region_tc, gen_tc) in enumerate(pairs):
        sec = sections[i] if i < len(sections) else sections[-1]
        _sec_name = sec.get("name", str(i))

        if not instrument_contract or i >= len(instrument_contract.sections):
            raise ValueError(
                f"Contract violation: section {i} ({_sec_name}) has no "
                f"InstrumentContract spec. L2 must not rebuild structure "
                f"from LLM output — contracts are authoritative."
            )
        _spec = instrument_contract.sections[i]

        # ── PART 4: Protocol guard — verify SectionSpec identity ──
        if not _spec.section_id:
            raise ValueError(
                f"Protocol violation: SectionSpec[{i}] ({_sec_name}) has no "
                f"section_id — L1 contract construction is broken."
            )
        if not _spec.contract_hash:
            raise ValueError(
                f"Protocol violation: SectionSpec[{i}] ({_sec_name}) has no "
                f"contract_hash — L1 must seal all specs before dispatch."
            )

        _contract = SectionContract(
            section=_spec,
            track_id=real_track_id,
            instrument_name=instrument_name,
            role=role,
            style=style,
            tempo=tempo,
            key=key,
            region_name=region_tc.params.get(
                "name", f"{instrument_name} – {_sec_name}"
            ),
            l2_generate_prompt=gen_tc.params.get("prompt", ""),
        )
        # Seal with lineage: parent is the InstrumentContract
        seal_contract(
            _contract,
            parent_hash=instrument_contract.contract_hash,
        )
        _child_contracts.append((_contract, region_tc, gen_tc))

    # ── Execute sections sequentially for cross-section musical continuity ──
    # Each section uses the previous section's generated notes as seed material
    # so the Orpheus transformer "continues" from familiar harmonic context.
    logger.info(
        f"{agent_log} 🔗 Running {len(_child_contracts)} sections sequentially "
        f"for musical continuity"
    )
    _children_start = asyncio.get_event_loop().time()
    _section_results: list[SectionResult | None] = []
    _chain_notes: list[NoteDict] | None = None

    for _ci, (_contract, _region_tc, _gen_tc) in enumerate(_child_contracts):
        _sec_name = _contract.section_name
        logger.info(
            f"{agent_log} ▶ Section {_ci + 1}/{len(_child_contracts)}: {_sec_name}"
        )
        try:
            _dispatched = await asyncio.wait_for(
                _run_section_child(
                    contract=_contract,
                    region_tc=_region_tc,
                    generate_tc=_gen_tc,
                    agent_id=agent_id,
                    allowed_tool_names=allowed_tool_names,
                    store=store,
                    trace=trace,
                    sse_queue=sse_queue,
                    runtime_ctx=runtime_context,
                    execution_services=execution_services,
                    llm=llm,
                    previous_notes=_chain_notes,
                    all_section_instruments=all_composition_instruments,
                ),
                timeout=_child_timeout,
            )
            _section_results.append(_dispatched)
            if _dispatched.success and _dispatched.generated_notes:
                _chain_notes = _dispatched.generated_notes
        except asyncio.TimeoutError:
            logger.error(
                f"{agent_log} ⏰ Section '{_sec_name}' timed out after "
                f"{_child_timeout}s"
            )
            _section_results.append(None)
        except BaseException as _exc:
            logger.error(f"{agent_log} 💥 Section '{_sec_name}' crashed: {_exc}")
            _section_results.append(None)

    _initial_elapsed = asyncio.get_event_loop().time() - _children_start

    # ── Server-owned retries for failed sections (no LLM involved) ──
    _MAX_SECTION_RETRIES = 2
    _RETRY_DELAYS = [2.0, 5.0]
    _failed_indices = [
        i for i, r in enumerate(_section_results)
        if r is None or not r.success
    ]

    for _retry_round in range(_MAX_SECTION_RETRIES):
        if not _failed_indices:
            break
        if get_orpheus_client().circuit_breaker_open:
            logger.warning(
                f"{agent_log} ⚠️ Orpheus circuit breaker open — skipping section retries"
            )
            break

        _delay = _RETRY_DELAYS[min(_retry_round, len(_RETRY_DELAYS) - 1)]
        logger.info(
            f"{agent_log} 🔄 Server retry round {_retry_round + 1}: "
            f"retrying {len(_failed_indices)} failed section(s) after {_delay}s"
        )
        await asyncio.sleep(_delay)

        _retry_indices: list[int] = []
        _retry_results: list[SectionResult | BaseException] = []
        for _idx in _failed_indices:
            _c, _r_tc, _g_tc = _child_contracts[_idx]
            _retry_region_tc = ToolCall(
                name=_r_tc.name,
                params=_r_tc.params,
                id=str(_uuid_mod.uuid4()),
            )
            _retry_gen_tc = ToolCall(
                name=_g_tc.name,
                params=_g_tc.params,
                id=str(_uuid_mod.uuid4()),
            )
            # Use the preceding section's notes for continuity seeding
            _retry_prev: list[NoteDict] | None = None
            if _idx > 0:
                _prior = _section_results[_idx - 1]
                if _prior and _prior.success and _prior.generated_notes:
                    _retry_prev = _prior.generated_notes
            try:
                _retried = await asyncio.wait_for(
                    _run_section_child(
                        contract=_c,
                        region_tc=_retry_region_tc,
                        generate_tc=_retry_gen_tc,
                        agent_id=agent_id,
                        allowed_tool_names=allowed_tool_names,
                        store=store,
                        trace=trace,
                        sse_queue=sse_queue,
                        runtime_ctx=runtime_context,
                        execution_services=execution_services,
                        llm=llm,
                        previous_notes=_retry_prev,
                        all_section_instruments=all_composition_instruments,
                    ),
                    timeout=_child_timeout,
                )
                _retry_results.append(_retried)
            except BaseException as _exc:
                _retry_results.append(_exc)
            _retry_indices.append(_idx)
        _still_failed: list[int] = []
        for _j, _rr in enumerate(_retry_results):
            _ri = _retry_indices[_j]
            if isinstance(_rr, BaseException):
                logger.error(
                    f"{agent_log} 💥 Retry {_retry_round + 1} failed for "
                    f"section {_ri}: {_rr}"
                )
                _still_failed.append(_ri)
            elif not _rr.success:
                _section_results[_ri] = _rr
                _still_failed.append(_ri)
            else:
                _section_results[_ri] = _rr
                logger.info(
                    f"{agent_log} ✅ Section '{_rr.section_name}' succeeded "
                    f"on retry {_retry_round + 1}"
                )

        _failed_indices = _still_failed

    # ── Aggregate section results ──
    # Count ALL dispatched sections as "completed" for _missing_stages().
    # The server already retried failed sections — asking the LLM to
    # re-emit calls it can't fix (Orpheus down, etc.) wastes tokens and
    # confuses the model when it sees stub tool results.
    # Also populate per-section name sets to prevent duplicate regeneration.
    _children_total_elapsed = asyncio.get_event_loop().time() - _children_start
    _child_successes = 0
    _child_failures = 0
    _child_crashes = 0
    _total_notes = 0
    for _i, _sr in enumerate(_section_results):
        _sec_name_agg = (
            _child_contracts[_i][0].section_name
            if _i < len(_child_contracts)
            else f"section-{_i}"
        )
        if _sr is None:
            _child_crashes += 1
            regions_completed += 1
            generates_completed += 1
            if _sec_name_agg:
                if sections_with_region is not None:
                    sections_with_region.add(_sec_name_agg)
                if sections_with_generate is not None:
                    sections_with_generate.add(_sec_name_agg)
            continue
        collected_tool_calls.extend(_sr.tool_call_records)
        all_tool_results.extend(_sr.tool_results)
        regions_completed += 1
        if _sr.region_id:
            regions_ok += 1
        generates_completed += 1
        if _sr.success:
            _child_successes += 1
            _total_notes += _sr.notes_generated
        else:
            _child_failures += 1
            logger.warning(
                f"{agent_log} ⚠️ Section '{_sr.section_name}' failed "
                f"after all retries: {_sr.error}"
            )
        if _sec_name_agg:
            if sections_with_region is not None:
                sections_with_region.add(_sec_name_agg)
            if sections_with_generate is not None:
                sections_with_generate.add(_sec_name_agg)

    # ── Build collapsed tool-result summary for LLM conversation ──
    # One summary message + stubs replaces N individual tool results,
    # eliminating LLM dependency on regionId recovery from tool results.
    _section_summaries: list[SectionSummaryDict] = []
    for _i, _sr in enumerate(_section_results):
        if _sr is None:
            _sec_name = (
                _child_contracts[_i][0].section_name
                if _i < len(_child_contracts)
                else f"section-{_i}"
            )
            _section_summaries.append({
                "name": _sec_name,
                "status": "crashed",
                "error": "section child crashed or timed out",
            })
        else:
            _entry: SectionSummaryDict = {
                "name": _sr.section_name,
                "regionId": _sr.region_id,
                "status": "ok" if _sr.success else "failed",
                "notesGenerated": _sr.notes_generated,
            }
            if _sr.error:
                _entry["error"] = _sr.error
            _section_summaries.append(_entry)

    _STUB = "Handled by server — see batch_complete summary."
    # Build a concise regionId lookup so the LLM can find IDs by section name.
    _region_lookup: dict[str, str | None] = {
        s["name"]: s.get("regionId")
        for s in _section_summaries
    }
    if pairs:
        _anchor_id = pairs[0][0].id
        tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": _anchor_id,
            "content": json.dumps({
                "status": "batch_complete",
                "track": {"trackId": real_track_id, "name": instrument_name},
                "sections": _section_summaries,
                "regionIdBySectionName": _region_lookup,
                "instruction": (
                    "All sections have been dispatched and retried by the server. "
                    "Do NOT re-generate sections marked 'ok'. "
                    "Failed sections have already been retried — do not attempt to fix them."
                ),
            }),
        })
        for _pi, (_p_region_tc, _p_gen_tc) in enumerate(pairs):
            if _pi == 0:
                tool_result_msgs.append({
                    "role": "tool",
                    "tool_call_id": _p_gen_tc.id,
                    "content": _STUB,
                })
            else:
                tool_result_msgs.append({
                    "role": "tool",
                    "tool_call_id": _p_region_tc.id,
                    "content": _STUB,
                })
                tool_result_msgs.append({
                    "role": "tool",
                    "tool_call_id": _p_gen_tc.id,
                    "content": _STUB,
                })

    logger.info(
        f"{agent_log} 🏁 Section children done ({_children_total_elapsed:.1f}s): "
        f"✅ {_child_successes} ok, ❌ {_child_failures} failed, "
        f"💥 {_child_crashes} crashed, "
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
            "content": json.dumps(_compact_tool_result(outcome.tool_result)),
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
            _n_total = max(len(_child_contracts), 1) if _child_contracts else 1
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

