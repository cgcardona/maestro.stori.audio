"""Tool-name category sets for plan tracking and routing."""

from __future__ import annotations

_SETUP_TOOL_NAMES: set[str] = {
    "stori_set_tempo", "stori_set_key",
}
_EFFECT_TOOL_NAMES: set[str] = {
    "stori_add_insert_effect",
    "stori_ensure_bus", "stori_add_send",
}
_MIXING_TOOL_NAMES: set[str] = {
    "stori_set_track_volume", "stori_set_track_pan",
    "stori_mute_track", "stori_solo_track",
    "stori_set_track_color", "stori_set_track_icon",
    "stori_set_track_name",
}
_TRACK_CREATION_NAMES: set[str] = {
    "stori_add_midi_track",
}
_CONTENT_TOOL_NAMES: set[str] = {
    "stori_add_midi_region", "stori_add_notes",
}
_EXPRESSIVE_TOOL_NAMES: set[str] = {
    "stori_add_midi_cc", "stori_add_pitch_bend", "stori_add_automation",
}
_GENERATOR_TOOL_NAMES: set[str] = {
    "stori_generate_midi", "stori_generate_drums", "stori_generate_bass",
    "stori_generate_melody", "stori_generate_chords",
}

# Tools whose track association can be determined from params
_TRACK_BOUND_TOOL_NAMES: set[str] = (
    _TRACK_CREATION_NAMES | _CONTENT_TOOL_NAMES | _EFFECT_TOOL_NAMES
    | _EXPRESSIVE_TOOL_NAMES | _GENERATOR_TOOL_NAMES | _MIXING_TOOL_NAMES
)

# Agent Teams — tools each instrument agent may call (no setup/mixing tools)
_INSTRUMENT_AGENT_TOOLS: frozenset[str] = frozenset({
    "stori_add_midi_track",
    "stori_add_midi_region",
    "stori_add_notes",
    "stori_generate_midi",
    "stori_generate_drums",
    "stori_generate_bass",
    "stori_generate_melody",
    "stori_generate_chords",
    "stori_add_insert_effect",
    "stori_add_midi_cc",
    "stori_add_pitch_bend",
    "stori_apply_swing",
    "stori_quantize_notes",
    "stori_set_track_icon",
    "stori_set_track_color",
})

# Agent Teams — tools the Phase 3 mixing coordinator may call
_AGENT_TEAM_PHASE3_TOOLS: frozenset[str] = frozenset({
    "stori_ensure_bus",
    "stori_add_send",
    "stori_set_track_volume",
    "stori_set_track_pan",
    "stori_mute_track",
    "stori_solo_track",
    "stori_add_automation",
})
