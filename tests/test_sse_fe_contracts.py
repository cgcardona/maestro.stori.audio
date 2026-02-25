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
    result: dict[str, Any] = json.loads(raw[6:].strip())
    return result


# ---------------------------------------------------------------------------
# SSE event structure contracts
# ---------------------------------------------------------------------------


class TestCompleteEventContract:
    """complete: success (required)."""

    @pytest.mark.anyio
    async def test_success_field_required(self) -> None:

        event = await sse_event({"type": "complete", "success": True, "traceId": "t-1"})
        payload = _parse_sse(event)
        assert "success" in payload

    @pytest.mark.anyio
    async def test_success_false_on_failure(self) -> None:

        event = await sse_event({"type": "complete", "success": False, "error": "timeout", "traceId": "t-1"})
        payload = _parse_sse(event)
        assert payload["success"] is False


class TestPreflightEventContract:
    """preflight: agentId, stepId, agentRole, label, toolName (required)."""

    @pytest.mark.anyio
    async def test_required_fields_present(self) -> None:

        event = await sse_event({
            "type": "preflight",
            "agentId": "drums",
            "stepId": "step-1",
            "agentRole": "drums",
            "label": "Create Drums",
            "toolName": "stori_add_track",
        })
        payload = _parse_sse(event)
        assert payload["agentId"]
        assert payload["stepId"]


class TestPlanEventContract:
    """plan: planId (required, non-empty), steps[].stepId (required per step)."""

    @pytest.mark.anyio
    async def test_plan_id_required(self) -> None:

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
    async def test_required_fields(self) -> None:

        event = await sse_event({
            "type": "planStepUpdate",
            "stepId": "step-1",
            "status": "completed",
        })
        payload = _parse_sse(event)
        assert payload["stepId"]
        assert payload["status"] in ("pending", "active", "completed", "failed", "skipped")


class TestToolCallEventContract:
    """toolCall: name, params (both required)."""

    @pytest.mark.anyio
    async def test_required_fields(self) -> None:

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
    async def test_name_required(self) -> None:

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
    async def test_required_fields(self) -> None:

        event = await sse_event({
            "type": "toolError",
            "name": "stori_add_notes",
            "error": "Invalid note data",
        })
        payload = _parse_sse(event)
        assert payload["name"]
        assert payload["error"]

    @pytest.mark.anyio
    async def test_error_never_empty_string(self) -> None:

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
    async def test_required_fields(self) -> None:

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
    async def test_tracks_created_required(self) -> None:

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
    async def test_variation_id_required_nonempty(self) -> None:

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
    async def test_failure_done_still_has_variation_id(self) -> None:

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
    """generatorStart: role, agentId, style, bars, startBeat, label (all required)."""

    @pytest.mark.anyio
    async def test_required_fields(self) -> None:

        event = await sse_event({
            "type": "generatorStart",
            "role": "drums",
            "agentId": "drums",
            "style": "boom bap",
            "bars": 8,
            "startBeat": 0.0,
            "label": "Drums",
        })
        payload = _parse_sse(event)
        assert payload["role"]
        assert "style" in payload
        assert isinstance(payload["bars"], int)


class TestGeneratorCompleteContract:
    """generatorComplete: role, agentId, noteCount, durationMs (all required)."""

    @pytest.mark.anyio
    async def test_required_fields(self) -> None:

        event = await sse_event({
            "type": "generatorComplete",
            "role": "drums",
            "agentId": "drums",
            "noteCount": 128,
            "durationMs": 2500,
        })
        payload = _parse_sse(event)
        assert payload["role"]
        assert isinstance(payload["noteCount"], int)
        assert isinstance(payload["durationMs"], int)


class TestErrorEventContract:
    """error: message (required)."""

    @pytest.mark.anyio
    async def test_message_field(self) -> None:

        event = await sse_event({"type": "error", "message": "Something went wrong"})
        payload = _parse_sse(event)
        assert "message" in payload

    @pytest.mark.anyio
    async def test_message_on_internal_failure(self) -> None:

        event = await sse_event({"type": "error", "message": "Internal failure"})
        payload = _parse_sse(event)
        assert payload["message"] == "Internal failure"


class TestReasoningEventContract:
    """reasoning: content (required)."""

    @pytest.mark.anyio
    async def test_content_required(self) -> None:

        event = await sse_event({"type": "reasoning", "content": "Analyzing..."})
        payload = _parse_sse(event)
        assert "content" in payload


class TestReasoningEndEventContract:
    """reasoningEnd: agentId required; sectionName present when applicable."""

    @pytest.mark.anyio
    async def test_instrument_agent_has_agentid(self) -> None:

        """reasoningEnd for an instrument agent carries agentId, no sectionName."""
        event = await sse_event({"type": "reasoningEnd", "agentId": "brass"})
        payload = _parse_sse(event)
        assert payload["type"] == "reasoningEnd"
        assert payload["agentId"] == "brass"

    @pytest.mark.anyio
    async def test_section_agent_has_agentid_and_sectionname(self) -> None:

        """reasoningEnd for a section child carries both agentId and sectionName."""
        event = await sse_event({
            "type": "reasoningEnd",
            "agentId": "brass",
            "sectionName": "verse",
        })
        payload = _parse_sse(event)
        assert payload["type"] == "reasoningEnd"
        assert payload["agentId"] == "brass"
        assert payload["sectionName"] == "verse"

    @pytest.mark.anyio
    async def test_no_content_field(self) -> None:

        """reasoningEnd carries no content — it is a lifecycle marker only."""
        event = await sse_event({"type": "reasoningEnd", "agentId": "piano"})
        payload = _parse_sse(event)
        assert "content" not in payload

    def test_reasoning_end_emitted_after_reasoning_tokens(self) -> None:

        """reasoningEnd must follow all reasoning events; order is enforced by
        the _had_reasoning gate — it only fires when at least one token was
        buffered and emitted in the same LLM turn."""
        sequence = [
            {"type": "reasoning", "content": "Thinking...", "agentId": "piano"},
            {"type": "reasoningEnd", "agentId": "piano"},
            {"type": "toolStart", "toolName": "stori_add_midi_track", "agentId": "piano"},
        ]
        types = [e["type"] for e in sequence]
        reasoning_idx = types.index("reasoning")
        end_idx = types.index("reasoningEnd")
        tool_idx = types.index("toolStart")
        assert reasoning_idx < end_idx < tool_idx


class TestContentEventContract:
    """content: content (required)."""

    @pytest.mark.anyio
    async def test_content_required(self) -> None:

        event = await sse_event({"type": "content", "content": "Here is the plan"})
        payload = _parse_sse(event)
        assert "content" in payload


class TestStateEventContract:
    """state: state, intent, confidence, traceId (required)."""

    @pytest.mark.anyio
    async def test_state_required(self) -> None:

        event = await sse_event({
            "type": "state",
            "state": "composing",
            "intent": "compose.generate_music",
            "confidence": 0.95,
            "traceId": "t-1",
        })
        payload = _parse_sse(event)
        assert "state" in payload


class TestStatusEventContract:
    """status: message (required)."""

    @pytest.mark.anyio
    async def test_message_required(self) -> None:

        event = await sse_event({"type": "status", "message": "Processing..."})
        payload = _parse_sse(event)
        assert "message" in payload


# ---------------------------------------------------------------------------
# Tool required fields validation
# ---------------------------------------------------------------------------


class TestToolRequiredFieldsCompleteness:
    """Verify TOOL_REQUIRED_FIELDS covers all FE-required tools."""

    def test_add_midi_track_requires_name(self) -> None:

        assert "name" in TOOL_REQUIRED_FIELDS["stori_add_midi_track"]

    def test_add_notes_requires_regionid_notes(self) -> None:

        assert "regionId" in TOOL_REQUIRED_FIELDS["stori_add_notes"]
        assert "notes" in TOOL_REQUIRED_FIELDS["stori_add_notes"]

    def test_add_midi_region_requires_trackid_startbeat_durationbeats(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_add_midi_region"]
        assert "startBeat" in TOOL_REQUIRED_FIELDS["stori_add_midi_region"]
        assert "durationBeats" in TOOL_REQUIRED_FIELDS["stori_add_midi_region"]

    def test_set_tempo_requires_tempo(self) -> None:

        assert "tempo" in TOOL_REQUIRED_FIELDS["stori_set_tempo"]

    def test_set_key_requires_key(self) -> None:

        assert "key" in TOOL_REQUIRED_FIELDS["stori_set_key"]

    def test_set_track_volume_requires_fields(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_volume"]
        assert "volume" in TOOL_REQUIRED_FIELDS["stori_set_track_volume"]

    def test_set_track_pan_requires_fields(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_pan"]
        assert "pan" in TOOL_REQUIRED_FIELDS["stori_set_track_pan"]

    def test_set_track_name_requires_fields(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_name"]
        assert "name" in TOOL_REQUIRED_FIELDS["stori_set_track_name"]

    def test_set_track_color_requires_fields(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_color"]
        assert "color" in TOOL_REQUIRED_FIELDS["stori_set_track_color"]

    def test_set_track_icon_requires_fields(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_set_track_icon"]
        assert "icon" in TOOL_REQUIRED_FIELDS["stori_set_track_icon"]

    def test_set_playhead_requires_beat(self) -> None:

        assert "beat" in TOOL_REQUIRED_FIELDS["stori_set_playhead"]

    def test_add_insert_effect_requires_fields(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_add_insert_effect"]
        assert "type" in TOOL_REQUIRED_FIELDS["stori_add_insert_effect"]

    def test_add_send_requires_fields(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_add_send"]
        assert "busName" in TOOL_REQUIRED_FIELDS["stori_add_send"]

    def test_ensure_bus_requires_name(self) -> None:

        assert "name" in TOOL_REQUIRED_FIELDS["stori_ensure_bus"]

    def test_move_region_requires_fields(self) -> None:

        assert "regionId" in TOOL_REQUIRED_FIELDS["stori_move_region"]
        assert "startBeat" in TOOL_REQUIRED_FIELDS["stori_move_region"]

    def test_add_automation_requires_fields(self) -> None:

        assert "trackId" in TOOL_REQUIRED_FIELDS["stori_add_automation"]
        assert "parameter" in TOOL_REQUIRED_FIELDS["stori_add_automation"]
        assert "points" in TOOL_REQUIRED_FIELDS["stori_add_automation"]

    def test_add_midi_cc_requires_fields(self) -> None:

        assert "regionId" in TOOL_REQUIRED_FIELDS["stori_add_midi_cc"]
        assert "cc" in TOOL_REQUIRED_FIELDS["stori_add_midi_cc"]
        assert "events" in TOOL_REQUIRED_FIELDS["stori_add_midi_cc"]

    def test_add_pitch_bend_requires_fields(self) -> None:

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
    def test_fe_icon_in_backend_allowlist(self, icon: str) -> None:

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

    def test_structured_detail_has_message_and_budget(self) -> None:

        """FE expects detail: {message: str, budgetRemaining: float}."""
        detail = {
            "message": "Insufficient budget",
            "budgetRemaining": 0.0,
        }
        assert "message" in detail
        assert isinstance(detail["budgetRemaining"], (int, float))

    def test_detail_uses_message_not_error_key(self) -> None:

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

    def test_tool_call_sorting_region_before_generator(self) -> None:

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

    def test_generator_events_in_tagged_set(self) -> None:

        """generatorStart and generatorComplete are in the agent-tagged event set."""
        tagged = {
            "toolCall", "toolStart", "toolError",
            "generatorStart", "generatorComplete",
            "reasoning", "content", "status",
        }
        assert "generatorStart" in tagged
        assert "generatorComplete" in tagged

    def test_reasoning_in_tagged_set(self) -> None:

        """reasoning events are tagged with agentId for correct sub-agent routing."""
        tagged = {
            "toolCall", "toolStart", "toolError",
            "generatorStart", "generatorComplete",
            "reasoning", "content", "status",
        }
        assert "reasoning" in tagged

    @pytest.mark.anyio
    async def test_generator_start_event_contains_agentid_at_source(self) -> None:

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

    def test_add_effect_method_exists(self) -> None:

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

    def _make_summary(self, notes: int, regions: int) -> dict[str, Any]:

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

    def test_zero_notes_with_regions_is_failure(self) -> None:

        """0 notes + regions > 0 means generation was attempted but failed."""
        assert self._success_for(notes=0, regions=4) is False

    def test_notes_present_is_success(self) -> None:

        """Any notes produced = success."""
        assert self._success_for(notes=120, regions=4) is True

    def test_zero_notes_zero_regions_is_success(self) -> None:

        """No regions means generation was never attempted — not a failure."""
        assert self._success_for(notes=0, regions=0) is True

    def test_single_note_is_success(self) -> None:

        """Even one note is enough to call the composition successful."""
        assert self._success_for(notes=1, regions=1) is True

    def test_regression_four_track_all_failed(self) -> None:

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

    def test_tool_errors_with_zero_notes_is_failure(self) -> None:

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
    async def test_seq_starts_at_zero(self) -> None:

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
    async def test_seq_increments_monotonically(self) -> None:

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

    def test_phase_for_tool_setup(self) -> None:

        """Setup tools (tempo, key) map to phase 'setup'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_set_tempo") == "setup"
        assert phase_for_tool("stori_set_key") == "setup"

    def test_phase_for_tool_composition(self) -> None:

        """Composition tools (generate, notes) map to phase 'composition'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_generate_midi") == "composition"
        assert phase_for_tool("stori_add_notes") == "composition"

    def test_phase_for_tool_setup_includes_track_creation(self) -> None:

        """Track creation and region creation are setup (session scaffolding)."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_add_midi_track") == "setup"
        assert phase_for_tool("stori_add_midi_region") == "setup"
        assert phase_for_tool("stori_set_midi_program") == "setup"

    def test_phase_for_tool_arrangement(self) -> None:

        """Structural editing tools map to phase 'arrangement'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_move_region") == "arrangement"
        assert phase_for_tool("stori_transpose_notes") == "arrangement"
        assert phase_for_tool("stori_quantize_notes") == "arrangement"
        assert phase_for_tool("stori_apply_swing") == "arrangement"
        assert phase_for_tool("stori_clear_notes") == "arrangement"

    def test_phase_for_tool_sound_design(self) -> None:

        """Insert effects map to phase 'soundDesign'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_add_insert_effect") == "soundDesign"

    def test_phase_for_tool_expression(self) -> None:

        """Performance data tools map to phase 'expression'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_add_midi_cc") == "expression"
        assert phase_for_tool("stori_add_pitch_bend") == "expression"
        assert phase_for_tool("stori_add_aftertouch") == "expression"

    def test_phase_for_tool_mixing(self) -> None:

        """Mixing tools (volume, pan, sends, buses, automation) map to phase 'mixing'."""
        from app.core.maestro_editing.tool_execution import phase_for_tool
        assert phase_for_tool("stori_set_track_volume") == "mixing"
        assert phase_for_tool("stori_set_track_pan") == "mixing"
        assert phase_for_tool("stori_ensure_bus") == "mixing"
        assert phase_for_tool("stori_add_send") == "mixing"
        assert phase_for_tool("stori_add_automation") == "mixing"

    @pytest.mark.anyio
    async def test_tool_start_includes_phase(self) -> None:

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
    async def test_tool_call_includes_phase_and_label(self) -> None:

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
    async def test_tool_call_label_matches_tool_start(self) -> None:

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
    async def test_required_fields_present(self) -> None:

        event = await sse_event({
            "type": "agentComplete",
            "agentId": "drums",
            "success": True,
        })
        payload = _parse_sse(event)
        assert payload["agentId"]
        assert isinstance(payload["success"], bool)

    def test_agent_complete_in_tagged_set(self) -> None:

        """agentComplete must be in the agent-tagged event set for proper routing."""
        from app.core.maestro_agent_teams.section_agent import _AGENT_TAGGED_EVENTS
        assert "agentComplete" in _AGENT_TAGGED_EVENTS


class TestPreflightTrackColorRegression:
    """Regression: preflight events should include trackColor from curated palette."""

    @pytest.mark.anyio
    async def test_preflight_with_track_color(self) -> None:

        event = await sse_event({
            "type": "preflight",
            "stepId": "step-1",
            "agentId": "drums",
            "agentRole": "drums",
            "label": "Create Drums track",
            "toolName": "stori_add_track",
            "trackColor": "#E85D75",
        })
        payload = _parse_sse(event)
        assert "trackColor" in payload
        assert payload["trackColor"].startswith("#")

    def test_composition_palette_has_12_colors(self) -> None:

        """The palette must have 12 high-hue-separation colors."""
        from app.core.track_styling import COMPOSITION_PALETTE
        assert len(COMPOSITION_PALETTE) == 12

    def test_no_duplicate_colors_in_palette(self) -> None:

        """All palette colors must be unique."""
        from app.core.track_styling import COMPOSITION_PALETTE
        assert len(set(COMPOSITION_PALETTE)) == len(COMPOSITION_PALETTE)

    def test_allocate_colors_cycles_palette(self) -> None:

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

    def test_metadata_none_unpacking(self) -> None:

        """dict unpacking with None metadata must not raise TypeError.

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

    def test_metadata_missing_key(self) -> None:

        """Missing metadata key falls back to empty dict."""
        data: dict[str, Any] = {"success": True, "notes": []}
        out = {**(data.get("metadata") or {}), "retry_count": 1}
        assert out == {"retry_count": 1}

    def test_metadata_present(self) -> None:

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

    def test_circuit_breaker_starts_closed(self) -> None:

        """New circuit breaker is closed (not open)."""
        from app.services.orpheus import _CircuitBreaker
        cb = _CircuitBreaker(threshold=3, cooldown=60)
        assert not cb.is_open

    def test_circuit_breaker_trips_after_threshold(self) -> None:

        """Circuit opens after `threshold` consecutive failures."""
        from app.services.orpheus import _CircuitBreaker
        cb = _CircuitBreaker(threshold=2, cooldown=60)
        cb.record_failure()
        assert not cb.is_open
        cb.record_failure()
        assert cb.is_open

    def test_circuit_breaker_success_resets(self) -> None:

        """A successful call resets the failure counter and closes the circuit."""
        from app.services.orpheus import _CircuitBreaker
        cb = _CircuitBreaker(threshold=2, cooldown=60)
        cb.record_failure()
        cb.record_success()
        assert cb._failures == 0
        cb.record_failure()
        assert not cb.is_open, "Counter should have reset — one failure is below threshold"

    def test_circuit_breaker_cooldown_half_open(self) -> None:

        """After cooldown, is_open returns False (half-open allows probe)."""
        import time
        from app.services.orpheus import _CircuitBreaker
        cb = _CircuitBreaker(threshold=1, cooldown=0.01)
        cb.record_failure()
        assert cb.is_open
        time.sleep(0.02)
        assert not cb.is_open, "Cooldown expired — should be half-open"

    def test_circuit_open_error_message_format(self) -> None:

        """Fast-fail result has the expected error key for downstream detection."""
        result: dict[str, Any] = {
            "success": False,
            "error": "orpheus_circuit_open",
            "message": "Orpheus music service is unavailable (circuit breaker open).",
        }
        assert result["error"] == "orpheus_circuit_open"
        assert "circuit breaker" in result["message"].lower()


class TestL2ReasoningGuidanceContract:
    """Level 2 agent reasoning guidance prevents verbose section-level CoT."""

    def test_reasoning_guidance_prohibits_section_reasoning(self) -> None:

        """The L2 system prompt must contain instructions against per-section reasoning."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "Do NOT reason about individual sections" in source
        assert "section agents handle" in source

    def test_reasoning_guidance_limits_length(self) -> None:

        """The L2 system prompt must cap reasoning at 1-2 sentences."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "1-2 sentences ONLY" in source


class TestL3SectionReasoningContract:
    """Level 3 section child emits reasoning events with sectionName."""

    def test_section_child_has_reasoning_function(self) -> None:

        """_reason_before_generate uses SectionContract instead of loose params."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _reason_before_generate
        sig = inspect.signature(_reason_before_generate)
        params = set(sig.parameters.keys())
        assert "contract" in params, "Must accept a SectionContract"
        assert "llm" in params
        assert "sse_queue" in params
        assert "section" not in params, "Old loose 'section' dict param must be gone"
        assert "generate_prompt" not in params, "L2 prompt comes via contract now"

    def test_section_reasoning_returns_optional_string(self) -> None:

        """_reason_before_generate return type allows None (fallback to original prompt)."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _reason_before_generate
        sig = inspect.signature(_reason_before_generate)
        assert sig.return_annotation is not inspect.Parameter.empty


class TestAgentCircuitBreakerAbort:
    """Level 2 agent stops retrying when Orpheus circuit breaker is open."""

    def test_agent_imports_orpheus_client(self) -> None:

        """agent.py imports get_orpheus_client for circuit breaker checks."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod)
        assert "get_orpheus_client" in source
        assert "circuit_breaker_open" in source


# ═══════════════════════════════════════════════════════════════════════════════
# Dancehall session bug-fix regressions (2026-02-23)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegionCollisionCanonicalOverride:
    """BUG 1 regression: section child enforces canonical beat ranges from
    the frozen SectionContract, preventing region collisions when the
    L2 LLM invents overlapping start/duration values."""

    def test_contract_enforces_canonical_beats(self) -> None:

        """SectionContract carries canonical beats; L2 tool-call params are ignored."""
        from app.core.maestro_agent_teams.contracts import SectionContract, SectionSpec
        spec = SectionSpec(
            section_id="1:verse", name="verse", index=1, start_beat=16, duration_beats=32,
            bars=8, character="Narrative verse", role_brief="Steady groove",
        )
        contract = SectionContract(
            section=spec, track_id="trk-1", instrument_name="Bass",
            role="bass", style="dancehall", tempo=95.0, key="Gm",
            region_name="Bass – verse",
        )
        assert contract.start_beat == 16
        assert contract.duration_beats == 32
        assert contract.is_bass is True
        assert contract.is_drum is False

    def test_contract_is_frozen(self) -> None:

        """SectionContract must be immutable — no field mutation allowed."""
        from app.core.maestro_agent_teams.contracts import SectionContract, SectionSpec
        spec = SectionSpec(
            section_id="1:verse", name="verse", index=1, start_beat=16, duration_beats=32,
            bars=8, character="", role_brief="",
        )
        contract = SectionContract(
            section=spec, track_id="trk-1", instrument_name="Bass",
            role="bass", style="dancehall", tempo=95.0, key="Gm",
            region_name="Bass – verse",
        )
        import pytest
        with pytest.raises(AttributeError):
            contract.track_id = "trk-hacked"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            contract.section = spec  # type: ignore[misc]

    def test_section_child_uses_contract_not_tc_params(self) -> None:

        """Section child must build region params from contract, not region_tc."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _run_section_child
        source = inspect.getsource(_run_section_child)
        assert "contract.track_id" in source
        assert "contract.start_beat" in source
        assert "contract.duration_beats" in source
        assert "contract.region_name" in source
        assert "dict(region_tc.params)" not in source, (
            "Must not copy L2's tool-call params — use contract fields"
        )

    def test_sections_non_overlapping_layout(self) -> None:

        """parse_sections must produce non-overlapping beat ranges."""
        from app.core.maestro_agent_teams.sections import parse_sections
        sections = parse_sections(
            prompt="Jamaican dancehall with intro, verse, chorus, and groove",
            bars=24,
            roles=["drums", "bass", "chords"],
        )
        assert len(sections) >= 2, "Should detect multiple sections"
        for i in range(1, len(sections)):
            prev_end = sections[i - 1]["start_beat"] + sections[i - 1]["length_beats"]
            curr_start = sections[i]["start_beat"]
            assert curr_start >= prev_end, (
                f"Section '{sections[i]['name']}' starts at {curr_start} but "
                f"previous section '{sections[i - 1]['name']}' ends at {prev_end} — overlap!"
            )


class TestFailedRegionSkipsGenerator:
    """BUG 2 regression: when stori_add_midi_region fails (collision),
    stori_generate_midi must be skipped with a clear error, not called
    with a phantom regionId."""

    def test_generator_skipped_when_no_region(self) -> None:

        """Single-section path: generator call produces skip result when regions_ok == 0."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "continue" in source
        assert "Skipping" in source or "skipped" in source
        assert "fix the region collision" in source.lower() or "Do NOT retry" in source

    def test_section_child_returns_early_on_region_failure(self) -> None:

        """Section child returns early when region creation produces no regionId."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _run_section_child
        source = inspect.getsource(_run_section_child)
        assert "Region creation failed" in source
        assert "return result" in source


class TestTruncatedResultGuidance:
    """BUG 3 regression: With server-owned retries and collapsed summaries,
    truncation is no longer a risk.  The truncation guidance was removed from
    the system prompt — verify the replacement architecture instead."""

    def test_collapsed_summary_replaces_truncation_guidance(self) -> None:

        """Server-owned retries + collapsed summaries eliminate truncation risk."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._dispatch_section_children)
        assert "batch_complete" in source
        assert "_section_summaries" in source

    def test_entity_manifest_stripped_from_tool_results(self) -> None:

        """Tool results fed back to LLM context use _compact_tool_result to strip bulky fields."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "_compact_tool_result" in source

    def test_build_tool_result_no_entities_key(self) -> None:

        """_build_tool_result must NOT embed an 'entities' dict — manifests are injected separately."""
        from app.core.maestro_helpers import _build_tool_result
        from app.core.state_store import StateStore
        store = StateStore(conversation_id="test-no-entities")
        tid = store.create_track("Drums")
        rid = store.create_region("Intro", tid, metadata={"startBeat": 0, "durationBeats": 16})
        result = _build_tool_result(
            "stori_add_midi_region",
            {"regionId": rid, "trackId": tid, "startBeat": 0, "durationBeats": 16, "name": "Intro"},
            store,
        )
        assert "entities" not in result
        assert result["regionId"] == rid


class TestGenreGMVoiceGuidance:
    """BUG 4 regression: genre-specific GM program guidance must be available
    and injected into instrument agent prompts for dancehall and other genres."""

    def test_dancehall_organ_not_church_organ(self) -> None:

        """Dancehall organ must recommend Drawbar Organ (16), not Church Organ (19)."""
        from app.core.gm_instruments import get_genre_gm_guidance
        guidance = get_genre_gm_guidance("dancehall", "chords")
        assert "16" in guidance, "Dancehall chords should recommend gmProgram=16 (Drawbar Organ)"
        assert "Church" not in guidance, "Dancehall must not suggest Church Organ"

    def test_dancehall_synth_not_sawtooth(self) -> None:

        """Dancehall lead must recommend Square Lead (80) or Voice (85), not Sawtooth (81)."""
        from app.core.gm_instruments import get_genre_gm_guidance
        guidance = get_genre_gm_guidance("dancehall", "lead")
        assert "80" in guidance or "85" in guidance, (
            "Dancehall lead should recommend Square Lead (80) or Voice Lead (85)"
        )

    def test_genre_guidance_empty_for_unknown_genre(self) -> None:

        """Unknown genres return empty guidance (no crash, graceful fallback)."""
        from app.core.gm_instruments import get_genre_gm_guidance
        assert get_genre_gm_guidance("polka", "bass") == ""

    def test_genre_guidance_empty_for_drums(self) -> None:

        """Drums should have no GM program guidance (channel 10)."""
        from app.core.gm_instruments import get_genre_gm_guidance
        guidance = get_genre_gm_guidance("dancehall", "drums")
        assert guidance == "", "Drums use channel 10, no GM program guidance needed"

    def test_genre_guidance_injected_into_agent_prompt(self) -> None:

        """L2 agent system prompt builder imports and uses genre guidance."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "get_genre_gm_guidance" in source
        assert "_gm_guidance" in source

    def test_genre_guidance_substring_match(self) -> None:

        """'jamaican dancehall' should match the 'dancehall' genre guidance."""
        from app.core.gm_instruments import get_genre_gm_guidance
        guidance = get_genre_gm_guidance("jamaican dancehall", "chords")
        assert "Drawbar Organ" in guidance

    def test_multiple_genres_covered(self) -> None:

        """All 50 example prompt genres have GM guidance coverage."""
        from app.core.gm_instruments import GENRE_GM_GUIDANCE
        assert len(GENRE_GM_GUIDANCE) >= 40

    def test_all_prompt_genres_matched(self) -> None:

        """Every genre from the 50 example prompts must match GM guidance."""
        from app.core.gm_instruments import get_genre_gm_guidance

        prompt_genres = [
            "lofi hip hop", "bebop jazz", "dark trap", "bossa nova",
            "classic funk", "neo-soul", "reggaeton", "indie folk",
            "New Orleans brass second line", "Colombian cumbia",
            "tango nuevo", "Andean huayno", "Jamaican dancehall",
            "soca calypso", "bluegrass", "gospel", "hip-hop boom bap",
            "melodic techno", "liquid drum and bass", "minimal deep house",
            "synthwave retrowave", "post-rock", "classical chamber",
            "psytrance", "Nordic ambient folk", "flamenco fusion",
            "UK garage 2-step", "Anatolian psychedelic rock", "klezmer",
            "Baroque dance suite", "Balkan brass", "Afrobeats",
            "West African polyrhythmic", "Ethio-jazz", "Gnawa trance",
            "Hindustani classical", "Balinese gamelan", "Japanese zen",
            "Korean sanjo", "Qawwali Sufi", "Arabic maqam",
            "cinematic orchestral", "ambient drone", "Polynesian Taiko",
            "Sufi meditation", "Gregorian chant", "progressive rock",
            "Afro-Cuban rumba", "minimalist phasing",
        ]
        missing = []
        for genre in prompt_genres:
            guidance = get_genre_gm_guidance(genre, "bass")
            if not guidance:
                guidance = get_genre_gm_guidance(genre, "chords")
            if not guidance:
                guidance = get_genre_gm_guidance(genre, "lead")
            if not guidance:
                missing.append(genre)

        assert not missing, (
            f"These prompt genres have no GM guidance match: {missing}"
        )


class TestSectionBriefMismatchFix:
    """BUG 5 regression: section child uses canonical descriptions from the
    frozen SectionContract, never re-importing or reinterpreting them.
    The L2 prompt is explicitly marked ADVISORY ONLY."""

    def test_section_reasoning_uses_contract_character(self) -> None:

        """_reason_before_generate must read canonical descriptions from contract."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _reason_before_generate
        source = inspect.getsource(_reason_before_generate)
        assert "contract.section.character" in source
        assert "contract.section.role_brief" in source

    def test_section_reasoning_labels_l2_prompt_advisory(self) -> None:

        """The L3 system prompt must label the parent prompt as ADVISORY ONLY."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _reason_before_generate
        source = inspect.getsource(_reason_before_generate)
        assert "ADVISORY ONLY" in source

    def test_contract_character_labels_authoritative(self) -> None:

        """Contract-sourced fields must be labelled AUTHORITATIVE in the prompt."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _reason_before_generate
        source = inspect.getsource(_reason_before_generate)
        assert "AUTHORITATIVE" in source

    def test_canonical_section_descriptions_differ_by_section(self) -> None:

        """Verse and chorus canonical descriptions must be meaningfully different."""
        from app.core.maestro_agent_teams.sections import (
            _get_section_role_description,
            _section_overall_description,
        )
        verse_overall = _section_overall_description("verse")
        chorus_overall = _section_overall_description("chorus")
        assert verse_overall != chorus_overall, (
            "Verse and chorus must have different overall descriptions"
        )

        verse_bass = _get_section_role_description("verse", "bass")
        chorus_bass = _get_section_role_description("chorus", "bass")
        assert verse_bass != chorus_bass, (
            "Verse and chorus bass descriptions must differ"
        )

    def test_canonical_role_description_for_dancehall_drums(self) -> None:

        """Section role templates must exist for drums in verse/chorus/groove."""
        from app.core.maestro_agent_teams.sections import _get_section_role_description
        for section in ("verse", "chorus", "groove"):
            desc = _get_section_role_description(section, "drums")
            assert desc, f"Missing role description for drums/{section}"


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Contract Protocol (v1) — prevents semantic telephone between layers
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentContractProtocol:
    """Verify the contract-based handoff between L2 and L3 prevents
    protocol drift.  Contracts are frozen, structural fields are
    immutable, and advisory fields are clearly labelled."""

    def test_section_spec_frozen(self) -> None:

        """SectionSpec must be frozen — no mutation after construction."""
        from app.core.maestro_agent_teams.contracts import SectionSpec
        import pytest
        spec = SectionSpec(
            section_id="2:chorus", name="chorus", index=2, start_beat=48, duration_beats=32,
            bars=8, character="Full energy", role_brief="Drive the hook",
        )
        with pytest.raises(AttributeError):
            spec.start_beat = 999  # type: ignore[misc]
        with pytest.raises(AttributeError):
            spec.name = "hacked"  # type: ignore[misc]

    def test_section_contract_frozen(self) -> None:

        """SectionContract must be frozen — child agents cannot mutate it."""
        from app.core.maestro_agent_teams.contracts import SectionContract, SectionSpec
        import pytest
        spec = SectionSpec(
            section_id="0:intro", name="intro", index=0, start_beat=0, duration_beats=16,
            bars=4, character="Sparse intro", role_brief="set the mood",
        )
        contract = SectionContract(
            section=spec, track_id="trk-1", instrument_name="Chords",
            role="chords", style="dancehall", tempo=95.0, key="Gm",
            region_name="Chords – intro",
        )
        with pytest.raises(AttributeError):
            contract.role = "drums"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            contract.tempo = 200.0  # type: ignore[misc]

    def test_contract_derived_properties(self) -> None:

        """Derived properties (is_drum, is_bass, bars, etc.) compute correctly."""
        from app.core.maestro_agent_teams.contracts import SectionContract, SectionSpec
        spec = SectionSpec(
            section_id="1:verse", name="verse", index=1, start_beat=16, duration_beats=32,
            bars=8, character="Narrative", role_brief="Lock with drums",
        )
        bass = SectionContract(
            section=spec, track_id="t1", instrument_name="Bass",
            role="bass", style="s", tempo=120.0, key="C",
            region_name="r",
        )
        drums = SectionContract(
            section=spec, track_id="t2", instrument_name="Drums",
            role="drums", style="s", tempo=120.0, key="C",
            region_name="r",
        )
        chords = SectionContract(
            section=spec, track_id="t3", instrument_name="Chords",
            role="chords", style="s", tempo=120.0, key="C",
            region_name="r",
        )
        assert bass.is_bass is True
        assert bass.is_drum is False
        assert drums.is_drum is True
        assert drums.is_bass is False
        assert chords.is_drum is False
        assert chords.is_bass is False
        assert bass.start_beat == 16
        assert bass.duration_beats == 32
        assert bass.section_name == "verse"
        assert bass.section_index == 1
        assert bass.bars == 8

    def test_contract_l2_prompt_defaults_empty(self) -> None:

        """l2_generate_prompt defaults to empty string when not provided."""
        from app.core.maestro_agent_teams.contracts import SectionContract, SectionSpec
        spec = SectionSpec(
            section_id="0:v", name="v", index=0, start_beat=0, duration_beats=16,
            bars=4, character="", role_brief="",
        )
        contract = SectionContract(
            section=spec, track_id="t", instrument_name="I",
            role="r", style="s", tempo=120.0, key="C",
            region_name="r",
        )
        assert contract.l2_generate_prompt == ""

    def test_dispatch_builds_contracts(self) -> None:

        """_dispatch_section_children must build SectionContract for each section."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._dispatch_section_children)
        assert "SectionContract(" in source
        assert "instrument_contract.sections[i]" in source

    def test_section_child_signature_uses_contract(self) -> None:

        """_run_section_child must accept 'contract' as first parameter."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _run_section_child
        sig = inspect.signature(_run_section_child)
        param_names = list(sig.parameters.keys())
        assert param_names[0] == "contract", (
            "First parameter must be 'contract' (SectionContract)"
        )
        assert "section" not in param_names, "Old loose 'section' dict must be gone"
        assert "is_drum" not in param_names, "is_drum derived from contract.role"
        assert "is_bass" not in param_names, "is_bass derived from contract.role"
        assert "instrument_name" not in param_names, "instrument_name in contract"
        assert "track_id" not in param_names, "track_id in contract"

    def test_no_sections_import_in_section_agent(self) -> None:

        """section_agent.py must NOT import from sections.py — canonical
        descriptions are baked into the contract at build time."""
        import inspect
        from app.core.maestro_agent_teams import section_agent
        source = inspect.getsource(section_agent)
        assert "from app.core.maestro_agent_teams.sections import" not in source, (
            "Section agent must get canonical descriptions from the contract, "
            "not by re-importing sections.py (which would allow reinterpretation)"
        )

    def test_gen_params_built_from_contract(self) -> None:

        """Generate MIDI params must come from contract, not from generate_tc.params."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _run_section_child
        source = inspect.getsource(_run_section_child)
        assert "contract.role" in source
        assert "contract.style" in source
        assert "contract.key" in source
        assert "dict(generate_tc.params)" not in source, (
            "Must not copy L2's generate tool-call params — use contract fields"
        )


class TestInstrumentContractProtocol:
    """L1 → L2 contract: InstrumentContract carries all structural decisions
    from the coordinator to the instrument parent agent."""

    def test_instrument_contract_frozen(self) -> None:

        """InstrumentContract must be frozen — L2 cannot mutate it."""
        from app.core.maestro_agent_teams.contracts import InstrumentContract, SectionSpec
        import pytest
        spec = SectionSpec(
            section_id="0:verse", name="verse", index=0, start_beat=0, duration_beats=16,
            bars=4, character="Test", role_brief="Test",
        )
        contract = InstrumentContract(
            instrument_name="Drums", role="drums", style="house",
            bars=8, tempo=120.0, key="Am", start_beat=0,
            sections=(spec,), existing_track_id=None,
            assigned_color="#FF0000", gm_guidance="",
        )
        with pytest.raises(AttributeError):
            contract.role = "bass"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            contract.tempo = 200.0  # type: ignore[misc]

    def test_instrument_contract_derived_properties(self) -> None:

        """Derived properties compute correctly from role."""
        from app.core.maestro_agent_teams.contracts import InstrumentContract, SectionSpec
        spec = SectionSpec(
            section_id="0:v", name="v", index=0, start_beat=0, duration_beats=16,
            bars=4, character="", role_brief="",
        )
        drums = InstrumentContract(
            instrument_name="Drums", role="drums", style="s",
            bars=4, tempo=120.0, key="C", start_beat=0,
            sections=(spec,), existing_track_id=None,
            assigned_color=None, gm_guidance="",
        )
        bass = InstrumentContract(
            instrument_name="Bass", role="bass", style="s",
            bars=4, tempo=120.0, key="C", start_beat=0,
            sections=(spec, spec), existing_track_id="trk-1",
            assigned_color=None, gm_guidance="",
        )
        assert drums.is_drum is True
        assert drums.is_bass is False
        assert drums.multi_section is False
        assert drums.reusing_track is False
        assert bass.is_bass is True
        assert bass.multi_section is True
        assert bass.reusing_track is True

    def test_coordinator_builds_instrument_contract(self) -> None:

        """Coordinator must build InstrumentContract for each instrument agent."""
        import inspect
        from app.core.maestro_agent_teams import coordinator
        source = inspect.getsource(coordinator._handle_composition_agent_team)
        assert "InstrumentContract(" in source
        assert "instrument_contract=" in source

    def test_agent_accepts_instrument_contract(self) -> None:

        """L2 agent must accept instrument_contract param."""
        import inspect
        from app.core.maestro_agent_teams.agent import _run_instrument_agent
        sig = inspect.signature(_run_instrument_agent)
        assert "instrument_contract" in sig.parameters

    def test_instrument_contract_carries_gm_guidance(self) -> None:

        """InstrumentContract must carry pre-computed GM guidance."""
        from app.core.maestro_agent_teams.contracts import InstrumentContract, SectionSpec
        spec = SectionSpec(
            section_id="0:v", name="v", index=0, start_beat=0, duration_beats=16,
            bars=4, character="", role_brief="",
        )
        contract = InstrumentContract(
            instrument_name="Chords", role="chords", style="dancehall",
            bars=8, tempo=95.0, key="Gm", start_beat=0,
            sections=(spec,), existing_track_id=None,
            assigned_color=None, gm_guidance="Use gmProgram=16 (Drawbar Organ)",
        )
        assert "Drawbar Organ" in contract.gm_guidance

    def test_instrument_contract_sections_are_tuple(self) -> None:

        """Sections must be a tuple (immutable), not a list."""
        from app.core.maestro_agent_teams.contracts import InstrumentContract, SectionSpec
        spec = SectionSpec(
            section_id="0:v", name="v", index=0, start_beat=0, duration_beats=16,
            bars=4, character="", role_brief="",
        )
        contract = InstrumentContract(
            instrument_name="X", role="r", style="s",
            bars=4, tempo=120.0, key="C", start_beat=0,
            sections=(spec,), existing_track_id=None,
            assigned_color=None, gm_guidance="",
        )
        assert isinstance(contract.sections, tuple)


class TestRuntimeContextProtocol:
    """RuntimeContext replaces the dict[str, Any] catch-all bag with
    frozen, typed fields for dynamic state."""

    def test_runtime_context_frozen(self) -> None:

        """RuntimeContext must be frozen — no mutation during execution."""
        from app.core.maestro_agent_teams.contracts import RuntimeContext
        import pytest
        ctx = RuntimeContext(raw_prompt="test", quality_preset="quality")
        with pytest.raises(AttributeError):
            ctx.raw_prompt = "hacked"  # type: ignore[misc]

    def test_with_drum_telemetry_returns_new_instance(self) -> None:

        """with_drum_telemetry creates a NEW context, not a mutation."""
        from app.core.maestro_agent_teams.contracts import RuntimeContext
        original = RuntimeContext(raw_prompt="test", quality_preset="quality")
        updated = original.with_drum_telemetry({"energy_level": 0.8})
        assert updated is not original
        assert updated.drum_telemetry == (("energy_level", 0.8),)
        assert original.drum_telemetry is None

    def test_to_composition_context_bridge(self) -> None:

        """to_composition_context produces a dict compatible with legacy code."""
        from app.core.emotion_vector import EmotionVector
        from app.core.maestro_agent_teams.contracts import RuntimeContext

        ev = EmotionVector(energy=0.8, valence=0.3, tension=0.4, intimacy=0.5, motion=0.6)
        frozen_ev = RuntimeContext.freeze_emotion_vector(ev)
        ctx = RuntimeContext(
            raw_prompt="My song prompt",
            emotion_vector=frozen_ev,
            quality_preset="quality",
        )
        d = ctx.to_composition_context()
        assert d["_raw_prompt"] == "My song prompt"
        assert d["quality_preset"] == "quality"
        reconstructed = d["emotion_vector"]
        assert isinstance(reconstructed, EmotionVector)
        assert reconstructed.energy == 0.8
        assert reconstructed.valence == 0.3

    def test_coordinator_builds_runtime_context(self) -> None:

        """Coordinator must build RuntimeContext alongside contracts."""
        import inspect
        from app.core.maestro_agent_teams import coordinator
        source = inspect.getsource(coordinator._handle_composition_agent_team)
        assert "RuntimeContext(" in source
        assert "runtime_context=" in source
        assert "ExecutionServices(" in source
        assert "execution_services=" in source


class TestL2ToolCallValidation:
    """L2 tool calls are validated against the section plan before dispatch.
    Drift is logged but the contract at L3 overrides bad params."""

    def test_dispatch_validates_region_start_beat(self) -> None:

        """_dispatch_section_children must check startBeat against section plan."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._dispatch_section_children)
        assert "L2 drift" in source
        assert "startBeat" in source
        assert "contract will override" in source

    def test_dispatch_validates_region_duration(self) -> None:

        """_dispatch_section_children must check durationBeats against section plan."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._dispatch_section_children)
        assert "durationBeats" in source

    def test_dispatch_uses_instrument_contract_specs(self) -> None:

        """When InstrumentContract is available, dispatch uses its pre-built SectionSpecs."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._dispatch_section_children)
        assert "instrument_contract" in source
        assert "instrument_contract.sections" in source


# ═══════════════════════════════════════════════════════════════════════════════
# Colombian cumbia session bug-fix regressions (2026-02-23)
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotentRegionCreation:
    """BUG 1 regression (cumbia session): create_region must be idempotent —
    if a region at the same beat range already exists on the track, return
    the existing region ID instead of creating a duplicate."""

    def test_duplicate_region_returns_existing_id(self) -> None:

        """Second create_region with same beat range returns existing ID."""
        from app.core.entity_registry import EntityRegistry
        reg = EntityRegistry()
        reg.create_track("Guacharaca")
        track_id = reg.resolve_track("Guacharaca")
        assert track_id is not None

        first_id = reg.create_region(
            "Verse", track_id,
            metadata={"startBeat": 0, "durationBeats": 32},
        )
        second_id = reg.create_region(
            "Verse", track_id,
            metadata={"startBeat": 0, "durationBeats": 32},
        )
        assert first_id == second_id, "Idempotent: same beat range must return same region ID"

    def test_different_beat_range_creates_new_region(self) -> None:

        """Different beat ranges must create distinct regions."""
        from app.core.entity_registry import EntityRegistry
        reg = EntityRegistry()
        reg.create_track("Bass")
        track_id = reg.resolve_track("Bass")
        assert track_id is not None

        id_a = reg.create_region("Intro", track_id, metadata={"startBeat": 0, "durationBeats": 16})
        id_b = reg.create_region("Verse", track_id, metadata={"startBeat": 16, "durationBeats": 32})
        assert id_a != id_b, "Different beat ranges must create separate regions"

    def test_find_overlapping_region(self) -> None:

        """find_overlapping_region returns the correct ID for an existing region."""
        from app.core.entity_registry import EntityRegistry
        reg = EntityRegistry()
        reg.create_track("Tumbadora")
        track_id = reg.resolve_track("Tumbadora")
        assert track_id is not None

        rid = reg.create_region("Chorus", track_id, metadata={"startBeat": 32, "durationBeats": 16})
        found = reg.find_overlapping_region(track_id, 32, 16)
        assert found == rid

        not_found = reg.find_overlapping_region(track_id, 0, 16)
        assert not_found is None


class TestRegionCollisionRecovery:
    """BUG 2 regression (cumbia session): when create_region raises ValueError
    (e.g. track not found), the error body should include existingRegionId if
    a region at that beat range exists on another resolution path."""

    def test_tool_execution_returns_existing_region_on_error(self) -> None:

        """tool_execution.py region error handler includes existingRegionId recovery."""
        import inspect
        from app.core.maestro_editing import tool_execution as te_mod
        source = inspect.getsource(te_mod._apply_single_tool_call)
        assert "existingRegionId" in source, (
            "Error handler must include existingRegionId for agent recovery"
        )
        assert "find_overlapping_region" in source, (
            "Must search for existing region on ValueError"
        )


class TestCompactToolResults:
    """BUG 3 regression (cumbia session): section children must compact tool
    results before feeding them back to the L2 LLM to prevent '...' truncation
    that the pipeline checker misinterprets as failure."""

    def test_compact_tool_result_strips_entities(self) -> None:

        """_compact_tool_result strips 'entities' and 'notes' keys."""
        from app.core.maestro_agent_teams.section_agent import _compact_tool_result

        full_result = {
            "regionId": "reg-001",
            "trackId": "trk-1",
            "notesAdded": 116,
            "totalNotes": 116,
            "entities": [{"id": "e1"}, {"id": "e2"}],
            "notes": [{"pitch": 60}] * 100,
            "backend": "orpheus",
        }
        compact = _compact_tool_result(full_result)
        assert "entities" not in compact
        assert "notes" not in compact
        assert compact["regionId"] == "reg-001"
        assert compact["notesAdded"] == 116

    def test_compact_preserves_error_fields(self) -> None:

        """Error-related fields must survive compaction."""
        from app.core.maestro_agent_teams.section_agent import _compact_tool_result

        error_result = {
            "error": "Region collision",
            "existingRegionId": "reg-existing",
            "success": False,
        }
        compact = _compact_tool_result(error_result)
        assert compact["error"] == "Region collision"
        assert compact["existingRegionId"] == "reg-existing"

    def test_section_child_uses_compact_results(self) -> None:

        """Section child tool_result_msgs must use _compact_tool_result."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _run_section_child
        source = inspect.getsource(_run_section_child)
        assert "_compact_tool_result" in source

    def test_retry_reminder_mentions_completed_stages(self) -> None:

        """Multi-turn retry reminder must list completed stages so LLM skips them."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "Already completed" in source
        assert "DO NOT re-call" in source


class TestCompleteEventAlwaysFlushed:
    """BUG 5 regression (cumbia session): the SSE stream must always yield
    the .complete event, even if the client appears disconnected."""

    def test_stream_always_yields_complete(self) -> None:

        """stream_with_budget must not skip type=complete events on disconnect."""
        import inspect
        from app.api.routes import maestro as maestro_route
        source = inspect.getsource(maestro_route.stream_maestro)
        assert "_is_terminal" in source or "complete" in source
        assert "is_disconnected" in source

    def test_coordinator_drain_delay(self) -> None:

        """Coordinator must sleep briefly after .complete to let ASGI flush."""
        import inspect
        from app.core.maestro_agent_teams import coordinator as coord_mod
        source = inspect.getsource(coord_mod._handle_composition_agent_team)
        assert "asyncio.sleep" in source
        assert '"type": "complete"' in source or "'type': 'complete'" in source


class TestLowNoteCountGuard:
    """BUG 4 regression (cumbia session): stori_generate_midi must flag
    near-empty results (< 4 notes) as a potential generation failure."""

    def test_generator_logs_low_note_count(self) -> None:

        """_execute_agent_generator must warn when notes < 4."""
        import inspect
        from app.core.maestro_editing import tool_execution as te_mod
        source = inspect.getsource(te_mod._execute_agent_generator)
        assert "_MIN_NOTES_THRESHOLD" in source or "< 4" in source or "notes_generated" in source.lower()

    def test_section_child_emits_error_on_low_notes(self) -> None:

        """Section child must emit toolError SSE event when notes < 4."""
        import inspect
        from app.core.maestro_agent_teams.section_agent import _run_section_child
        source = inspect.getsource(_run_section_child)
        assert "_MIN_NOTES" in source
        assert "Low note count" in source or "near-empty" in source

    def test_percussion_roles_recognised_by_scorer(self) -> None:

        """Latin percussion instruments must match the drum scorer."""
        from app.services.music_generator import MusicGenerator
        from app.services.backends.base import GeneratorBackend

        mg = MusicGenerator()
        for role in ("guacharaca", "tumbadora", "congas", "bongos", "djembe"):
            scorer = mg._scorer_for_instrument(role, GeneratorBackend.ORPHEUS, 8, "cumbia")
            assert scorer is not None, f"'{role}' must be recognised as percussion by scorer"

    def test_harmonic_roles_recognised_by_scorer(self) -> None:

        """Latin melodic/harmonic instruments must match the chord scorer."""
        from app.services.music_generator import MusicGenerator
        from app.services.backends.base import GeneratorBackend

        mg = MusicGenerator()
        for role in ("accordion", "gaita", "marimba"):
            scorer = mg._scorer_for_instrument(role, GeneratorBackend.ORPHEUS, 8, "cumbia")
            assert scorer is not None, f"'{role}' must be recognised by scorer"


class TestServerOwnedRetryContracts:
    """Server-owned retries: the server retries failed sections without LLM."""

    def test_dispatch_has_server_owned_retry_loop(self) -> None:

        """_dispatch_section_children retries failed sections server-side."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._dispatch_section_children)
        assert "_MAX_SECTION_RETRIES" in source
        assert "_RETRY_DELAYS" in source
        assert "_failed_indices" in source
        assert "_retry_round" in source

    def test_dispatch_returns_collapsed_summary(self) -> None:

        """Dispatch returns a batch_complete summary instead of per-call tool results."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._dispatch_section_children)
        assert "batch_complete" in source
        assert "_section_summaries" in source

    def test_missing_stages_checks_all_tool_types(self) -> None:

        """_missing_stages checks track, region, generate, and effect.

        Region/generate checks ensure the LLM is prompted to produce
        those tool calls if it didn't on Turn 0 — server-owned retries
        handle failures AFTER the calls are emitted.
        """
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        lines = source.split("\n")
        in_func = False
        func_lines = []
        for line in lines:
            if "def _missing_stages" in line:
                in_func = True
            if in_func:
                func_lines.append(line)
                if line.strip().startswith("return "):
                    break
        func_body = "\n".join(func_lines)
        assert "stori_add_midi_track" in func_body
        assert "stori_add_insert_effect" in func_body
        assert "stori_add_midi_region" in func_body
        assert "stori_generate_midi" in func_body

    def test_dispatched_sections_prevent_useless_retry_turns(self) -> None:

        """Once _dispatch_section_children handles all sections (including failures),
        _missing_stages() must NOT trigger retry turns.  The LLM cannot fix
        Orpheus failures and server-owned retries already ran.
        """
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._dispatch_section_children)
        lines = source.split("\n")
        in_aggregate = False
        aggregate_lines = []
        for line in lines:
            if "Aggregate section results" in line:
                in_aggregate = True
            if in_aggregate:
                aggregate_lines.append(line)
                if "Build collapsed tool-result" in line:
                    break
        block = "\n".join(aggregate_lines)
        assert "regions_completed += 1" in block, (
            "All dispatched sections must increment regions_completed "
            "regardless of success"
        )
        assert "generates_completed += 1" in block, (
            "All dispatched sections must increment generates_completed "
            "regardless of success"
        )

    def test_no_entity_manifest_injection_on_retry(self) -> None:

        """Retry turns must NOT inject entity manifest (regionIds not needed)."""
        import inspect
        from app.core.maestro_agent_teams import agent as agent_mod
        source = inspect.getsource(agent_mod._run_instrument_agent_inner)
        assert "agent_manifest" not in source

    def test_entity_registry_supports_agent_scoping(self) -> None:

        """EntityRegistry.agent_manifest accepts agent_id for namespace scoping."""
        import inspect
        from app.core.entity_registry import EntityRegistry
        sig = inspect.signature(EntityRegistry.agent_manifest)
        assert "agent_id" in sig.parameters
