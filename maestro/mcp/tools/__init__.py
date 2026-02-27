"""MCP tool access — delegates to the Stori DAW adapter's tool registry.

MCP transport code imports tool definitions from here.  The canonical
definitions live in ``app.daw.stori.tool_registry``.
"""
from __future__ import annotations

from maestro.daw.stori.tool_registry import (
    MCP_TOOLS,
    TOOL_CATEGORIES,
    SERVER_SIDE_TOOLS,
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
