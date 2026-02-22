"""Per-instrument LLM agent for Agent Teams parallel composition."""

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
        )
    except Exception as exc:
        logger.exception(f"{agent_log} Unhandled agent error: {exc}")
        await _fail_all_steps(f"Failed: {exc}")


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
) -> None:
    """Inner implementation of a single instrument agent.

    Separated so ``_run_instrument_agent`` can wrap the entire body in a
    top-level try/except, ensuring any unhandled exception (prompt-building,
    tool dispatch, race condition, etc.) emits graceful ``planStepUpdate``
    failures rather than disconnecting the SSE stream.
    """
    _agent_id = agent_id

    logger.info(
        f"{agent_log} Starting — style={style}, bars={bars}, tempo={tempo}, key={key}"
        + (f", reusing trackId={existing_track_id}, startBeat={start_beat}" if reusing else "")
    )

    from app.data.role_profiles import get_role_profile

    beat_count = bars * 4
    _length_emphasis = (
        f"REQUESTED LENGTH: {bars} bars = {beat_count} beats total. "
        f"The stori_add_midi_region durationBeats MUST be {beat_count}.\n"
    )

    _role_profile = get_role_profile(role)
    _musical_dna = ""
    if _role_profile:
        _musical_dna = f"\n{_role_profile.prompt_block()}\n"

    if reusing:
        system_content = (
            f"You are a music production agent for the **{instrument_name}** track. "
            f"Execute the pipeline below immediately — do NOT reason about music theory "
            f"or composition choices. Orpheus handles all musical decisions. "
            f"Just call the tools.\n\n"
            f"Context: {style} | {tempo} BPM | {key} | {_length_emphasis}\n"
            f"{_musical_dna}"
            f"Track already exists: trackId='{existing_track_id}', content ends at beat {start_beat}.\n\n"
            f"Pipeline (execute ALL now, in order):\n"
            f"1. stori_add_midi_region — {beat_count} beats starting at beat {start_beat} "
            f"on trackId='{existing_track_id}'\n"
            f"2. stori_generate_midi — role=\"{role}\", style=\"{style}\", "
            f"tempo={int(tempo)}, bars={bars}, key=\"{key}\"\n"
            f"3. stori_add_insert_effect — one effect on trackId='{existing_track_id}'\n\n"
            f"Rules: DO NOT call stori_add_midi_track. DO NOT use stori_add_notes. "
            f"DO NOT create tracks for other instruments. Start at beat {start_beat}."
        )
    else:
        system_content = (
            f"You are a music production agent for the **{instrument_name}** track. "
            f"Execute the pipeline below immediately — do NOT reason about music theory "
            f"or composition choices. Orpheus handles all musical decisions. "
            f"Just call the tools.\n\n"
            f"Context: {style} | {tempo} BPM | {key} | {_length_emphasis}\n"
            f"{_musical_dna}"
            f"Pipeline (execute ALL now, in order):\n"
            f"1. stori_add_midi_track — create the {instrument_name} track\n"
            f"2. stori_add_midi_region — {beat_count} beats at beat 0 "
            f"(use $0.trackId). durationBeats MUST be {beat_count}.\n"
            f"3. stori_generate_midi — role=\"{role}\", style=\"{style}\", "
            f"tempo={int(tempo)}, bars={bars}, key=\"{key}\"\n"
            f"4. stori_add_insert_effect — one effect on the track\n\n"
            f"Rules: DO NOT use stori_add_notes. DO NOT create tracks for other instruments. "
            f"Make all 4 tool calls now."
        )

    agent_tools = [
        t for t in ALL_TOOLS
        if t["function"]["name"] in _INSTRUMENT_AGENT_TOOLS
    ]
    if reusing:
        user_message = (
            f"Go. Add region → generate → effect. "
            f"trackId='{existing_track_id}', startBeat={start_beat}, "
            f"{beat_count} beats. No reasoning needed."
        )
    else:
        user_message = (
            f"Go. Create track → add region → generate → effect. "
            f"{bars} bars, {beat_count} beats. No reasoning needed."
        )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_message},
    ]

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
            missing.append(
                f"stori_generate_midi (generate content with role=\"{role}\", "
                f"style=\"{style}\", tempo={int(tempo)}, bars={bars}, key=\"{key}\")"
            )
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
                composition_context=composition_context,
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
