"""Music generation MCP tool definitions (Tier 1 — server-side).

Every tool in this module sets ``server_side: True`` — these tools are
executed on the Maestro backend (via Orpheus) and never forwarded to
the DAW.  The registry uses this flag to build ``SERVER_SIDE_TOOLS``
dynamically instead of maintaining a hardcoded set.
"""

GENERATION_TOOLS = [
    {
        "name": "stori_generate_midi",
        "server_side": True,
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
        "server_side": True,
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
        "server_side": True,
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
        "server_side": True,
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
        "server_side": True,
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
