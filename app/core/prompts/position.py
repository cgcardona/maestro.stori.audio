"""Sequential arrangement helpers: position resolution and context injection."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.prompt_parser import AfterSpec, PositionSpec


def _tracks_matching(label: str | None, tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tracks whose name or any region name contains label."""
    if not label:
        return tracks
    label = label.lower()
    matching: list[dict[str, Any]] = []
    for track in tracks:
        if label in track.get("name", "").lower():
            matching.append(track)
        else:
            for region in track.get("regions", []):
                if label in region.get("name", "").lower():
                    matching.append(track)
                    break
    return matching


def _max_end_beat(tracks: list[dict[str, Any]]) -> float:
    """Maximum (startBeat + durationBeats) across all regions in tracks."""
    end = 0.0
    for track in tracks:
        for region in track.get("regions", []):
            end = max(end, region.get("startBeat", 0.0) + region.get("durationBeats", 0.0))
    return end


def _min_start_beat(tracks: list[dict[str, Any]]) -> float:
    """Minimum startBeat across all regions in tracks."""
    starts = [
        region.get("startBeat", 0.0)
        for track in tracks
        for region in track.get("regions", [])
    ]
    return min(starts) if starts else 0.0


def resolve_position(pos: "PositionSpec", project_context: dict[str, Any]) -> float:
    """Resolve a PositionSpec to a concrete start beat using the project state.

    Relationship semantics:
      absolute  → beat value, apply offset
      last      → max end beat across all regions, apply offset
      after X   → max end beat of X's tracks/regions, apply offset
      before X  → min start beat of X's tracks/regions, apply offset
      alongside X → min start beat of X (parallel entry), apply offset
      between X Y → max end beat of X (gap start), apply offset
      within X  → min start beat of X, apply offset
    """
    tracks: list[dict[str, Any]] = project_context.get("tracks", [])

    if pos.kind == "absolute":
        return float((pos.beat or 0.0) + pos.offset)
    if pos.kind == "last":
        return _max_end_beat(tracks) + pos.offset

    ref_tracks = _tracks_matching(pos.ref, tracks)
    if not ref_tracks and pos.ref:
        ref_tracks = tracks

    if pos.kind == "after":
        return _max_end_beat(ref_tracks) + pos.offset
    if pos.kind == "before":
        return _min_start_beat(ref_tracks) + pos.offset
    if pos.kind == "alongside":
        return _min_start_beat(ref_tracks) + pos.offset
    if pos.kind == "within":
        return _min_start_beat(ref_tracks) + pos.offset
    if pos.kind == "between":
        end_of_ref = _max_end_beat(ref_tracks)
        if pos.ref2:
            ref2_tracks = _tracks_matching(pos.ref2, tracks)
            start_of_ref2 = _min_start_beat(ref2_tracks) if ref2_tracks else end_of_ref
            gap = (start_of_ref2 - end_of_ref) / 2
            return end_of_ref + gap + pos.offset
        return end_of_ref + pos.offset


def resolve_after_beat(after: "AfterSpec", project_context: dict[str, Any]) -> float:
    """Backwards-compatible wrapper — delegates to resolve_position."""
    return resolve_position(after, project_context)


def sequential_context(
    start_beat: float,
    section_name: str | None = None,
    pos: "PositionSpec" | None = None,
) -> str:
    """Return an LLM instruction block for arrangement placement.

    Injected into the system prompt whenever a Position: (or After:) field
    is present. Communicates the resolved beat and the musical intent of the
    positioning relationship.
    """
    beat_int = int(start_beat)
    lines = ["═════════════════════════════════════", "ARRANGEMENT POSITION"]

    if pos is not None:
        kind = pos.kind
        ref = pos.ref or ""
        if kind == "absolute":
            lines.append(f"Absolute placement — start at beat {beat_int}.")
        elif kind == "last":
            lines.append(f"Append after all existing content — start at beat {beat_int}.")
        elif kind == "after":
            lines.append(f"Sequential — starts after '{ref}' ends, at beat {beat_int}.")
        elif kind == "before":
            verb = "pickup" if pos.offset < 0 else "insert"
            lines.append(f"Anticipatory {verb} — starts before '{ref}' at beat {beat_int}.")
            if pos.offset < 0:
                lines.append(
                    f"This is a {abs(int(pos.offset))}-beat lead-in into '{ref}'. "
                    "The material should feel like a natural pickup."
                )
        elif kind == "alongside":
            lines.append(
                f"Parallel layer — starts alongside '{ref}' at beat {beat_int}. "
                "Add new tracks; do NOT move existing tracks."
            )
        elif kind == "between":
            ref2 = pos.ref2 or "next section"
            lines.append(
                f"Transition bridge — fills the gap between '{ref}' and '{ref2}', "
                f"starting at beat {beat_int}."
            )
        elif kind == "within":
            lines.append(f"Nested placement — starts inside '{ref}' at beat {beat_int}.")
    else:
        lines.append(f"Start ALL new regions at beat {beat_int}.")

    lines.append(f"All new regions MUST use startBeat >= {beat_int}.")
    lines.append("Do not modify or overlap existing regions unless the relationship requires it.")

    if section_name:
        lines.append(f"This prompt creates the '{section_name}' section.")
        lines.append(
            f"Name new tracks and regions to reflect the section "
            f"(e.g. '{section_name.title()} Drums', '{section_name.title()} Bass')."
        )

    lines.append("═════════════════════════════════════")
    lines.append("")
    return "\n".join(lines)
