"""MIDI CC, pitch bend, and aftertouch MCP tool definitions."""
from __future__ import annotations

from app.contracts.mcp_types import MCPToolDef

MIDI_CONTROL_TOOLS: list[MCPToolDef] = [
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
                    "description": "list of {beat, value} events",
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
                    "description": "list of {beat, value} events (value typically -8192 to 8191)"
                }
            },
            "required": ["regionId", "events"]
        }
    },
    {
        "name": "stori_add_aftertouch",
        "description": "Add aftertouch events to a region. Channel pressure: {beat, value}. Polyphonic key pressure: {beat, value, pitch}.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string", "description": "Region ID"},
                "events": {
                    "type": "array",
                    "description": "list of aftertouch events. Each: {beat, value} for channel, {beat, value, pitch} for polyphonic.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "beat": {"type": "number"},
                            "value": {"type": "integer", "minimum": 0, "maximum": 127},
                            "pitch": {"type": "integer", "minimum": 0, "maximum": 127, "description": "MIDI note for polyphonic aftertouch (omit for channel)"}
                        },
                        "required": ["beat", "value"]
                    }
                }
            },
            "required": ["regionId", "events"]
        }
    },
]
