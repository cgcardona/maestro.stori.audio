"""Stori DAW tool registry — metadata, MCP definitions, and derived sets.

Single entry point for all Stori tool vocabulary.  Consolidates the MCP
wire definitions, LLM tool schemas, per-tool metadata, and derived
classification sets (server-side vs. DAW, categories).
"""
from __future__ import annotations

from app.contracts.llm_types import ToolSchemaDict
from app.contracts.mcp_types import MCPToolDef
from app.core.tools.metadata import ToolMeta, ToolTier, ToolKind
from app.daw.ports import ToolMetaRegistry

from app.daw.stori.tools.project import PROJECT_TOOLS
from app.daw.stori.tools.track import TRACK_TOOLS
from app.daw.stori.tools.region import REGION_TOOLS
from app.daw.stori.tools.notes import NOTE_TOOLS
from app.daw.stori.tools.effects import EFFECTS_TOOLS
from app.daw.stori.tools.automation import AUTOMATION_TOOLS
from app.daw.stori.tools.midi_control import MIDI_CONTROL_TOOLS
from app.daw.stori.tools.generation import GENERATION_TOOLS
from app.daw.stori.tools.playback import PLAYBACK_TOOLS
from app.daw.stori.tools.ui import UI_TOOLS
from app.daw.stori.tool_schemas import TIER1_TOOLS, TIER2_TOOLS, ALL_TOOLS

ToolCategoryEntry = tuple[str, list[MCPToolDef]]
"""A (category_name, tools) pair — e.g. ``("track", TRACK_TOOLS)``."""


# ---------------------------------------------------------------------------
# MCP-format combined registry
# ---------------------------------------------------------------------------

MCP_TOOLS: list[MCPToolDef] = (
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

_CATEGORY_LISTS: list[ToolCategoryEntry] = [
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
    tool["name"]: category
    for category, tools in _CATEGORY_LISTS
    for tool in tools
}

SERVER_SIDE_TOOLS: set[str] = {
    tool["name"] for tool in MCP_TOOLS if tool.get("server_side", False)
}
DAW_TOOLS: set[str] = {
    tool["name"] for tool in MCP_TOOLS if not tool.get("server_side", False)
}


# ---------------------------------------------------------------------------
# Tool metadata registry (ToolMeta per tool)
# ---------------------------------------------------------------------------

_TOOL_META: ToolMetaRegistry = {}


def _register(meta: ToolMeta) -> None:
    _TOOL_META[meta.name] = meta


def build_tool_registry() -> ToolMetaRegistry:
    """Populate ``_TOOL_META`` with every Stori DAW tool and return it.

    Idempotent — returns the cached dict on subsequent calls.
    """
    if _TOOL_META:
        return _TOOL_META

    # Tier 1 generators
    _register(ToolMeta("stori_generate_midi", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False))

    # Tier 2 primitives — project
    _register(ToolMeta("stori_read_project", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_create_project", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="project", id_fields=("projectId",), reversible=False))
    _register(ToolMeta("stori_set_tempo", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_key", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_play", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_stop", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_playhead", ToolTier.TIER2, ToolKind.PRIMITIVE))

    _register(ToolMeta("stori_show_panel", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_zoom", ToolTier.TIER2, ToolKind.PRIMITIVE))

    _register(ToolMeta("stori_add_midi_track", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="track", id_fields=("trackId",)))
    _register(ToolMeta("stori_set_midi_program", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_name", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_mute_track", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_solo_track", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_volume", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_pan", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_color", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_icon", ToolTier.TIER2, ToolKind.PRIMITIVE))

    _register(ToolMeta("stori_add_midi_region", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="region", id_fields=("regionId",)))
    _register(ToolMeta("stori_add_notes", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_clear_notes", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_quantize_notes", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_apply_swing", ToolTier.TIER2, ToolKind.PRIMITIVE))

    _register(ToolMeta("stori_transpose_notes", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_move_region", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_duplicate_region", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_delete_region", ToolTier.TIER2, ToolKind.PRIMITIVE, reversible=False))

    _register(ToolMeta("stori_add_insert_effect", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_send", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_ensure_bus", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="bus", id_fields=("busId",)))
    _register(ToolMeta("stori_add_automation", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_midi_cc", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_pitch_bend", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_aftertouch", ToolTier.TIER2, ToolKind.PRIMITIVE))

    return _TOOL_META


def get_tool_meta(name: str) -> ToolMeta | None:
    """Return the ``ToolMeta`` for a tool by canonical name, or ``None``."""
    build_tool_registry()
    return _TOOL_META.get(name)


def tools_by_kind(kind: ToolKind) -> list[ToolSchemaDict]:
    """Return LLM schema dicts for all non-planner-only tools of the given kind."""
    build_tool_registry()
    allowed = {k for k, v in _TOOL_META.items() if v.kind == kind and not v.planner_only}
    return [t for t in ALL_TOOLS if t["function"]["name"] in allowed]


def tool_schema_by_name(name: str) -> ToolSchemaDict | None:
    """Return the raw LLM schema dict for a single tool by name, or ``None``."""
    for t in ALL_TOOLS:
        if t["function"]["name"] == name:
            return t
    return None
