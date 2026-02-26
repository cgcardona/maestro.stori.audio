"""
Entity and project context for LLM prompts (Cursor-of-DAWs).

Centralizes the project snapshot and entity listing injected into EDITING
and other tool-calling flows so the LLM can reference tracks/regions/buses
by ID or by name (the server resolves names → IDs).
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.contracts.project_types import ProjectContext
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


_ROLE_KEYWORDS: dict[str, list[str]] = {
    "drums": ["drum", "kit", "percussion", "beat", "trap"],
    "bass": ["bass"],
    "pads": ["pad", "atmosphere", "ambient", "string", "choir", "cathedral", "organ", "church"],
    "chords": ["chord", "piano", "rhodes", "keys", "keyboard", "guitar", "pluck", "strum"],
    "melody": ["melody", "lead", "solo", "flute", "sax", "violin", "trumpet", "vocal", "vox", "voice"],
    "arp": ["arp", "arpegg"],
    "fx": ["fx", "effect", "noise", "texture"],
}

_GM_ROLE_RANGES: list[tuple[int, int, str]] = [
    (0, 7, "chords"),     # Piano family
    (16, 23, "pads"),     # Organ family
    (24, 31, "chords"),   # Guitar family
    (32, 39, "bass"),     # Bass family
    (40, 55, "melody"),   # Strings / orchestral
    (56, 79, "melody"),   # Brass / Winds
    (80, 87, "lead"),     # Synth Leads
    (88, 103, "pads"),    # Synth Pads / FX
]


def infer_track_role(
    track_name: str,
    gm_program: int | None,
    drum_kit_id: str | None,
) -> str:
    """Infer the musical role for a track from its instrument metadata and name.

    Returns one of: drums, bass, pads, chords, melody, arp, lead, fx.
    Defaults to "melody" when no match is found.
    """
    if drum_kit_id:
        return "drums"

    name_lower = track_name.lower()

    # Name-keyword match (highest priority — explicit user intent)
    for role, keywords in _ROLE_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return role

    # GM program range fallback
    if gm_program is not None:
        for lo, hi, role in _GM_ROLE_RANGES:
            if lo <= gm_program <= hi:
                return role

    return "melody"


def format_project_context(project: ProjectContext) -> str:
    """Format the project snapshot from the request body into a concise,
    human-readable string for the LLM system message.

    This is the **authoritative** project context — built from the live
    project state at request time, not from any cache.
    """
    name = project.get("name", "Untitled")
    tempo = project.get("tempo", 120)
    key = project.get("key", "C")
    _raw_ts: Any = project.get("timeSignature")
    time_sig: str | dict[str, Any] = _raw_ts or "4/4"
    if isinstance(time_sig, dict):
        time_sig = f"{time_sig.get('numerator', 4)}/{time_sig.get('denominator', 4)}"
    tracks = project.get("tracks", [])

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
            drum_kit = track.get("drumKitId")
            gm = track.get("gmProgram")

            if drum_kit:
                instrument = f"Drums ({drum_kit})"
            else:
                instrument = _gm_instrument_name(gm) or "Unknown"

            role = track.get("role") or infer_track_role(tname, gm, drum_kit)

            regions = track.get("regions", [])
            if not regions:
                region_desc = "no regions"
            else:
                parts: list[str] = []
                for r in regions:
                    rname = r.get("name", "Untitled")
                    rid = r.get("id", "?")
                    start = r.get("startBeat", 0)
                    dur = r.get("durationBeats", 0)
                    nc = r.get("noteCount", "?")
                    parts.append(
                        f'"{rname}" [id={rid}] '
                        f"({start}–{start + dur} beats, {nc} notes)"
                    )
                region_desc = "; ".join(parts)

            lines.append(
                f"  {i}. {tname} ({instrument}) [role={role}] [trackId={tid}] — {region_desc}"
            )

    lines.append("")
    lines.append(
        "Use the track IDs and region IDs above when calling tools. "
        "Do NOT use IDs from previous messages or conversations."
    )
    if tracks:
        lines.append(
            "NEW SECTION RULE: When adding a new section (verse, chorus, bridge, etc.), "
            "add new MIDI regions to EXISTING tracks that match the required role. "
            "Match roles from the prompt (drums, bass, pads, chords, melody) to the "
            "[role=X] tags above. Only create a NEW track if no existing track has that role."
        )

    return "\n".join(lines)


def build_entity_context_for_llm(store: "StateStore") -> str:
    """
    Build the "Available entities in the project" string for LLM system messages.

    Used by the EDITING handler so the model can reference existing entities
    by trackId/regionId/busId or by trackName/regionName (server resolves).

    Each region includes noteCount so the model knows whether notes exist and
    avoids destructive clear-and-redo loops.
    """
    registry = store.registry
    tracks_info = [{"name": t.name, "id": t.id} for t in registry.list_tracks()]
    regions_info = []
    for r in registry.list_regions():
        region_entry: dict[str, Any] = {
            "name": r.name,
            "id": r.id,
            "trackId": r.parent_id,
            "noteCount": len(store.get_region_notes(r.id)),
        }
        region_entry["startBeat"] = r.metadata.start_beat
        region_entry["durationBeats"] = r.metadata.duration_beats
        regions_info.append(region_entry)
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
        f'or stori_add_midi_region(trackName="{example_track_name}", ...)\n\n'
        "IMPORTANT: Check noteCount before calling stori_add_notes — if a region already\n"
        "has notes, do not re-add them. Only append new notes or skip if content is complete."
    )
