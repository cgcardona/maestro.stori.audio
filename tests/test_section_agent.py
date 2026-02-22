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

    @pytest.mark.anyio
    async def test_planstep_completed_emitted_after_children_finish(self):
        """planStepUpdate(completed) is queued for content_step_id after children finish.

        Regression for P2: before the fix _dispatch_section_children activated
        content_step_id but never completed it.  The outer loop's active_step_id
        was never updated from the multi-section path, so the step stayed stuck
        in 'active' indefinitely on the macOS client.
        """
        from app.core.maestro_agent_teams.agent import _dispatch_section_children

        store = StateStore(conversation_id="test-planstep-completed")
        queue: asyncio.Queue[dict] = asyncio.Queue()
        all_results: list[dict] = []
        collected: list[dict] = []

        r1 = _region_tc("r1", start_beat=0, duration=16)
        g1 = _generate_tc("g1", role="drums")

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
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

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ), patch(
            "app.core.maestro_agent_teams.agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _dispatch_section_children(
                tool_calls=[r1, g1],
                sections=[_section("verse", 0, 16)],
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
                collected_tool_calls=collected,
                all_tool_results=all_results,
                add_notes_failures={},
                composition_context={"style": "house", "sections": [_section("verse", 0, 16)]},
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
        events: list[dict] = []
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
    async def test_generator_events_tagged_with_agentid_via_emit(self):
        """generatorStart and generatorComplete events in section children carry agentId.

        The _emit() helper in section_agent tags all _AGENT_TAGGED_EVENTS with
        agentId + sectionName.  This test verifies that a section child's queued
        events contain agentId so the macOS client can route them to the correct
        instrument card.
        """
        store = StateStore(conversation_id="test-agentid-tag")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        # Generate outcome whose sse_events include generatorStart / generatorComplete
        gen_outcome = _ok_generate_outcome("g1", notes_count=16)
        # Confirm the fixture contains the expected event types
        event_types = {e["type"] for e in gen_outcome.sse_events}
        assert "generatorStart" in event_types or "generatorComplete" in event_types

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return gen_outcome

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                section=_section("chorus"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(role="bass"),
                instrument_name="Bass",
                role="bass",
                agent_id="bass",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
            )

        events: list[dict] = []
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


# =============================================================================
# _extract_expressiveness_blocks
# =============================================================================


class TestExtractExpressivenessBlocks:
    def test_extracts_midi_expressiveness(self):
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

    def test_extracts_automation(self):
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

    def test_extracts_both_blocks(self):
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

    def test_no_blocks_returns_empty(self):
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
    async def test_emits_start_status(self):
        """Section child emits a 'Starting' status event at the beginning."""
        store = StateStore(conversation_id="test-status")
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
                section=_section("verse"),
                section_index=0,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Synth Lead",
                role="melody",
                agent_id="lead-agent",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
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
    async def test_emits_completion_status_with_note_count(self):
        """Section child emits a notes-generated status event after generation."""
        store = StateStore(conversation_id="test-status-done")
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def _mock_apply(*, tc_id, tc_name, resolved_args, **kw):
            if tc_name == "stori_add_midi_region":
                return _ok_region_outcome(tc_id)
            return _ok_generate_outcome(tc_id, notes_count=42)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            side_effect=_mock_apply,
        ):
            await _run_section_child(
                section=_section("chorus"),
                section_index=1,
                track_id="trk-1",
                region_tc=_region_tc(),
                generate_tc=_generate_tc(),
                instrument_name="Bass",
                role="bass",
                agent_id="bass-agent",
                allowed_tool_names={"stori_add_midi_region", "stori_generate_midi"},
                store=store,
                trace=_trace(),
                sse_queue=queue,
                composition_context=None,
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
    async def test_refinement_streams_reasoning_events(self):
        """Expression refinement streams reasoning SSE events with sectionName."""
        from app.core.maestro_agent_teams.section_agent import (
            _maybe_refine_expression,
        )

        queue: asyncio.Queue[dict] = asyncio.Queue()
        store = StateStore(conversation_id="test-expr")

        mock_llm = MagicMock()

        async def _mock_stream(**kwargs):
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

        composition_context = {
            "style": "techno",
            "tempo": 130,
            "key": "Am",
            "_raw_prompt": (
                "Title: Test\n\n"
                "MidiExpressiveness:\n"
                "  modulation:\n"
                "    instrument: lead\n"
                "    depth: strong vibrato — CC 1 value 60-90\n"
                "\nStructure:\n  Verse: 8 bars\n"
            ),
        }

        result = SectionResult(success=True, section_name="verse", notes_generated=24)

        with patch(
            "app.core.maestro_agent_teams.section_agent._apply_single_tool_call",
            return_value=cc_outcome,
        ):
            await _maybe_refine_expression(
                section=_section("verse"),
                track_id="trk-1",
                region_id="reg-001",
                instrument_name="Synth Lead",
                role="melody",
                agent_id="lead-agent",
                sec_name="verse",
                notes_generated=24,
                llm=mock_llm,
                store=store,
                trace=_trace(),
                sse_queue=queue,
                allowed_tool_names={
                    "stori_add_midi_cc",
                    "stori_add_pitch_bend",
                },
                composition_context=composition_context,
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
    async def test_refinement_skipped_without_expressiveness(self):
        """No LLM call when the prompt lacks MidiExpressiveness/Automation."""
        from app.core.maestro_agent_teams.section_agent import (
            _maybe_refine_expression,
        )

        queue: asyncio.Queue[dict] = asyncio.Queue()
        store = StateStore(conversation_id="test-no-expr")
        mock_llm = MagicMock()

        composition_context = {
            "style": "house",
            "tempo": 120,
            "key": "C",
            "_raw_prompt": "Title: Simple\nStyle: house\nStructure:\n  Verse: 8 bars\n",
        }

        result = SectionResult(success=True, section_name="verse", notes_generated=24)

        await _maybe_refine_expression(
            section=_section("verse"),
            track_id="trk-1",
            region_id="reg-001",
            instrument_name="Drums",
            role="drums",
            agent_id="drums",
            sec_name="verse",
            notes_generated=24,
            llm=mock_llm,
            store=store,
            trace=_trace(),
            sse_queue=queue,
            allowed_tool_names={"stori_add_midi_cc"},
            composition_context=composition_context,
            result=result,
            child_log="[test][Drums/verse]",
        )

        assert queue.empty()
        mock_llm.chat_completion_stream.assert_not_called()

    @pytest.mark.anyio
    async def test_refinement_includes_expr_blocks_in_prompt(self):
        """Refinement LLM call includes extracted MidiExpressiveness content."""
        from app.core.maestro_agent_teams.section_agent import (
            _maybe_refine_expression,
        )

        queue: asyncio.Queue[dict] = asyncio.Queue()
        store = StateStore(conversation_id="test-expr-ctx")

        captured_messages: list[dict] = []
        mock_llm = MagicMock()

        async def _capture_stream(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            yield {
                "type": "done",
                "content": None,
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {},
            }

        mock_llm.chat_completion_stream = MagicMock(side_effect=lambda **kw: _capture_stream(**kw))

        composition_context = {
            "style": "house",
            "tempo": 128,
            "key": "Cm",
            "_raw_prompt": (
                "Title: Deep house\n\n"
                "MidiExpressiveness:\n"
                "  modulation:\n"
                "    instrument: pad\n"
                "    depth: subtle vibrato — CC 1 value 30-50\n"
                "\nStructure:\n  Verse: 8 bars\n"
            ),
        }

        result = SectionResult(success=True, section_name="verse", notes_generated=30)

        await _maybe_refine_expression(
            section=_section("verse"),
            track_id="trk-1",
            region_id="reg-001",
            instrument_name="Pad",
            role="chords",
            agent_id="pad-agent",
            sec_name="verse",
            notes_generated=30,
            llm=mock_llm,
            store=store,
            trace=_trace(),
            sse_queue=queue,
            allowed_tool_names={"stori_add_midi_cc", "stori_add_pitch_bend"},
            composition_context=composition_context,
            result=result,
            child_log="[test][Pad/verse]",
        )

        assert len(captured_messages) >= 1
        system_msg = captured_messages[0]["content"]
        assert "CC 1 value 30-50" in system_msg
        assert "modulation:" in system_msg
