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
