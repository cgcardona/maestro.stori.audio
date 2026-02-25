"""Note-level MCP tool definitions."""
from __future__ import annotations

NOTE_TOOLS = [
    {
        "name": "stori_add_notes",
        "description": """Add MIDI notes to a region.

Note timing is relative to region start. Pitch is MIDI note number (60 = Middle C).
Velocity controls dynamics (0-127, typically 70-100 for normal playing).

For drums, use standard GM drum map:
- 36: Kick, 38: Snare, 42: Closed Hi-Hat, 46: Open Hi-Hat, 49: Crash

For regions requiring more than 128 notes, call stori_add_notes multiple times
with the same regionId — each call appends to existing notes.
NEVER use shorthand params like _noteCount or _beatRange; always provide a real 'notes' array.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {
                    "type": "string",
                    "description": "Region ID to add notes to"
                },
                "notes": {
                    "type": "array",
                    "description": "Array of MIDI notes (use startBeat, durationBeats, velocity 1-127)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pitch": {"type": "integer", "description": "MIDI note 0-127 (60=Middle C)", "minimum": 0, "maximum": 127},
                            "startBeat": {"type": "number", "description": "Start relative to region (beats)", "minimum": 0},
                            "durationBeats": {"type": "number", "description": "Duration in beats", "minimum": 0.01},
                            "velocity": {"type": "integer", "description": "Velocity 1-127", "minimum": 1, "maximum": 127, "default": 100}
                        },
                        "required": ["pitch", "startBeat", "durationBeats", "velocity"]
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
        "description": "Quantize notes in a region to a rhythmic grid.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID"},
                "gridSize": {
                    "type": "number",
                    "description": "Grid in beats: 0.125=1/32, 0.25=1/16, 0.5=1/8, 1.0=1/4, 2.0=1/2, 4.0=whole",
                    "enum": [0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0]
                },
                "strength": {
                    "type": "number",
                    "description": "Quantize strength (0.0-1.0), default 1.0",
                    "minimum": 0,
                    "maximum": 1
                }
            },
            "required": ["regionId", "gridSize"]
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
