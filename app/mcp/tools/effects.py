"""Effects and routing MCP tool definitions."""
from __future__ import annotations

from app.contracts.mcp_types import MCPToolDef

EFFECTS_TOOLS: list[MCPToolDef] = [
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
        "description": "Add a send from a track to a named bus (for reverb/delay routing).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trackId": {"type": "string", "description": "UUID of source track"},
                "busName": {"type": "string", "description": "Name of destination bus (e.g. 'Reverb', 'Delay')"},
                "sendLevel": {"type": "number", "description": "Send level 0.0–1.0, default 0.3"}
            },
            "required": ["trackId", "busName"]
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
