"""Stori DAW tool → execution phase mapping.

Classifies every tool into one of three execution phases for the
parallel instrument pipeline:

  * ``setup``      — project-level (tempo, key)
  * ``instrument`` — per-instrument (track, region, notes, generation)
  * ``mixing``     — shared buses, sends, volume/pan
"""
from __future__ import annotations

from typing import NamedTuple

from maestro.core.expansion import ToolCall

InstrumentGroups = dict[str, list[ToolCall]]
"""Tool calls grouped by instrument name (lowercased).

Key is the normalised instrument name (e.g. ``"drums"``, ``"bass"``).
Value is the ordered list of tool calls belonging to that instrument.
"""

_SETUP_TOOLS: frozenset[str] = frozenset({"stori_set_tempo", "stori_set_key"})
_MIXING_TOOLS: frozenset[str] = frozenset({
    "stori_ensure_bus", "stori_add_send",
    "stori_set_track_volume", "stori_set_track_pan",
    "stori_mute_track", "stori_solo_track",
})


def phase_for_tool(name: str) -> str:
    """Return ``"setup"``, ``"instrument"``, or ``"mixing"``."""
    if name in _SETUP_TOOLS:
        return "setup"
    if name in _MIXING_TOOLS:
        return "mixing"
    return "instrument"


def _str_param(v: object, default: str = "") -> str:
    """Safely narrow an object param value to str."""
    return v if isinstance(v, str) else default


def get_instrument_for_call(call: ToolCall) -> str | None:
    """Extract the instrument/track name a tool call belongs to.

    Returns ``None`` for project-level (setup/mixing) calls.
    """
    if call.name == "stori_add_midi_track":
        v = call.params.get("name")
        return _str_param(v) or None
    if call.name in _SETUP_TOOLS | _MIXING_TOOLS:
        return None
    name = (
        _str_param(call.params.get("trackName"))
        or _str_param(call.params.get("name"))
    )
    if name:
        return name
    if call.name.startswith("stori_generate"):
        role = _str_param(call.params.get("role"))
        return role.capitalize() if role else None
    return None


class PhaseSplit(NamedTuple):
    """Result of splitting tool calls into three execution phases.

    Attributes:
        setup: Phase 1 — project-level calls (tempo, key).
        instruments: Phase 2 — per-instrument tool calls grouped by name.
        instrument_order: Insertion-order list of instrument keys in ``instruments``.
        mixing: Phase 3 — shared bus routing, volume, pan.
    """

    setup: list[ToolCall]
    instruments: InstrumentGroups
    instrument_order: list[str]
    mixing: list[ToolCall]


def group_into_phases(tool_calls: list[ToolCall]) -> PhaseSplit:
    """Split tool calls into three execution phases."""
    phase1: list[ToolCall] = []
    groups: InstrumentGroups = {}
    order: list[str] = []
    phase3: list[ToolCall] = []

    for call in tool_calls:
        if call.name in _SETUP_TOOLS:
            phase1.append(call)
        elif call.name in _MIXING_TOOLS:
            phase3.append(call)
        else:
            instrument = get_instrument_for_call(call)
            if instrument:
                key = instrument.lower()
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(call)
            else:
                phase3.append(call)

    return PhaseSplit(phase1, groups, order, phase3)
