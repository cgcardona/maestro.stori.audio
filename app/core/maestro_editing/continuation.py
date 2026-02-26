"""Composition continuation helpers — incomplete track detection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.contracts.json_types import ToolCallDict

if TYPE_CHECKING:
    from app.core.state_store import StateStore
    from app.core.prompt_parser import ParsedPrompt


def _get_incomplete_tracks(
    store: "StateStore",
    tool_calls_collected: list[ToolCallDict] | None = None,
) -> list[str]:
    """Return names of tracks that are missing regions or notes.

    Checks two conditions:
    1. Track has no regions at all
    2. Track has regions but none of them have notes — either from the current
       iteration's tool calls OR persisted in the StateStore from a prior
       iteration. Checking both sources prevents false "still needs notes"
       continuations that cause the model to clear and re-add valid content.

    Used by the composition continuation loop to detect premature LLM stops.
    """
    regions_with_notes_this_iter: set[str] = set()
    if tool_calls_collected:
        for tc in tool_calls_collected:
            if tc["tool"] == "stori_add_notes":
                rid = tc["params"].get("regionId")
                if rid:
                    regions_with_notes_this_iter.add(rid)

    incomplete: list[str] = []
    for track in store.registry.list_tracks():
        regions = store.registry.get_track_regions(track.id)
        if not regions:
            incomplete.append(track.name)
        elif not any(
            r.id in regions_with_notes_this_iter or bool(store.get_region_notes(r.id))
            for r in regions
        ):
            incomplete.append(track.name)
    return incomplete


def _get_missing_expressive_steps(
    parsed: "ParsedPrompt" | None,
    tool_calls_collected: list[ToolCallDict],
) -> list[str]:
    """Return human-readable descriptions of expressive steps not yet executed.

    Checks Effects, MidiExpressiveness, and Automation blocks from the parsed
    STORI PROMPT against the tool calls already made this session. Returns an
    empty list when everything has been called (or when the parsed prompt has
    no expressive blocks).
    """
    if parsed is None:
        return []

    # Keys are lowercased by the parser (prompt_parser.py line 177)
    extensions: dict[str, Any] = parsed.extensions or {}
    called_tools = {tc["tool"] for tc in tool_calls_collected}

    missing: list[str] = []

    if extensions.get("effects") and "stori_add_insert_effect" not in called_tools:
        missing.append(
            "Effects block present but stori_add_insert_effect was never called. "
            "Call stori_add_insert_effect for each effects entry (compressor, reverb, eq, etc.)."
        )

    me = extensions.get("midiexpressiveness") or {}
    if me.get("cc_curves") and "stori_add_midi_cc" not in called_tools:
        missing.append(
            "MidiExpressiveness.cc_curves present but stori_add_midi_cc was never called. "
            "Call stori_add_midi_cc for each cc_curves entry."
        )

    if me.get("sustain_pedal") and "stori_add_midi_cc" not in called_tools:
        missing.append(
            "MidiExpressiveness.sustain_pedal present but stori_add_midi_cc (CC 64) was never called. "
            "Call stori_add_midi_cc with cc=64 on the target region."
        )

    if me.get("pitch_bend") and "stori_add_pitch_bend" not in called_tools:
        missing.append(
            "MidiExpressiveness.pitch_bend present but stori_add_pitch_bend was never called. "
            "Call stori_add_pitch_bend with slide events on the target region."
        )

    if extensions.get("automation") and "stori_add_automation" not in called_tools:
        missing.append(
            "Automation block present but stori_add_automation was never called. "
            "Call stori_add_automation(trackId=..., parameter='Volume', points=[...]) "
            "for each lane. Use trackId (NOT 'target'). parameter must be a canonical "
            "string like 'Volume', 'Pan', 'Synth Cutoff', 'Expression (CC11)', etc."
        )

    effects_data = extensions.get("effects") or {}
    if isinstance(effects_data, dict):
        tracks_needing_reverb = [
            k for k, v in effects_data.items()
            if isinstance(v, dict) and "reverb" in v
        ]
        if len(tracks_needing_reverb) >= 2 and "stori_ensure_bus" not in called_tools:
            missing.append(
                f"Multiple tracks ({', '.join(tracks_needing_reverb)}) need reverb — "
                "use a shared Reverb bus: call stori_ensure_bus(name='Reverb') once, "
                "then stori_add_send(trackId=X, busId=$N.busId, levelDb=-6) for each track."
            )

    return missing
