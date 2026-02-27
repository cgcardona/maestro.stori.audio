"""Region-level MCP tool definitions."""
from __future__ import annotations

from app.contracts.mcp_types import MCPToolDef

REGION_TOOLS: list[MCPToolDef] = [
    {
        "name": "stori_add_midi_region",
        "description": "Add a MIDI region to a track. Regions are containers for MIDI notes. Position and duration are in beats (quarter notes).",
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
