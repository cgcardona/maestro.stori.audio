"""Composition summary helpers for the Agent Teams coordinator."""

from __future__ import annotations

from typing import Any, Optional

from app.core.maestro_agent_teams.constants import _CC_NAMES


def _build_composition_summary(
    tool_calls_collected: list[dict[str, Any]],
    tempo: Optional[float] = None,
    key: Optional[str] = None,
    style: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregate composition metadata for the summary.final SSE event.

    Recognises the synthetic ``_reused_track`` tool name injected by the
    coordinator for tracks that already existed so the frontend can display
    "reused" vs "created" labels correctly.

    When ``tempo``, ``key``, or ``style`` are provided, a human-readable
    ``text`` field is included so the frontend can display a completion
    paragraph below the agent execution feed.
    """
    tracks_created: list[dict[str, Any]] = []
    tracks_reused: list[dict[str, Any]] = []
    regions_created = 0
    notes_generated = 0
    effects_added: list[dict[str, str]] = []
    sends_created = 0
    cc_counts: dict[int, str] = {}
    automation_lanes = 0

    for tc in tool_calls_collected:
        name = tc.get("tool", "")
        params = tc.get("params", {})
        if name == "stori_add_midi_track":
            tracks_created.append({
                "name": params.get("name", ""),
                "instrument": params.get("_gmInstrumentName") or params.get("drumKitId") or "Unknown",
                "trackId": params.get("trackId", ""),
            })
        elif name == "_reused_track":
            tracks_reused.append({
                "name": params.get("name", ""),
                "trackId": params.get("trackId", ""),
            })
        elif name == "stori_add_midi_region":
            regions_created += 1
        elif name == "stori_add_notes":
            notes_generated += len(params.get("notes", []))
        elif name == "stori_generate_midi":
            notes_generated += params.get("_notesGenerated", 0)
        elif name == "stori_add_insert_effect":
            effects_added.append({
                "trackId": params.get("trackId", ""),
                "type": params.get("effectType") or params.get("type", ""),
            })
        elif name == "stori_add_send":
            sends_created += 1
        elif name == "stori_add_midi_cc":
            cc_num = int(params.get("cc", 0))
            cc_counts[cc_num] = _CC_NAMES.get(cc_num, f"CC {cc_num}")
        elif name == "stori_add_automation":
            automation_lanes += 1

    result: dict[str, Any] = {
        "tracksCreated": tracks_created,
        "tracksReused": tracks_reused,
        "trackCount": len(tracks_created) + len(tracks_reused),
        "regionsCreated": regions_created,
        "notesGenerated": notes_generated,
        "effectsAdded": effects_added,
        "effectCount": len(effects_added),
        "sendsCreated": sends_created,
        "ccEnvelopes": [{"cc": k, "name": v} for k, v in sorted(cc_counts.items())],
        "automationLanes": automation_lanes,
    }
    result["text"] = _compose_summary_text(
        result, tempo=tempo, key=key, style=style,
    )
    return result


def _compose_summary_text(
    summary: dict[str, Any],
    tempo: Optional[float] = None,
    key: Optional[str] = None,
    style: Optional[str] = None,
) -> str:
    """Build a concise natural-language summary of a completed composition."""
    all_tracks = summary.get("tracksCreated", []) + summary.get("tracksReused", [])
    track_count = len(all_tracks)
    track_names = [t.get("name", "") for t in all_tracks if t.get("name")]
    notes = summary.get("notesGenerated", 0)
    regions = summary.get("regionsCreated", 0)
    effects = summary.get("effectCount", 0)

    parts: list[str] = []
    verb = "Created"
    if summary.get("tracksReused"):
        verb = "Extended"

    desc = f"{verb} a"
    if style and style != "default":
        desc += f" {style}"
    desc += " composition"
    if key:
        desc += f" in {key}"
    if tempo:
        bpm = int(tempo) if tempo == int(tempo) else tempo
        desc += f" at {bpm} BPM"
    parts.append(desc)

    if track_count and track_names:
        if len(track_names) <= 4:
            if len(track_names) == 1:
                names_str = track_names[0]
            elif len(track_names) == 2:
                names_str = f"{track_names[0]} and {track_names[1]}"
            else:
                names_str = ", ".join(track_names[:-1]) + f", and {track_names[-1]}"
            parts.append(f"with {track_count} tracks \u2014 {names_str}")
        else:
            parts.append(f"with {track_count} tracks")

    stats: list[str] = []
    if notes:
        stats.append(f"{notes} notes")
    if regions:
        stats.append(f"{regions} {'region' if regions == 1 else 'regions'}")
    if effects:
        stats.append(f"{effects} {'effect' if effects == 1 else 'effects'}")
    if stats:
        parts.append("totaling " + " across ".join(stats[:2]))
        if len(stats) > 2:
            parts[-1] += f" with {stats[2]}"

    return " ".join(parts) + "."
