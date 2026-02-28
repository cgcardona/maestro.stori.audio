"""MCP tool access — combined registry for DAW tools and MuseHub browsing tools.

DAW tool definitions (``stori_*``) come from ``maestro.daw.stori.tool_registry``.
MuseHub browsing tools (``musehub_*``) are defined here and are always
server-side (never forwarded to the DAW).

``MCP_TOOLS``         — full combined list (DAW + MuseHub), used by the MCP server.
``SERVER_SIDE_TOOLS`` — names of all server-side tools (generation + musehub).
``DAW_TOOLS``         — names of all DAW-forwarded tools.
``TOOL_CATEGORIES``   — maps every tool name to its category string.
``MUSEHUB_TOOL_NAMES``— set of ``musehub_*`` tool names for routing.
"""
from __future__ import annotations

from maestro.contracts.mcp_types import MCPToolDef
from maestro.daw.stori.tool_registry import (
    MCP_TOOLS as _DAW_MCP_TOOLS,
    TOOL_CATEGORIES as _DAW_TOOL_CATEGORIES,
    SERVER_SIDE_TOOLS as _DAW_SERVER_SIDE_TOOLS,
    DAW_TOOLS,
)
from maestro.daw.stori.tools.project import PROJECT_TOOLS
from maestro.daw.stori.tools.track import TRACK_TOOLS
from maestro.daw.stori.tools.region import REGION_TOOLS
from maestro.daw.stori.tools.notes import NOTE_TOOLS
from maestro.daw.stori.tools.effects import EFFECTS_TOOLS
from maestro.daw.stori.tools.automation import AUTOMATION_TOOLS
from maestro.daw.stori.tools.midi_control import MIDI_CONTROL_TOOLS
from maestro.daw.stori.tools.generation import GENERATION_TOOLS
from maestro.daw.stori.tools.playback import PLAYBACK_TOOLS
from maestro.daw.stori.tools.ui import UI_TOOLS
from maestro.mcp.tools.musehub import MUSEHUB_TOOLS, MUSEHUB_TOOL_NAMES

# Combined tool list: DAW tools first, then MuseHub browsing tools.
MCP_TOOLS: list[MCPToolDef] = _DAW_MCP_TOOLS + MUSEHUB_TOOLS

# MuseHub tools are always server-side — merge with the DAW server-side set.
SERVER_SIDE_TOOLS: set[str] = _DAW_SERVER_SIDE_TOOLS | MUSEHUB_TOOL_NAMES

# Category map: DAW categories + musehub category for all MuseHub tools.
TOOL_CATEGORIES: dict[str, str] = {
    **_DAW_TOOL_CATEGORIES,
    **{name: "musehub" for name in MUSEHUB_TOOL_NAMES},
}

__all__ = [
    "PROJECT_TOOLS",
    "TRACK_TOOLS",
    "REGION_TOOLS",
    "NOTE_TOOLS",
    "EFFECTS_TOOLS",
    "AUTOMATION_TOOLS",
    "MIDI_CONTROL_TOOLS",
    "GENERATION_TOOLS",
    "PLAYBACK_TOOLS",
    "UI_TOOLS",
    "MUSEHUB_TOOLS",
    "MUSEHUB_TOOL_NAMES",
    "MCP_TOOLS",
    "TOOL_CATEGORIES",
    "SERVER_SIDE_TOOLS",
    "DAW_TOOLS",
]
