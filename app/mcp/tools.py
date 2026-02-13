"""
MCP Tool Definitions for Stori DAW

These tools are exposed via MCP for LLMs to control the DAW.
They follow the MCP tool schema format.
"""
from typing import Any

# =============================================================================
# PROJECT TOOLS
# =============================================================================

PROJECT_TOOLS = [
    {
        "name": "stori_read_project",
        "description": """Read the current project state from the DAW.
Returns tempo, key signature, time signature, and all tracks with their regions.
Use this to understand the current composition before making changes.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_notes": {
                    "type": "boolean",
                    "description": "Whether to include individual MIDI notes (can be large)",
                    "default": False
                },
                "include_automation": {
                    "type": "boolean",
                    "description": "Whether to include automation data",
                    "default": False
                }
            }
        }
    },
    {
        "name": "stori_create_project",
        "description": "Create a new project with the specified tempo and name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Project name"
                },
                "tempo": {
                    "type": "number",
                    "description": "Tempo in BPM (40-240)",
                    "minimum": 40,
                    "maximum": 240
                },
                "keySignature": {
                    "type": "string",
                    "description": "Key signature (e.g., 'C', 'Am', 'F#m')"
                },
                "timeSignature": {
                    "type": "object",
                    "properties": {
                        "numerator": {"type": "integer", "default": 4},
                        "denominator": {"type": "integer", "default": 4}
                    }
                }
            },
            "required": ["name", "tempo"]
        }
    },
    {
        "name": "stori_set_tempo",
        "description": "Change the project tempo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tempo": {
                    "type": "number",
                    "description": "New tempo in BPM",
                    "minimum": 40,
                    "maximum": 240
                }
            },
            "required": ["tempo"]
        }
    },
    {
        "name": "stori_set_key",
        "description": "Change the project key signature (alias for stori_set_key_signature).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Key signature (e.g., 'C', 'Am', 'Bb', 'F#m')"
                }
            },
            "required": ["key"]
        }
    },
    {
        "name": "stori_set_key_signature",
        "description": "Set the project key signature (e.g. Cm, F# minor).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Key signature (e.g., 'C', 'Am', 'Bb', 'F#m', 'Cm')"
                }
            },
            "required": ["key"]
        }
    },
]

# =============================================================================
# TRACK TOOLS
# =============================================================================

TRACK_TOOLS = [
    {
        "name": "stori_add_track",
        "description": """Add a new MIDI track to the project.
For drums, use drumKitId. For melodic instruments, use gmProgram (General MIDI program number).

Common GM Programs:
- 0: Piano, 4: Rhodes, 33: Electric Bass, 38: Synth Bass
- 48: Strings, 80: Square Lead, 81: Saw Lead, 89: Warm Pad""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Track name"
                },
                "gmProgram": {
                    "type": "integer",
                    "description": "GM program number (0-127) for melodic instruments",
                    "minimum": 0,
                    "maximum": 127
                },
                "drumKitId": {
                    "type": "string",
                    "description": "Drum kit: 'acoustic', 'TR-909', 'TR-808', 'jazz'"
                },
                "color": {
                    "type": "string",
                    "description": "Track color",
                    "enum": ["red", "orange", "yellow", "green", "blue", "purple", "pink", "teal", "indigo"]
                },
                "volume": {
                    "type": "number",
                    "description": "Initial volume (0.0-1.0)",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.8
                },
                "pan": {
                    "type": "number",
                    "description": "Pan position (0.0=left, 0.5=center, 1.0=right)",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.5
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "stori_add_midi_track",
        "description": "Add a new MIDI track with optional instrument, GM program, color, and icon (alternative to stori_add_track).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Track name"},
                "instrument": {"type": "string", "description": "Optional instrument name for voice selection"},
                "gmProgram": {"type": "integer", "description": "GM MIDI program 0-127", "minimum": 0, "maximum": 127},
                "color": {"type": "string", "description": "Optional hex color (e.g. #FF6B6B)"},
                "icon": {"type": "string", "description": "Optional SF Symbol icon (e.g. pianokeys, guitars.fill)"}
            }
        }
    },
    {
        "name": "stori_set_track_volume",
        "description": "Set the volume of a track (in dB; 0 = unity, negative = attenuation).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {
                    "type": "string",
                    "description": "Track ID (from stori_read_project or stori_add_track result)"
                },
                "volumeDb": {
                    "type": "number",
                    "description": "Volume in dB (0 = unity, e.g. -6 for half, -inf for mute)"
                }
            },
            "required": ["trackId", "volumeDb"]
        }
    },
    {
        "name": "stori_set_track_pan",
        "description": "Set the pan position of a track (-100 = full left, 0 = center, +100 = full right).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "pan": {
                    "type": "number",
                    "description": "Pan (-100 to 100)",
                    "minimum": -100,
                    "maximum": 100
                }
            },
            "required": ["trackId", "pan"]
        }
    },
    {
        "name": "stori_set_track_name",
        "description": "Rename a track.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "name": {"type": "string", "description": "New track name"}
            },
            "required": ["trackId", "name"]
        }
    },
    {
        "name": "stori_set_midi_program",
        "description": "Set the MIDI program (instrument voice) for a track. General MIDI 0-127 (e.g. 0=Piano, 33=Bass, 10=drums on channel 10).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "program": {
                    "type": "integer",
                    "description": "GM program number 0-127",
                    "minimum": 0,
                    "maximum": 127
                },
                "channel": {
                    "type": "integer",
                    "description": "MIDI channel 1-16 (default 1; use 10 for drums)",
                    "minimum": 1,
                    "maximum": 16,
                    "default": 1
                }
            },
            "required": ["trackId", "program"]
        }
    },
    {
        "name": "stori_mute_track",
        "description": "Mute or unmute a track.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "muted": {"type": "boolean", "description": "True to mute, false to unmute"}
            },
            "required": ["trackId", "muted"]
        }
    },
    {
        "name": "stori_solo_track",
        "description": "Solo or unsolo a track.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "solo": {"type": "boolean", "description": "True to solo, false to unsolo"}
            },
            "required": ["trackId", "solo"]
        }
    },
    {
        "name": "stori_set_track_color",
        "description": "Set a track's color.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "color": {
                    "type": "string",
                    "description": "Track color",
                    "enum": ["red", "orange", "yellow", "green", "blue", "purple", "pink", "teal", "indigo"]
                }
            },
            "required": ["trackId", "color"]
        }
    },
    {
        "name": "stori_set_track_icon",
        "description": "Set a track's icon (SF Symbol name, e.g. pianokeys, guitars, music.note).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "icon": {
                    "type": "string",
                    "description": "SF Symbol name: e.g. pianokeys, guitars, music.note, waveform, hifispeaker.fill"
                }
            },
            "required": ["trackId", "icon"]
        }
    },
]

# =============================================================================
# REGION TOOLS
# =============================================================================

REGION_TOOLS = [
    {
        "name": "stori_add_region",
        "description": """Add a MIDI region to a track.
Regions are containers for MIDI notes. Position and duration are in beats (quarter notes).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID to add region to"},
                "name": {"type": "string", "description": "Region name"},
                "startBeat": {"type": "number", "description": "Start position in beats (0 = beginning)", "minimum": 0},
                "durationBeats": {"type": "number", "description": "Duration in beats (4 = one bar in 4/4)", "minimum": 0.25},
                "color": {"type": "string", "description": "Region color (hex e.g. #3B82F6)"}
            },
            "required": ["trackId", "startBeat", "durationBeats"]
        }
    },
    {
        "name": "stori_add_midi_region",
        "description": "Create a MIDI region on a track (same as stori_add_region; param names align with core).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "startBeat": {"type": "number", "description": "Start in beats (>= 0)", "minimum": 0},
                "durationBeats": {"type": "number", "description": "Duration in beats (> 0)", "minimum": 0.01},
                "name": {"type": "string", "description": "Optional display name"}
            },
            "required": ["trackId", "startBeat", "durationBeats"]
        }
    },
    {
        "name": "stori_delete_region",
        "description": "Delete a MIDI region.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID to delete"}
            },
            "required": ["regionId"]
        }
    },
    {
        "name": "stori_move_region",
        "description": "Move a region to a new position.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID"},
                "startBeat": {"type": "number", "description": "New start position in beats"}
            },
            "required": ["regionId", "startBeat"]
        }
    },
    {
        "name": "stori_duplicate_region",
        "description": "Duplicate a region to a new position.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID to duplicate"},
                "startBeat": {"type": "number", "description": "Start position for the copy"}
            },
            "required": ["regionId", "startBeat"]
        }
    },
]

# =============================================================================
# NOTE TOOLS
# =============================================================================

NOTE_TOOLS = [
    {
        "name": "stori_add_notes",
        "description": """Add MIDI notes to a region.
        
Note timing is relative to region start. Pitch is MIDI note number (60 = Middle C).
Velocity controls dynamics (0-127, typically 70-100 for normal playing).

For drums, use standard GM drum map:
- 36: Kick, 38: Snare, 42: Closed Hi-Hat, 46: Open Hi-Hat, 49: Crash""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {
                    "type": "string",
                    "description": "Region ID to add notes to"
                },
                "notes": {
                    "type": "array",
                    "description": "Array of MIDI notes (use startBeats, durationBeats, velocity 1-127)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pitch": {"type": "integer", "description": "MIDI note 0-127 (60=Middle C)", "minimum": 0, "maximum": 127},
                            "startBeats": {"type": "number", "description": "Start relative to region (beats)", "minimum": 0},
                            "durationBeats": {"type": "number", "description": "Duration in beats", "minimum": 0.01},
                            "velocity": {"type": "integer", "description": "Velocity 1-127", "minimum": 1, "maximum": 127, "default": 100}
                        },
                        "required": ["pitch", "startBeats", "durationBeats", "velocity"]
                    }
                }
            },
            "required": ["regionId", "notes"]
        }
    },
    {
        "name": "stori_clear_notes",
        "description": "Remove all notes from a region.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID"}
            },
            "required": ["regionId"]
        }
    },
    {
        "name": "stori_quantize_notes",
        "description": "Quantize notes in a region to a grid.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID"},
                "grid": {
                    "type": "string",
                    "description": "Grid size",
                    "enum": ["1/4", "1/8", "1/16", "1/32", "1/64"],
                    "default": "1/16"
                },
                "strength": {
                    "type": "number",
                    "description": "Quantize strength (0.0-1.0)",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 1.0
                }
            },
            "required": ["regionId"]
        }
    },
    {
        "name": "stori_apply_swing",
        "description": "Apply swing feel to notes in a region.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID"},
                "amount": {
                    "type": "number",
                    "description": "Swing amount (0.0 = none, 0.5 = moderate, 1.0 = heavy)",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.3
                }
            },
            "required": ["regionId"]
        }
    },
]

# =============================================================================
# EFFECTS TOOLS
# =============================================================================

EFFECTS_TOOLS = [
    {
        "name": "stori_add_effect",
        "description": "Add an insert effect to a track (alias: use effectType; for core alignment use stori_add_insert_effect with 'type').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "effectType": {
                    "type": "string",
                    "description": "Effect type",
                    "enum": ["compressor", "eq", "reverb", "delay", "chorus", "distortion", "filter", "overdrive", "phaser", "flanger", "modulation", "tremolo"]
                }
            },
            "required": ["trackId", "effectType"]
        }
    },
    {
        "name": "stori_add_insert_effect",
        "description": "Add an insert effect to a track. Valid types: reverb, delay, compressor, eq, distortion, filter, chorus, modulation, overdrive, phaser, flanger, tremolo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "type": {
                    "type": "string",
                    "description": "Effect type",
                    "enum": ["reverb", "delay", "compressor", "eq", "distortion", "filter", "chorus", "modulation", "overdrive", "phaser", "flanger", "tremolo"]
                }
            },
            "required": ["trackId", "type"]
        }
    },
    {
        "name": "stori_add_send",
        "description": "Add a send from a track to a bus (for reverb/delay).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "busId": {"type": "string", "description": "Bus ID (from stori_ensure_bus or DAW)"},
                "levelDb": {"type": "number", "description": "Send level in dB"}
            },
            "required": ["trackId", "busId"]
        }
    },
    {
        "name": "stori_ensure_bus",
        "description": "Ensure a named bus exists (create if missing). Returns bus ID for use in stori_add_send.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Bus name (e.g. 'Reverb', 'Delay')"}
            },
            "required": ["name"]
        }
    },
]

# =============================================================================
# AUTOMATION TOOLS
# =============================================================================

AUTOMATION_TOOLS = [
    {
        "name": "stori_add_automation",
        "description": "Add automation to a track parameter (volume, pan, filter, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Target (e.g. trackId or parameter path)"},
                "points": {
                    "type": "array",
                    "description": "Automation points [{beat, value}, ...]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "beat": {"type": "number"},
                            "value": {"type": "number"},
                            "curve": {"type": "string", "enum": ["Linear", "Smooth", "Step", "Exp", "Log"]}
                        },
                        "required": ["beat", "value"]
                    }
                }
            },
            "required": ["target", "points"]
        }
    },
]

# =============================================================================
# MIDI CC / PITCH BEND
# =============================================================================

MIDI_CONTROL_TOOLS = [
    {
        "name": "stori_add_midi_cc",
        "description": "Add MIDI CC (control change) events to a region.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID"},
                "cc": {"type": "integer", "description": "CC number 0-127", "minimum": 0, "maximum": 127},
                "events": {
                    "type": "array",
                    "description": "List of {beat, value} events",
                    "items": {"type": "object", "properties": {"beat": {"type": "number"}, "value": {"type": "integer", "minimum": 0, "maximum": 127}}}
                }
            },
            "required": ["regionId", "cc", "events"]
        }
    },
    {
        "name": "stori_add_pitch_bend",
        "description": "Add pitch bend events to a region.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID"},
                "events": {
                    "type": "array",
                    "description": "List of {beat, value} events (value typically -8192 to 8191)"
                }
            },
            "required": ["regionId", "events"]
        }
    },
]

# =============================================================================
# MUSIC GENERATION TOOLS (Tier 1 - Server-side)
# =============================================================================

GENERATION_TOOLS = [
    {
        "name": "stori_generate_midi",
        "description": "Generate MIDI for a musical role (drums, bass, chords, melody, arp, pads, fx). Returns MIDI notes. Preferred general generator.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "Role: drums, bass, chords, melody, arp, pads, fx",
                    "enum": ["drums", "bass", "chords", "melody", "arp", "pads", "fx"]
                },
                "style": {"type": "string", "description": "Style: boom_bap, trap, house, lofi, jazz, funk, etc."},
                "tempo": {"type": "number", "description": "Tempo in BPM"},
                "bars": {"type": "integer", "description": "Number of bars (1-64)", "minimum": 1, "maximum": 64},
                "key": {"type": "string", "description": "Key e.g. Cm, F# minor"},
                "constraints": {"type": "object", "description": "Optional: density, syncopation, swing, note_range"}
            },
            "required": ["role", "style", "tempo", "bars"]
        }
    },
    {
        "name": "stori_generate_drums",
        "description": """Generate a drum pattern using AI.
Returns MIDI notes that can be added to a drum track.

Styles: boom_bap, trap, house, lofi, jazz, rock, latin""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "description": "Drum style",
                    "enum": ["boom_bap", "trap", "house", "lofi", "jazz", "rock", "latin"]
                },
                "tempo": {
                    "type": "number",
                    "description": "Tempo in BPM"
                },
                "bars": {
                    "type": "integer",
                    "description": "Number of bars to generate",
                    "minimum": 1,
                    "maximum": 16,
                    "default": 4
                },
                "complexity": {
                    "type": "number",
                    "description": "Pattern complexity (0.0 = simple, 1.0 = complex)",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.5
                }
            },
            "required": ["style", "tempo"]
        }
    },
    {
        "name": "stori_generate_bass",
        "description": """Generate a bass line using AI.
Returns MIDI notes that follow the specified chord progression.

Styles: boom_bap, jazz_walk, funk, house, synth, reggae""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "description": "Bass style",
                    "enum": ["boom_bap", "jazz_walk", "funk", "house", "synth", "reggae"]
                },
                "tempo": {
                    "type": "number",
                    "description": "Tempo in BPM"
                },
                "bars": {
                    "type": "integer",
                    "description": "Number of bars",
                    "minimum": 1,
                    "maximum": 16
                },
                "key": {
                    "type": "string",
                    "description": "Musical key (e.g., 'Am', 'C', 'F#m')"
                },
                "chords": {
                    "type": "array",
                    "description": "Chord progression (e.g., ['Am7', 'Dm7', 'G7', 'Cmaj7'])",
                    "items": {"type": "string"}
                }
            },
            "required": ["style", "tempo", "bars"]
        }
    },
    {
        "name": "stori_generate_melody",
        "description": """Generate a melody using AI.
Returns MIDI notes for a lead or melodic line.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "description": "Melody style",
                    "enum": ["soulful", "jazzy", "pop", "ambient", "aggressive", "simple"]
                },
                "tempo": {"type": "number", "description": "Tempo in BPM"},
                "bars": {"type": "integer", "minimum": 1, "maximum": 16},
                "key": {"type": "string", "description": "Musical key"},
                "scale": {
                    "type": "string",
                    "description": "Scale type",
                    "enum": ["major", "minor", "pentatonic", "blues", "dorian", "mixolydian"],
                    "default": "minor"
                },
                "octave": {
                    "type": "integer",
                    "description": "Base octave (4 = middle range)",
                    "minimum": 2,
                    "maximum": 6,
                    "default": 4
                }
            },
            "required": ["style", "tempo", "bars"]
        }
    },
    {
        "name": "stori_generate_chords",
        "description": """Generate a chord progression using AI.
Returns MIDI notes for chord voicings.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "description": "Chord style",
                    "enum": ["jazz", "soul", "pop", "ambient", "classical", "neosoul"]
                },
                "tempo": {"type": "number", "description": "Tempo in BPM"},
                "bars": {"type": "integer", "minimum": 1, "maximum": 16},
                "key": {"type": "string", "description": "Musical key"},
                "progression": {
                    "type": "string",
                    "description": "Progression type (e.g., 'ii-V-I', 'I-vi-IV-V')"
                }
            },
            "required": ["style", "tempo", "bars"]
        }
    },
]

# =============================================================================
# PLAYBACK TOOLS
# =============================================================================

PLAYBACK_TOOLS = [
    {
        "name": "stori_play",
        "description": "Start playback from current position or specified beat.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fromBeat": {"type": "number", "description": "Beat to start from (optional)"}
            }
        }
    },
    {
        "name": "stori_stop",
        "description": "Stop playback.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "stori_set_playhead",
        "description": "Move the playhead to a bar/beat or absolute time.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bar": {"type": "integer", "description": "Bar number"},
                "beat": {"type": "number", "description": "Beat position"},
                "seconds": {"type": "number", "description": "Time in seconds"}
            }
        }
    },
]

# =============================================================================
# UI TOOLS
# =============================================================================

UI_TOOLS = [
    {
        "name": "stori_show_panel",
        "description": "Show or hide a panel (mixer, inspector, piano_roll, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "panel": {"type": "string", "description": "Panel: mixer, inspector, piano_roll, etc."},
                "visible": {"type": "boolean", "description": "True to show, false to hide"}
            },
            "required": ["panel", "visible"]
        }
    },
    {
        "name": "stori_set_zoom",
        "description": "Set editor zoom (percent).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "zoomPercent": {"type": "number", "description": "Zoom percentage"}
            },
            "required": ["zoomPercent"]
        }
    },
]

# =============================================================================
# ALL MCP TOOLS
# =============================================================================

MCP_TOOLS = (
    PROJECT_TOOLS +
    TRACK_TOOLS +
    REGION_TOOLS +
    NOTE_TOOLS +
    EFFECTS_TOOLS +
    AUTOMATION_TOOLS +
    MIDI_CONTROL_TOOLS +
    GENERATION_TOOLS +
    PLAYBACK_TOOLS +
    UI_TOOLS
)

# Tool name to category mapping for easier lookup
TOOL_CATEGORIES = {
    tool["name"]: category
    for category, tools in [
        ("project", PROJECT_TOOLS),
        ("track", TRACK_TOOLS),
        ("region", REGION_TOOLS),
        ("note", NOTE_TOOLS),
        ("effects", EFFECTS_TOOLS),
        ("automation", AUTOMATION_TOOLS),
        ("midi_control", MIDI_CONTROL_TOOLS),
        ("generation", GENERATION_TOOLS),
        ("playback", PLAYBACK_TOOLS),
        ("ui", UI_TOOLS),
    ]
    for tool in tools
}

# Generation tools execute server-side, others forward to DAW
SERVER_SIDE_TOOLS = {tool["name"] for tool in GENERATION_TOOLS}
DAW_TOOLS = {tool["name"] for tool in MCP_TOOLS if tool["name"] not in SERVER_SIDE_TOOLS}
