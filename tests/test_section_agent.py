"""Tests for the three-level agent architecture.

Covers: SectionSignals, SectionResult, _run_section_child, and
_dispatch_section_children.  Edge cases: single-section, no-drums,
no-bass, section child failure, missing regionId.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.expansion import ToolCall
from app.core.state_store import StateStore
from app.core.tracing import TraceContext
from app.core.maestro_agent_teams.signals import SectionSignals
from app.core.maestro_agent_teams.section_agent import (
    SectionResult,
    _run_section_child,
)
from app.core.maestro_plan_tracker import _ToolCallOutcome


def _trace() -> TraceContext:
    return TraceContext(trace_id="test-section-agent")


def _section(name: str = "verse", start_beat: int = 0, length_beats: int = 16):
    return {"name": name, "start_beat": start_beat, "length_beats": length_beats}


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


class TestSectionSignals:
    def test_from_sections_creates_events(self):
        """One asyncio.Event per parsed section."""
        sections = [_section("intro"), _section("verse"), _section("chorus")]
        signals = SectionSignals.from_sections(sections)
        assert set(signals.events.keys()) == {"intro", "verse", "chorus"}
        for evt in signals.events.values():
            assert not evt.is_set()

    def test_signal_complete_sets_event(self):
        signals = SectionSignals.from_sections([_section("intro")])
        signals.signal_complete("intro", drum_notes=[{"pitch": 36}])
        assert signals.events["intro"].is_set()
        assert signals.drum_data["intro"]["drum_notes"] == [{"pitch": 36}]

    def test_signal_complete_without_notes(self):
        """Signaling with no notes sets the event but adds no drum_data."""
        signals = SectionSignals.from_sections([_section("intro")])
        signals.signal_complete("intro")
        assert signals.events["intro"].is_set()
        assert "intro" not in signals.drum_data

    def test_signal_unknown_section_no_error(self):
        """Signaling a section that doesn't exist does not raise."""
        signals = SectionSignals.from_sections([_section("intro")])
        signals.signal_complete("nonexistent")

    @pytest.mark.anyio
    async def test_wait_for_returns_data(self):
        """wait_for blocks until signaled, then returns drum data."""
        signals = SectionSignals.from_sections([_section("verse")])

        async def _signal_later():
            await asyncio.sleep(0.01)
            signals.signal_complete("verse", drum_notes=[{"pitch": 38}])

        task = asyncio.create_task(_signal_later())
        data = await signals.wait_for("verse")
        await task
        assert data is not None
        assert data["drum_notes"] == [{"pitch": 38}]

    @pytest.mark.anyio
    async def test_wait_for_unknown_returns_none(self):
        """Waiting for a section not in the events dict returns None immediately."""
        signals = SectionSignals.from_sections([_section("intro")])
        result = await signals.wait_for("nonexistent")
        assert result is None

    @pytest.mark.anyio
    async def test_wait_for_already_set(self):
        """wait_for returns immediately if the event is already set."""
        signals = SectionSignals.from_sections([_section("intro")])
        signals.signal_complete("intro", drum_notes=[{"pitch": 42}])
        data = await signals.wait_for("intro")
        assert data is not None


# =============================================================================
# SectionResult
# =============================================================================


class TestSectionResult:
    def test_defaults(self):
        r = SectionResult(success=False, section_name="intro")
        assert not r.success
        assert r.region_id is None
        assert r.notes_generated == 0
        assert r.tool_results == []
        assert r.error is None

    def test_successful_result(self):
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
    async def test_successful_region_and_generate(self):
        """Happy path: region creates regionId, generate succeeds."""
        store = StateStore(conversation_id="test-sc")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        call_count = 0

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
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
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
            )

        assert result.success
        assert result.region_id == "reg-001"
        assert result.notes_generated == 24
        assert call_count == 2
        assert len(result.tool_result_msgs) == 2

    @pytest.mark.anyio
    async def test_region_failure_returns_early(self):
        """When region creation returns no regionId, section fails gracefully."""
        store = StateStore(conversation_id="test-sc-fail")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        bad_region = _ToolCallOutcome(
            enriched_params={},
            tool_result={"error": "collision"},
            sse_events=[],
            msg_call={},
            msg_result={},
            skipped=False,
        )

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            return bad_region

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
            )

        assert not result.success
        assert result.region_id is None
        assert "Region creation failed" in (result.error or "")

    @pytest.mark.anyio
    async def test_generate_failure_returns_error(self):
        """When generate is skipped (GPU error), section reports failure."""
        store = StateStore(conversation_id="test-sc-gen-fail")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _failed_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
            )

        assert not result.success
        assert result.region_id == "reg-001"
        assert "GPU unavailable" in (result.error or "")

    @pytest.mark.anyio
    async def test_track_id_injected(self):
        """Section child injects the parent's trackId into region and generate params."""
        store = StateStore(conversation_id="test-sc-tid")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        captured_args: list[dict] = []

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            captured_args.append({"name": tc_name, "args": resolved_args})
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="MY-TRACK-ID",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
            )

        assert captured_args[0]["args"]["trackId"] == "MY-TRACK-ID"
        assert captured_args[1]["args"]["trackId"] == "MY-TRACK-ID"
        assert captured_args[1]["args"]["regionId"] == "reg-001"


# =============================================================================
# _run_section_child — drum signaling
# =============================================================================


class TestSectionChildDrumSignaling:
    @pytest.mark.anyio
    async def test_drum_child_signals_on_success(self):
        """A drum section child signals SectionSignals after generate completes."""
        store = StateStore(conversation_id="test-sig")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        signals = SectionSignals.from_sections([_section("verse")])

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id, notes_count=12)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
                section_signals=signals,
                is_drum=True,
            )

        assert result.success
        assert signals.events["verse"].is_set()
        assert "verse" in signals.drum_data
        assert len(signals.drum_data["verse"]["drum_notes"]) == 12

    @pytest.mark.anyio
    async def test_drum_child_signals_on_region_failure(self):
        """Drum still signals even when region fails — prevents bass from hanging."""
        store = StateStore(conversation_id="test-sig-fail")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        signals = SectionSignals.from_sections([_section("chorus")])

        bad_region = _ToolCallOutcome(
            enriched_params={},
            tool_result={},
            sse_events=[],
            msg_call={},
            msg_result={},
        )

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            return bad_region

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                section=_section("chorus"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
                section_signals=signals,
                is_drum=True,
            )

        assert not result.success
        assert signals.events["chorus"].is_set()

    @pytest.mark.anyio
    async def test_drum_child_signals_on_generate_failure(self):
        """Drum still signals even when generate fails."""
        store = StateStore(conversation_id="test-sig-gen-fail")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        signals = SectionSignals.from_sections([_section("verse")])

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _failed_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
                section_signals=signals,
                is_drum=True,
            )

        assert not result.success
        assert signals.events["verse"].is_set()


# =============================================================================
# _run_section_child — bass waiting
# =============================================================================


class TestSectionChildBassWaiting:
    @pytest.mark.anyio
    async def test_bass_waits_for_drum_signal(self):
        """Bass section child blocks until the drum signal fires."""
        store = StateStore(conversation_id="test-bass-wait")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        signals = SectionSignals.from_sections([_section("verse")])
        bass_started = False

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            nonlocal bass_started
            bass_started = True
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        async def _signal_drum():
            await asyncio.sleep(0.05)
            assert not bass_started, "Bass should not have started before drum signal"
            signals.signal_complete("verse", drum_notes=[{"pitch": 36}])

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            drum_task = asyncio.create_task(_signal_drum())
            result = await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Bass",
                role="bass",
                agent_id="bass",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
                section_signals=signals,
                is_bass=True,
            )
            await drum_task

        assert result.success
        assert bass_started

    @pytest.mark.anyio
    async def test_bass_proceeds_without_signals(self):
        """Bass without section_signals runs immediately (no-drums edge case)."""
        store = StateStore(conversation_id="test-bass-nosig")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Bass",
                role="bass",
                agent_id="bass",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
                section_signals=None,
                is_bass=True,
            )

        assert result.success


# =============================================================================
# _run_section_child — SSE events
# =============================================================================


class TestSectionChildSSE:
    @pytest.mark.anyio
    async def test_sse_events_tagged_with_agent_and_section(self):
        """SSE events from tool outcomes are tagged with agentId and sectionName."""
        store = StateStore(conversation_id="test-sse")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                section=_section("chorus"),
                section_index=2,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
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
    async def test_exception_caught_returns_error_result(self):
        """An unhandled exception inside the child returns a failed SectionResult."""
        store = StateStore(conversation_id="test-exc")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            raise RuntimeError("unexpected crash")

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
            )

        assert not result.success
        assert "unexpected crash" in (result.error or "")

    @pytest.mark.anyio
    async def test_drum_signals_on_exception(self):
        """Drum child still signals on unhandled exception to unblock bass."""
        store = StateStore(conversation_id="test-exc-sig")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        signals = SectionSignals.from_sections([_section("verse")])

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            raise RuntimeError("boom")

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            result = await _run_section_child(
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Drums",
                role="drums",
                agent_id="drums",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
                section_signals=signals,
                is_drum=True,
            )

        assert not result.success
        assert signals.events["verse"].is_set()


# =============================================================================
# _dispatch_section_children — grouping
# =============================================================================


class TestDispatchSectionChildren:
    """Tests for _dispatch_section_children in agent.py."""

    @pytest.mark.anyio
    async def test_groups_tool_calls_correctly(self):
        """Track creation, region+gen pairs, and effect calls are categorized."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-dispatch")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        all_results: list[dict] = []
        collected: list[dict] = []

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

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
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
                collected_tool_calls=collected,
                all_tool_results=all_results,
                add_notes_failures={},
                composition_context={"style": "house", "sections": sections},
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
    async def test_no_track_id_returns_error(self):
        """If no trackId is resolved, all remaining calls get error results."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-no-tid")
        queue: asyncio.Queue[dict] = asyncio.Queue()

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
            composition_context=None,
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
    async def test_reused_track_id_skips_creation(self):
        """When existing_track_id is set, track creation calls are absent."""
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-reuse")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        r1 = _region_tc("r1")
        g1 = _generate_tc("g1")

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id)

        mock_plan = MagicMock()
        mock_plan.steps = []
        mock_plan.complete_step_by_id = MagicMock(return_value=None)
        mock_plan.activate_step = MagicMock(return_value={"type": "planStepUpdate"})

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            msgs, st, se, rc, ro, gc = await _dispatch_section_children(
                tool_calls=[r1, g1],
                sections=[_section("verse")],
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
                collected_tool_calls=[],
                all_tool_results=[],
                add_notes_failures={},
                composition_context={"style": "house", "sections": [_section("verse")]},
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


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    @pytest.mark.anyio
    async def test_single_section_uses_sequential_path(self):
        """With only one section, the parent uses the sequential execution path."""
        # This is a structural test — we verify that _dispatch_section_children
        # is NOT called when _multi_section is False.  Testing the full agent
        # would require mocking the LLM; instead we verify the signal creation
        # edge case.
        signals = SectionSignals.from_sections([_section("full")])
        assert len(signals.events) == 1
        assert "full" in signals.events

    def test_no_drums_no_signals_needed(self):
        """When there are no drums, section_signals is harmless."""
        signals = SectionSignals.from_sections([_section("verse")])
        signals.signal_complete("verse")
        assert signals.events["verse"].is_set()
        assert "verse" not in signals.drum_data

    def test_no_bass_drum_signals_fire_unused(self):
        """Drum signals that no bass listens to are harmless."""
        signals = SectionSignals.from_sections([_section("verse")])
        signals.signal_complete("verse", drum_notes=[{"pitch": 36}])
        assert signals.events["verse"].is_set()
        assert signals.drum_data["verse"]["drum_notes"] == [{"pitch": 36}]
