"""Bar labels and change-tag detection for variation phrases."""

from __future__ import annotations

from app.services.variation.note_matching import TIMING_TOLERANCE_BEATS


def _beat_to_bar(beat: float, beats_per_bar: int = 4) -> int:
    """Convert beat position to bar number (1-indexed)."""
    return int(beat // beats_per_bar) + 1


def _generate_bar_label(start_bar: int, end_bar: int) -> str:
    """Generate a human-readable bar range label."""
    if start_bar == end_bar:
        return f"Bar {start_bar}"
    return f"Bars {start_bar}-{end_bar}"


def _detect_change_tags(note_changes: list) -> list[str]:
    """Detect what types of changes are present in a phrase."""
    tags: set[str] = set()

    for nc in note_changes:
        if nc.change_type == "added":
            tags.add("densityChange")
        elif nc.change_type == "removed":
            tags.add("densityChange")
        elif nc.change_type == "modified":
            if nc.before and nc.after:
                if nc.before.pitch != nc.after.pitch:
                    tags.add("pitchChange")
                    interval = abs(nc.after.pitch - nc.before.pitch)
                    if interval in (1, 2):
                        tags.add("scaleChange")
                    elif interval in (3, 4):
                        tags.add("harmonyChange")

                if abs(nc.before.start_beat - nc.after.start_beat) > TIMING_TOLERANCE_BEATS:
                    tags.add("rhythmChange")

                if abs(nc.before.duration_beats - nc.after.duration_beats) > TIMING_TOLERANCE_BEATS:
                    tags.add("articulationChange")

                if nc.before.velocity != nc.after.velocity:
                    tags.add("velocityChange")

                if abs(nc.before.pitch - nc.after.pitch) >= 12:
                    tags.add("registerChange")

    return sorted(tags)
