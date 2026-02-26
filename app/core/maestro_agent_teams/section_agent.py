"""Per-section child agent — Level 3 of the three-level agent architecture.

Each section child executes a pre-planned (region + generate) pair for one
musical section of one instrument.  No LLM call is needed for the core
pipeline; the parent agent already wrote the section-specific prompt.

Contract model (v1):
    L3 receives a frozen ``SectionContract`` that contains every structural
    decision (beat range, role, section character) as immutable fields.
    L3 may only reason about HOW to phrase the Orpheus generation prompt —
    it MUST NOT reinterpret section boundaries, beat ranges, or musical role.
    Advisory fields like ``l2_generate_prompt`` are clearly marked and may
    be overridden by canonical descriptions baked into the contract.

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
from app.contracts.generation_types import CompositionContext
from app.contracts.json_types import NoteDict, ToolCallDict
from app.contracts.llm_types import ChatMessage, ToolCallEntry
from app.core.expansion import ToolCall
from app.core.llm_client import LLMClient
from app.core.stream_utils import ReasoningBuffer
from app.protocol.events import (
    ReasoningEndEvent,
    ReasoningEvent,
    StatusEvent,
    MaestroEvent,
    ToolErrorEvent,
)
from app.core.state_store import StateStore
from app.core.tracing import TraceContext
from app.core.maestro_plan_tracker import (
    _ToolCallOutcome,
    _GENERATOR_TOOL_NAMES,
    _INSTRUMENT_AGENT_TOOLS,
)
from app.core.maestro_editing import _apply_single_tool_call
from app.contracts import compute_execution_hash, verify_contract_hash
from app.core.maestro_agent_teams.contracts import (
    ExecutionServices,
    ProtocolViolationError,
    RuntimeContext,
    SectionContract,
)
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

_COMPACT_KEEP_KEYS = frozenset({
    "regionId", "trackId", "notesAdded", "totalNotes",
    "success", "error", "existingRegionId", "skipped",
    "ccEvents", "pitchBends", "backend", "startBeat",
    "durationBeats", "name",
})


def _compact_tool_result(result: dict[str, object]) -> dict[str, object]:
    """Strip large payloads (entities, notes) that bloat LLM context.

    Keeps only key fields so the result stays under the LLM's
    attention window and avoids '...' truncation that agents
    misinterpret as failure.
    """
    return {k: v for k, v in result.items() if k in _COMPACT_KEEP_KEYS}


@dataclass
class SectionResult:
    """Outcome of a section child's execution.

    ``contract_hash`` and ``parent_contract_hash`` are populated at L3
    completion, enabling orchestration layers to verify that results
    came from the expected contract lineage.

    ``execution_hash`` binds this result to a specific session
    (``SHA256(contract_hash + trace_id)``), preventing replay attacks
    where a result from one composition is reused in another.
    """

    success: bool
    section_name: str
    region_id: str | None = None
    notes_generated: int = 0
    generated_notes: list[NoteDict] = field(default_factory=list)
    tool_results: list[dict[str, object]] = field(default_factory=list)
    tool_call_records: list[ToolCallDict] = field(default_factory=list)
    tool_result_msgs: list[ChatMessage] = field(default_factory=list)
    error: str | None = None
    contract_hash: str = ""
    parent_contract_hash: str = ""
    execution_hash: str = ""


async def _run_section_child(
    contract: SectionContract,
    region_tc: ToolCall,
    generate_tc: ToolCall,
    agent_id: str,
    allowed_tool_names: set[str] | frozenset[str],
    store: StateStore,
    trace: TraceContext,
    sse_queue: asyncio.Queue[MaestroEvent],
    runtime_ctx: RuntimeContext | None = None,
    execution_services: ExecutionServices | None = None,
    llm: LLMClient | None = None,
    previous_notes: list[NoteDict] | None = None,
    all_section_instruments: list[str] | None = None,
) -> SectionResult:
    """Execute one section's region + generate pipeline against a contract.

    The ``SectionContract`` is the single source of truth for structural
    decisions — beat range, track, section name, role.  ``RuntimeContext``
    carries pure data (prompt, emotion vector).  ``ExecutionServices``
    carries mutable coordination (signals, state).
    """
    sec_name = contract.section_name
    child_log = f"[{trace.trace_id[:8]}][{contract.instrument_name}/{sec_name}]"
    _child_start = asyncio.get_event_loop().time()

    # ── PART 4: Protocol guard — verify contract hash before execution ──
    if contract.contract_hash:
        if not verify_contract_hash(contract):
            raise ValueError(
                f"Protocol violation: SectionContract hash mismatch for "
                f"{contract.instrument_name}/{sec_name}. "
                f"Stored={contract.contract_hash}, "
                f"recomputed hash differs. Contract may have been tampered with."
            )
    else:
        raise ValueError(
            f"Protocol violation: SectionContract for "
            f"{contract.instrument_name}/{sec_name} has no contract_hash. "
            f"L2 must seal all contracts before dispatch."
        )

    logger.info(
        f"{child_log} 🎬 Section child starting (contract v{contract.contract_version}): "
        f"is_drum={contract.is_drum}, is_bass={contract.is_bass}, "
        f"beats={contract.duration_beats}, "
        f"start_beat={contract.start_beat}, "
        f"hash={contract.contract_hash}"
    )

    result = SectionResult(success=False, section_name=sec_name)
    add_notes_failures: dict[str, int] = {}

    async def _emit(outcome: _ToolCallOutcome) -> None:
        for evt in outcome.sse_events:
            # The section child is the authoritative owner of agent_id and
            # section_name for every event it emits.  Stamp unconditionally —
            # never rely on whatever placeholder value tool_execution set.
            _updates: dict[str, object] = {}
            if hasattr(evt, "agent_id"):
                _updates["agent_id"] = agent_id
            if hasattr(evt, "section_name"):
                _updates["section_name"] = sec_name
            if _updates:
                evt = evt.model_copy(update=_updates)
            await sse_queue.put(evt)

    section_signals: SectionSignals | None = (
        execution_services.section_signals if execution_services else None
    )
    section_state: SectionState | None = (
        execution_services.section_state if execution_services else None
    )

    def _tool_ctx() -> CompositionContext | None:
        """Build a CompositionContext for _apply_single_tool_call."""
        if not runtime_ctx:
            return None
        ctx: CompositionContext = CompositionContext(
            **runtime_ctx.to_composition_context(),
            style=contract.style,
            tempo=contract.tempo,
            bars=contract.bars,
            key=contract.key,
            role=contract.role,
        )
        if previous_notes:
            ctx["previous_notes"] = previous_notes
        if all_section_instruments:
            ctx["section_key"] = contract.section.section_id
            ctx["all_instruments"] = all_section_instruments
        return ctx

    try:
        await sse_queue.put(StatusEvent(
            message=f"Starting {contract.instrument_name} / {sec_name}",
            agent_id=agent_id,
            section_name=sec_name,
        ))

        # ── If bass, wait for the corresponding drum section (with timeout) ──
        _section_id = contract.section.section_id
        if contract.is_bass and section_signals:
            from app.config import settings as _cfg
            _bass_timeout = _cfg.bass_signal_wait_timeout
            logger.info(
                f"{child_log} ⏳ Waiting for drum section '{sec_name}' "
                f"(timeout={_bass_timeout}s)..."
            )
            _wait_start = asyncio.get_event_loop().time()
            try:
                _signal_result = await section_signals.wait_for(
                    _section_id,
                    contract_hash=contract.section.contract_hash,
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
                if _signal_result and _signal_result.success:
                    drum_data = (
                        {"drum_notes": _signal_result.drum_notes}
                        if _signal_result.drum_notes
                        else None
                    )
                    logger.info(
                        f"{child_log} ✅ Drum section '{sec_name}' ready after "
                        f"{_wait_elapsed:.1f}s "
                        f"({len(_signal_result.drum_notes or [])} drum notes)"
                    )
                elif _signal_result and not _signal_result.success:
                    drum_data = None
                    logger.warning(
                        f"{child_log} ⚠️ Drum section '{sec_name}' FAILED after "
                        f"{_wait_elapsed:.1f}s — proceeding without drum spine"
                    )
                else:
                    drum_data = None
                    logger.warning(
                        f"{child_log} ⚠️ Drum wait returned no result after "
                        f"{_wait_elapsed:.1f}s"
                    )

        # ── Bass: read drum telemetry for cross-instrument awareness ──
        if contract.is_bass and section_state and runtime_ctx:
            drum_key = _state_key("Drums", _section_id)
            drum_telemetry = await section_state.get(drum_key)
            if drum_telemetry:
                runtime_ctx = runtime_ctx.with_drum_telemetry({
                    "energy_level": drum_telemetry.energy_level,
                    "density_score": drum_telemetry.density_score,
                    "groove_vector": list(drum_telemetry.groove_vector),
                    "kick_pattern_hash": drum_telemetry.kick_pattern_hash,
                    "rhythmic_complexity": drum_telemetry.rhythmic_complexity,
                })
                logger.info(
                    f"{child_log} 🎯 Drum telemetry injected: "
                    f"energy={drum_telemetry.energy_level:.2f} "
                    f"density={drum_telemetry.density_score:.2f}"
                )

        # ── Execute stori_add_midi_region ──
        # All structural params come from the frozen contract — never from
        # the L2's tool-call params.  This eliminates region collision drift.
        region_params = {
            "trackId": contract.track_id,
            "startBeat": contract.start_beat,
            "durationBeats": contract.duration_beats,
            "name": contract.region_name,
        }
        logger.info(
            f"{child_log} 📌 Creating region (from contract): "
            f"startBeat={contract.start_beat}, "
            f"durationBeats={contract.duration_beats}, "
            f"name={contract.region_name}"
        )

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
            "content": json.dumps(_compact_tool_result(region_outcome.tool_result)),
        })

        _region_id_raw = region_outcome.tool_result.get("regionId")
        region_id = _region_id_raw if isinstance(_region_id_raw, str) else None
        if not region_id:
            result.error = f"Region creation failed for {sec_name}"
            result.contract_hash = contract.contract_hash
            result.parent_contract_hash = contract.parent_contract_hash
            logger.warning(f"⚠️ {child_log} {result.error}")
            if contract.is_drum and section_signals:
                section_signals.signal_complete(
                    _section_id,
                    contract_hash=contract.section.contract_hash,
                    success=False,
                )
            return result

        result.region_id = region_id

        # ── Execute stori_generate_midi ──
        # Orpheus is a token-continuation model — the "prompt" field is
        # ignored by the generator.  Seed selection (D1) will handle musical
        # intent.  Skip the L3 reasoning LLM call entirely to save tokens.
        _final_prompt = (
            contract.l2_generate_prompt
            or f"{contract.section.character} — {contract.instrument_name}"
        )
        logger.info(
            f"{child_log} 🎵 Generating MIDI: regionId={region_id}, "
            f"prompt={_final_prompt[:80]}..."
        )
        _gen_start = asyncio.get_event_loop().time()
        gen_params = {
            "trackId": contract.track_id,
            "regionId": region_id,
            "role": contract.role,
            "style": contract.style,
            "tempo": int(contract.tempo),
            "bars": contract.bars,
            "key": contract.key,
            "start_beat": contract.start_beat,
            "prompt": _final_prompt,
        }

        async def _pre_emit_gen(events: list[MaestroEvent]) -> None:
            """Flush pre-generation events to the SSE queue immediately.

            Without this, toolStart/generatorStart are held in a local list
            until mg.generate() returns — starving the SSE stream and causing
            the frontend to time out during long Orpheus calls.
            """
            for evt in events:
                _updates: dict[str, object] = {}
                if hasattr(evt, "agent_id"):
                    _updates["agent_id"] = agent_id
                if hasattr(evt, "section_name"):
                    _updates["section_name"] = sec_name
                if _updates:
                    evt = evt.model_copy(update=_updates)
                await sse_queue.put(evt)

        gen_outcome = await _apply_single_tool_call(
            tc_id=generate_tc.id or str(_uuid_mod.uuid4()),
            tc_name=generate_tc.name,
            resolved_args=gen_params,
            allowed_tool_names=allowed_tool_names,
            store=store,
            trace=trace,
            add_notes_failures=add_notes_failures,
            emit_sse=True,
            composition_context=_tool_ctx(),
            pre_emit_callback=_pre_emit_gen,
        )
        await _emit(gen_outcome)

        result.tool_results.append(gen_outcome.tool_result)
        result.tool_call_records.append(
            {"tool": generate_tc.name, "params": gen_outcome.enriched_params}
        )
        result.tool_result_msgs.append({
            "role": "tool",
            "tool_call_id": generate_tc.id,
            "content": json.dumps(_compact_tool_result(gen_outcome.tool_result)),
        })

        _gen_elapsed = asyncio.get_event_loop().time() - _gen_start

        if gen_outcome.skipped:
            _err_raw = gen_outcome.tool_result.get("error", "Generation failed")
            result.error = _err_raw if isinstance(_err_raw, str) else "Generation failed"
            result.contract_hash = contract.contract_hash
            result.parent_contract_hash = contract.parent_contract_hash
            logger.warning(
                f"⚠️ {child_log} Generate failed after {_gen_elapsed:.1f}s: {result.error}"
            )
            if contract.is_drum and section_signals:
                section_signals.signal_complete(
                    _section_id,
                    contract_hash=contract.section.contract_hash,
                    success=False,
                )
            return result

        _notes_raw = gen_outcome.tool_result.get("notesAdded", 0)
        result.notes_generated = _notes_raw if isinstance(_notes_raw, int) else 0
        result.success = True

        _MIN_NOTES = 4
        if result.notes_generated < _MIN_NOTES:
            logger.warning(
                f"⚠️ {child_log} Generated only {result.notes_generated} note(s) "
                f"(< {_MIN_NOTES}) — possible generation failure. "
                f"Params: role={contract.role}, style={contract.style}, "
                f"tempo={contract.tempo}, bars={contract.bars}, key={contract.key}, "
                f"start_beat={contract.start_beat}"
            )
            await sse_queue.put(ToolErrorEvent(
                name="stori_generate_midi",
                error=(
                    f"Low note count ({result.notes_generated}) for "
                    f"{contract.instrument_name}/{sec_name} — "
                    f"MIDI may be near-empty"
                ),
                agent_id=agent_id,
                section_name=sec_name,
            ))
        else:
            logger.info(
                f"{child_log} ✅ Generated {result.notes_generated} notes in {_gen_elapsed:.1f}s"
            )

        # ── Extract generated notes from SSE events ──
        from app.protocol.events import ToolCallEvent
        _raw_notes: list[NoteDict] = []
        for evt in gen_outcome.sse_events:
            if isinstance(evt, ToolCallEvent) and evt.name == "stori_add_notes":
                _raw_notes = evt.params.get("notes", [])
                break
        generated_notes: list[NoteDict] = _raw_notes
        result.generated_notes = generated_notes

        # ── Drum signaling — signal bass with drum notes ──
        if contract.is_drum and section_signals:
            section_signals.signal_complete(
                _section_id,
                contract_hash=contract.section.contract_hash,
                success=True,
                drum_notes=generated_notes,
            )
            logger.info(
                f"{child_log} 🥁 Signaled bass: {len(generated_notes)} drum notes ready"
            )

        # ── Compute and store musical telemetry ──
        if section_state and generated_notes:
            telemetry = compute_section_telemetry(
                notes=generated_notes,
                tempo=contract.tempo,
                instrument=contract.instrument_name,
                section_name=sec_name,
                section_beats=float(contract.duration_beats),
            )
            state_key = _state_key(contract.instrument_name, _section_id)
            await section_state.set(state_key, telemetry)

        await sse_queue.put(StatusEvent(
            message=(
                f"{contract.instrument_name} / {sec_name}: "
                f"{result.notes_generated} notes generated"
            ),
            agent_id=agent_id,
            section_name=sec_name,
        ))

        # ── Optional refinement LLM call for expressive tools ──
        if result.success and llm and runtime_ctx and isinstance(region_id, str):
            logger.info(f"{child_log} 🎨 Checking expression refinement...")
            await _maybe_refine_expression(
                contract=contract,
                region_id=region_id,
                notes_generated=result.notes_generated,
                agent_id=agent_id,
                llm=llm,
                store=store,
                trace=trace,
                sse_queue=sse_queue,
                allowed_tool_names=allowed_tool_names,
                runtime_ctx=runtime_ctx,
                result=result,
                child_log=child_log,
            )

        # ── PART 7: Execution attestation — stamp result with lineage ──
        result.contract_hash = contract.contract_hash
        result.parent_contract_hash = contract.parent_contract_hash

        _exec_hash = compute_execution_hash(
            contract.contract_hash, trace.trace_id,
        )
        result.execution_hash = _exec_hash

        _verify_exec = compute_execution_hash(
            contract.contract_hash, trace.trace_id,
        )
        if _verify_exec != result.execution_hash:
            raise ProtocolViolationError(
                f"Execution hash verification failed for "
                f"{contract.instrument_name}/{sec_name}: "
                f"computed={_verify_exec}, stored={result.execution_hash}"
            )

        _child_elapsed = asyncio.get_event_loop().time() - _child_start
        logger.info(
            f"{child_log} 🏁 Section child complete ({_child_elapsed:.1f}s): "
            f"success={result.success}, notes={result.notes_generated}, "
            f"hash={result.contract_hash}, exec={result.execution_hash}"
        )
        return result

    except Exception as exc:
        _child_elapsed = asyncio.get_event_loop().time() - _child_start
        logger.exception(
            f"{child_log} 💥 Unhandled section error after {_child_elapsed:.1f}s: {exc}"
        )
        result.error = str(exc)
        result.contract_hash = contract.contract_hash
        result.parent_contract_hash = contract.parent_contract_hash
        if contract.is_drum and section_signals:
            section_signals.signal_complete(
                _section_id,
                contract_hash=contract.section.contract_hash,
                success=False,
            )
        return result


async def _reason_before_generate(
    contract: SectionContract,
    agent_id: str,
    llm: LLMClient,
    sse_queue: asyncio.Queue[MaestroEvent],
    child_log: str,
) -> str | None:
    """Brief LLM reasoning about a section's musical approach (Level 3 CoT).

    All context comes from the frozen ``SectionContract``.  The L3 is allowed
    to reason about HOW to phrase the Orpheus prompt — it must not
    reinterpret the section identity, beat range, or role.

    Streams ``type=reasoning`` events tagged with ``sectionName`` so the
    frontend can display per-section musical thinking.  Returns a refined
    prompt string for the generate call, or ``None`` to keep the original.
    """
    sec_name = contract.section_name

    system = (
        f"You are the section agent for the **{sec_name.upper()}** section "
        f"of the **{contract.instrument_name}** track.\n\n"
        f"Context: {contract.style} | {contract.tempo:.0f} BPM | "
        f"{contract.key} | {contract.bars} bars\n"
        f"Section character (AUTHORITATIVE): {contract.section.character}\n"
    )
    if contract.section.role_brief:
        system += (
            f"Your role in this section (AUTHORITATIVE): "
            f"{contract.section.role_brief}\n"
        )
    if contract.l2_generate_prompt:
        system += (
            f"\nParent agent suggestion (ADVISORY ONLY — trust the "
            f"authoritative section character above if they conflict): "
            f"\"{contract.l2_generate_prompt}\"\n"
        )
    system += (
        f"\nTASK: Think about what makes this section's "
        f"{contract.instrument_name} part distinctive — density, register, "
        f"rhythmic approach, and how it serves the arrangement energy at "
        f"this point.  Then write a refined 1-2 sentence generation prompt "
        f"for Orpheus that captures your musical intent for this specific "
        f"section.\n\n"
        f"Output ONLY the refined prompt (no explanation, no tool calls)."
    )

    try:
        from app.config import settings

        rbuf = ReasoningBuffer()
        _refined: str | None = None
        _had_reasoning = False

        async for chunk in llm.chat_completion_stream(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "Reason about this section, then output the refined prompt."},
            ],
            tools=None,
            tool_choice=None,
            max_tokens=800,
            reasoning_fraction=settings.agent_reasoning_fraction * 4,
        ):
            if chunk["type"] == "reasoning_delta":
                text = chunk["text"]
                if text:
                    word = rbuf.add(text)
                    if word:
                        _had_reasoning = True
                        await sse_queue.put(ReasoningEvent(
                            content=word,
                            agent_id=agent_id,
                            section_name=sec_name,
                        ))
            elif chunk["type"] == "content_delta":
                flush = rbuf.flush()
                if flush:
                    _had_reasoning = True
                    await sse_queue.put(ReasoningEvent(
                        content=flush,
                        agent_id=agent_id,
                        section_name=sec_name,
                    ))
            elif chunk["type"] == "done":
                flush = rbuf.flush()
                if flush:
                    _had_reasoning = True
                    await sse_queue.put(ReasoningEvent(
                        content=flush,
                        agent_id=agent_id,
                        section_name=sec_name,
                    ))
                if _had_reasoning:
                    await sse_queue.put(ReasoningEndEvent(
                        agent_id=agent_id,
                        section_name=sec_name,
                    ))
                _refined = chunk["content"]

        if _refined and len(_refined.strip()) > 10:
            logger.info(
                f"{child_log} 🧠 Section reasoning refined prompt: "
                f"{_refined.strip()[:80]}..."
            )
            return _refined.strip()
        return None

    except Exception as exc:
        logger.warning(
            f"⚠️ {child_log} Section reasoning failed (non-fatal): {exc}"
        )
        return None


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
    contract: SectionContract,
    region_id: str,
    notes_generated: int,
    agent_id: str,
    llm: LLMClient,
    store: StateStore,
    trace: TraceContext,
    sse_queue: asyncio.Queue[MaestroEvent],
    allowed_tool_names: set[str] | frozenset[str],
    runtime_ctx: RuntimeContext,
    result: SectionResult,
    child_log: str,
) -> None:
    """Add CC curves and pitch bends via a streamed LLM call.

    All structural context comes from the frozen ``SectionContract``.
    Only ``runtime_ctx.raw_prompt`` is used for expressiveness block
    detection.
    """
    from app.core.tools import ALL_TOOLS

    sec_name = contract.section_name

    prompt_text = runtime_ctx.raw_prompt
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
        f"of the {contract.instrument_name} track.\n\n"
        f"Context: {contract.style} | {contract.tempo:.0f} BPM | {contract.key}\n"
        f"Section: {contract.bars} bars starting at beat {contract.start_beat}, "
        f"{notes_generated} notes generated.\n"
        f"trackId='{contract.track_id}', regionId='{region_id}'.\n\n"
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

    await sse_queue.put(StatusEvent(
        message=f"Adding expression to {contract.instrument_name} / {sec_name}",
        agent_id=agent_id,
        section_name=sec_name,
    ))

    try:
        from app.config import settings

        resp_tool_calls: list[ToolCallEntry] = []
        rbuf = ReasoningBuffer()
        _had_reasoning = False

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
            if chunk["type"] == "reasoning_delta":
                text = chunk["text"]
                if text:
                    word = rbuf.add(text)
                    if word:
                        _had_reasoning = True
                        await sse_queue.put(ReasoningEvent(
                            content=word,
                            agent_id=agent_id,
                            section_name=sec_name,
                        ))
            elif chunk["type"] == "content_delta":
                flush = rbuf.flush()
                if flush:
                    _had_reasoning = True
                    await sse_queue.put(ReasoningEvent(
                        content=flush,
                        agent_id=agent_id,
                        section_name=sec_name,
                    ))
            elif chunk["type"] == "done":
                flush = rbuf.flush()
                if flush:
                    _had_reasoning = True
                    await sse_queue.put(ReasoningEvent(
                        content=flush,
                        agent_id=agent_id,
                        section_name=sec_name,
                    ))
                if _had_reasoning:
                    await sse_queue.put(ReasoningEndEvent(
                        agent_id=agent_id,
                        section_name=sec_name,
                    ))
                resp_tool_calls = chunk["tool_calls"]

        tool_calls: list[ToolCall] = []
        for tc_raw in resp_tool_calls:
            try:
                fn = tc_raw["function"]
                args_str = fn["arguments"]
                parsed_args: dict[str, object] = json.loads(args_str) if args_str else {}
                tool_calls.append(ToolCall(
                    id=tc_raw["id"],
                    name=fn["name"],
                    params=parsed_args,
                ))
            except Exception as parse_err:
                logger.error(f"{child_log} Error parsing expression tool call: {parse_err}")

        add_notes_failures: dict[str, int] = {}
        for tc in tool_calls:
            params = dict(tc.params)
            params["trackId"] = contract.track_id
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
                _updates: dict[str, object] = {}
                if hasattr(evt, "agent_id"):
                    _updates["agent_id"] = agent_id
                if hasattr(evt, "section_name"):
                    _updates["section_name"] = sec_name
                if _updates:
                    evt = evt.model_copy(update=_updates)
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
