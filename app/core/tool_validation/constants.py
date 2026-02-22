"""Validation constants: icon allowlist, entity fields, value ranges, tool requirements."""

from __future__ import annotations

VALID_SF_SYMBOL_ICONS: frozenset[str] = frozenset({
    # Instruments
    "instrument.trumpet", "instrument.violin", "instrument.saxophone",
    "instrument.flute", "instrument.drum", "instrument.harp", "instrument.xylophone",
    "guitars", "guitars.fill", "pianokeys", "pianokeys.inverse",
    "music.mic", "music.mic.circle", "music.mic.circle.fill",
    "headphones", "headphones.circle", "headphones.circle.fill",
    "hifispeaker", "hifispeaker.fill", "hifispeaker.2", "hifispeaker.2.fill",
    "tuningfork",
    # Notes & Waveforms
    "music.note", "music.note.list", "music.quarternote.3",
    "music.note.house", "music.note.house.fill",
    "music.note.tv", "music.note.tv.fill",
    "waveform", "waveform.circle", "waveform.circle.fill",
    "waveform.path", "waveform.path.ecg",
    "waveform.and.mic", "waveform.badge.mic",
    # Effects & Controls
    "slider.horizontal.3", "slider.vertical.3",
    "sparkles", "wand.and.rays", "wand.and.stars",
    "bolt", "bolt.fill",
    "flame", "flame.fill", "metronome",
    "dial.min", "dial.medium", "dial.max",
    "repeat", "repeat.1", "shuffle",
    "ear", "ear.badge.waveform",
    "star", "star.fill",
    "globe",
})

# Entity reference fields that need validation
ENTITY_REF_FIELDS: dict[str, str] = {
    "trackId": "track",
    "regionId": "region",
    "busId": "bus",
    "trackName": "track",  # resolved to trackId
}

# Entity-creating tools → ID fields to skip validation on (server replaces with fresh UUIDs)
_ENTITY_CREATING_SKIP: dict[str, set[str]] = {
    "stori_add_midi_track": {"trackId"},
    "stori_add_midi_region": {"regionId"},
    "stori_ensure_bus": {"busId"},
}

# Value range constraints
VALUE_RANGES: dict[str, tuple[float, float]] = {
    "volume": (0.0, 1.5),
    "pan": (0.0, 1.0),
    "sendLevel": (0.0, 1.0),
    "gridSize": (0.0625, 4.0),
    "tempo": (20, 300),
    "bars": (1, 64),
    "zoomPercent": (10, 500),
    "velocity": (1, 127),
    "pitch": (0, 127),
    "amount": (0, 1),
    "strength": (0, 1),
    "startBeat": (0, float("inf")),
    "durationBeats": (0.01, 1000),
}

# Name length constraints (per frontend validation)
NAME_LENGTH_LIMITS: dict[str, int] = {
    "track": 50,
    "region": 50,
    "bus": 50,
    "project": 100,
}

# Required fields per tool (beyond JSON schema "required").
# The FE client throws on any missing required field — no silent defaults.
TOOL_REQUIRED_FIELDS: dict[str, list[str]] = {
    "stori_add_notes": ["regionId", "notes"],
    "stori_add_midi_region": ["trackId", "startBeat", "durationBeats"],
    "stori_set_tempo": ["tempo"],
    "stori_set_key": ["key"],
    "stori_set_track_volume": ["trackId", "volume"],
    "stori_set_track_pan": ["trackId", "pan"],
    "stori_add_insert_effect": ["trackId", "type"],
    "stori_add_send": ["trackId", "busName"],
    "stori_quantize_notes": ["regionId", "gridSize"],
    "stori_transpose_notes": ["regionId", "semitones"],
    "stori_move_region": ["regionId", "startBeat"],
    "stori_add_automation": ["trackId", "parameter", "points"],
    "stori_add_midi_cc": ["regionId", "cc", "events"],
    "stori_add_pitch_bend": ["regionId", "events"],
}

# Canonical automation parameter values (frontend AutomationParameter.rawValue)
AUTOMATION_CANONICAL_PARAMETERS: set[str] = {
    "Volume", "Pan",
    "EQ Low", "EQ Mid", "EQ High",
    "Mod Wheel (CC1)", "Volume (CC7)", "Pan (CC10)",
    "Expression (CC11)", "Sustain (CC64)", "Filter Cutoff (CC74)",
    "Pitch Bend",
    "Synth Cutoff", "Synth Resonance", "Synth Attack", "Synth Release",
}
