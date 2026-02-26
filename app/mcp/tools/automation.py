"""Automation MCP tool definitions."""
from __future__ import annotations

from app.contracts.mcp_types import MCPToolDef

AUTOMATION_TOOLS: list[MCPToolDef] = [
    {
        "name": "stori_add_automation",
        "description": (
            "Add automation to a track parameter. "
            "trackId identifies the track. parameter is the exact canonical string "
            "(e.g. 'Volume', 'Pan', 'EQ Low', 'Mod Wheel (CC1)', 'Pitch Bend', "
            "'Synth Cutoff'). points is an array of {beat, value, curve?} objects. "
            "Volume/Pan/EQ/Synth params use 0.0–1.0; MIDI CC params use 0–127."
        ),
        "inputSchema": {
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
]
