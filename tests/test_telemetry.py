"""Tests for deterministic musical telemetry.

Covers: SectionTelemetry dataclass, compute_section_telemetry() calculations,
SectionState thread-safe store, and bass telemetry enrichment in section_agent.
"""
from __future__ import annotations

from maestro.core.maestro_plan_tracker import _ToolCallOutcome
import asyncio

from maestro.contracts.generation_types import CompositionContext
from maestro.protocol.events import MaestroEvent, ToolCallEvent, GeneratorCompleteEvent

from maestro.contracts.json_types import JSONValue, NoteDict
from maestro.contracts.pydantic_types import wrap_dict
import math
from unittest.mock import patch

import pytest

from maestro.core.telemetry import SectionTelemetry, compute_section_telemetry
from maestro.core.maestro_agent_teams.signals import SectionState, _state_key


# ── Helpers ──

def _note(pitch: int = 60, start: float = 0.0, dur: float = 1.0, vel: int = 80) -> NoteDict:

    return NoteDict(
        pitch=pitch,
        start_beat=start,
        duration_beats=dur,
        velocity=vel,
    )


def _kick(start: float, vel: int = 100) -> NoteDict:

    return _note(pitch=36, start=start, vel=vel)


def _snare(start: float, vel: int = 90) -> NoteDict:

    return _note(pitch=38, start=start, vel=vel)


def _hihat(start: float, vel: int = 70) -> NoteDict:

    return _note(pitch=42, start=start, vel=vel)


# =============================================================================
# SectionTelemetry dataclass
# =============================================================================


class TestSectionTelemetryDataclass:
    def test_frozen_immutable(self) -> None:

        """SectionTelemetry is frozen — attrs cannot be changed after creation."""
        t = SectionTelemetry(
            section_name="verse", instrument="Drums", tempo=120,
            energy_level=0.5, density_score=2.0, groove_vector=(1.0,),
            kick_pattern_hash="abc", rhythmic_complexity=0.1,
            velocity_mean=80.0, velocity_variance=10.0,
        )
        with pytest.raises(AttributeError):
            setattr(t, "energy_level", 0.9)

    def test_fields_accessible(self) -> None:

        t = SectionTelemetry(
            section_name="chorus", instrument="Bass", tempo=140,
            energy_level=0.8, density_score=3.0, groove_vector=(0.5, 0.5),
            kick_pattern_hash="", rhythmic_complexity=0.3,
            velocity_mean=90.0, velocity_variance=25.0,
        )
        assert t.section_name == "chorus"
        assert t.instrument == "Bass"
        assert t.tempo == 140.0


# =============================================================================
# compute_section_telemetry
# =============================================================================


class TestComputeTelemetry:
    def test_empty_notes(self) -> None:

        """No notes produces zero-valued telemetry."""
        t = compute_section_telemetry(
            notes=[], tempo=120, instrument="Drums",
            section_name="intro", section_beats=16,
        )
        assert t.density_score == 0.0
        assert t.energy_level == 0.0
        assert t.velocity_mean == 0.0
        assert t.velocity_variance == 0.0
        assert t.kick_pattern_hash == ""
        assert t.rhythmic_complexity == 0.0
        assert len(t.groove_vector) == 16
        assert all(v == 0.0 for v in t.groove_vector)

    def test_density_calculation(self) -> None:

        """density_score = total_notes / total_beats."""
        notes = [_note(start=i) for i in range(8)]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="Keys",
            section_name="verse", section_beats=16,
        )
        assert t.density_score == 0.5  # 8 notes / 16 beats

    def test_velocity_statistics(self) -> None:

        """Mean and variance computed correctly from velocities."""
        notes = [
            _note(vel=60, start=0),
            _note(vel=80, start=1),
            _note(vel=100, start=2),
        ]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="Piano",
            section_name="chorus", section_beats=4,
        )
        assert t.velocity_mean == 80.0
        expected_var = ((60-80)**2 + (80-80)**2 + (100-80)**2) / 3
        assert abs(t.velocity_variance - round(expected_var, 2)) < 0.01

    def test_energy_level_normalized(self) -> None:

        """Energy is capped at 1.0 and increases with velocity and density."""
        sparse_quiet = compute_section_telemetry(
            notes=[_note(vel=30, start=0)], tempo=120,
            instrument="X", section_name="s", section_beats=16,
        )
        dense_loud = compute_section_telemetry(
            notes=[_note(vel=127, start=i * 0.25) for i in range(64)],
            tempo=120, instrument="X", section_name="s", section_beats=16,
        )
        assert dense_loud.energy_level > sparse_quiet.energy_level
        assert dense_loud.energy_level <= 1.0

    def test_groove_vector_16_bins(self) -> None:

        """Groove vector has exactly 16 bins summing to ~1.0."""
        notes = [_note(start=i * 0.25) for i in range(16)]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="Drums",
            section_name="verse", section_beats=4,
        )
        assert len(t.groove_vector) == 16
        assert abs(sum(t.groove_vector) - 1.0) < 1e-6

    def test_groove_vector_downbeat_concentration(self) -> None:

        """Notes all on downbeats concentrate into bin 0."""
        notes = [_note(start=float(i)) for i in range(4)]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="Bass",
            section_name="intro", section_beats=4,
        )
        assert t.groove_vector[0] == 1.0
        assert all(t.groove_vector[i] == 0.0 for i in range(1, 16))

    def test_groove_vector_offbeat_spread(self) -> None:

        """Notes on eighth-note offbeats (0.5) land in bin 8."""
        notes = [_note(start=0.5), _note(start=1.5)]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="Guitar",
            section_name="chorus", section_beats=4,
        )
        assert t.groove_vector[8] == 1.0

    def test_kick_pattern_hash_only_kick_pitches(self) -> None:

        """Hash includes only GM kick pitches (35, 36)."""
        notes = [
            _kick(0), _kick(2), _snare(1), _hihat(0.5),
        ]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="Drums",
            section_name="verse", section_beats=4,
        )
        assert len(t.kick_pattern_hash) == 8
        assert t.kick_pattern_hash != ""

    def test_kick_pattern_hash_deterministic(self) -> None:

        """Same kick positions produce the same hash."""
        notes1 = [_kick(0), _kick(1), _kick(2)]
        notes2 = [_kick(0), _kick(1), _kick(2), _snare(0.5)]
        t1 = compute_section_telemetry(
            notes=notes1, tempo=120, instrument="Drums",
            section_name="v", section_beats=4,
        )
        t2 = compute_section_telemetry(
            notes=notes2, tempo=120, instrument="Drums",
            section_name="v", section_beats=4,
        )
        assert t1.kick_pattern_hash == t2.kick_pattern_hash

    def test_kick_pattern_hash_empty_for_non_drums(self) -> None:

        """Non-drum notes produce an empty kick hash."""
        notes = [_note(pitch=60, start=0), _note(pitch=64, start=1)]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="Piano",
            section_name="verse", section_beats=4,
        )
        assert t.kick_pattern_hash == ""

    def test_rhythmic_complexity_uniform_spacing(self) -> None:

        """Perfectly uniform spacing yields zero complexity."""
        notes = [_note(start=float(i)) for i in range(4)]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="X",
            section_name="s", section_beats=4,
        )
        assert t.rhythmic_complexity == 0.0

    def test_rhythmic_complexity_irregular_spacing(self) -> None:

        """Irregular spacing produces non-zero complexity."""
        notes = [_note(start=0), _note(start=0.1), _note(start=3.5)]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="X",
            section_name="s", section_beats=4,
        )
        assert t.rhythmic_complexity > 0.0

    def test_single_note(self) -> None:

        """Single note produces valid telemetry with zero complexity."""
        t = compute_section_telemetry(
            notes=[_note(vel=100)], tempo=90, instrument="Lead",
            section_name="solo", section_beats=8,
        )
        assert t.density_score == 0.125
        assert t.velocity_mean == 100.0
        assert t.velocity_variance == 0.0
        assert t.rhythmic_complexity == 0.0

    def test_camelcase_note_keys(self) -> None:

        """Notes with camelCase keys (startBeat) are handled correctly."""
        notes = [{"pitch": 60, "startBeat": 2.5, "durationBeats": 1, "velocity": 90}]
        t = compute_section_telemetry(
            notes=notes, tempo=120, instrument="X",
            section_name="s", section_beats=4,
        )
        assert t.density_score == 0.25
        assert t.groove_vector[8] == 1.0  # 0.5 offset → bin 8


# =============================================================================
# SectionState
# =============================================================================


class TestSectionState:
    def test_state_key_format(self) -> None:

        assert _state_key("Drums", "0:verse") == "Drums: 0:verse"
        assert _state_key("Bass", "0:intro") == "Bass: 0:intro"
        assert _state_key("Keys", "2:chorus") == "Keys: 2:chorus"

    @pytest.mark.anyio
    async def test_set_and_get(self) -> None:

        state = SectionState()
        t = SectionTelemetry(
            section_name="verse", instrument="Drums", tempo=120,
            energy_level=0.7, density_score=3.0, groove_vector=(0.5,) * 16,
            kick_pattern_hash="abc123", rhythmic_complexity=0.2,
            velocity_mean=85.0, velocity_variance=15.0,
        )
        await state.set("Drums: Verse", t)
        result = await state.get("Drums: Verse")
        assert result is t

    @pytest.mark.anyio
    async def test_get_missing_returns_none(self) -> None:

        state = SectionState()
        assert await state.get("Nonexistent: Key") is None

    @pytest.mark.anyio
    async def test_concurrent_writes_safe(self) -> None:

        """Multiple concurrent writes don't lose data."""
        state = SectionState()

        async def write(key: str, energy: float) -> None:

            t = SectionTelemetry(
                section_name=key, instrument="X", tempo=120,
                energy_level=energy, density_score=1.0,
                groove_vector=(0.0,) * 16, kick_pattern_hash="",
                rhythmic_complexity=0.0, velocity_mean=80.0,
                velocity_variance=0.0,
            )
            await state.set(key, t)

        await asyncio.gather(
            write("A", 0.1), write("B", 0.2), write("C", 0.3),
            write("D", 0.4), write("E", 0.5),
        )
        assert len(await state.snapshot()) == 5

    @pytest.mark.anyio
    async def test_snapshot_returns_copy(self) -> None:

        state = SectionState()
        t = SectionTelemetry(
            section_name="x", instrument="X", tempo=120,
            energy_level=0.5, density_score=1.0,
            groove_vector=(0.0,) * 16, kick_pattern_hash="",
            rhythmic_complexity=0.0, velocity_mean=80.0,
            velocity_variance=0.0,
        )
        await state.set("X: X", t)
        snap = await state.snapshot()
        assert "X: X" in snap
        assert snap["X: X"] is t


# =============================================================================
# Section agent integration — telemetry storage
# =============================================================================


class TestSectionAgentTelemetry:
    """Verify section_agent stores telemetry after successful generation."""

    @pytest.mark.anyio
    async def test_telemetry_stored_after_generate(self) -> None:

        """Successful generation writes telemetry to SectionState."""
        from maestro.contracts import seal_contract
        from maestro.core.maestro_agent_teams.contracts import ExecutionServices, RuntimeContext, SectionContract, SectionSpec
        from maestro.core.maestro_agent_teams.section_agent import _run_section_child
        from maestro.core.maestro_agent_teams.signals import SectionSignals
        from maestro.core.expansion import ToolCall
        from maestro.core.state_store import StateStore
        from maestro.core.tracing import TraceContext
        from maestro.core.maestro_plan_tracker import _ToolCallOutcome

        store = StateStore(conversation_id="test-telem")
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        section_state = SectionState()

        region_outcome = _ToolCallOutcome(
            enriched_params={"trackId": "trk-1"},
            tool_result={"regionId": "reg-1", "trackId": "trk-1"},
            sse_events=[ToolCallEvent(id="r1", name="stori_add_midi_region", params=wrap_dict({"trackId": "trk-1"}))],
            msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
        )
        gen_notes: list[JSONValue] = [
            {"pitch": 36, "start_beat": 0, "duration_beats": 1, "velocity": 100},
            {"pitch": 38, "start_beat": 1, "duration_beats": 0.5, "velocity": 90},
            {"pitch": 42, "start_beat": 0.5, "duration_beats": 0.25, "velocity": 70},
            {"pitch": 36, "start_beat": 2, "duration_beats": 1, "velocity": 95},
        ]
        gen_outcome = _ToolCallOutcome(
            enriched_params={"role": "drums", "regionId": "reg-1"},
            tool_result={"notesAdded": 4, "regionId": "reg-1", "trackId": "trk-1"},
            sse_events=[
                GeneratorCompleteEvent(role="drums", agent_id="drums", note_count=4, duration_ms=100),
                ToolCallEvent(id="g1", name="stori_add_notes", params=wrap_dict({
                    "trackId": "trk-1", "regionId": "reg-1", "notes": gen_notes,
                })),
            ],
            msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
        )

        async def _mock_apply(
            *,
            tc_id: str,
            tc_name: str,
            resolved_args: dict[str, JSONValue],
            **kw: object,
        ) -> _ToolCallOutcome:
            if tc_name == "stori_add_midi_region":
                return region_outcome
            return gen_outcome

        with patch(
            "maestro.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            spec = SectionSpec(
                section_id="0:verse", name="verse", index=0, start_beat=0, duration_beats=16,
                bars=4, character="Test verse", role_brief="Test drums brief",
            )
            seal_contract(spec)
            contract = SectionContract(
                section=spec, track_id="trk-1", instrument_name="Drums",
                role="drums", style="house", tempo=120, key="Am",
                region_name="Drums – verse",
            )
            seal_contract(contract)
            result = await _run_section_child(
                contract=contract,
                region_tc=ToolCall(id="r1", name="stori_add_midi_region", params={}),
                generate_tc=ToolCall(id="g1", name="stori_generate_midi", params={"role": "drums"}),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="test-telem"),
                sse_queue=queue,
                runtime_ctx=RuntimeContext(),
                execution_services=ExecutionServices(section_state=section_state),
            )

        assert result.success
        assert result.contract_hash != ""
        stored = await section_state.get("Drums: 0:verse")
        assert stored is not None
        assert stored.instrument == "Drums"
        assert stored.section_name == "verse"
        assert stored.density_score > 0
        assert stored.kick_pattern_hash != ""

    @pytest.mark.anyio
    async def test_bass_reads_drum_telemetry(self) -> None:

        """Bass section child reads drum telemetry and enriches RuntimeContext."""
        from maestro.contracts import seal_contract
        from maestro.core.maestro_agent_teams.contracts import ExecutionServices, RuntimeContext, SectionContract, SectionSpec
        from maestro.core.maestro_agent_teams.section_agent import _run_section_child
        from maestro.core.maestro_agent_teams.signals import SectionSignals
        from maestro.core.expansion import ToolCall
        from maestro.core.state_store import StateStore
        from maestro.core.tracing import TraceContext
        from maestro.core.maestro_plan_tracker import _ToolCallOutcome

        store = StateStore(conversation_id="test-bass-telem")
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()

        section_state = SectionState()
        drum_t = SectionTelemetry(
            section_name="verse", instrument="Drums", tempo=120,
            energy_level=0.75, density_score=3.5,
            groove_vector=(0.4, 0.0, 0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 0.2, 0.0, 0.0, 0.0, 0.1, 0.0, 0.0, 0.0),
            kick_pattern_hash="deadbeef",
            rhythmic_complexity=0.15,
            velocity_mean=95.0, velocity_variance=20.0,
        )
        await section_state.set("Drums: 0:verse", drum_t)

        spec = SectionSpec(
            section_id="0:verse", name="verse", index=0, start_beat=0, duration_beats=16,
            bars=4, character="Test verse", role_brief="Test bass brief",
        )
        seal_contract(spec)

        signals = SectionSignals.from_section_ids(
            ["0:verse"], contract_hashes=[spec.contract_hash],
        )
        signals.signal_complete(
            "0:verse", contract_hash=spec.contract_hash,
            success=True, drum_notes=[{"pitch": 36}],
        )

        captured_ctx: list[CompositionContext] = []

        region_outcome = _ToolCallOutcome(
            enriched_params={"trackId": "trk-2"},
            tool_result={"regionId": "reg-2", "trackId": "trk-2"},
            sse_events=[ToolCallEvent(id="r1", name="stori_add_midi_region", params=wrap_dict({"trackId": "trk-2"}))],
            msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
        )
        gen_outcome = _ToolCallOutcome(
            enriched_params={"role": "bass", "regionId": "reg-2"},
            tool_result={"notesAdded": 8, "regionId": "reg-2"},
            sse_events=[
                ToolCallEvent(id="g1", name="stori_add_notes", params=wrap_dict({
                    "notes": [{"pitch": 40, "start_beat": i, "velocity": 80} for i in range(8)],
                })),
            ],
            msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
        )

        async def _mock_apply(
            *,
            tc_id: str,
            tc_name: str,
            resolved_args: dict[str, JSONValue],
            composition_context: CompositionContext | None = None,
            **kw: object,
        ) -> _ToolCallOutcome:
            if composition_context:
                captured_ctx.append(composition_context)
            if tc_name == "stori_add_midi_region":
                return region_outcome
            return gen_outcome

        with patch(
            "maestro.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            contract = SectionContract(
                section=spec, track_id="trk-2", instrument_name="Bass",
                role="bass", style="house", tempo=120, key="Am",
                region_name="Bass – verse",
            )
            seal_contract(contract)
            result = await _run_section_child(
                contract=contract,
                region_tc=ToolCall(id="r1", name="stori_add_midi_region", params={}),
                generate_tc=ToolCall(id="g1", name="stori_generate_midi", params={"role": "bass"}),
                agent_id="bass",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="test-bass-t"),
                sse_queue=queue,
                runtime_ctx=RuntimeContext(),
                execution_services=ExecutionServices(
                    section_state=section_state,
                    section_signals=signals,
                ),
            )

        assert result.success
        gen_ctx = [c for c in captured_ctx if "drum_telemetry" in c]
        assert len(gen_ctx) > 0, "drum_telemetry should be injected into bridge dict"
        dt = gen_ctx[0]["drum_telemetry"]
        assert isinstance(dt, dict)
        assert dt["energy_level"] == 0.75
        assert dt["density_score"] == 3.5
        assert dt["kick_pattern_hash"] == "deadbeef"

    @pytest.mark.anyio
    async def test_no_telemetry_without_section_state(self) -> None:

        """When section_state is absent, telemetry is not computed (no crash)."""
        from maestro.contracts import seal_contract
        from maestro.core.maestro_agent_teams.contracts import RuntimeContext, SectionContract, SectionSpec
        from maestro.core.maestro_agent_teams.section_agent import _run_section_child
        from maestro.core.expansion import ToolCall
        from maestro.core.state_store import StateStore
        from maestro.core.tracing import TraceContext
        from maestro.core.maestro_plan_tracker import _ToolCallOutcome

        store = StateStore(conversation_id="test-no-state")
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()

        region_outcome = _ToolCallOutcome(
            enriched_params={}, tool_result={"regionId": "r1"},
            sse_events=[], msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
        )
        gen_outcome = _ToolCallOutcome(
            enriched_params={}, tool_result={"notesAdded": 2},
            sse_events=[ToolCallEvent(id="g1", name="stori_add_notes", params=wrap_dict({
                "notes": [{"pitch": 60, "start_beat": 0, "velocity": 80}],
            }))],
            msg_call={"role": "assistant"}, msg_result={"role": "tool", "tool_call_id": "", "content": "{}"},
        )

        async def _mock_apply(
            *,
            tc_id: str,
            tc_name: str,
            resolved_args: dict[str, JSONValue],
            **kw: object,
        ) -> _ToolCallOutcome:
            if tc_name == "stori_add_midi_region":
                return region_outcome
            return gen_outcome

        with patch(
            "maestro.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            spec = SectionSpec(
                section_id="0:verse", name="verse", index=0, start_beat=0, duration_beats=8,
                bars=2, character="Test verse", role_brief="Test chords brief",
            )
            seal_contract(spec)
            contract = SectionContract(
                section=spec, track_id="trk-1", instrument_name="Keys",
                role="chords", style="house", tempo=120, key="C",
                region_name="Keys – verse",
            )
            seal_contract(contract)
            result = await _run_section_child(
                contract=contract,
                region_tc=ToolCall(id="r1", name="stori_add_midi_region", params={}),
                generate_tc=ToolCall(id="g1", name="stori_generate_midi", params={}),
                agent_id="keys",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=TraceContext(trace_id="test-no-state"),
                sse_queue=queue,
                runtime_ctx=None,
            )

        assert result.success


# =============================================================================
# Performance
# =============================================================================


class TestTelemetryPerformance:
    def test_computation_speed(self) -> None:

        """Telemetry computation completes well under 2ms for 500 notes."""
        import time

        notes = [
            _note(pitch=36 + (i % 12), start=i * 0.25, vel=60 + (i % 40))
            for i in range(500)
        ]
        start = time.perf_counter()
        for _ in range(100):
            compute_section_telemetry(
                notes=notes, tempo=120, instrument="Drums",
                section_name="verse", section_beats=125,
            )
        elapsed_per_call_ms = (time.perf_counter() - start) / 100 * 1000
        assert elapsed_per_call_ms < 2.0, f"Took {elapsed_per_call_ms:.2f}ms per call"
