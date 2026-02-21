"""Three-phase grouping of tool calls for parallel instrument execution."""

from __future__ import annotations

from typing import Optional

from app.core.expansion import ToolCall

_PHASE1_TOOLS: set[str] = {"stori_set_tempo", "stori_set_key"}
_PHASE3_TOOLS: set[str] = {
    "stori_ensure_bus", "stori_add_send",
    "stori_set_track_volume", "stori_set_track_pan",
    "stori_mute_track", "stori_solo_track",
}


def _get_instrument_for_call(call: ToolCall) -> Optional[str]:
    """Extract the instrument/track name a tool call belongs to.

    Returns None for project-level (setup/mixing) calls.
    """
    if call.name == "stori_add_midi_track":
        return call.params.get("name")
    if call.name in _PHASE1_TOOLS | _PHASE3_TOOLS:
        return None
    return (
        call.params.get("trackName")
        or call.params.get("name")
        or (
            call.params.get("role", "").capitalize()
            if call.name.startswith("stori_generate") else None
        )
    )


def _group_into_phases(
    tool_calls: list[ToolCall],
) -> tuple[
    list[ToolCall],
    dict[str, list[ToolCall]],
    list[str],
    list[ToolCall],
]:
    """Split tool calls into three execution phases.

    Returns:
        (phase1_setup, instrument_groups, instrument_order, phase3_mixing)

    Phase 1 — project-level setup (tempo, key).
    Phase 2 — per-instrument groups keyed by lowercase track name.
              ``instrument_order`` preserves first-seen ordering.
    Phase 3 — shared buses, sends, volume/pan adjustments.
    """
    phase1: list[ToolCall] = []
    groups: dict[str, list[ToolCall]] = {}
    order: list[str] = []
    phase3: list[ToolCall] = []

    for call in tool_calls:
        if call.name in _PHASE1_TOOLS:
            phase1.append(call)
        elif call.name in _PHASE3_TOOLS:
            phase3.append(call)
        else:
            instrument = _get_instrument_for_call(call)
            if instrument:
                key = instrument.lower()
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(call)
            else:
                phase3.append(call)

    return phase1, groups, order, phase3
