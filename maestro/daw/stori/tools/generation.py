"""Music generation MCP tool definitions (Tier 1 — server-side).

Every tool in this module sets ``server_side: True`` — these tools are
executed on the Maestro backend (via Orpheus) and never forwarded to
the DAW.  The registry uses this flag to build ``SERVER_SIDE_TOOLS``
dynamically instead of maintaining a hardcoded set.
"""
from __future__ import annotations

from maestro.contracts.mcp_types import MCPToolDef

GENERATION_TOOLS: list[MCPToolDef] = [
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
]
