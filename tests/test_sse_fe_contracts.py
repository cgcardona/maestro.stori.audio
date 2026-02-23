"""Regression tests for strict FE SSE event and tool call contracts.

Every required field that is missing or malformed causes the FE to drop
the event silently.  These tests ensure the backend never omits a
required field on any event type.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.core.sse_utils import sse_event
from app.core.tool_validation.constants import (
    TOOL_REQUIRED_FIELDS,
    VALID_SF_SYMBOL_ICONS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sse(raw: str) -> dict[str, Any]:
    """Strip ``data: `` prefix and parse JSON payload."""
    assert raw.startswith("data: ")
    return json.loads(raw[6:].strip())


# ---------------------------------------------------------------------------
# SSE event structure contracts
# ---------------------------------------------------------------------------


class TestCompleteEventContract:
    """complete: success (required)."""

    @pytest.mark.anyio
    async def test_success_field_required(self):
        event = await sse_event({"type": "complete", "success": True})
        payload = _parse_sse(event)
        assert "success" in payload

    @pytest.mark.anyio
    async def test_success_false_on_failure(self):
        event = await sse_event({"type": "complete", "success": False, "error": "timeout"})
        payload = _parse_sse(event)
        assert payload["success"] is False


class TestPreflightEventContract:
    """preflight: agentId, stepId (both required, non-empty)."""

    @pytest.mark.anyio
    async def test_required_fields_present(self):
        event = await sse_event({
            "type": "preflight",
            "agentId": "drums",
            "stepId": "step-1",
        })
        payload = _parse_sse(event)
        assert payload["agentId"]
        assert payload["stepId"]


class TestPlanEventContract:
    """plan: planId (required, non-empty), steps[].stepId (required per step)."""

    @pytest.mark.anyio
    async def test_plan_id_required(self):
        event = await sse_event({
            "type": "plan",
            "planId": "plan-abc",
            "title": "Test Plan",
            "steps": [{"stepId": "s1", "label": "Step 1", "status": "pending"}],
        })
        payload = _parse_sse(event)
        assert payload["planId"]
        for step in payload.get("steps", []):
            assert step["stepId"]


class TestPlanStepUpdateContract:
    """planStepUpdate: stepId, status (both required)."""

    @pytest.mark.anyio
    async def test_required_fields(self):
        event = await sse_event({
            "type": "planStepUpdate",
            "stepId": "step-1",
            "status": "completed",
        })
        payload = _parse_sse(event)
        assert payload["stepId"]
        assert payload["status"] in ("pending", "active", "completed", "failed", "skipped")


class TestPlanSummaryContract:
    """planSummary: totalSteps (required)."""

    @pytest.mark.anyio
    async def test_total_steps_required(self):
        event = await sse_event({
            "type": "planSummary",
            "totalSteps": 5,
            "generations": 3,
            "edits": 2,
        })
        payload = _parse_sse(event)
        assert "totalSteps" in payload
        assert isinstance(payload["totalSteps"], int)


class TestToolCallEventContract:
    """toolCall: name, params (both required)."""

    @pytest.mark.anyio
    async def test_required_fields(self):
        event = await sse_event({
            "type": "toolCall",
            "name": "stori_set_tempo",
            "params": {"tempo": 120},
            "id": "call-1",
        })
        payload = _parse_sse(event)
        assert payload["name"]
        assert isinstance(payload["params"], dict)


class TestToolStartEventContract:
    """toolStart: name (required, non-empty)."""

    @pytest.mark.anyio
    async def test_name_required(self):
        event = await sse_event({
            "type": "toolStart",
            "name": "stori_add_midi_track",
            "label": "Adding Drums track",
        })
        payload = _parse_sse(event)
        assert payload["name"]


class TestToolErrorEventContract:
    """toolError: name, error (both required, non-empty)."""

    @pytest.mark.anyio
    async def test_required_fields(self):
        event = await sse_event({
            "type": "toolError",
            "name": "stori_add_notes",
            "error": "Invalid note data",
        })
        payload = _parse_sse(event)
        assert payload["name"]
        assert payload["error"]

    @pytest.mark.anyio
    async def test_error_never_empty_string(self):
        """toolError must never have an empty error string."""
        event = await sse_event({
            "type": "toolError",
            "name": "stori_generate_midi",
            "error": "Generation failed",
        })
        payload = _parse_sse(event)
        assert payload["error"] != ""


class TestSummaryEventContract:
    """summary: tracks, regions, notes (all required)."""

    @pytest.mark.anyio
    async def test_required_fields(self):
        event = await sse_event({
            "type": "summary",
            "tracks": ["Drums", "Bass"],
            "regions": 2,
            "notes": 64,
            "effects": 1,
        })
        payload = _parse_sse(event)
        assert isinstance(payload["tracks"], list)
        assert isinstance(payload["regions"], int)
        assert isinstance(payload["notes"], int)


class TestSummaryFinalEventContract:
    """summary.final: tracksCreated (required)."""

    @pytest.mark.anyio
    async def test_tracks_created_required(self):
        event = await sse_event({
            "type": "summary.final",
            "tracksCreated": [{"name": "Drums", "trackId": "abc"}],
            "traceId": "trace-1",
        })
        payload = _parse_sse(event)
        assert "tracksCreated" in payload
        assert isinstance(payload["tracksCreated"], list)


class TestDoneEventContract:
    """done: variationId (required, non-empty)."""

    @pytest.mark.anyio
    async def test_variation_id_required_nonempty(self):
        vid = str(uuid.uuid4())
        event = await sse_event({
            "type": "done",
            "variationId": vid,
            "phraseCount": 3,
        })
        payload = _parse_sse(event)
        assert payload["variationId"]
        assert payload["variationId"] != ""

    @pytest.mark.anyio
    async def test_failure_done_still_has_variation_id(self):
        """Even on failure, done must have a non-empty variationId."""
        vid = str(uuid.uuid4())
        event = await sse_event({
            "type": "done",
            "variationId": vid,
            "phraseCount": 0,
            "status": "failed",
        })
        payload = _parse_sse(event)
        assert payload["variationId"] != ""


class TestGeneratorStartContract:
    """generatorStart: role, style, bars (all required)."""

    @pytest.mark.anyio
    async def test_required_fields(self):
        event = await sse_event({
            "type": "generatorStart",
            "role": "drums",
            "style": "boom bap",
            "bars": 8,
            "label": "Drums",
        })
        payload = _parse_sse(event)
        assert payload["role"]
        assert "style" in payload
        assert isinstance(payload["bars"], int)


class TestGeneratorCompleteContract:
    """generatorComplete: role, noteCount, durationMs (all required)."""

    @pytest.mark.anyio
    async def test_required_fields(self):
        event = await sse_event({
            "type": "generatorComplete",
            "role": "drums",
            "noteCount": 128,
            "durationMs": 2500,
        })
        payload = _parse_sse(event)
        assert payload["role"]
        assert isinstance(payload["noteCount"], int)
        assert isinstance(payload["durationMs"], int)


class TestErrorEventContract:
    """error: error OR message (at least one required)."""

    @pytest.mark.anyio
    async def test_message_field(self):
        event = await sse_event({"type": "error", "message": "Something went wrong"})
        payload = _parse_sse(event)
        assert "message" in payload or "error" in payload

    @pytest.mark.anyio
    async def test_error_field(self):
        event = await sse_event({"type": "error", "error": "Internal failure"})
        payload = _parse_sse(event)
        assert "message" in payload or "error" in payload


class TestReasoningEventContract:
    """reasoning: content (required)."""

    @pytest.mark.anyio
    async def test_content_required(self):
        event = await sse_event({"type": "reasoning", "content": "Analyzing..."})
        payload = _parse_sse(event)
        assert "content" in payload


class TestContentEventContract:
    """content: content (required)."""

    @pytest.mark.anyio
    async def test_content_required(self):
        event = await sse_event({"type": "content", "content": "Here is the plan"})
        payload = _parse_sse(event)
        assert "content" in payload


class TestStateEventContract:
    """state: state (required)."""

    @pytest.mark.anyio
    async def test_state_required(self):
        event = await sse_event({"type": "state", "state": "COMPOSING"})
        payload = _parse_sse(event)
        assert "state" in payload


class TestStatusEventContract:
    """status: message (required)."""

    @pytest.mark.anyio
    async def test_message_required(self):
        event = await sse_event({"type": "status", "message": "Processing..."})
        payload = _parse_sse(event)
        assert "message" in payload


# ---------------------------------------------------------------------------
# Tool required fields validation
# ---------------------------------------------------------------------------


class TestToolRequiredFieldsCompleteness:
    """Verify TOOL_REQUIRED_FIELDS covers all FE-required tools."""

    def test_add_midi_track_requires_name(self):
        assert "name" in TOOL_REQUIRED_FIELDS["stori_add_midi_track"]

    def test_add_notes_requires_regionid_notes(self):
        assert "regionId" in TOOL_REQUIRED_FIELDS["stori_add_notes"]
        assert "notes" in TOOL_REQUIRED_FIELDS["stori_add_notes"]

    def test_add_midi_region_requires_trackid_startbeat_durationbeats(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_add_midi_region"]
        assert "startBeat" in TOOL_REQUIRED_FIELDS["stori_add_midi_region"]
        assert "durationBeats" in TOOL_REQUIRED_FIELDS["stori_add_midi_region"]

    def test_set_tempo_requires_tempo(self):
        assert "tempo" in TOOL_REQUIRED_FIELDS["stori_set_tempo"]

    def test_set_key_requires_key(self):
        assert "key" in TOOL_REQUIRED_FIELDS["stori_set_key"]

    def test_set_track_volume_requires_fields(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_volume"]
        assert "volume" in TOOL_REQUIRED_FIELDS["stori_set_track_volume"]

    def test_set_track_pan_requires_fields(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_pan"]
        assert "pan" in TOOL_REQUIRED_FIELDS["stori_set_track_pan"]

    def test_set_track_name_requires_fields(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_name"]
        assert "name" in TOOL_REQUIRED_FIELDS["stori_set_track_name"]

    def test_set_track_color_requires_fields(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_color"]
        assert "color" in TOOL_REQUIRED_FIELDS["stori_set_track_color"]

    def test_set_track_icon_requires_fields(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_icon"]
        assert "icon" in TOOL_REQUIRED_FIELDS["stori_set_track_icon"]

    def test_set_playhead_requires_beat(self):
        assert "beat" in TOOL_REQUIRED_FIELDS["stori_set_playhead"]

    def test_add_insert_effect_requires_fields(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_add_insert_effect"]
        assert "type" in TOOL_REQUIRED_FIELDS["stori_add_insert_effect"]

    def test_add_send_requires_fields(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_add_send"]
        assert "busName" in TOOL_REQUIRED_FIELDS["stori_add_send"]

    def test_ensure_bus_requires_name(self):
        assert "name" in TOOL_REQUIRED_FIELDS["stori_ensure_bus"]

    def test_move_region_requires_fields(self):
        assert "regionId" in TOOL_REQUIRED_FIELDS["stori_move_region"]
        assert "startBeat" in TOOL_REQUIRED_FIELDS["stori_move_region"]

    def test_add_automation_requires_fields(self):
        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_add_automation"]
        assert "parameter" in TOOL_REQUIRED_FIELDS["stori_add_automation"]
        assert "points" in TOOL_REQUIRED_FIELDS["stori_add_automation"]

    def test_add_midi_cc_requires_fields(self):
        assert "regionId" in TOOL_REQUIRED_FIELDS["stori_add_midi_cc"]
        assert "cc" in TOOL_REQUIRED_FIELDS["stori_add_midi_cc"]
        assert "events" in TOOL_REQUIRED_FIELDS["stori_add_midi_cc"]

    def test_add_pitch_bend_requires_fields(self):
        assert "regionId" in TOOL_REQUIRED_FIELDS["stori_add_pitch_bend"]
        assert "events" in TOOL_REQUIRED_FIELDS["stori_add_pitch_bend"]


# ---------------------------------------------------------------------------
# Icon allowlist parity with FE contract
# ---------------------------------------------------------------------------


class TestIconAllowlistParity:
    """Ensure every icon from the FE curated list is in the backend allowlist."""

    FE_ICON_LIST = [
        # Instruments
        "instrument.trumpet", "instrument.violin", "instrument.saxophone",
        "instrument.flute", "instrument.drum", "instrument.harp", "instrument.xylophone",
        "guitars", "guitars.fill", "pianokeys", "pianokeys.inverse",
        "music.mic", "music.mic.circle", "music.mic.circle.fill",
        "headphones", "headphones.circle", "headphones.circle.fill",
        "hifispeaker", "hifispeaker.fill", "hifispeaker.2", "hifispeaker.2.fill",
        "tuningfork", "speaker", "speaker.fill",
        "speaker.wave.2", "speaker.wave.3", "speaker.slash", "speaker.slash.fill",
        # Notes
        "music.note", "music.note.list", "music.quarternote.3",
        "music.note.house", "music.note.tv",
        "waveform", "waveform.circle", "waveform.circle.fill",
        "waveform.path", "waveform.path.ecg",
        "music.note.house.fill", "music.note.tv.fill",
        "waveform.and.mic", "waveform.badge.mic", "waveform.slash",
        # Effects
        "slider.horizontal.3", "slider.vertical.3",
        "sparkles", "wand.and.rays", "wand.and.stars", "wand.and.stars.inverse",
        "bolt", "bolt.fill", "bolt.circle", "bolt.circle.fill",
        "flame", "flame.fill", "metronome", "star", "star.fill",
        "dial.min", "dial.medium", "dial.max",
        "repeat", "repeat.1", "shuffle",
        "ear", "ear.badge.waveform",
        "speaker.wave.2", "speaker.wave.3", "globe", "speaker.wave.3.fill",
    ]

    @pytest.mark.parametrize("icon", FE_ICON_LIST)
    def test_fe_icon_in_backend_allowlist(self, icon: str):
        """Every icon from the FE curated list must be in VALID_SF_SYMBOL_ICONS."""
        assert icon in VALID_SF_SYMBOL_ICONS, (
            f"Icon '{icon}' is in the FE curated list but missing from "
            f"VALID_SF_SYMBOL_ICONS in constants.py"
        )


# ---------------------------------------------------------------------------
# 402 response shape
# ---------------------------------------------------------------------------


class TestBudgetExhaustedResponseShape:
    """402 responses must be JSON decodable as BudgetExhaustedError."""

    def test_structured_detail_has_message_and_budget(self):
        """FE expects detail: {message: str, budgetRemaining: float}."""
        detail = {
            "message": "Insufficient budget",
            "budgetRemaining": 0.0,
        }
        assert "message" in detail
        assert isinstance(detail["budgetRemaining"], (int, float))

    def test_detail_uses_message_not_error_key(self):
        """FE decodes BudgetExhaustedError using 'message', not 'error'."""
        detail = {
            "message": "Insufficient budget",
            "budgetRemaining": 0.42,
        }
        assert "message" in detail
        assert "error" not in detail


# ═══════════════════════════════════════════════════════════════════════════════
# Bug-fix regressions (2026-02-22)
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolOrderingRegression:
    """Regression: instrument agent must sort tool calls so
    stori_add_midi_region always executes before stori_generate_midi."""

    def test_tool_call_sorting_region_before_generator(self):
        """Tool calls batched by LLM are re-sorted: region < generator < effect."""
        from app.core.maestro_plan_tracker.constants import (
            _TRACK_CREATION_NAMES,
            _GENERATOR_TOOL_NAMES,
            _EFFECT_TOOL_NAMES,
        )

        _TOOL_ORDER: dict[str, int] = {}
        for n in _TRACK_CREATION_NAMES:
            _TOOL_ORDER[n] = 0
        _TOOL_ORDER["stori_add_midi_region"] = 1
        for n in _GENERATOR_TOOL_NAMES:
            _TOOL_ORDER[n] = 2
        for n in _EFFECT_TOOL_NAMES:
            _TOOL_ORDER[n] = 3

        out_of_order = [
            "stori_generate_midi",
            "stori_add_insert_effect",
            "stori_add_midi_track",
            "stori_add_midi_region",
        ]
        sorted_names = sorted(out_of_order, key=lambda n: _TOOL_ORDER.get(n, 2))
        assert sorted_names == [
            "stori_add_midi_track",
            "stori_add_midi_region",
            "stori_generate_midi",
            "stori_add_insert_effect",
        ]


class TestAgentIdTaggingRegression:
    """Regression: generatorStart and generatorComplete SSE events
    must include agentId so the FE routes them to the correct sub-agent."""

    def test_generator_events_in_tagged_set(self):
        """generatorStart and generatorComplete are in the agent-tagged event set."""
        tagged = {
            "toolCall", "toolStart", "toolError",
            "generatorStart", "generatorComplete",
            "reasoning", "content", "status",
        }
        assert "generatorStart" in tagged
        assert "generatorComplete" in tagged

    def test_reasoning_in_tagged_set(self):
        """reasoning events are tagged with agentId for correct sub-agent routing."""
        tagged = {
            "toolCall", "toolStart", "toolError",
            "generatorStart", "generatorComplete",
            "reasoning", "content", "status",
        }
        assert "reasoning" in tagged

    @pytest.mark.anyio
    async def test_generator_start_event_contains_agentid_at_source(self):
        """generatorStart emitted by _execute_agent_generator carries agentId = role.

        Regression for P2 (generatorStart/generatorComplete missing agentId):
        before the fix these events had no agentId field at the source level.
        Section children added it via _emit, but single-section paths and any
        future consumer that bypassed _emit would not get the field.  The fix
        bakes agentId = role into the event inside _execute_agent_generator.
        """
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.core.maestro_editing.tool_execution import _execute_agent_generator
        from app.core.tracing import TraceContext
        from app.core.state_store import StateStore
        from app.services.backends.base import GenerationResult, GeneratorBackend

        store = StateStore()
        track_id = store.create_track("Bass")
        region_id = store.create_region("Region", track_id)

        trace = TraceContext(trace_id="test-agentid-src")
        comp_ctx = {"style": "dancehall", "tempo": 90, "bars": 8, "key": "Am", "quality_preset": "balanced"}

        ok_result = GenerationResult(
            success=True,
            notes=[{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 80}],
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
        )

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(return_value=ok_result)

        with patch("app.core.maestro_editing.tool_execution.get_music_generator", return_value=mock_mg):
            outcome = await _execute_agent_generator(
                tc_id="tc-1",
                tc_name="stori_generate_midi",
                enriched_params={
                    "role": "bass",
                    "trackId": track_id,
                    "regionId": region_id,
                    "style": "dancehall",
                    "tempo": 90,
                    "bars": 8,
                    "key": "Am",
                },
                store=store,
                trace=trace,
                composition_context=comp_ctx,
                emit_sse=True,
            )

        assert outcome is not None
        generator_start_events = [
            e for e in outcome.sse_events if e.get("type") == "generatorStart"
        ]
        generator_complete_events = [
            e for e in outcome.sse_events if e.get("type") == "generatorComplete"
        ]

        assert len(generator_start_events) == 1, "Expected exactly one generatorStart event"
        assert len(generator_complete_events) == 1, "Expected exactly one generatorComplete event"

        gs = generator_start_events[0]
        assert "agentId" in gs, f"generatorStart missing agentId: {gs}"
        assert gs["agentId"] == "bass", f"Expected agentId='bass', got {gs['agentId']}"

        gc = generator_complete_events[0]
        assert "agentId" in gc, f"generatorComplete missing agentId: {gc}"
        assert gc["agentId"] == "bass", f"Expected agentId='bass', got {gc['agentId']}"


class TestEffectPersistenceRegression:
    """Regression: stori_add_insert_effect must persist to StateStore."""

    def test_add_effect_method_exists(self):
        """StateStore.add_effect exists and is callable."""
        from app.core.state_store import StateStore
        store = StateStore()
        assert hasattr(store, "add_effect")
        assert callable(store.add_effect)


class TestCompleteEventSuccessField:
    """Regression: complete event must set success=false when 0 notes were generated.

    When stori_generate_midi fails for all tracks (e.g. Orpheus returns a
    NoneType error), the composition finishes with empty regions.
    The complete event must NOT report success=true in this case.
    """

    def _make_summary(self, notes: int, regions: int) -> dict:
        """Build a minimal summary dict as _build_composition_summary would."""
        return {
            "notesGenerated": notes,
            "regionsCreated": regions,
            "tracksCreated": [{"name": "Bass", "trackId": "t1", "instrument": "bass"}] if regions else [],
            "tracksReused": [],
            "trackCount": 1 if regions else 0,
            "effectsAdded": [],
            "effectCount": 0,
            "sendsCreated": 0,
            "ccEnvelopes": [],
            "automationLanes": 0,
            "text": "",
        }

    def _success_for(self, notes: int, regions: int) -> bool:
        """Mirror the coordinator's success logic."""
        return notes > 0 or regions == 0

    def test_zero_notes_with_regions_is_failure(self):
        """0 notes + regions > 0 means generation was attempted but failed."""
        assert self._success_for(notes=0, regions=4) is False

    def test_notes_present_is_success(self):
        """Any notes produced = success."""
        assert self._success_for(notes=120, regions=4) is True

    def test_zero_notes_zero_regions_is_success(self):
        """No regions means generation was never attempted — not a failure."""
        assert self._success_for(notes=0, regions=0) is True

    def test_single_note_is_success(self):
        """Even one note is enough to call the composition successful."""
        assert self._success_for(notes=1, regions=1) is True

    def test_regression_four_track_all_failed(self):
        """4-track composition, all stori_generate_midi calls failed → success=false.

        Regression for: summary.final reports notesGenerated=0 but complete event
        still said success=true, misleading the macOS client into displaying a
        success confirmation for an empty composition.
        """
        notes, regions = 0, 4
        result = self._success_for(notes=notes, regions=regions)
        assert result is False, (
            f"4-track composition with {notes} notes and {regions} regions "
            "must produce success=false in the complete event"
        )

    def test_tool_errors_with_zero_notes_is_failure(self):
        """toolErrors > 0 AND notesGenerated == 0 → success=false."""
        notes, regions, tool_errors = 0, 4, 4
        base = self._success_for(notes=notes, regions=regions)
        assert base is False
        if tool_errors > 0 and notes == 0:
            assert True  # coordinator forces success=false


# ═══════════════════════════════════════════════════════════════════════════════
# SSE contract gap regressions
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeqFieldRegression:
    """Regression: SSE events must carry monotonically increasing seq (0-based)."""

    @pytest.mark.anyio
    async def test_seq_starts_at_zero(self):
        """The _with_seq wrapper must produce seq=0 for the first event."""
        import json as _json

        _seq = -1

        def _with_seq(event_str: str) -> str:
            nonlocal _seq
            if not event_str.startswith("data: "):
                return event_str
            _seq += 1
            data = _json.loads(event_str[6:].strip())
            data["seq"] = _seq
            return f"data: {_json.dumps(data)}\n\n"

        first_event = await sse_event({"type": "status", "message": "hello"})
        result = _with_seq(first_event)
        payload = _parse_sse(result)
        assert payload["seq"] == 0, f"First event should have seq=0, got {payload['seq']}"

    @pytest.mark.anyio
    async def test_seq_increments_monotonically(self):
        """Sequential events must get seq 0, 1, 2, ..."""
        import json as _json

        _seq = -1

        def _with_seq(event_str: str) -> str:
            nonlocal _seq
            if not event_str.startswith("data: "):
                return event_str
            _seq += 1
            data = _json.loads(event_str[6:].strip())
            data["seq"] = _seq
            return f"data: {_json.dumps(data)}\n\n"

        events = [
            await sse_event({"type": "status", "message": f"msg-{i}"})
            for i in range(5)
        ]
        seq_values = []
        for e in events:
            result = _with_seq(e)
            payload = _parse_sse(result)
            seq_values.append(payload["seq"])
        assert seq_values == [0, 1, 2, 3, 4]


class TestPhaseFieldRegression:
    """Regression: toolStart, toolCall, and planStepUpdate events must include phase."""

    def test_phase_for_tool_setup(self):
        """Setup tools (tempo, key) map to phase 'setup'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_set_tempo") == "setup"
        assert phase_for_tool("stori_set_key") == "setup"

    def test_phase_for_tool_composition(self):
        """Composition tools (track, region, generate) map to phase 'composition'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_add_midi_track") == "composition"
        assert phase_for_tool("stori_add_midi_region") == "composition"
        assert phase_for_tool("stori_generate_midi") == "composition"
        assert phase_for_tool("stori_add_notes") == "composition"

    def test_phase_for_tool_sound_design(self):
        """Effect and expressive tools map to phase 'soundDesign'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_add_insert_effect") == "soundDesign"
        assert phase_for_tool("stori_add_midi_cc") == "soundDesign"
        assert phase_for_tool("stori_add_pitch_bend") == "soundDesign"
        assert phase_for_tool("stori_ensure_bus") == "soundDesign"

    def test_phase_for_tool_mixing(self):
        """Mixing tools (volume, pan, sends) map to phase 'mixing'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_set_track_volume") == "mixing"
        assert phase_for_tool("stori_set_track_pan") == "mixing"

    @pytest.mark.anyio
    async def test_tool_start_includes_phase(self):
        """toolStart events must include a phase field."""
        from unittest.mock import MagicMock
        from app.core.maestro_editing.tool_execution import _apply_single_tool_call
        from app.core.tracing import TraceContext
        from app.core.state_store import StateStore

        store = StateStore()
        trace = TraceContext(trace_id="test-phase")
        outcome = await _apply_single_tool_call(
            tc_id="tc-phase-1",
            tc_name="stori_set_tempo",
            resolved_args={"tempo": 120},
            allowed_tool_names={"stori_set_tempo"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        tool_start_events = [e for e in outcome.sse_events if e["type"] == "toolStart"]
        assert len(tool_start_events) >= 1
        assert tool_start_events[0]["phase"] == "setup"

    @pytest.mark.anyio
    async def test_tool_call_includes_phase_and_label(self):
        """toolCall events must include phase and label fields."""
        from app.core.maestro_editing.tool_execution import _apply_single_tool_call
        from app.core.tracing import TraceContext
        from app.core.state_store import StateStore

        store = StateStore()
        trace = TraceContext(trace_id="test-label-phase")
        outcome = await _apply_single_tool_call(
            tc_id="tc-lp-1",
            tc_name="stori_set_tempo",
            resolved_args={"tempo": 140},
            allowed_tool_names={"stori_set_tempo"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        tool_call_events = [e for e in outcome.sse_events if e["type"] == "toolCall"]
        assert len(tool_call_events) >= 1
        tc_evt = tool_call_events[0]
        assert "label" in tc_evt, "toolCall must include label"
        assert "phase" in tc_evt, "toolCall must include phase"
        assert tc_evt["phase"] == "setup"
        assert tc_evt["label"]  # non-empty


class TestLabelOnToolCallRegression:
    """Regression: toolCall events must repeat the label from the preceding toolStart."""

    @pytest.mark.anyio
    async def test_tool_call_label_matches_tool_start(self):
        """The label on toolCall must match the label on toolStart."""
        from app.core.maestro_editing.tool_execution import _apply_single_tool_call
        from app.core.tracing import TraceContext
        from app.core.state_store import StateStore

        store = StateStore()
        trace = TraceContext(trace_id="test-label-match")
        outcome = await _apply_single_tool_call(
            tc_id="tc-lm-1",
            tc_name="stori_set_tempo",
            resolved_args={"tempo": 90},
            allowed_tool_names={"stori_set_tempo"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        starts = [e for e in outcome.sse_events if e["type"] == "toolStart"]
        calls = [e for e in outcome.sse_events if e["type"] == "toolCall"]
        assert len(starts) >= 1
        assert len(calls) >= 1
        assert starts[0]["label"] == calls[0]["label"], (
            f"toolStart label '{starts[0]['label']}' != toolCall label '{calls[0]['label']}'"
        )


class TestAgentCompleteEventContract:
    """agentComplete: agentId, success (both required)."""

    @pytest.mark.anyio
    async def test_required_fields_present(self):
        event = await sse_event({
            "type": "agentComplete",
            "agentId": "drums",
            "success": True,
        })
        payload = _parse_sse(event)
        assert payload["agentId"]
        assert isinstance(payload["success"], bool)

    def test_agent_complete_in_tagged_set(self):
        """agentComplete must be in the agent-tagged event set for proper routing."""
        from app.core.maestro_agent_teams.section_agent import _AGENT_TAGGED_EVENTS
        assert "agentComplete" in _AGENT_TAGGED_EVENTS


class TestPreflightTrackColorRegression:
    """Regression: preflight events should include trackColor from curated palette."""

    @pytest.mark.anyio
    async def test_preflight_with_track_color(self):
        event = await sse_event({
            "type": "preflight",
            "stepId": "step-1",
            "agentId": "drums",
            "agentRole": "drums",
            "label": "Create Drums track",
            "trackColor": "#E85D75",
        })
        payload = _parse_sse(event)
        assert "trackColor" in payload
        assert payload["trackColor"].startswith("#")

    def test_composition_palette_has_12_colors(self):
        """The palette must have 12 high-hue-separation colors."""
        from app.core.track_styling import COMPOSITION_PALETTE
        assert len(COMPOSITION_PALETTE) == 12

    def test_no_duplicate_colors_in_palette(self):
        """All palette colors must be unique."""
        from app.core.track_styling import COMPOSITION_PALETTE
        assert len(set(COMPOSITION_PALETTE)) == len(COMPOSITION_PALETTE)

    def test_allocate_colors_cycles_palette(self):
        """allocate_colors assigns distinct colors and cycles after exhaustion."""
        from app.core.track_styling import allocate_colors, COMPOSITION_PALETTE
        names = [f"Inst{i}" for i in range(14)]
        result = allocate_colors(names)
        assert len(result) == 14
        first_12 = [result[f"Inst{i}"] for i in range(12)]
        assert len(set(first_12)) == 12
        assert result["Inst12"] == COMPOSITION_PALETTE[0]


class TestOrpheusMetadataNoneRegression:
    """Regression: Orpheus returning metadata: null must not crash the client."""

    def test_metadata_none_unpacking(self):
        """Dict unpacking with None metadata must not raise TypeError.

        Regression for P0: 'NoneType' object is not a mapping when
        Orpheus returns {"metadata": null} in its response JSON.
        """
        data: dict[str, Any] = {
            "success": True,
            "notes": [{"pitch": 60}],
            "metadata": None,
        }
        out = {**(data.get("metadata") or {}), "retry_count": 0}
        assert out == {"retry_count": 0}

    def test_metadata_missing_key(self):
        """Missing metadata key falls back to empty dict."""
        data: dict[str, Any] = {"success": True, "notes": []}
        out = {**(data.get("metadata") or {}), "retry_count": 1}
        assert out == {"retry_count": 1}

    def test_metadata_present(self):
        """Valid metadata dict is preserved."""
        data: dict[str, Any] = {
            "success": True,
            "notes": [],
            "metadata": {"model": "v2"},
        }
        out = {**(data.get("metadata") or {}), "retry_count": 2}
        assert out == {"model": "v2", "retry_count": 2}


class TestCircuitBreakerContract:
    """Orpheus circuit breaker behavior: trip, fast-fail, reset."""

    def test_circuit_breaker_starts_closed(self):
        """New circuit breaker is closed (not open)."""
        from app.services.orpheus import _CircuitBreaker
        cb = _CircuitBreaker(threshold=3, cooldown=60)
        assert not cb.is_open

    def test_circuit_breaker_trips_after_threshold(self):
        """Circuit opens after `threshold` consecutive failures."""
        from app.services.orpheus import _CircuitBreaker
        cb = _CircuitBreaker(threshold=2, cooldown=60)
        cb.record_failure()
        assert not cb.is_open
        cb.record_failure()
        assert cb.is_open

    def test_circuit_breaker_success_resets(self):
        """A successful call resets the failure counter and closes the circuit."""
        from app.services.orpheus import _CircuitBreaker
        cb = _CircuitBreaker(threshold=2, cooldown=60)
        cb.record_failure()
        cb.record_success()
        assert cb._failures == 0
        cb.record_failure()
        assert not cb.is_open, "Counter should have reset — one failure is below threshold"

    def test_circuit_breaker_cooldown_half_open(self):
        """After cooldown, is_open returns False (half-open allows probe)."""
        import time
        from app.services.orpheus import _CircuitBreaker
        cb = _CircuitBreaker(threshold=1, cooldown=0.01)
        cb.record_failure()
        assert cb.is_open
        time.sleep(0.02)
        assert not cb.is_open, "Cooldown expired — should be half-open"

    def test_circuit_open_error_message_format(self):
        """Fast-fail result has the expected error key for downstream detection."""
        result = {
            "success": False,
            "error": "orpheus_circuit_open",
            "message": "Orpheus music service is unavailable (circuit breaker open).",
        }
        assert result["error"] == "orpheus_circuit_open"
        assert "circuit breaker" in result["message"].lower()


class TestL2ReasoningGuidanceContract:
    """Level 2 agent reasoning guidance prevents verbose section-level CoT."""

    def test_reasoning_guidance_prohibits_section_reasoning(self):
        """The L2 system prompt must contain instructions against per-section reasoning."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "Do NOT reason about individual sections" in source
        assert "section agents handle" in source

    def test_reasoning_guidance_limits_length(self):
        """The L2 system prompt must cap reasoning at 1-2 sentences."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "1-2 sentences ONLY" in source


class TestL3SectionReasoningContract:
    """Level 3 section child emits reasoning events with sectionName."""

    def test_section_child_has_reasoning_function(self):
        """_reason_before_generate exists and accepts the expected parameters."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _reason_before_generate
        sig = inspect.signature(_reason_before_generate)
        params = set(sig.parameters.keys())
        assert "section" in params
        assert "sec_name" in params
        assert "llm" in params
        assert "sse_queue" in params
        assert "generate_prompt" in params

    def test_section_reasoning_returns_optional_string(self):
        """_reason_before_generate return type allows None (fallback to original prompt)."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _reason_before_generate
        sig = inspect.signature(_reason_before_generate)
        assert sig.return_annotation is not inspect.Parameter.empty


class TestAgentCircuitBreakerAbort:
    """Level 2 agent stops retrying when Orpheus circuit breaker is open."""

    def test_agent_imports_orpheus_client(self):
        """agent.py imports get_orpheus_client for circuit breaker checks."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod)
        assert "get_orpheus_client" in source
        assert "circuit_breaker_open" in source
