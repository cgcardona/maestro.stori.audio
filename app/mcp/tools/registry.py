"""MCP tool registry — combines all category lists into the master lists."""
from __future__ import annotations

from typing import Any

from app.mcp.tools.project import PROJECT_TOOLS
from app.mcp.tools.track import TRACK_TOOLS
from app.mcp.tools.region import REGION_TOOLS
from app.mcp.tools.notes import NOTE_TOOLS
from app.mcp.tools.effects import EFFECTS_TOOLS
from app.mcp.tools.automation import AUTOMATION_TOOLS
from app.mcp.tools.midi_control import MIDI_CONTROL_TOOLS
from app.mcp.tools.generation import GENERATION_TOOLS
from app.mcp.tools.playback import PLAYBACK_TOOLS
from app.mcp.tools.ui import UI_TOOLS

MCP_TOOLS = (
    PROJECT_TOOLS
    + TRACK_TOOLS
    + REGION_TOOLS
    + NOTE_TOOLS
    + EFFECTS_TOOLS
    + AUTOMATION_TOOLS
    + MIDI_CONTROL_TOOLS
    + GENERATION_TOOLS
    + PLAYBACK_TOOLS
    + UI_TOOLS
)

_CATEGORY_LISTS: list[tuple[str, list[dict[str, Any]]]] = [
    ("project", PROJECT_TOOLS),
    ("track", TRACK_TOOLS),
    ("region", REGION_TOOLS),
    ("note", NOTE_TOOLS),
    ("effects", EFFECTS_TOOLS),
    ("automation", AUTOMATION_TOOLS),
    ("midi_control", MIDI_CONTROL_TOOLS),
    ("generation", GENERATION_TOOLS),
    ("playback", PLAYBACK_TOOLS),
    ("ui", UI_TOOLS),
]

TOOL_CATEGORIES: dict[str, str] = {
    str(tool["name"]): category
    for category, tools in _CATEGORY_LISTS
    for tool in tools
}

SERVER_SIDE_TOOLS: set[str] = {
    str(tool["name"]) for tool in MCP_TOOLS if tool.get("server_side", False)
}
DAW_TOOLS: set[str] = {
    str(tool["name"]) for tool in MCP_TOOLS if not tool.get("server_side", False)
}
