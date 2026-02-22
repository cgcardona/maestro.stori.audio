"""Agent Teams coordinator — three-phase parallel instrument composition."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid as _uuid_mod
from typing import Any, AsyncIterator, Optional

from app.core.emotion_vector import emotion_vector_from_stori_prompt
from app.core.llm_client import LLMClient
from app.core.prompts import system_prompt_base
from app.core.sse_utils import sse_event
from app.core.state_store import StateStore
from app.core.tools import ALL_TOOLS
from app.core.maestro_helpers import (
    UsageTracker,
    _context_usage_fields,
    _entity_manifest,
    _resolve_variable_refs,
)
from app.core.maestro_plan_tracker import (
    _PlanTracker,
    _AGENT_TEAM_PHASE3_TOOLS,
    _INSTRUMENT_AGENT_TOOLS,
)
from app.core.maestro_editing import _apply_single_tool_call
from app.core.maestro_agent_teams.agent import _run_instrument_agent
from app.core.maestro_agent_teams.summary import _build_composition_summary

logger = logging.getLogger(__name__)

_BARS_RE = re.compile(r"\b(\d{1,3})[\s-]*bars?\b", re.IGNORECASE)


def _parse_bars_from_text(text: str) -> Optional[int]:
    """Extract an explicit bar count from natural language (e.g. '24-bar bridge')."""
    m = _BARS_RE.search(text)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 128:
            return val
    return None


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
    constraints = getattr(parsed, "constraints", {}) or {}
    bars = int(
        constraints.get("bars")
        or ext.get("bars") or ext.get("Bars")
        or _parse_bars_from_text(getattr(parsed, "request", "") or prompt)
        or 4
    )
    tempo = float(parsed.tempo or project_context.get("tempo") or 120)
    key = parsed.key or project_context.get("key") or "C"
    logger.info(
        f"[{trace.trace_id[:8]}] Composition parameters: bars={bars}, tempo={tempo}, "
        f"key={key}, style={style} (source: "
        f"{'constraints' if constraints.get('bars') else 'extensions' if ext.get('bars') or ext.get('Bars') else 'prompt-parse' if _parse_bars_from_text(getattr(parsed, 'request', '') or prompt) else 'default'})"
    )

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

    # ── Build composition context for Orpheus routing ──
    _emotion_vector = None
    try:
        _emotion_vector = emotion_vector_from_stori_prompt(prompt)
    except Exception as _ev_exc:
        logger.warning(
            f"[{trace.trace_id[:8]}] EmotionVector parse failed: {_ev_exc}"
        )
    _composition_context: dict[str, Any] = {
        "style": style,
        "tempo": tempo,
        "bars": bars,
        "key": key,
        "emotion_vector": _emotion_vector,
        "quality_preset": "quality",
    }

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

    def _spawn_agent(role: str) -> asyncio.Task:
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
        _ctx = {**_composition_context, "role": role}
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
                composition_context=_ctx,
            )
        )
        logger.info(
            f"[{trace.trace_id[:8]}] 🚀 Spawned {instrument_name} agent "
            f"(step_ids={step_ids_for_role}"
            + (f", reusing trackId={existing_track_id}, startBeat={agent_start_beat}" if existing_track_id else "")
            + ")"
        )
        return task

    # ── Phase 2a: Run drums first for RhythmSpine coupling ──
    drum_roles = [r for r in parsed.roles if r.lower() in ("drums", "drum")]
    other_roles = [r for r in parsed.roles if r not in drum_roles]

    if drum_roles:
        drum_task = _spawn_agent(drum_roles[0])
        await drum_task
        while not sse_queue.empty():
            yield await sse_event(sse_queue.get_nowait())
        if not drum_task.cancelled() and drum_task.exception() is not None:
            exc = drum_task.exception()
            logger.error(f"[{trace.trace_id[:8]}] ❌ Drum agent crashed: {exc}")
        else:
            logger.info(
                f"[{trace.trace_id[:8]}] ✅ Drums complete — "
                f"RhythmSpine captured for bass coupling"
            )

    # ── Phase 2b: Spawn remaining agents in parallel ──
    for role in other_roles:
        tasks.append(_spawn_agent(role))

    # Drain queue while agents run — forward events to client as they arrive.
    # Task-level exceptions are caught here as a failsafe; the agent's own
    # try/except should handle most failures, but a truly unexpected crash
    # (e.g. OOM, interpreter error) could bypass it.
    pending: set[asyncio.Task] = set(tasks)
    while pending:
        done, pending = await asyncio.wait(pending, timeout=0.05)
        while not sse_queue.empty():
            yield await sse_event(sse_queue.get_nowait())
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                exc = task.exception()
                logger.error(
                    f"[{trace.trace_id[:8]}] ❌ Instrument agent task crashed: {exc}"
                )
                # Failsafe: mark any steps that are still pending/active as
                # failed so the client never sees steps stuck in limbo.
                for step in plan_tracker.steps:
                    if (
                        step.parallel_group == "instruments"
                        and step.status in ("pending", "active")
                    ):
                        role_for_step = (step.track_name or "").lower()
                        step_ids_for_role = instrument_step_ids.get(role_for_step, [])
                        if step.step_id in step_ids_for_role:
                            step.status = "failed"
                            yield await sse_event({
                                "type": "planStepUpdate",
                                "stepId": step.step_id,
                                "status": "failed",
                                "result": f"Failed: {exc}",
                                "agentId": role_for_step,
                            })
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

    _all_tracks = summary.get("tracksCreated", []) + summary.get("tracksReused", [])
    yield await sse_event({
        "type": "summary",
        "tracks": [t.get("name", "") for t in _all_tracks],
        "regions": summary.get("regionsCreated", 0),
        "notes": summary.get("notesGenerated", 0),
        "effects": summary.get("effectCount", 0),
    })

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
