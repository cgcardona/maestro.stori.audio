"""Track-level MCP tool definitions."""
from __future__ import annotations

from maestro.contracts.mcp_types import MCPToolDef

TRACK_TOOLS: list[MCPToolDef] = [
    {
        "name": "stori_add_midi_track",
        "description": "Add a new MIDI track. DRUMS: set drumKitId ('acoustic', 'TR-909', 'TR-808', 'jazz'). ALL OTHER instruments: set gmProgram (0-127). Never set both.",
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
        "description": "set the volume of a track. Linear scale: 0.0 = silent, 1.0 = unity gain, 1.5 = +50%.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {
                    "type": "string",
                    "description": "Track ID (from stori_read_project or stori_add_midi_track result)"
                },
                "volume": {
                    "type": "number",
                    "description": "Linear volume 0.0–1.5 (1.0 = unity gain)",
                    "minimum": 0.0,
                    "maximum": 1.5
                }
            },
            "required": ["trackId", "volume"]
        }
    },
    {
        "name": "stori_set_track_pan",
        "description": "set the pan position of a track. 0.0 = hard left, 0.5 = center, 1.0 = hard right.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "Track ID"},
                "pan": {
                    "type": "number",
                    "description": "Pan position 0.0 (left) to 1.0 (right), 0.5 = center",
                    "minimum": 0.0,
                    "maximum": 1.0
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
        "description": "set the MIDI program (instrument voice) for a track. General MIDI 0-127 (e.g. 0=Piano, 33=Bass, 10=drums on channel 10).",
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
        "description": "set a track's color.",
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
        "description": "set a track's icon (SF Symbol name, e.g. pianokeys, guitars, music.note).",
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
