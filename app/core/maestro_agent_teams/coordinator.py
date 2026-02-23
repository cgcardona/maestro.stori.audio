"""Agent Teams coordinator (Level 1) — three-phase parallel composition.

Three-level architecture:
  Level 1 — Coordinator (this file): deterministic setup, spawns
            instrument parents, optional mixing pass.
  Level 2 — Instrument Parent (agent.py): one LLM call per instrument,
            dispatches section children.
  Level 3 — Section Child (section_agent.py): per-section executor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid as _uuid_mod
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from app.config import settings
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
from app.core.maestro_agent_teams.contracts import (
    InstrumentContract,
    RuntimeContext,
    SectionSpec,
)
from app.core.maestro_agent_teams.sections import (
    parse_sections,
    _get_section_role_description,
    _section_overall_description,
)
from app.core.gm_instruments import get_genre_gm_guidance
from app.core.track_styling import allocate_colors
from app.core.maestro_agent_teams.signals import SectionSignals, SectionState
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
    is_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
) -> AsyncIterator[str]:
    """Agent Teams coordinator for multi-instrument STORI PROMPT compositions.

    Three-level, three-phase execution:

    - **Phase 1** (sequential): tempo and key applied deterministically from
      the parsed prompt — no LLM call needed.
    - **Phase 2** (parallel, all instruments launched simultaneously):
      All instrument parents (including bass) start in one wave.  Drum-to-bass
      coupling is handled at the section level: each bass section child waits
      on its corresponding drum section child via ``SectionSignals``.  This
      means bass LLM planning happens immediately (parallel with drums); only
      bass section execution waits per-section.
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
    # ── Pre-allocate one distinct color per instrument ──
    # Must happen before preflight so trackColor can be included.
    _instrument_names_ordered = [
        _ROLE_LABELS.get(r.lower(), r.title()) for r in parsed.roles
    ]
    _color_map: dict[str, str] = allocate_colors(_instrument_names_ordered)
    logger.info(
        f"[{trace.trace_id[:8]}] 🎨 Color allocation: "
        + ", ".join(f"{n}={c}" for n, c in _color_map.items())
    )

    # ── Preflight events — latency masking (emit before agents start) ──
    # Lets the frontend pre-allocate timeline rows and show "incoming" states
    # for every predicted instrument step. Derived from the plan, no LLM needed.
    for role in parsed.roles:
        instrument_name = _ROLE_LABELS.get(role.lower(), role.title())
        step_ids_for_role = instrument_step_ids.get(instrument_name.lower(), [])
        steps_for_role = [s for s in plan_tracker.steps if s.step_id in step_ids_for_role]
        _preflight_color = _color_map.get(instrument_name)
        for step in steps_for_role:
            _preflight_evt: dict[str, Any] = {
                "type": "preflight",
                "stepId": step.step_id,
                "agentId": instrument_name.lower(),
                "agentRole": role,
                "label": step.label,
                "toolName": step.tool_name,
                "parallelGroup": step.parallel_group,
                "confidence": 0.9,
            }
            if _preflight_color:
                _preflight_evt["trackColor"] = _preflight_color
            yield await sse_event(_preflight_evt)

    sse_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    agent_tool_calls: list[dict[str, Any]] = []
    tasks: list[asyncio.Task] = []

    # ── GPU warm-up: fire a lightweight health probe before spawning agents ──
    # This primes the Gradio Space GPU pod so the first real generation call
    # does not hit the 60-second cold-start timeout.  If Orpheus is unreachable
    # we surface a user-facing status event so the macOS client can show a
    # warning before committing to a full composition run.
    _orpheus_healthy = True
    try:
        from app.services.orpheus import get_orpheus_client
        _orpheus = get_orpheus_client()
        _orpheus_healthy = await _orpheus.health_check()
        if _orpheus_healthy:
            logger.debug(f"[{trace.trace_id[:8]}] Orpheus GPU warm-up: healthy ✓")
        else:
            logger.warning(
                f"⚠️ [{trace.trace_id[:8]}] Orpheus health check failed before composition — "
                "generation may fail; retry logic will attempt recovery"
            )
            yield await sse_event({
                "type": "status",
                "message": (
                    "⚠️ Music generation (Orpheus) is not responding. "
                    "MIDI regions may be empty — verify Orpheus is running on port 10002, "
                    "then retry."
                ),
            })
    except Exception as _wu_exc:
        _orpheus_healthy = False
        logger.warning(f"⚠️ [{trace.trace_id[:8]}] Orpheus warm-up probe failed: {_wu_exc}")
        yield await sse_event({
            "type": "status",
            "message": (
                f"⚠️ Could not reach music generation service (Orpheus): {_wu_exc}. "
                "MIDI regions will be empty — check that Orpheus is running."
            ),
        })

    # ── Section parsing: decompose STORI PROMPT into musical sections ──
    _sections = parse_sections(
        prompt=prompt,
        bars=bars,
        roles=list(parsed.roles),
    )
    _multi_section = len(_sections) > 1
    logger.info(
        f"[{trace.trace_id[:8]}] 📋 Section parsing: {len(_sections)} section(s) — "
        + ", ".join(
            f"{s['name']}(start={s.get('start_beat', '?')}, len={s.get('length_beats', '?')})"
            for s in _sections
        )
    )
    if _multi_section:
        yield await sse_event({
            "type": "status",
            "message": (
                f"Parsed {len(_sections)} sections: "
                + ", ".join(s["name"] for s in _sections)
            ),
        })

    # ── Build composition context for Orpheus routing ──
    _emotion_vector = None
    try:
        _emotion_vector = emotion_vector_from_stori_prompt(prompt)
    except Exception as _ev_exc:
        logger.warning(
            f"[{trace.trace_id[:8]}] EmotionVector parse failed: {_ev_exc}"
        )
    # ── Create section-level signals and shared telemetry state ──
    _section_signals: SectionSignals | None = None
    _section_state = SectionState()
    if _multi_section:
        _section_signals = SectionSignals.from_sections(_sections)
        logger.info(
            f"[{trace.trace_id[:8]}] 🔗 SectionSignals created for drum→bass pipelining: "
            f"{list(_section_signals.events.keys())}"
        )

    _runtime_context = RuntimeContext(
        raw_prompt=prompt,
        emotion_vector=_emotion_vector,
        quality_preset="quality",
        section_signals=_section_signals,
        section_state=_section_state,
    )

    # Build canonical SectionSpecs once — shared across all instruments
    _section_specs: tuple[SectionSpec, ...] = tuple(
        SectionSpec(
            name=s.get("name", f"section_{i}"),
            index=i,
            start_beat=int(s.get("start_beat", 0)),
            duration_beats=int(s.get("length_beats", 16)),
            bars=max(1, int(s.get("length_beats", 16)) // 4),
            character=_section_overall_description(s.get("name", f"section_{i}")),
            role_brief="",
        )
        for i, s in enumerate(_sections)
    )

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
    _reused_ids = [
        info["existing_track_id"]
        for info in _role_track_info.values()
        if info["existing_track_id"]
    ]
    if len(_reused_ids) != len(set(_reused_ids)):
        logger.error(
            f"[{trace.trace_id[:8]}] Duplicate trackId in role→track mapping: "
            f"ids={_reused_ids}"
        )

    def _spawn_agent(role: str) -> asyncio.Task:
        role_info = _role_track_info[role]
        instrument_name = role_info["instrument_name"]
        step_ids_for_role = instrument_step_ids.get(instrument_name.lower(), [])
        existing_track_id = role_info["existing_track_id"]
        agent_start_beat = role_info["start_beat"]
        assigned_color = _color_map.get(instrument_name)
        if existing_track_id:
            agent_tool_calls.append({
                "tool": "_reused_track",
                "params": {"name": instrument_name, "trackId": existing_track_id},
            })

        # ── Build role-specific SectionSpecs (with per-role brief) ──
        _role_specs = tuple(
            SectionSpec(
                name=s.name,
                index=s.index,
                start_beat=s.start_beat,
                duration_beats=s.duration_beats,
                bars=s.bars,
                character=s.character,
                role_brief=_get_section_role_description(s.name, role),
            )
            for s in _section_specs
        )

        _instrument_contract = InstrumentContract(
            instrument_name=instrument_name,
            role=role,
            style=style,
            bars=bars,
            tempo=tempo,
            key=key,
            start_beat=agent_start_beat,
            sections=_role_specs,
            existing_track_id=existing_track_id,
            assigned_color=assigned_color,
            gm_guidance=get_genre_gm_guidance(style, role),
        )

        _agent_timeout = settings.instrument_agent_timeout
        task = asyncio.create_task(
            asyncio.wait_for(
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
                    assigned_color=assigned_color,
                    instrument_contract=_instrument_contract,
                    runtime_context=_runtime_context,
                ),
                timeout=_agent_timeout,
            ),
            name=f"agent/{instrument_name}",
        )
        return task

    # ── Phase 2: All instrument parents launched simultaneously ──
    #
    # Three-level architecture: drum-to-bass coupling is handled at the
    # section child level via SectionSignals.  Bass section children wait
    # on their corresponding drum section child — the coordinator no
    # longer needs two-wave sequencing.  All instrument parents (including
    # bass) start at the same time so bass LLM planning runs in parallel
    # with drums.

    async def _handle_task_failure(task: asyncio.Task) -> None:
        """Emit plan step failures for a crashed agent task."""
        if task.cancelled() or task.exception() is None:
            return
        exc = task.exception()
        logger.error(f"[{trace.trace_id[:8]}] Instrument agent crashed: {exc}")
        for step in plan_tracker.steps:
            if (
                step.parallel_group == "instruments"
                and step.status in ("pending", "active")
            ):
                role_for_step = (step.track_name or "").lower()
                sids = instrument_step_ids.get(role_for_step, [])
                if step.step_id in sids:
                    step.status = "failed"
                    await sse_queue.put({
                        "type": "planStepUpdate",
                        "stepId": step.step_id,
                        "status": "failed",
                        "result": f"Failed: {exc}",
                        "agentId": role_for_step,
                    })

    all_tasks: list[asyncio.Task] = []
    for role in parsed.roles:
        all_tasks.append(_spawn_agent(role))

    _heartbeat_interval = 8.0   # seconds between keepalive pings
    _stall_warn_interval = 30.0  # warn if no real events for this long
    _now_init = asyncio.get_event_loop().time()
    _last_heartbeat_time = _now_init
    _last_progress_time = _now_init   # only reset by real events, not heartbeats
    _last_warn_time = _now_init
    _total_events_emitted = 0
    _stall_warnings = 0
    _tool_error_count = 0

    logger.info(
        f"[{trace.trace_id[:8]}] 🚀 Phase 2: launching {len(all_tasks)} instrument agents "
        f"({', '.join(parsed.roles)})"
    )

    pending: set[asyncio.Task] = set(all_tasks)
    while pending:
        done, pending = await asyncio.wait(pending, timeout=0.05)

        _drained = 0
        while not sse_queue.empty():
            evt = sse_queue.get_nowait()
            if evt.get("type") == "toolError":
                _tool_error_count += 1
            yield await sse_event(evt)
            _drained += 1
            _total_events_emitted += 1
            _last_heartbeat_time = asyncio.get_event_loop().time()
            _last_progress_time = _last_heartbeat_time
            _stall_warnings = 0  # reset stall counter on any activity

        _now = asyncio.get_event_loop().time()

        # SSE keepalive — prevents proxy/client timeout during GPU-bound work
        if _now - _last_heartbeat_time > _heartbeat_interval:
            yield ": heartbeat\n\n"
            _last_heartbeat_time = _now
            logger.debug(
                f"[{trace.trace_id[:8]}] 💓 SSE heartbeat "
                f"(events={_total_events_emitted}, pending_agents={len(pending)})"
            )

        # Client disconnect detection — stop wasting GPU/LLM tokens
        if is_cancelled:
            try:
                if await is_cancelled():
                    logger.warning(
                        f"⚠️ [{trace.trace_id[:8]}] Client disconnected — "
                        f"cancelling {len(pending)} pending agent tasks "
                        f"(events={_total_events_emitted})"
                    )
                    for task in pending:
                        task.cancel()
                    break
            except Exception:
                pass

        # Frozen progress detection — separate timer from heartbeat
        _silence = _now - _last_progress_time
        if _silence > _stall_warn_interval and _now - _last_warn_time > _stall_warn_interval:
            _stall_warnings += 1
            _pending_names = [t.get_name() for t in pending]
            logger.warning(
                f"⚠️ [{trace.trace_id[:8]}] FROZEN PROGRESS: "
                f"no SSE events for {_silence:.0f}s "
                f"(warning #{_stall_warnings}, "
                f"total_events={_total_events_emitted}, "
                f"pending_agents={_pending_names})"
            )
            _last_warn_time = _now

        for task in done:
            _task_name = task.get_name()
            _exc = task.exception() if not task.cancelled() else None
            if task.cancelled():
                logger.warning(
                    f"[{trace.trace_id[:8]}] 🚫 Agent task '{_task_name}' cancelled"
                )
            elif isinstance(_exc, asyncio.TimeoutError):
                logger.error(
                    f"[{trace.trace_id[:8]}] ⏰ Agent task '{_task_name}' TIMED OUT "
                    f"after {settings.instrument_agent_timeout}s — orphaned agent killed"
                )
            elif _exc:
                logger.error(
                    f"[{trace.trace_id[:8]}] ❌ Agent task '{_task_name}' crashed: {_exc}"
                )
            else:
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ Agent task '{_task_name}' completed "
                    f"({len(pending)} still running)"
                )
            await _handle_task_failure(task)

    # Final drain — catch any events queued during the last task completions
    _final_drained = 0
    while not sse_queue.empty():
        _final_evt = sse_queue.get_nowait()
        if _final_evt.get("type") == "toolError":
            _tool_error_count += 1
        yield await sse_event(_final_evt)
        _final_drained += 1
        _total_events_emitted += 1

    logger.info(
        f"[{trace.trace_id[:8]}] 🏁 Phase 2 complete: "
        f"{_total_events_emitted} SSE events emitted "
        f"({_final_drained} in final drain)"
    )

    # ── Phase 3: Mixing coordinator (optional, one LLM call) ──
    logger.info(f"[{trace.trace_id[:8]}] 🎛️  Entering Phase 3 (mixing)")
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
    _notes_generated = summary.get("notesGenerated", 0)
    _regions_created = summary.get("regionsCreated", 0)
    yield await sse_event({
        "type": "summary",
        "tracks": [t.get("name", "") for t in _all_tracks],
        "regions": _regions_created,
        "notes": _notes_generated,
        "effects": summary.get("effectCount", 0),
    })

    yield await sse_event({
        "type": "summary.final",
        "traceId": trace.trace_id,
        **summary,
    })

    # success is False when generation was attempted (regions created) but
    # produced no notes, or when toolErrors were recorded.
    _generation_succeeded = _notes_generated > 0 or _regions_created == 0
    if _tool_error_count > 0 and _notes_generated == 0:
        _generation_succeeded = False
    _complete_warnings: list[str] = []
    if not _generation_succeeded:
        if _tool_error_count > 0:
            _complete_warnings.append(
                f"{_tool_error_count} tool error(s) occurred during composition"
            )
        if _notes_generated == 0 and _regions_created > 0:
            _complete_warnings.append(
                f"MIDI generation failed for all {_regions_created} region(s) — "
                "Orpheus returned no notes. Check that the Orpheus service is "
                "reachable and returning valid responses."
            )
        logger.warning(
            f"⚠️ [{trace.trace_id[:8]}] Composition complete but notesGenerated=0 "
            f"with {_regions_created} region(s), {_tool_error_count} toolError(s) — "
            f"marking success=false"
        )

    yield await sse_event({
        "type": "complete",
        "success": _generation_succeeded,
        "toolCalls": all_collected,
        "stateVersion": store.version,
        "traceId": trace.trace_id,
        **({"warnings": _complete_warnings} if _complete_warnings else {}),
        **_context_usage_fields(usage_tracker, llm.model),
    })
