"""
Entity and project context for LLM prompts (Cursor-of-DAWs).

Centralizes the project snapshot and entity listing injected into EDITING
and other tool-calling flows so the LLM can reference tracks/regions/buses
by ID or by name (the server resolves names → IDs).
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.state_store import StateStore


# ---------------------------------------------------------------------------
# GM program number → human-readable instrument name (common programs)
# ---------------------------------------------------------------------------
_GM_NAMES: dict[int, str] = {
    0: "Acoustic Grand Piano", 1: "Bright Piano", 2: "Electric Grand Piano",
    4: "Rhodes Piano", 5: "Chorus Piano", 6: "Harpsichord", 7: "Clavinet",
    16: "Drawbar Organ", 19: "Church Organ", 24: "Nylon Guitar",
    25: "Steel Guitar", 26: "Jazz Guitar", 27: "Clean Electric Guitar",
    29: "Overdriven Guitar", 30: "Distortion Guitar", 32: "Acoustic Bass",
    33: "Electric Bass (finger)", 34: "Electric Bass (pick)", 35: "Fretless Bass",
    36: "Slap Bass 1", 38: "Synth Bass 1", 39: "Synth Bass 2",
    40: "Violin", 41: "Viola", 42: "Cello", 48: "String Ensemble",
    56: "Trumpet", 57: "Trombone", 61: "French Horn", 65: "Alto Sax",
    66: "Tenor Sax", 67: "Baritone Sax", 73: "Flute", 74: "Recorder",
    80: "Square Lead", 81: "Saw Lead", 88: "Pad (New Age)", 89: "Warm Pad",
    105: "Banjo", 110: "Fiddle",
}


def _gm_instrument_name(gm_program: int | None) -> str | None:
    """Return a human-readable instrument name for a GM program number."""
    if gm_program is None:
        return None
    return _GM_NAMES.get(gm_program, f"GM #{gm_program}")


def format_project_context(project: dict[str, Any]) -> str:
    """Format the project snapshot from the request body into a concise,
    human-readable string for the LLM system message.

    This is the **authoritative** project context — built from the live
    project state at request time, not from any cache.
    """
    name = project.get("name", "Untitled")
    tempo = project.get("tempo", 120)
    key = project.get("key", "C")
    time_sig = project.get("time_signature") or project.get("timeSignature") or "4/4"
    if isinstance(time_sig, dict):
        time_sig = f"{time_sig.get('numerator', 4)}/{time_sig.get('denominator', 4)}"
    tracks: list[dict[str, Any]] = project.get("tracks", [])

    lines: list[str] = [
        "Current project state (source of truth — use these IDs for tool calls):",
        f"- Name: \"{name}\"",
        f"- Tempo: {tempo} BPM",
        f"- Key: {key}",
        f"- Time Signature: {time_sig}",
    ]

    if not tracks:
        lines.append("- Tracks: (none — empty project, create tracks from scratch)")
    else:
        lines.append(f"- Tracks: {len(tracks)}")
        for i, track in enumerate(tracks, 1):
            tid = track.get("id", "?")
            tname = track.get("name", "Untitled")
            drum_kit = track.get("drum_kit_id")
            gm = track.get("gm_program")

            if drum_kit:
                instrument = f"Drums ({drum_kit})"
            else:
                instrument = _gm_instrument_name(gm) or "Unknown"

            regions: list[dict[str, Any]] = track.get("regions", [])
            if not regions:
                region_desc = "no regions"
            else:
                parts: list[str] = []
                for r in regions:
                    rname = r.get("name", "Untitled")
                    rid = r.get("id", "?")
                    start = r.get("start_beat", r.get("startBeat", 0))
                    dur = r.get("duration_beats", r.get("durationBeats", 0))
                    nc = r.get("note_count", r.get("noteCount", "?"))
                    parts.append(
                        f'"{rname}" [id={rid}] '
                        f"({start}–{start + dur} beats, {nc} notes)"
                    )
                region_desc = "; ".join(parts)

            lines.append(f"  {i}. {tname} ({instrument}) [trackId={tid}] — {region_desc}")

    lines.append("")
    lines.append(
        "Use the track IDs and region IDs above when calling tools. "
        "Do NOT use IDs from previous messages or conversations."
    )

    return "\n".join(lines)


def build_entity_context_for_llm(store: "StateStore") -> str:
    """
    Build the "Available entities in the project" string for LLM system messages.

    Used by the EDITING handler so the model can reference existing entities
    by trackId/regionId/busId or by trackName/regionName (server resolves).
    """
    registry = store.registry
    tracks_info = [{"name": t.name, "id": t.id} for t in registry.list_tracks()]
    regions_info = [
        {"name": r.name, "id": r.id, "trackId": r.parent_id}
        for r in registry.list_regions()
    ]
    buses_info = [{"name": b.name, "id": b.id} for b in registry.list_buses()]

    example_track_id = tracks_info[0]["id"] if tracks_info else "abc-123"
    example_track_name = tracks_info[0]["name"] if tracks_info else "My Track"

    return (
        "Available entities in the project:\n"
        f"- Tracks: {tracks_info or '(none)'}\n"
        f"- Regions: {regions_info or '(none)'}\n"
        f"- Buses: {buses_info or '(none)'}\n\n"
        "When referencing existing entities, you can use either:\n"
        "1. The trackId/regionId/busId directly (preferred), OR\n"
        "2. The trackName/regionName - the server will resolve it to the correct ID.\n"
        f'Example: stori_add_midi_region(trackId="{example_track_id}", ...) '
        f'or stori_add_midi_region(trackName="{example_track_name}", ...)'
    )
