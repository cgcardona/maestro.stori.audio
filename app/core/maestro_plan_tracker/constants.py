"""Tool-name category sets for plan tracking and routing."""

from __future__ import annotations

# ── Phase sets (mirror a professional DAW session) ───────────────────────────
#
#   setup       → project scaffolding, track creation, transport, UI
#   composition → writing notes, MIDI generation
#   arrangement → structural edits after initial writing
#   soundDesign → tone shaping via insert effects
#   expression  → performance data: CC, pitch bend, aftertouch
#   mixing      → balance, routing, & automation

_SETUP_TOOL_NAMES: set[str] = {
    "stori_create_project",
    "stori_set_tempo", "stori_set_key",
    "stori_add_midi_track", "stori_add_midi_region",
    "stori_set_midi_program",
    "stori_set_track_name", "stori_set_track_color", "stori_set_track_icon",
    "stori_play", "stori_stop", "stori_set_playhead",
    "stori_show_panel", "stori_set_zoom",
}

# Narrow subset for the tracker's initial grouping loop — only project-level
# setup that happens once before any track work begins.  Track creation and
# regions have dedicated grouping logic and must NOT be consumed here.
_PROJECT_SETUP_TOOL_NAMES: set[str] = {
    "stori_create_project",
    "stori_set_tempo", "stori_set_key",
}
_ARRANGEMENT_TOOL_NAMES: set[str] = {
    "stori_move_region", "stori_transpose_notes", "stori_clear_notes",
    "stori_quantize_notes", "stori_apply_swing",
}
_EFFECT_TOOL_NAMES: set[str] = {
    "stori_add_insert_effect",
}
_EXPRESSION_TOOL_NAMES: set[str] = {
    "stori_add_midi_cc", "stori_add_pitch_bend", "stori_add_aftertouch",
}
_MIXING_TOOL_NAMES: set[str] = {
    "stori_set_track_volume", "stori_set_track_pan",
    "stori_mute_track", "stori_solo_track",
    "stori_ensure_bus", "stori_add_send",
    "stori_add_automation",
}

# ── Convenience aliases (used by other modules) ──────────────────────────────

_TRACK_CREATION_NAMES: set[str] = {
    "stori_add_midi_track",
}
_CONTENT_TOOL_NAMES: set[str] = {
    "stori_add_midi_region", "stori_add_notes",
}
_EXPRESSIVE_TOOL_NAMES: set[str] = _EXPRESSION_TOOL_NAMES
_GENERATOR_TOOL_NAMES: set[str] = {
    "stori_generate_midi", "stori_generate_drums", "stori_generate_bass",
    "stori_generate_melody", "stori_generate_chords",
}

# Tools whose track association can be determined from params
_TRACK_BOUND_TOOL_NAMES: set[str] = (
    _TRACK_CREATION_NAMES | _CONTENT_TOOL_NAMES | _EFFECT_TOOL_NAMES
    | _EXPRESSIVE_TOOL_NAMES | _GENERATOR_TOOL_NAMES | _MIXING_TOOL_NAMES
    | _ARRANGEMENT_TOOL_NAMES
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
    "stori_add_aftertouch",
    "stori_apply_swing",
    "stori_quantize_notes",
    "stori_transpose_notes",
    "stori_move_region",
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
