"""
Tool definitions in OpenAI tool schema format.

Tools are classified into:
  * PRIMITIVE (deterministic, reversible, single-mutation)  -> safe for direct LLM use
  * GENERATOR  (creative / stochastic / expensive)          -> planner-gated
  * MACRO      (multi-step convenience)                     -> never directly callable by LLM

Tools are additionally grouped by tier:
  * Tier 1: server-side generation/execution
  * Tier 2: client-side DAW control (Swift)
"""

from __future__ import annotations

from typing import Any

# ---- Tier 1: Generators ------------------------------------------------------
# Prefer 1 general generator tool over N specialized tools.
# Older specific generators kept for backwards compatibility but marked deprecated.
TIER1_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "stori_generate_midi",
            "description": "Generate MIDI for a musical role (drums/bass/chords/melody/etc). Returns MIDI notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "Role: drums, bass, chords, melody, arp, pads, fx"},
                    "style": {"type": "string", "description": "Style tag: boom_bap, trap, house, lofi, jazz, funk, etc"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-64)"},
                    "key": {"type": "string", "description": "Key like Cm, F# minor, etc"},
                    "constraints": {
                        "type": "object",
                        "description": "Optional structured constraints (density, syncopation, swing, note_range, etc)",
                    },
                },
                "required": ["role", "style", "tempo", "bars"],
            },
        },
    },
    # Back-compat specialized generators
    {
        "type": "function",
        "function": {
            "name": "stori_generate_drums",
            "description": "Generate a drum pattern using AI. Returns MIDI notes for drums.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "description": "Drum style: boom_bap, trap, house, lofi, jazz"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-16)"},
                },
                "required": ["style", "tempo", "bars"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_generate_bass",
            "description": "Generate a bass line using AI. Returns MIDI notes for bass.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "description": "Bass style: boom_bap, trap, house, lofi, funk"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-16)"},
                },
                "required": ["style", "tempo", "bars"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_generate_chords",
            "description": "Generate chord progression using AI. Returns MIDI notes for chords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "description": "Chord style: lofi, jazz, pop, house, trap"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-16)"},
                    "key": {"type": "string", "description": "Key like Cm, F# minor"},
                },
                "required": ["style", "tempo", "bars", "key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_generate_melody",
            "description": "Generate a melody using AI. Returns MIDI notes for melody.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "description": "Melody style: lofi, trap, house, jazz, pop"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-16)"},
                    "key": {"type": "string", "description": "Key like Cm, F# minor"},
                },
                "required": ["style", "tempo", "bars", "key"],
            },
        },
    },
]


# ---- Tier 2: DAW primitives --------------------------------------------------
TIER2_TOOLS: list[dict[str, Any]] = [
    # Project / transport
    {
        "type": "function",
        "function": {
            "name": "stori_create_project",
            "description": "Create a new project.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Project name"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_tempo",
            "description": "Set project tempo in BPM.",
            "parameters": {
                "type": "object",
                "properties": {"tempo": {"type": "integer", "description": "Tempo in BPM"}},
                "required": ["tempo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_key",
            "description": "Set project key signature (e.g. Cm, F# minor).",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "description": "Key string"}},
                "required": ["key"],
            },
        },
    },
    {"type": "function", "function": {"name": "stori_play", "description": "Start playback.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "stori_stop", "description": "Stop playback.", "parameters": {"type": "object", "properties": {}}}},
    {
        "type": "function",
        "function": {
            "name": "stori_set_playhead",
            "description": "Move playhead to a bar/beat or absolute time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bar": {"type": "integer"},
                    "beat": {"type": "integer"},
                    "seconds": {"type": "number"},
                },
            },
        },
    },

    # UI
    {
        "type": "function",
        "function": {
            "name": "stori_show_panel",
            "description": "Show or hide a panel (mixer, inspector, piano_roll, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "panel": {"type": "string"},
                    "visible": {"type": "boolean"},
                },
                "required": ["panel", "visible"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_zoom",
            "description": "Set editor zoom (percent).",
            "parameters": {
                "type": "object",
                "properties": {"zoomPercent": {"type": "number"}},
                "required": ["zoomPercent"],
            },
        },
    },

    # Tracks
    {
        "type": "function",
        "function": {
            "name": "stori_add_midi_track",
            "description": (
                "Add a new MIDI track. "
                "DRUMS: set drumKitId ('acoustic', 'TR-909', 'TR-808', 'jazz') — do NOT set gmProgram. "
                "ALL OTHER instruments: set gmProgram (0-127, e.g. 33=Electric Bass, 19=Church Organ, 0=Piano). "
                "Never set both drumKitId and gmProgram on the same track."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Track name (e.g. 'Drums', 'Bass', 'Organ')"},
                    "drumKitId": {"type": "string", "description": "Drum kit — ONLY for drum tracks. Options: 'acoustic', 'TR-909', 'TR-808', 'jazz'. Mutually exclusive with gmProgram."},
                    "instrument": {"type": "string", "description": "Optional instrument name hint for voice selection (melodic tracks only)"},
                    "gmProgram": {"type": "integer", "description": "GM program 0-127 for melodic/harmonic tracks only. Do NOT use for drums — use drumKitId instead."},
                    "color": {"type": "string", "description": "Optional hex color (e.g. '#FF6B6B'). Auto-generated if omitted."},
                    "icon": {"type": "string", "description": "Optional SF Symbol name (e.g. 'pianokeys', 'guitars.fill'). Auto-inferred from name if omitted."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_midi_program",
            "description": "Set the MIDI program (instrument voice) for a track. Uses General MIDI standard (0-127).",
            "parameters": {
                "type": "object",
                "properties": {
                    "trackId": {"type": "string", "description": "Track UUID"},
                    "program": {"type": "integer", "description": "GM program number 0-127 (e.g., 0=Piano, 25=Acoustic Guitar, 33=Electric Bass)"},
                    "channel": {"type": "integer", "description": "MIDI channel 1-16 (default: 1, drums should use 10)"},
                },
                "required": ["trackId", "program"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_name",
            "description": "Rename a track.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "name": {"type": "string"}}, "required": ["trackId", "name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_mute_track",
            "description": "Mute/unmute a track.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "muted": {"type": "boolean"}}, "required": ["trackId", "muted"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_solo_track",
            "description": "Solo/unsolo a track.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "solo": {"type": "boolean"}}, "required": ["trackId", "solo"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_volume",
            "description": "Set track volume. Linear scale: 0.0 = silent, 1.0 = unity gain, 1.5 = +50%.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "volume": {"type": "number", "description": "Linear volume 0.0–1.5 (1.0 = unity)"}}, "required": ["trackId", "volume"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_pan",
            "description": "Set track pan. 0.0 = hard left, 0.5 = center, 1.0 = hard right.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "pan": {"type": "number", "description": "Pan position 0.0 (left) to 1.0 (right), 0.5 = center"}}, "required": ["trackId", "pan"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_color",
            "description": "Set track color.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "color": {"type": "string"}}, "required": ["trackId", "color"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_icon",
            "description": "Set track icon.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "icon": {"type": "string"}}, "required": ["trackId", "icon"]},
        },
    },

    # Regions / Notes
    {
        "type": "function",
        "function": {
            "name": "stori_add_midi_region",
            "description": "Create a MIDI region on a track.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trackId": {"type": "string", "description": "Track ID to add the region to"},
                    "startBeat": {"type": "number", "description": "Start position in beats (must be >= 0)"},
                    "durationBeats": {"type": "number", "description": "Region duration in beats (must be > 0)"},
                    "name": {"type": "string", "description": "Optional display name for the region"},
                },
                "required": ["trackId", "startBeat", "durationBeats"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_notes",
            "description": (
                "Add MIDI notes into a region. Notes are explicit (pitch/start/duration/velocity). "
                "For regions requiring more than 128 notes, call stori_add_notes multiple times with "
                "the same regionId — each call appends to existing notes. "
                "NEVER use shorthand params like _noteCount or _beatRange; always provide a real 'notes' array."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "regionId": {"type": "string"},
                    "notes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "pitch": {"type": "integer"},
                                "startBeat": {"type": "number"},
                                "durationBeats": {"type": "number"},
                                "velocity": {"type": "integer"},
                                "channel": {"type": "integer"},
                            },
                            "required": ["pitch", "startBeat", "durationBeats", "velocity"],
                        },
                    },
                },
                "required": ["regionId", "notes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_clear_notes",
            "description": "Clear notes in current selection or by regionId.",
            "parameters": {"type": "object", "properties": {"regionId": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_quantize_notes",
            "description": "Quantize notes in a region to a rhythmic grid.",
            "parameters": {
                "type": "object",
                "properties": {
                    "regionId": {"type": "string", "description": "UUID of the region"},
                    "gridSize": {"type": "number", "description": "Grid in beats: 0.125=1/32, 0.25=1/16, 0.5=1/8, 1.0=1/4, 2.0=1/2, 4.0=whole"},
                    "strength": {"type": "number", "description": "Quantize strength 0.0–1.0, default 1.0"},
                },
                "required": ["regionId", "gridSize"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_apply_swing",
            "description": "Apply swing to selection/region.",
            "parameters": {"type": "object", "properties": {"regionId": {"type": "string"}, "amount": {"type": "number", "description": "0.0–1.0"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_transpose_notes",
            "description": "Transpose all notes in a region by semitones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "regionId": {"type": "string", "description": "UUID of the region"},
                    "semitones": {"type": "integer", "description": "Semitones to transpose: positive = up, negative = down"},
                },
                "required": ["regionId", "semitones"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_move_region",
            "description": "Move a region to a new start position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "regionId": {"type": "string", "description": "UUID of the region to move"},
                    "startBeat": {"type": "number", "description": "New start position in beats"},
                },
                "required": ["regionId", "startBeat"],
            },
        },
    },

    # FX / routing
    {
        "type": "function",
        "function": {
            "name": "stori_add_insert_effect",
            "description": "Add an insert effect to a track. Valid effect types: compressor, eq, reverb, delay, chorus, flanger, phaser, distortion, overdrive, limiter, gate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trackId": {"type": "string", "description": "UUID of the track"},
                    "type": {"type": "string", "description": "Effect type: compressor, eq, reverb, delay, chorus, flanger, phaser, distortion, overdrive, limiter, gate"}
                },
                "required": ["trackId", "type"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_send",
            "description": "Add a send from a track to a bus (for reverb/delay routing).",
            "parameters": {
                "type": "object",
                "properties": {
                    "trackId": {"type": "string", "description": "UUID of source track"},
                    "busName": {"type": "string", "description": "Name of destination bus (e.g. 'Reverb', 'Delay')"},
                    "sendLevel": {"type": "number", "description": "Send level 0.0–1.0, default 0.3"},
                },
                "required": ["trackId", "busName"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_ensure_bus",
            "description": "Ensure a named bus exists (create if missing).",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_automation",
            "description": (
                "Add automation to a track parameter. "
                "trackId identifies the track. parameter is the exact canonical string "
                "(e.g. 'Volume', 'Pan', 'EQ Low', 'Mod Wheel (CC1)', 'Pitch Bend', "
                "'Synth Cutoff'). points is an array of {beat, value, curve?} objects. "
                "Volume/Pan/EQ/Synth params use 0.0–1.0; MIDI CC params use 0–127."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trackId": {"type": "string", "description": "Track ID"},
                    "parameter": {
                        "type": "string",
                        "description": (
                            "Canonical parameter name. Exact values: 'Volume', 'Pan', "
                            "'EQ Low', 'EQ Mid', 'EQ High', 'Mod Wheel (CC1)', "
                            "'Volume (CC7)', 'Pan (CC10)', 'Expression (CC11)', "
                            "'Sustain (CC64)', 'Filter Cutoff (CC74)', 'Pitch Bend', "
                            "'Synth Cutoff', 'Synth Resonance', 'Synth Attack', 'Synth Release'"
                        ),
                    },
                    "points": {
                        "type": "array",
                        "description": "Automation points [{beat, value, curve?}]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "beat": {"type": "number"},
                                "value": {"type": "number"},
                                "curve": {
                                    "type": "string",
                                    "enum": ["linear", "smooth", "step", "exp", "log"],
                                },
                            },
                            "required": ["beat", "value"],
                        },
                    },
                },
                "required": ["trackId", "parameter", "points"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_midi_cc",
            "description": "Add MIDI CC events.",
            "parameters": {"type": "object", "properties": {"regionId": {"type": "string"}, "cc": {"type": "integer"}, "events": {"type": "array"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_pitch_bend",
            "description": "Add pitch bend events.",
            "parameters": {"type": "object", "properties": {"regionId": {"type": "string"}, "events": {"type": "array"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_aftertouch",
            "description": "Add aftertouch events (channel pressure or polyphonic key pressure).",
            "parameters": {"type": "object", "properties": {"regionId": {"type": "string"}, "events": {"type": "array"}}},
        },
    },
]

ALL_TOOLS: list[dict[str, Any]] = TIER1_TOOLS + TIER2_TOOLS
