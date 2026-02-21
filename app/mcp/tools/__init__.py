"""
MCP Tool Definitions for Stori DAW.

These tools are exposed via MCP for LLMs to control the DAW.
They follow the MCP tool schema format.

Import from this package for the combined registry, or from the individual
category modules for focused access.
"""

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
from app.mcp.tools.registry import (
    MCP_TOOLS,
    TOOL_CATEGORIES,
    SERVER_SIDE_TOOLS,
    DAW_TOOLS,
)

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
    "MCP_TOOLS",
    "TOOL_CATEGORIES",
    "SERVER_SIDE_TOOLS",
    "DAW_TOOLS",
]
