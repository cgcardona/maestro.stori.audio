"""Tool metadata registry — build, query, and look up ToolMeta entries."""

from __future__ import annotations

from typing import Any, Optional, cast

from app.core.tools.metadata import ToolMeta, ToolTier, ToolKind
from app.core.tools.definitions import ALL_TOOLS, TIER1_TOOLS, TIER2_TOOLS  # noqa: F401 (re-exported)

_TOOL_META: dict[str, ToolMeta] = {}


def _register(meta: ToolMeta) -> None:
    _TOOL_META[meta.name] = meta


def build_tool_registry() -> dict[str, ToolMeta]:
    if _TOOL_META:
        return _TOOL_META

    # Tier 1 generators
    _register(ToolMeta("stori_generate_midi", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False))
    _register(ToolMeta("stori_generate_drums", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False, deprecated=True))
    _register(ToolMeta("stori_generate_bass", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False, deprecated=True))
    _register(ToolMeta("stori_generate_chords", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False, deprecated=True))
    _register(ToolMeta("stori_generate_melody", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False, deprecated=True))

    # Tier 2 primitives
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

    _register(ToolMeta("stori_add_insert_effect", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_send", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_ensure_bus", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="bus", id_fields=("busId",)))
    _register(ToolMeta("stori_add_automation", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_midi_cc", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_pitch_bend", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_aftertouch", ToolTier.TIER2, ToolKind.PRIMITIVE))

    return _TOOL_META


def get_tool_meta(name: str) -> Optional[ToolMeta]:
    build_tool_registry()
    return _TOOL_META.get(name)


def tools_by_kind(kind: ToolKind) -> list[dict[str, Any]]:
    build_tool_registry()
    allowed = {k for k, v in _TOOL_META.items() if v.kind == kind and not v.planner_only}
    return [t for t in ALL_TOOLS if t["function"]["name"] in allowed]


def tool_schema_by_name(name: str) -> Optional[dict[str, Any]]:
    for t in ALL_TOOLS:
        if t["function"]["name"] == name:
            return cast(dict[str, Any], t)
    return None
