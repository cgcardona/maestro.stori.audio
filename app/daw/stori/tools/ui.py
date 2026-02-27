"""UI control MCP tool definitions."""
from __future__ import annotations

from app.contracts.mcp_types import MCPToolDef

UI_TOOLS: list[MCPToolDef] = [
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
        "description": "set editor zoom (percent).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "zoomPercent": {"type": "number", "description": "Zoom percentage"}
            },
            "required": ["zoomPercent"]
        }
    },
]
