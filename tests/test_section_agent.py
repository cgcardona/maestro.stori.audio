"""Tests for the three-level agent architecture.

Covers: SectionSignals, SectionResult, _run_section_child, and
_dispatch_section_children.  Edge cases: single-section, no-drums,
no-bass, section child failure, missing regionId.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.expansion import ToolCall
from app.core.state_store import StateStore
from app.core.tracing import TraceContext
from app.contracts import seal_contract
from app.core.maestro_agent_teams.contracts import (
    ExecutionServices,
    InstrumentContract,
    RuntimeContext,
    SectionContract,
    SectionSpec,
)
from app.core.maestro_agent_teams.signals import SectionSignalResult, SectionSignals
from app.core.maestro_agent_teams.section_agent import (
    SectionResult,
    _run_section_child,
)
from app.core.maestro_plan_tracker import _ToolCallOutcome


def _trace() -> TraceContext:
    return TraceContext(trace_id="test-section-agent")


def _section(name: str = "verse", start_beat: int = 0, length_beats: int = 16) -> dict[str, Any]:

    return {"name": name, "start_beat": start_beat, "length_beats": length_beats}


def _instrument_contract(
    sections: list[dict[str, Any]],
    instrument_name: str = "Drums",
    role: str = "drums",
    style: str = "house",
    tempo: float = 120.0,
    key: str = "Am",
    existing_track_id: str | None = None,
) -> InstrumentContract:
    """Build an InstrumentContract from section dicts for dispatch tests."""
    specs = tuple(
        SectionSpec(
            section_id=f"{i}:{s['name']}",
            name=s["name"],
            index=i,
            start_beat=s.get("start_beat", 0),
            duration_beats=s.get("length_beats", 16),
            bars=max(1, s.get("length_beats", 16) // 4),
            character=f"Test {s['name']}",
            role_brief=f"Test {role} brief",
        )
        for i, s in enumerate(sections)
    )
    for s in specs:
        seal_contract(s)
    total_bars = sum(s.get("length_beats", 16) // 4 for s in sections)
    ic = InstrumentContract(
        instrument_name=instrument_name,
        role=role,
        style=style,
        bars=total_bars,
        tempo=tempo,
        key=key,
        start_beat=0,
        sections=specs,
        existing_track_id=existing_track_id,
        assigned_color=None,
        gm_guidance="",
    )
    seal_contract(ic)
    return ic


def _contract(
    name: str = "verse",
    start_beat: int = 0,
    duration_beats: int = 16,
    instrument_name: str = "Drums",
    role: str = "drums",
    track_id: str = "trk-1",
    style: str = "house",
    tempo: float = 120.0,
    key: str = "Am",
    l2_generate_prompt: str = "",
) -> SectionContract:
    """Build a frozen SectionContract for test use."""
    bars = max(1, duration_beats // 4)
    spec = SectionSpec(
        section_id=f"0:{name}",
        name=name,
        index=0,
        start_beat=start_beat,
        duration_beats=duration_beats,
        bars=bars,
        character=f"Test {name} section",
        role_brief=f"Test {role} brief",
    )
    seal_contract(spec)
    sc = SectionContract(
        section=spec,
        track_id=track_id,
        instrument_name=instrument_name,
        role=role,
        style=style,
        tempo=tempo,
        key=key,
        region_name=f"{instrument_name} – {name}",
        l2_generate_prompt=l2_generate_prompt,
    )
    seal_contract(sc)
    return sc


def _region_tc(tc_id: str = "r1", start_beat: int = 0, duration: int = 16) -> ToolCall:

    return ToolCall(
        id=tc_id,
        name="stori_add_midi_region",
        params={"startBeat": start_beat, "durationBeats": duration},
    )


def _generate_tc(tc_id: str = "g1", role: str = "drums") -> ToolCall:

    return ToolCall(
        id=tc_id,
        name="stori_generate_midi",
        params={"role": role, "style": "house", "tempo": 120, "bars": 4, "key": "Am"},
    )


def _ok_region_outcome(tc_id: str = "r1", region_id: str = "reg-001") -> _ToolCallOutcome:

    return _ToolCallOutcome(
        enriched_params={"startBeat": 0, "durationBeats": 16, "trackId": "trk-1"},
        tool_result={"regionId": region_id, "trackId": "trk-1"},
        sse_events=[{"type": "toolCall", "name": "stori_add_midi_region"}],
        msg_call={"role": "assistant", "tool_calls": []},
        msg_result={"role": "tool", "tool_call_id": tc_id, "content": "{}"},
        skipped=False,
    )


def _ok_generate_outcome(tc_id: str = "g1", notes_count: int = 24) -> _ToolCallOutcome:

    return _ToolCallOutcome(
        enriched_params={"role": "drums", "regionId": "reg-001", "trackId": "trk-1"},
        tool_result={"notesAdded": notes_count, "regionId": "reg-001", "trackId": "trk-1"},
        sse_events=[
            {"type": "generatorStart", "role": "drums"},
            {"type": "toolCall", "name": "stori_add_notes", "params": {
                "trackId": "trk-1", "regionId": "reg-001",
                "notes": [{"pitch": 36, "start_beat": 0, "duration_beats": 1}] * notes_count,
            }},
            {"type": "generatorComplete", "role": "drums", "noteCount": notes_count},
        ],
        msg_call={"role": "assistant", "tool_calls": []},
        msg_result={"role": "tool", "tool_call_id": tc_id, "content": "{}"},
        skipped=False,
    )


def _failed_generate_outcome(tc_id: str = "g1") -> _ToolCallOutcome:

    return _ToolCallOutcome(
        enriched_params={"role": "drums"},
        tool_result={"error": "GPU unavailable"},
        sse_events=[{"type": "toolError", "name": "stori_generate_midi", "error": "GPU unavailable"}],
        msg_call={"role": "assistant", "tool_calls": []},
        msg_result={"role": "tool", "tool_call_id": tc_id, "content": "{}"},
        skipped=True,
    )


# =============================================================================
# SectionSignals
# =============================================================================


_H = "h_test_00000001"
_H2 = "h_test_00000002"
_H3 = "h_test_00000003"


class TestSectionSignals:
    def test_from_sections_creates_events(self) -> None:

        """One asyncio.Event per section_id:contract_hash key."""
        signals = SectionSignals.from_section_ids(
            ["0:intro", "0:verse", "0:chorus"], [_H, _H2, _H3],
        )
        assert len(signals.events) == 3
        for evt in signals.events.values():
            assert not evt.is_set()

    def test_signal_complete_sets_event(self) -> None:

        signals = SectionSignals.from_section_ids(["0:intro"], [_H])
        signals.signal_complete("0:intro", contract_hash=_H, success=True, drum_notes=[{"pitch": 36}])
        key = f"0:intro:{_H}"
        assert signals.events[key].is_set()
        assert signals._results[key].drum_notes == [{"pitch": 36}]

    def test_signal_complete_without_notes(self) -> None:

        """Signaling with no notes sets the event but stores no drum_notes."""
        signals = SectionSignals.from_section_ids(["0:intro"], [_H])
        signals.signal_complete("0:intro", contract_hash=_H, success=True)
        key = f"0:intro:{_H}"
        assert signals.events[key].is_set()
        assert signals._results[key].success is True
        assert signals._results[key].drum_notes is None

    def test_signal_unknown_section_no_raise(self) -> None:

        """Signaling a section that doesn't exist does not raise — no event set."""
        signals = SectionSignals.from_section_ids(["0:intro"], [_H])
        signals.signal_complete("nonexistent", contract_hash="nohash", success=True)

    @pytest.mark.anyio
    async def test_wait_for_returns_data(self) -> None:

        """wait_for blocks until signaled, then returns SectionSignalResult."""
        signals = SectionSignals.from_section_ids(["0:verse"], [_H])

        async def _signal_later() -> None:
            await asyncio.sleep(0.01)
            signals.signal_complete("0:verse", contract_hash=_H, success=True, drum_notes=[{"pitch": 38}])

        task = asyncio.create_task(_signal_later())
        data = await signals.wait_for("0:verse", contract_hash=_H)
        await task
        assert data is not None
        assert data.drum_notes == [{"pitch": 38}]

    @pytest.mark.anyio
    async def test_wait_for_unknown_returns_none(self) -> None:

        """Waiting for a key not in the events dict returns None immediately."""
        signals = SectionSignals.from_section_ids(["0:intro"], [_H])
        result = await signals.wait_for("nonexistent", contract_hash="nohash")
        assert result is None

    @pytest.mark.anyio
    async def test_wait_for_already_set(self) -> None:

        """wait_for returns immediately if the event is already set."""
        signals = SectionSignals.from_section_ids(["0:intro"], [_H])
        signals.signal_complete("0:intro", contract_hash=_H, success=True, drum_notes=[{"pitch": 42}])
        data = await signals.wait_for("0:intro", contract_hash=_H)
        assert data is not None


# =============================================================================
# SectionResult
# =============================================================================


class TestSectionResult:
    def test_defaults(self) -> None:

        r = SectionResult(success=False, section_name="intro")
        assert not r.success
        assert r.region_id is None
        assert r.notes_generated == 0
        assert r.tool_results == []
        assert r.error is None

    def test_successful_result(self) -> None:

        r = SectionResult(
            success=True,
            section_name="verse",
            region_id="reg-1",
            notes_generated=48,
        )
        assert r.success
        assert r.region_id == "reg-1"


# =============================================================================
# _run_section_child — core pipeline
# =============================================================================


class TestRunSectionChild:
    @pytest.mark.anyio
    async def test_successful_region_and_generate(self) -> None:

        """Happy path: region creates regionId, generate succeeds."""
        store = StateStore(conversation_id="test-sc")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        call_count = 0

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            nonlocal call_count
            call_count += 1
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=_contract(),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        assert result.success
        assert result.region_id == "reg-001"
        assert result.notes_generated == 24
        assert call_count == 2
        assert len(result.tool_result_msgs) == 2

    @pytest.mark.anyio
    async def test_region_failure_returns_early(self) -> None:

        """When region creation returns no regionId, section fails gracefully."""
        store = StateStore(conversation_id="test-sc-fail")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        bad_region = _ToolCallOutcome(
            enriched_params={},
            tool_result={"error": "collision"},
            sse_events=[],
            msg_call={},
            msg_result={},
            skipped=False,
        )

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            return bad_region

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=_contract(),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        assert not result.success
        assert result.region_id is None
        assert "Region creation failed" in (result.error or "")

    @pytest.mark.anyio
    async def test_generate_failure_returns_error(self) -> None:

        """When generate is skipped (GPU error), section reports failure."""
        store = StateStore(conversation_id="test-sc-gen-fail")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _failed_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=_contract(),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        assert not result.success
        assert result.region_id == "reg-001"
        assert "GPU unavailable" in (result.error or "")

    @pytest.mark.anyio
    async def test_track_id_injected(self) -> None:

        """Section child injects the parent's trackId into region and generate params."""
        store = StateStore(conversation_id="test-sc-tid")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        captured_args: list[dict[str, Any]] = []

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            captured_args.append({"name": tc_name, "args": resolved_args})
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                contract=_contract(track_id="MY-TRACK-ID"),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        assert captured_args[0]["args"]["trackId"] == "MY-TRACK-ID"
        assert captured_args[1]["args"]["trackId"] == "MY-TRACK-ID"
        assert captured_args[1]["args"]["regionId"] == "reg-001"


# =============================================================================
# _run_section_child — drum signaling
# =============================================================================


class TestSectionChildDrumSignaling:
    @pytest.mark.anyio
    async def test_drum_child_signals_on_success(self) -> None:

        """A drum section child signals SectionSignals after generate completes."""
        store = StateStore(conversation_id="test-sig")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        contract = _contract(role="drums")
        ch = contract.section.contract_hash
        signals = SectionSignals.from_section_ids(["0:verse"], [ch])

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id, notes_count=12)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=contract,
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
                execution_services=ExecutionServices(section_signals=signals),
            )

        assert result.success
        key = f"0:verse:{ch}"
        assert signals.events[key].is_set()
        assert key in signals._results
        _drum_notes = signals._results[key].drum_notes
        assert _drum_notes is not None
        assert len(_drum_notes) == 12

    @pytest.mark.anyio
    async def test_drum_child_signals_on_region_failure(self) -> None:

        """Drum still signals even when region fails — prevents bass from hanging."""
        store = StateStore(conversation_id="test-sig-fail")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        contract = _contract(name="chorus", role="drums")
        ch = contract.section.contract_hash
        signals = SectionSignals.from_section_ids(["0:chorus"], [ch])

        bad_region = _ToolCallOutcome(
            enriched_params={},
            tool_result={},
            sse_events=[],
            msg_call={},
            msg_result={},
        )

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            return bad_region

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=contract,
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
                execution_services=ExecutionServices(section_signals=signals),
            )

        assert not result.success
        assert signals.events[f"0:chorus:{ch}"].is_set()

    @pytest.mark.anyio
    async def test_drum_child_signals_on_generate_failure(self) -> None:

        """Drum still signals even when generate fails."""
        store = StateStore(conversation_id="test-sig-gen-fail")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        contract = _contract(role="drums")
        ch = contract.section.contract_hash
        signals = SectionSignals.from_section_ids(["0:verse"], [ch])

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _failed_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=contract,
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
                execution_services=ExecutionServices(section_signals=signals),
            )

        assert not result.success
        assert signals.events[f"0:verse:{ch}"].is_set()


# =============================================================================
# _run_section_child — bass waiting
# =============================================================================


class TestSectionChildBassWaiting:
    @pytest.mark.anyio
    async def test_bass_waits_for_drum_signal(self) -> None:

        """Bass section child blocks until the drum signal fires."""
        store = StateStore(conversation_id="test-bass-wait")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        contract = _contract(instrument_name="Bass", role="bass")
        ch = contract.section.contract_hash
        signals = SectionSignals.from_section_ids(["0:verse"], [ch])
        bass_started = False

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            nonlocal bass_started
            bass_started = True
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        async def _signal_drum() -> None:
            await asyncio.sleep(0.05)
            assert not bass_started, "Bass should not have started before drum signal"
            signals.signal_complete("0:verse", contract_hash=ch, success=True, drum_notes=[{"pitch": 36}])

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            drum_task = asyncio.create_task(_signal_drum())
            result = await _run_section_child(
                contract=contract,
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="bass",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
                execution_services=ExecutionServices(section_signals=signals),
            )
            await drum_task

        assert result.success
        assert bass_started

    @pytest.mark.anyio
    async def test_bass_proceeds_without_signals(self) -> None:

        """Bass without execution_services runs immediately (no-drums edge case)."""
        store = StateStore(conversation_id="test-bass-nosig")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=_contract(instrument_name="Bass", role="bass"),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="bass",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
                execution_services=None,
            )

        assert result.success


# =============================================================================
# _run_section_child — SSE events
# =============================================================================


class TestSectionChildSSE:
    @pytest.mark.anyio
    async def test_sse_events_tagged_with_agent_and_section(self) -> None:

        """SSE events from tool outcomes are tagged with agentId and sectionName."""
        store = StateStore(conversation_id="test-sse")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                contract=_contract(name="chorus", role="drums"),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        tagged = [e for e in events if "agentId" in e]
        assert len(tagged) > 0
        for e in tagged:
            assert e["agentId"] == "drums"
            assert e["sectionName"] == "chorus"


# =============================================================================
# _run_section_child — unhandled exception
# =============================================================================


class TestSectionChildException:
    @pytest.mark.anyio
    async def test_exception_caught_returns_error_result(self) -> None:

        """An unhandled exception inside the child returns a failed SectionResult."""
        store = StateStore(conversation_id="test-exc")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> None:

            raise RuntimeError("unexpected crash")

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=_contract(),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        assert not result.success
        assert "unexpected crash" in (result.error or "")

    @pytest.mark.anyio
    async def test_drum_signals_on_exception(self) -> None:

        """Drum child still signals on unhandled exception to unblock bass."""
        store = StateStore(conversation_id="test-exc-sig")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        contract = _contract(role="drums")
        ch = contract.section.contract_hash
        signals = SectionSignals.from_section_ids(["0:verse"], [ch])

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> None:

            raise RuntimeError("boom")

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=contract,
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
                execution_services=ExecutionServices(section_signals=signals),
            )

        assert not result.success
        assert signals.events[f"0:verse:{ch}"].is_set()


# =============================================================================
# _dispatch_section_children — grouping
# =============================================================================


class TestDispatchSectionChildren:
    """Tests for _dispatch_section_children in agent.py."""

    @pytest.mark.anyio
    async def test_groups_tool_calls_correctly(self) -> None:

        """Track creation, region+gen pairs, and effect calls are categorized."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-dispatch")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        all_results: list[dict[str, Any]] = []
        collected: list[dict[str, Any]] = []

        track_tc = ToolCall(id="t1", name="stori_add_midi_track", params={"name": "Drums"})
        r1 = _region_tc("r1", start_beat=0, duration=16)
        g1 = _generate_tc("g1", role="drums")
        r2 = _region_tc("r2", start_beat=16, duration=16)
        g2 = _generate_tc("g2", role="drums")
        effect_tc = ToolCall(id="e1", name="stori_add_insert_effect", params={"type": "compressor"})

        track_outcome = _ToolCallOutcome(
            enriched_params={"name": "Drums", "trackId": "trk-42"},
            tool_result={"trackId": "trk-42"},
            sse_events=[{"type": "toolCall", "name": "stori_add_midi_track"}],
            msg_call={},
            msg_result={},
        )

        call_log: list[str] = []

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            call_log.append(tc_name)
            if tc_name == "stori_add_midi_track":
                return track_outcome
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            if tc_name == "stori_generate_midi":
                return _ok_generate_outcome(tc_id)
            if tc_name == "stori_add_insert_effect":
                return _ToolCallOutcome(
                    enriched_params=resolved_args,
                    tool_result={"effectId": "fx-1"},
                    sse_events=[],
                    msg_call={},
                    msg_result={},
                )
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={},
                sse_events=[],
                msg_call={},
                msg_result={},
            )

        sections = [_section("intro", 0, 16), _section("verse", 16, 16)]
        ic = _instrument_contract(sections)

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.complete_step_by_id = MagicMock(return_value=None)
        mock_plan.activate_step = MagicMock(return_value={"type": "planStepUpdate"})

        mock_llm = MagicMock()

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            msgs, st, se, rc, ro, gc = await _dispatch_section_children(
                tool_calls=[track_tc, r1, g1, r2, g2, effect_tc],
                sections=sections,
                existing_track_id=None,
                instrument_name="Drums",
                role="drums",
                style="house",
                tempo=120.0,
                key="Am",
                agent_id="drums",
                agent_log="[test][Drums]",
                reusing=False,
                allowed_tool_names={
                    "stori_add_midi_track", "stori_add_midi_region",
                    "stori_generate_midi", "stori_add_insert_effect",
                },
                store=store,
                trace=_trace(),
                sse_queue=queue,
                instrument_contract=ic,
                collected_tool_calls=collected,
                all_tool_results=all_results,
                add_notes_failures={},
                runtime_context=None,
                plan_tracker=mock_plan,
                step_ids=["s1", "s2"],
                active_step_id=None,
                llm=mock_llm,
                prior_stage_track=False,
                prior_stage_effect=False,
                prior_regions_completed=0,
                prior_regions_ok=0,
                prior_generates_completed=0,
            )

        assert st is True
        assert se is True
        assert rc == 2
        assert ro == 2
        assert gc == 2
        assert "stori_add_midi_track" in call_log
        assert "stori_add_insert_effect" in call_log
        assert len(msgs) > 0

    @pytest.mark.anyio
    async def test_no_track_id_returns_error(self) -> None:

        """If no trackId is resolved, all remaining calls get error results."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-no-tid")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        r1 = _region_tc("r1")
        g1 = _generate_tc("g1")

        mock_plan = MagicMock()
        mock_plan.steps = []

        msgs, st, se, rc, ro, gc = await _dispatch_section_children(
            tool_calls=[r1, g1],
            sections=[_section("verse")],
            existing_track_id=None,
            instrument_name="Drums",
            role="drums",
            style="house",
            tempo=120.0,
            key="Am",
            agent_id="drums",
            agent_log="[test]",
            reusing=False,
            allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
            store=store,
            trace=_trace(),
            sse_queue=queue,
            collected_tool_calls=[],
            all_tool_results=[],
            add_notes_failures={},
            runtime_context=None,
            plan_tracker=mock_plan,
            step_ids=[],
            active_step_id=None,
            llm=MagicMock(),
            prior_stage_track=False,
            prior_stage_effect=False,
            prior_regions_completed=0,
            prior_regions_ok=0,
            prior_generates_completed=0,
        )

        assert rc == 0
        assert gc == 0
        error_msgs = [
            m for m in msgs
            if "No trackId" in json.loads(m.get("content", "{}")).get("error", "")
        ]
        assert len(error_msgs) == 2

    @pytest.mark.anyio
    async def test_reused_track_id_skips_creation(self) -> None:

        """When existing_track_id is set, track creation calls are absent."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-reuse")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        r1 = _region_tc("r1")
        g1 = _generate_tc("g1")

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.complete_step_by_id = MagicMock(return_value=None)
        mock_plan.activate_step = MagicMock(return_value={"type": "planStepUpdate"})

        sections = [_section("verse")]
        ic = _instrument_contract(sections, existing_track_id="existing-trk-99")

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            msgs, st, se, rc, ro, gc = await _dispatch_section_children(
                tool_calls=[r1, g1],
                sections=sections,
                existing_track_id="existing-trk-99",
                instrument_name="Drums",
                role="drums",
                style="house",
                tempo=120.0,
                key="Am",
                agent_id="drums",
                agent_log="[test]",
                reusing=True,
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                instrument_contract=ic,
                collected_tool_calls=[],
                all_tool_results=[],
                add_notes_failures={},
                runtime_context=None,
                plan_tracker=mock_plan,
                step_ids=["s1"],
                active_step_id=None,
                llm=MagicMock(),
                prior_stage_track=True,
                prior_stage_effect=False,
                prior_regions_completed=0,
                prior_regions_ok=0,
                prior_generates_completed=0,
            )

        assert st is True
        assert rc == 1
        assert ro == 1
        assert gc == 1

    @pytest.mark.anyio
    async def test_planstep_completed_emitted_after_children_finish(self) -> None:

        """planStepUpdate(completed) is queued for content_step_id after children finish.

        Regression for P2: before the fix _dispatch_section_children activated
        content_step_id but never completed it.  The outer loop's active_step_id
        was never updated from the multi-section path, so the step stayed stuck
        in 'active' indefinitely on the macOS client.
        """
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-planstep-completed")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        all_results: list[dict[str, Any]] = []
        collected: list[dict[str, Any]] = []

        r1 = _region_tc("r1", start_beat=0, duration=16)
        g1 = _generate_tc("g1", role="drums")

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        # Fake plan step that is "active" — simulates what the coordinator sets up.
        content_step = MagicMock()
        content_step.status = "active"

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.activate_step = MagicMock(return_value={
            "type": "planStepUpdate", "stepId": "s2", "status": "active",
        })
        mock_plan.complete_step_by_id = MagicMock(return_value={
            "type": "planStepUpdate", "stepId": "s2", "status": "completed",
            "result": "1/1 sections completed, 24 notes",
        })
        mock_plan.get_step = MagicMock(return_value=content_step)

        sections = [_section("verse", 0, 16)]
        ic = _instrument_contract(sections, existing_track_id="trk-99")

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _dispatch_section_children(
                tool_calls=[r1, g1],
                sections=sections,
                existing_track_id="trk-99",
                instrument_name="Drums",
                role="drums",
                style="house",
                tempo=120.0,
                key="Am",
                agent_id="drums",
                agent_log="[test]",
                reusing=True,
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                instrument_contract=ic,
                collected_tool_calls=collected,
                all_tool_results=all_results,
                add_notes_failures={},
                runtime_context=None,
                plan_tracker=mock_plan,
                step_ids=["s1", "s2"],
                active_step_id=None,
                llm=MagicMock(),
                prior_stage_track=True,
                prior_stage_effect=False,
                prior_regions_completed=0,
                prior_regions_ok=0,
                prior_generates_completed=0,
            )

        # Drain the queue and look for planStepUpdate(completed) for step s2.
        events: list[dict[str, Any]] = []
        while not queue.empty():
            events.append(queue.get_nowait())

        completed_events = [
            e for e in events
            if e.get("type") == "planStepUpdate" and e.get("status") == "completed"
            and e.get("stepId") == "s2"
        ]
        assert len(completed_events) >= 1, (
            f"Expected planStepUpdate(completed) for s2 but got: {events}"
        )
        assert completed_events[0].get("agentId") == "drums"

    @pytest.mark.anyio
    async def test_generator_events_tagged_with_agentid_via_emit(self) -> None:

        """generatorStart and generatorComplete events in section children carry agentId.

        The _emit() helper in section_agent tags all _AGENT_TAGGED_EVENTS with
        agentId + sectionName.  This test verifies that a section child's queued
        events contain agentId so the macOS client can route them to the correct
        instrument card.
        """
        store = StateStore(conversation_id="test-agentid-tag")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        # Generate outcome whose sse_events include generatorStart / generatorComplete
        gen_outcome = _ok_generate_outcome("g1", notes_count=16)
        # Confirm the fixture contains the expected event types
        event_types = {e["type"] for e in gen_outcome.sse_events}
        assert "generatorStart" in event_types or "generatorComplete" in event_types

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return gen_outcome

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                contract=_contract(
                    name="chorus", instrument_name="Bass", role="bass",
                ),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(role="bass"),
                agent_id="bass",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        events: list[dict[str, Any]] = []
        while not queue.empty():
            events.append(queue.get_nowait())

        # Every tagged event type should have agentId = "bass" and sectionName = "chorus"
        for evt in events:
            if evt.get("type") in {"generatorStart", "generatorComplete", "toolCall", "status"}:
                assert evt.get("agentId") == "bass", (
                    f"Event {evt['type']} missing agentId: {evt}"
                )
                assert evt.get("sectionName") == "chorus", (
                    f"Event {evt['type']} missing sectionName: {evt}"
                )


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    @pytest.mark.anyio
    async def test_single_section_uses_sequential_path(self) -> None:

        """With only one section, the parent uses the sequential execution path."""
        signals = SectionSignals.from_section_ids(["0:full"], [_H])
        assert len(signals.events) == 1

    def test_no_drums_no_signals_needed(self) -> None:

        """When there are no drums, section_signals is harmless."""
        signals = SectionSignals.from_section_ids(["0:verse"], [_H])
        signals.signal_complete("0:verse", contract_hash=_H, success=True)
        key = f"0:verse:{_H}"
        assert signals.events[key].is_set()
        assert signals._results[key].drum_notes is None

    def test_no_bass_drum_signals_fire_unused(self) -> None:

        """Drum signals that no bass listens to are harmless."""
        signals = SectionSignals.from_section_ids(["0:verse"], [_H])
        signals.signal_complete("0:verse", contract_hash=_H, success=True, drum_notes=[{"pitch": 36}])
        key = f"0:verse:{_H}"
        assert signals.events[key].is_set()
        assert signals._results[key].drum_notes == [{"pitch": 36}]


# =============================================================================
# Contract hardening regressions (ChatGPT review)
# =============================================================================


class TestSectionIdCollisionPrevention:
    """Regression: duplicate section names must not collide when keyed by section_id."""

    def test_duplicate_section_names_get_unique_ids(self) -> None:

        """Two 'verse' sections with different indices get separate events."""
        signals = SectionSignals.from_section_ids(["0:verse", "1:verse"], [_H, _H2])
        assert len(signals.events) == 2

    def test_signal_first_verse_does_not_affect_second(self) -> None:

        """Signaling section 0:verse does not set 1:verse."""
        signals = SectionSignals.from_section_ids(["0:verse", "1:verse"], [_H, _H2])
        signals.signal_complete("0:verse", contract_hash=_H, success=True, drum_notes=[{"pitch": 36}])
        assert signals.events[f"0:verse:{_H}"].is_set()
        assert not signals.events[f"1:verse:{_H2}"].is_set()

    @pytest.mark.anyio
    async def test_wait_for_correct_verse(self) -> None:

        """Bass waiting on 1:verse does not receive 0:verse data."""
        signals = SectionSignals.from_section_ids(["0:verse", "1:verse"], [_H, _H2])
        signals.signal_complete("0:verse", contract_hash=_H, success=True, drum_notes=[{"pitch": 36}])

        async def _signal_later() -> None:
            await asyncio.sleep(0.01)
            signals.signal_complete("1:verse", contract_hash=_H2, success=True, drum_notes=[{"pitch": 38}])

        task = asyncio.create_task(_signal_later())
        result = await signals.wait_for("1:verse", contract_hash=_H2)
        await task
        assert result is not None
        assert result.drum_notes == [{"pitch": 38}]


class TestSignalIdempotency:
    """Regression: signal_complete called twice must not corrupt state."""

    def test_double_signal_is_idempotent(self) -> None:

        """Second call to signal_complete is silently ignored."""
        signals = SectionSignals.from_section_ids(["0:verse"], [_H])
        key = f"0:verse:{_H}"
        signals.signal_complete("0:verse", contract_hash=_H, success=True, drum_notes=[{"pitch": 36}])
        signals.signal_complete("0:verse", contract_hash=_H, success=True, drum_notes=[{"pitch": 99}])
        assert signals._results[key].drum_notes == [{"pitch": 36}]

    def test_failure_then_success_keeps_failure(self) -> None:

        """First signal wins — even if failure followed by success attempt."""
        signals = SectionSignals.from_section_ids(["0:verse"], [_H])
        key = f"0:verse:{_H}"
        signals.signal_complete("0:verse", contract_hash=_H, success=False)
        signals.signal_complete("0:verse", contract_hash=_H, success=True, drum_notes=[{"pitch": 36}])
        assert signals._results[key].success is False
        assert signals._results[key].drum_notes is None


class TestFailureSignaling:
    """Regression: wait_for returns SectionSignalResult(success=False) when drums fail."""

    @pytest.mark.anyio
    async def test_wait_for_returns_failure_result(self) -> None:

        """Bass receives explicit failure signal — not None or timeout."""
        signals = SectionSignals.from_section_ids(["0:verse"], [_H])

        async def _fail_drums() -> None:
            await asyncio.sleep(0.01)
            signals.signal_complete("0:verse", contract_hash=_H, success=False)

        task = asyncio.create_task(_fail_drums())
        result = await signals.wait_for("0:verse", contract_hash=_H)
        await task
        assert result is not None
        assert result.success is False
        assert result.drum_notes is None

    @pytest.mark.anyio
    async def test_wait_for_timeout_raises(self) -> None:

        """If no signal arrives within timeout, asyncio.TimeoutError is raised."""
        signals = SectionSignals.from_section_ids(["0:verse"], [_H])
        with pytest.raises(asyncio.TimeoutError):
            await signals.wait_for("0:verse", contract_hash=_H, timeout=0.01)


class TestContractHardError:
    """Regression: L2 fallback SectionSpec rebuild is a hard error."""

    @pytest.mark.anyio
    async def test_dispatch_raises_without_instrument_contract(self) -> None:

        """_dispatch_section_children raises ValueError when contract is missing."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-hard-err")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        with pytest.raises(ValueError, match="Contract violation"):
            await _dispatch_section_children(
                tool_calls=[_region_tc(), _generate_tc()],
                sections=[_section("verse")],
                existing_track_id="trk-1",
                instrument_name="Drums",
                role="drums",
                style="house",
                tempo=120.0,
                key="Am",
                agent_id="drums",
                agent_log="[test]",
                reusing=True,
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                instrument_contract=None,
                collected_tool_calls=[],
                all_tool_results=[{"trackId": "trk-1"}],
                add_notes_failures={},
                runtime_context=None,
                plan_tracker=MagicMock(steps=[]),
                step_ids=["s1"],
                active_step_id=None,
                llm=MagicMock(),
                prior_stage_track=True,
                prior_stage_effect=False,
                prior_regions_completed=0,
                prior_regions_ok=0,
                prior_generates_completed=0,
            )


class TestOrphanedRegionHandling:
    """Regression: orphaned regions (sent without paired generates) must be
    processed and counted toward _regions_completed so the multi-turn retry
    loop in _run_instrument_agent_inner makes progress.

    Before this fix, the LLM would send N regions in Turn 1 without generates.
    ``_dispatch_section_children`` used ``zip(region_tcs, generate_tcs)``
    producing 0 pairs, so regions were never executed and _regions_completed
    stayed at 0.  ``_missing_stages()`` then told the LLM all stages were
    still pending, wasting all max_turns and producing zero notes.
    """

    @pytest.mark.anyio
    async def test_orphaned_regions_increment_regions_completed(self) -> None:

        """Regions sent without generates are executed and counted."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        sections = [
            _section("intro", start_beat=0, length_beats=16),
            _section("groove", start_beat=16, length_beats=16),
        ]
        ic = _instrument_contract(sections, role="drums")

        store = StateStore(conversation_id="test-orphaned")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        region_tcs = [
            _region_tc("r1", start_beat=0, duration=16),
            _region_tc("r2", start_beat=16, duration=16),
        ]

        with patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call"
        ) as mock_apply:
            mock_apply.return_value = _ok_region_outcome("r1", "reg-001")

            result = await _dispatch_section_children(
                tool_calls=region_tcs,
                sections=sections,
                existing_track_id="trk-1",
                instrument_name="Drums",
                role="drums",
                style="neo-soul",
                tempo=92.0,
                key="Fm",
                agent_id="drums",
                agent_log="[test]",
                reusing=True,
                allowed_tool_names={
                    "stori_add_midi_region",
                    "stori_generate_midi",
                },
                store=store,
                trace=_trace(),
                sse_queue=queue,
                instrument_contract=ic,
                collected_tool_calls=[],
                all_tool_results=[{"trackId": "trk-1"}],
                add_notes_failures={},
                runtime_context=None,
                plan_tracker=MagicMock(
                    steps=[],
                    complete_step_by_id=MagicMock(return_value=None),
                    activate_step=MagicMock(return_value={"type": "planStepUpdate"}),
                    get_step=MagicMock(return_value=None),
                ),
                step_ids=["s1"],
                active_step_id=None,
                llm=MagicMock(),
                prior_stage_track=True,
                prior_stage_effect=False,
                prior_regions_completed=0,
                prior_regions_ok=0,
                prior_generates_completed=0,
            )

            tool_result_msgs, _, _, regions_completed, regions_ok, generates_completed = result

            assert regions_completed == 2, (
                f"Both orphaned regions should be counted, got {regions_completed}"
            )
            assert regions_ok == 2
            assert generates_completed == 0
            assert len(tool_result_msgs) == 2, (
                "Each orphaned region must produce a tool result message"
            )
            assert mock_apply.call_count == 2


class TestExecutionServicesSeparation:
    """Regression: RuntimeContext is pure data; mutable services are in ExecutionServices."""

    def test_runtime_context_has_no_signals(self) -> None:

        """RuntimeContext must not carry section_signals or section_state."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(RuntimeContext)}
        assert "section_signals" not in field_names
        assert "section_state" not in field_names

    def test_execution_services_carries_signals(self) -> None:

        """ExecutionServices carries the mutable coordination primitives."""
        signals = SectionSignals.from_section_ids(["0:verse"], [_H])
        svc = ExecutionServices(section_signals=signals)
        assert svc.section_signals is signals

    def test_mapping_proxy_is_readonly(self) -> None:

        """to_composition_context returns a read-only mapping."""
        import types
        ctx = RuntimeContext(raw_prompt="test", quality_preset="quality")
        bridge = ctx.to_composition_context()
        assert isinstance(bridge, types.MappingProxyType)
        with pytest.raises(TypeError):
            bridge["new_key"] = "value"  # type: ignore[index]


# =============================================================================
# _extract_expressiveness_blocks
# =============================================================================


class TestExtractExpressivenessBlocks:
    def test_extracts_midi_expressiveness(self) -> None:

        """Extracts MidiExpressiveness: block from raw prompt."""
        from app.core.maestro_agent_teams.section_agent import (
            _extract_expressiveness_blocks,
        )

        prompt = (
            "Title: Test\nStyle: house\n\n"
            "MidiExpressiveness:\n"
            "  modulation:\n"
            "    instrument: lead\n"
            "    depth: strong\n"
            "\nStructure:\n  Verse: 8 bars\n"
        )
        result = _extract_expressiveness_blocks(prompt)
        assert "MidiExpressiveness:" in result
        assert "modulation:" in result
        assert "depth: strong" in result

    def test_extracts_automation(self) -> None:

        """Extracts Automation: block from raw prompt."""
        from app.core.maestro_agent_teams.section_agent import (
            _extract_expressiveness_blocks,
        )

        prompt = (
            "Title: Test\n\n"
            "Automation:\n"
            "  filter_sweep:\n"
            "    target: cutoff\n"
            "\nStructure:\n  Verse: 8 bars\n"
        )
        result = _extract_expressiveness_blocks(prompt)
        assert "Automation:" in result
        assert "filter_sweep:" in result

    def test_extracts_both_blocks(self) -> None:

        """Extracts both MidiExpressiveness and Automation blocks."""
        from app.core.maestro_agent_teams.section_agent import (
            _extract_expressiveness_blocks,
        )

        prompt = (
            "Title: Test\n\n"
            "MidiExpressiveness:\n"
            "  cc_curves:\n"
            "    - cc: 74\n"
            "\n"
            "Automation:\n"
            "  filter:\n"
            "    cutoff: sweep\n"
            "\nStructure:\n  Verse: 8 bars\n"
        )
        result = _extract_expressiveness_blocks(prompt)
        assert "MidiExpressiveness:" in result
        assert "Automation:" in result

    def test_no_blocks_returns_empty(self) -> None:

        """Returns empty string when no expressiveness blocks found."""
        from app.core.maestro_agent_teams.section_agent import (
            _extract_expressiveness_blocks,
        )

        prompt = "Title: Test\nStyle: house\nStructure:\n  Verse: 8 bars\n"
        result = _extract_expressiveness_blocks(prompt)
        assert result == ""


# =============================================================================
# Section child — status SSE events
# =============================================================================


class TestSectionChildStatusEvents:
    @pytest.mark.anyio
    async def test_emits_start_status(self) -> None:

        """Section child emits a 'Starting' status event at the beginning."""
        store = StateStore(conversation_id="test-status")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                contract=_contract(instrument_name="Synth Lead", role="melody"),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="lead-agent",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        status_events = [e for e in events if e.get("type") == "status"]
        assert len(status_events) >= 2

        start_evt = status_events[0]
        assert "Starting" in start_evt["message"]
        assert "Synth Lead" in start_evt["message"]
        assert "verse" in start_evt["message"]
        assert start_evt["agentId"] == "lead-agent"
        assert start_evt["sectionName"] == "verse"

    @pytest.mark.anyio
    async def test_emits_completion_status_with_note_count(self) -> None:

        """Section child emits a notes-generated status event after generation."""
        store = StateStore(conversation_id="test-status-done")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id, notes_count=42)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                contract=_contract(
                    name="chorus", instrument_name="Bass", role="bass",
                ),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="bass-agent",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=None,
            )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        status_events = [e for e in events if e.get("type") == "status"]
        notes_evt = [e for e in status_events if "42 notes" in e.get("message", "")]
        assert len(notes_evt) >= 1
        assert notes_evt[0]["sectionName"] == "chorus"


# =============================================================================
# _maybe_refine_expression — streaming CoT
# =============================================================================


class TestExpressionRefinementStreaming:
    @pytest.mark.anyio
    async def test_refinement_streams_reasoning_events(self) -> None:

        """Expression refinement streams reasoning SSE events with sectionName."""
        from app.core.maestro_agent_teams.section_agent import (
            _maybe_refine_expression,
        )

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        store = StateStore(conversation_id="test-expr")

        mock_llm = MagicMock()

        async def _mock_stream(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:

            yield {"type": "reasoning_delta", "text": "Adding modulation "}
            yield {"type": "reasoning_delta", "text": "sweep for warmth."}
            yield {
                "type": "done",
                "content": None,
                "tool_calls": [{
                    "id": "cc1",
                    "function": {
                        "name": "stori_add_midi_cc",
                        "arguments": json.dumps({
                            "ccNumber": 1,
                            "points": [{"beat": 0, "value": 60}],
                        }),
                    },
                }],
                "finish_reason": "tool_calls",
                "usage": {},
            }

        mock_llm.chat_completion_stream = MagicMock(return_value=_mock_stream())

        cc_outcome = _ToolCallOutcome(
            enriched_params={"ccNumber": 1},
            tool_result={"success": True},
            sse_events=[{"type": "toolCall", "name": "stori_add_midi_cc"}],
            msg_call={},
            msg_result={},
            skipped=False,
        )

        _runtime_ctx = RuntimeContext(
            raw_prompt=(
                "Title: Test\n\n"
                "MidiExpressiveness:\n"
                "  modulation:\n"
                "    instrument: lead\n"
                "    depth: strong vibrato — CC 1 value 60-90\n"
                "\nStructure:\n  Verse: 8 bars\n"
            ),
        )

        result = SectionResult(success=True, section_name="verse", notes_generated=24)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            return_value=cc_outcome,
        ):
            await _maybe_refine_expression(
                contract=_contract(
                    instrument_name="Synth Lead", role="melody",
                    style="techno", tempo=130.0, key="Am",
                ),
                region_id="reg-001",
                notes_generated=24,
                agent_id="lead-agent",
                llm=mock_llm,
                store=store,
                trace=_trace(),
                sse_queue=queue,
                allowed_tool_names={
                    "stori_add_midi_cc",
                    "stori_add_pitch_bend",
                },
                runtime_ctx=_runtime_ctx,
                result=result,
                child_log="[test][Synth Lead/verse]",
            )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        reasoning_events = [
            e for e in events if e.get("type") == "reasoning"
        ]
        assert len(reasoning_events) > 0
        for evt in reasoning_events:
            assert evt["agentId"] == "lead-agent"
            assert evt["sectionName"] == "verse"

        status_events = [e for e in events if e.get("type") == "status"]
        expr_status = [
            e for e in status_events if "expression" in e.get("message", "").lower()
        ]
        assert len(expr_status) >= 1

    @pytest.mark.anyio
    async def test_refinement_skipped_without_expressiveness(self) -> None:

        """No LLM call when the prompt lacks MidiExpressiveness/Automation."""
        from app.core.maestro_agent_teams.section_agent import (
            _maybe_refine_expression,
        )

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        store = StateStore(conversation_id="test-no-expr")
        mock_llm = MagicMock()

        _runtime_ctx = RuntimeContext(
            raw_prompt="Title: Simple\nStyle: house\nStructure:\n  Verse: 8 bars\n",
        )

        result = SectionResult(success=True, section_name="verse", notes_generated=24)

        await _maybe_refine_expression(
            contract=_contract(),
            region_id="reg-001",
            notes_generated=24,
            agent_id="drums",
            llm=mock_llm,
            store=store,
            trace=_trace(),
            sse_queue=queue,
            allowed_tool_names={"stori_add_midi_cc"},
            runtime_ctx=_runtime_ctx,
            result=result,
            child_log="[test][Drums/verse]",
        )

        assert queue.empty()
        mock_llm.chat_completion_stream.assert_not_called()

    @pytest.mark.anyio
    async def test_refinement_includes_expr_blocks_in_prompt(self) -> None:

        """Refinement LLM call includes extracted MidiExpressiveness content."""
        from app.core.maestro_agent_teams.section_agent import (
            _maybe_refine_expression,
        )

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        store = StateStore(conversation_id="test-expr-ctx")

        captured_messages: list[dict[str, Any]] = []
        mock_llm = MagicMock()

        async def _capture_stream(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:

            captured_messages.extend(kwargs.get("messages", []))
            yield {
                "type": "done",
                "content": None,
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {},
            }

        mock_llm.chat_completion_stream = MagicMock(side_effect=lambda **kw: _capture_stream(**kw))

        _runtime_ctx = RuntimeContext(
            raw_prompt=(
                "Title: Deep house\n\n"
                "MidiExpressiveness:\n"
                "  modulation:\n"
                "    instrument: pad\n"
                "    depth: subtle vibrato — CC 1 value 30-50\n"
                "\nStructure:\n  Verse: 8 bars\n"
            ),
        )

        result = SectionResult(success=True, section_name="verse", notes_generated=30)

        await _maybe_refine_expression(
            contract=_contract(
                instrument_name="Pad", role="chords",
                style="house", tempo=128.0, key="Cm",
            ),
            region_id="reg-001",
            notes_generated=30,
            agent_id="pad-agent",
            llm=mock_llm,
            store=store,
            trace=_trace(),
            sse_queue=queue,
            allowed_tool_names={"stori_add_midi_cc", "stori_add_pitch_bend"},
            runtime_ctx=_runtime_ctx,
            result=result,
            child_log="[test][Pad/verse]",
        )

        assert len(captured_messages) >= 1
        system_msg = captured_messages[0]["content"]
        assert "CC 1 value 30-50" in system_msg
        assert "modulation:" in system_msg


# =============================================================================
# Server-Owned Retries — regression tests
# =============================================================================


class TestServerOwnedRetries:
    """Regression tests for server-owned section retries (no LLM on retry)."""

    @pytest.mark.anyio
    async def test_failed_section_retried_server_side(self) -> None:

        """A section that fails on first attempt is retried without LLM."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-server-retry")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        all_results: list[dict[str, Any]] = []
        collected: list[dict[str, Any]] = []

        track_tc = ToolCall(id="t1", name="stori_add_midi_track", params={"name": "Drums"})
        r1 = _region_tc("r1", start_beat=0, duration=16)
        g1 = _generate_tc("g1", role="drums")
        effect_tc = ToolCall(
            id="e1", name="stori_add_insert_effect", params={"type": "compressor"},
        )

        track_outcome = _ToolCallOutcome(
            enriched_params={"name": "Drums", "trackId": "trk-42"},
            tool_result={"trackId": "trk-42"},
            sse_events=[{"type": "toolCall", "name": "stori_add_midi_track"}],
            msg_call={},
            msg_result={},
        )

        _gen_attempts = 0

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            nonlocal _gen_attempts
            if tc_name == "stori_add_midi_track":
                return track_outcome
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            if tc_name == "stori_generate_midi":
                _gen_attempts += 1
                if _gen_attempts == 1:
                    return _failed_generate_outcome(tc_id)
                return _ok_generate_outcome(tc_id)
            if tc_name == "stori_add_insert_effect":
                return _ToolCallOutcome(
                    enriched_params=resolved_args,
                    tool_result={"effectId": "fx-1"},
                    sse_events=[],
                    msg_call={},
                    msg_result={},
                )
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={},
                sse_events=[],
                msg_call={},
                msg_result={},
            )

        sections = [_section("verse", 0, 16)]
        ic = _instrument_contract(sections)

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.complete_step_by_id = MagicMock(return_value=None)
        mock_plan.activate_step = MagicMock(return_value={"type": "planStepUpdate"})
        mock_plan.get_step = MagicMock(return_value=None)

        mock_orpheus = MagicMock()
        mock_orpheus.circuit_breaker_open = False

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent.get_orpheus_client",
            return_value=mock_orpheus,
        ), patch(
            "asyncio.sleep", new_callable=AsyncMock,
        ):
            msgs, st, se, rc, ro, gc = await _dispatch_section_children(
                tool_calls=[track_tc, r1, g1, effect_tc],
                sections=sections,
                existing_track_id=None,
                instrument_name="Drums",
                role="drums",
                style="house",
                tempo=120.0,
                key="Am",
                agent_id="drums",
                agent_log="[test][Drums]",
                reusing=False,
                allowed_tool_names={
                    "stori_add_midi_track", "stori_add_midi_region",
                    "stori_generate_midi", "stori_add_insert_effect",
                },
                store=store,
                trace=_trace(),
                sse_queue=queue,
                instrument_contract=ic,
                collected_tool_calls=collected,
                all_tool_results=all_results,
                add_notes_failures={},
                runtime_context=None,
                plan_tracker=mock_plan,
                step_ids=["s1", "s2"],
                active_step_id=None,
                llm=MagicMock(),
                prior_stage_track=False,
                prior_stage_effect=False,
                prior_regions_completed=0,
                prior_regions_ok=0,
                prior_generates_completed=0,
            )

        assert gc == 1, "Section should succeed after server retry"
        assert ro == 1
        assert _gen_attempts == 2, "Generate should be called twice (fail + retry)"

    @pytest.mark.anyio
    async def test_circuit_breaker_skips_section_retries(self) -> None:

        """Server retries are skipped when Orpheus circuit breaker is open."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-cb-skip")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        track_tc = ToolCall(id="t1", name="stori_add_midi_track", params={"name": "Drums"})
        r1 = _region_tc("r1", start_beat=0, duration=16)
        g1 = _generate_tc("g1", role="drums")

        track_outcome = _ToolCallOutcome(
            enriched_params={"name": "Drums", "trackId": "trk-42"},
            tool_result={"trackId": "trk-42"},
            sse_events=[{"type": "toolCall", "name": "stori_add_midi_track"}],
            msg_call={},
            msg_result={},
        )

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_track":
                return track_outcome
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            if tc_name == "stori_generate_midi":
                return _failed_generate_outcome(tc_id)
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={},
                sse_events=[],
                msg_call={},
                msg_result={},
            )

        sections = [_section("verse", 0, 16)]
        ic = _instrument_contract(sections)

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.complete_step_by_id = MagicMock(return_value=None)
        mock_plan.activate_step = MagicMock(return_value={"type": "planStepUpdate"})
        mock_plan.get_step = MagicMock(return_value=None)

        mock_orpheus = MagicMock()
        mock_orpheus.circuit_breaker_open = True

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent.get_orpheus_client",
            return_value=mock_orpheus,
        ):
            msgs, st, se, rc, ro, gc = await _dispatch_section_children(
                tool_calls=[track_tc, r1, g1],
                sections=sections,
                existing_track_id=None,
                instrument_name="Drums",
                role="drums",
                style="house",
                tempo=120.0,
                key="Am",
                agent_id="drums",
                agent_log="[test][Drums]",
                reusing=False,
                allowed_tool_names={
                    "stori_add_midi_track", "stori_add_midi_region",
                    "stori_generate_midi",
                },
                store=store,
                trace=_trace(),
                sse_queue=queue,
                instrument_contract=ic,
                collected_tool_calls=[],
                all_tool_results=[],
                add_notes_failures={},
                runtime_context=None,
                plan_tracker=mock_plan,
                step_ids=["s1", "s2"],
                active_step_id=None,
                llm=MagicMock(),
                prior_stage_track=False,
                prior_stage_effect=False,
                prior_regions_completed=0,
                prior_regions_ok=0,
                prior_generates_completed=0,
            )

        assert gc == 1, "Section dispatched (counted as completed) even though generate failed — server already retried"

    @pytest.mark.anyio
    async def test_summary_message_replaces_individual_tool_results(self) -> None:

        """Dispatch returns collapsed summary instead of N individual tool results."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-summary")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        track_tc = ToolCall(id="t1", name="stori_add_midi_track", params={"name": "Drums"})
        r1 = _region_tc("r1", start_beat=0, duration=16)
        g1 = _generate_tc("g1", role="drums")
        r2 = _region_tc("r2", start_beat=16, duration=16)
        g2 = _generate_tc("g2", role="drums")

        track_outcome = _ToolCallOutcome(
            enriched_params={"name": "Drums", "trackId": "trk-42"},
            tool_result={"trackId": "trk-42"},
            sse_events=[{"type": "toolCall", "name": "stori_add_midi_track"}],
            msg_call={},
            msg_result={},
        )

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_track":
                return track_outcome
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            if tc_name == "stori_generate_midi":
                return _ok_generate_outcome(tc_id)
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={},
                sse_events=[],
                msg_call={},
                msg_result={},
            )

        sections = [_section("intro", 0, 16), _section("verse", 16, 16)]
        ic = _instrument_contract(sections)

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.complete_step_by_id = MagicMock(return_value=None)
        mock_plan.activate_step = MagicMock(return_value={"type": "planStepUpdate"})
        mock_plan.get_step = MagicMock(return_value=None)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            msgs, st, se, rc, ro, gc = await _dispatch_section_children(
                tool_calls=[track_tc, r1, g1, r2, g2],
                sections=sections,
                existing_track_id=None,
                instrument_name="Drums",
                role="drums",
                style="house",
                tempo=120.0,
                key="Am",
                agent_id="drums",
                agent_log="[test][Drums]",
                reusing=False,
                allowed_tool_names={
                    "stori_add_midi_track", "stori_add_midi_region",
                    "stori_generate_midi",
                },
                store=store,
                trace=_trace(),
                sse_queue=queue,
                instrument_contract=ic,
                collected_tool_calls=[],
                all_tool_results=[],
                add_notes_failures={},
                runtime_context=None,
                plan_tracker=mock_plan,
                step_ids=["s1", "s2"],
                active_step_id=None,
                llm=MagicMock(),
                prior_stage_track=False,
                prior_stage_effect=False,
                prior_regions_completed=0,
                prior_regions_ok=0,
                prior_generates_completed=0,
            )

        # Track result (1) + summary anchor (1) + 3 stubs (g1 + r2 + g2) = 5
        assert len(msgs) == 5

        # First msg is track creation result
        track_msg = msgs[0]
        track_content = json.loads(track_msg["content"])
        assert "trackId" in track_content

        # Second msg is the summary (anchored to first region tc)
        summary_msg = msgs[1]
        assert summary_msg["tool_call_id"] == "r1"
        summary = json.loads(summary_msg["content"])
        assert summary["status"] == "batch_complete"
        assert len(summary["sections"]) == 2
        assert all(s["status"] == "ok" for s in summary["sections"])

        # Remaining msgs are stubs (explicit, not ambiguous "...")
        for stub in msgs[2:]:
            assert "batch_complete" in stub["content"]
            assert stub["content"] != "..."


# =============================================================================
# Regression: pre-generation events must stream immediately (SSE relay bug)
# =============================================================================


class TestPreEmitGeneratorEvents:
    """Regression for SSE relay bug where generatorStart/toolStart were
    accumulated in a local list inside _execute_agent_generator and only
    returned after mg.generate() completed.  This starved the SSE queue
    during long Orpheus calls, causing the frontend to time out.

    The fix adds a ``pre_emit_callback`` that flushes toolStart and
    generatorStart to the SSE queue BEFORE generation blocks.
    """

    @pytest.mark.anyio
    async def test_pre_emit_callback_receives_generator_start_events(self) -> None:

        """pre_emit_callback fires with toolStart + generatorStart before generate."""
        from app.core.maestro_editing.tool_execution import _execute_agent_generator
        from app.core.tracing import TraceContext
        from app.services.backends.base import GenerationResult, GeneratorBackend

        store = StateStore()
        track_id = store.create_track("Drums")
        region_id = store.create_region("Region", track_id)

        trace = TraceContext(trace_id="test-pre-emit")
        comp_ctx = {
            "style": "house", "tempo": 120, "bars": 4,
            "key": "Am", "quality_preset": "balanced",
        }

        ok_result = GenerationResult(
            success=True,
            notes=[{"pitch": 36, "startBeat": 0, "durationBeats": 1, "velocity": 80}] * 12,
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
        )

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(return_value=ok_result)

        pre_emitted: list[dict[str, Any]] = []

        async def _capture_pre(events: list[dict[str, Any]]) -> None:

            pre_emitted.extend(events)

        with patch(
            "app.core.maestro_editing.tool_execution.get_music_generator",
            return_value=mock_mg,
        ):
            outcome = await _execute_agent_generator(
                tc_id="tc-pre",
                tc_name="stori_generate_midi",
                enriched_params={
                    "role": "drums",
                    "trackId": track_id,
                    "regionId": region_id,
                    "style": "house",
                    "tempo": 120,
                    "bars": 4,
                    "key": "Am",
                },
                store=store,
                trace=trace,
                composition_context=comp_ctx,
                emit_sse=True,
                pre_emit_callback=_capture_pre,
            )

        assert outcome is not None

        pre_types = [e["type"] for e in pre_emitted]
        assert "toolStart" in pre_types, "toolStart must be pre-emitted"
        assert "generatorStart" in pre_types, "generatorStart must be pre-emitted"

        outcome_types = [e["type"] for e in outcome.sse_events]
        assert "toolStart" not in outcome_types, (
            "toolStart must NOT be in deferred sse_events when pre_emit_callback is set"
        )
        assert "generatorStart" not in outcome_types, (
            "generatorStart must NOT be in deferred sse_events when pre_emit_callback is set"
        )
        assert "generatorComplete" in outcome_types
        assert "toolCall" in outcome_types

    @pytest.mark.anyio
    async def test_no_callback_preserves_old_behavior(self) -> None:

        """Without pre_emit_callback, all events stay in sse_events (backward compat)."""
        from app.core.maestro_editing.tool_execution import _execute_agent_generator
        from app.core.tracing import TraceContext
        from app.services.backends.base import GenerationResult, GeneratorBackend

        store = StateStore()
        track_id = store.create_track("Bass")
        region_id = store.create_region("Region", track_id)

        trace = TraceContext(trace_id="test-no-cb")
        comp_ctx = {
            "style": "house", "tempo": 120, "bars": 4,
            "key": "Am", "quality_preset": "balanced",
        }

        ok_result = GenerationResult(
            success=True,
            notes=[{"pitch": 40, "startBeat": 0, "durationBeats": 1, "velocity": 80}] * 8,
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
        )

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(return_value=ok_result)

        with patch(
            "app.core.maestro_editing.tool_execution.get_music_generator",
            return_value=mock_mg,
        ):
            outcome = await _execute_agent_generator(
                tc_id="tc-nocb",
                tc_name="stori_generate_midi",
                enriched_params={
                    "role": "bass",
                    "trackId": track_id,
                    "regionId": region_id,
                    "style": "house",
                    "tempo": 120,
                    "bars": 4,
                    "key": "Am",
                },
                store=store,
                trace=trace,
                composition_context=comp_ctx,
                emit_sse=True,
            )

        assert outcome is not None
        types = [e["type"] for e in outcome.sse_events]
        assert "toolStart" in types
        assert "generatorStart" in types
        assert "generatorComplete" in types
        assert "toolCall" in types

    @pytest.mark.anyio
    async def test_section_child_streams_generator_start_to_queue_before_generate(self) -> None:

        """End-to-end: _run_section_child puts generatorStart in the queue
        BEFORE mg.generate() returns — the fix that prevents frontend timeout.
        """
        store = StateStore(conversation_id="test-e2e-pre-emit")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        events_at_generate_time: list[dict[str, Any]] = []

        async def _mock_apply(*, tc_id: Any, tc_name: Any, resolved_args: Any, pre_emit_callback: Any = None, **kw: Any) -> _ToolCallOutcome:

            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)

            if pre_emit_callback is not None:
                await pre_emit_callback([
                    {"type": "toolStart", "name": tc_name, "label": "Generating"},
                    {"type": "generatorStart", "role": "drums", "agentId": "drums"},
                ])

            # Snapshot the queue at the moment generate would block on Orpheus.
            # In the old code, the queue would be empty here.
            while not queue.empty():
                events_at_generate_time.append(queue.get_nowait())
            # Re-enqueue so _emit can still process
            for evt in events_at_generate_time:
                await queue.put(evt)

            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                contract=_contract(role="drums"),
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                runtime_ctx=RuntimeContext(
                    raw_prompt="test prompt",
                    emotion_vector=None,
                    quality_preset="balanced",
                ),
            )

        assert result.success

        gen_start_in_queue = [
            e for e in events_at_generate_time
            if e.get("type") == "generatorStart"
        ]
        assert len(gen_start_in_queue) >= 1, (
            "generatorStart must be in the SSE queue BEFORE mg.generate() runs — "
            "this is the fix for the frontend timeout bug"
        )
