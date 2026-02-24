"""Canonical tool name enum — replace scattered string comparisons."""

from __future__ import annotations

from enum import Enum


class ToolName(str, Enum):
    """MCP tool names used in executor dispatch and validation."""

    ADD_MIDI_TRACK = "stori_add_midi_track"
    ADD_MIDI_REGION = "stori_add_midi_region"
    ADD_NOTES = "stori_add_notes"
    CLEAR_NOTES = "stori_clear_notes"
    ADD_MIDI_CC = "stori_add_midi_cc"
    ADD_PITCH_BEND = "stori_add_pitch_bend"
    ADD_AFTERTOUCH = "stori_add_aftertouch"
    ADD_INSERT_EFFECT = "stori_add_insert_effect"
    ADD_AUTOMATION = "stori_add_automation"
    ENSURE_BUS = "stori_ensure_bus"
    ADD_SEND = "stori_add_send"
    SET_TEMPO = "stori_set_tempo"
    SET_KEY_SIGNATURE = "stori_set_key_signature"
    SET_TIME_SIGNATURE = "stori_set_time_signature"
    GENERATE_DRUMS = "stori_generate_drums"
    GENERATE_BASS = "stori_generate_bass"
    GENERATE_KEYS = "stori_generate_keys"

    def __str__(self) -> str:
        return self.value
