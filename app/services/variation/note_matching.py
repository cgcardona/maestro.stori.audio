"""Note and controller event matching between base and proposed states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# Matching tolerances
TIMING_TOLERANCE_BEATS = 0.05  # Notes within 0.05 beats are considered same timing
PITCH_TOLERANCE = 0  # Exact pitch match required


@dataclass
class NoteMatch:
    """A matched pair of notes (base and proposed)."""
    base_note: dict[str, Any] | None
    proposed_note: dict[str, Any] | None
    base_index: int | None
    proposed_index: int | None

    @property
    def is_added(self) -> bool:
        return self.base_note is None and self.proposed_note is not None

    @property
    def is_removed(self) -> bool:
        return self.base_note is not None and self.proposed_note is None

    @property
    def is_modified(self) -> bool:
        if self.base_note is None or self.proposed_note is None:
            return False
        return self._has_changes()

    @property
    def is_unchanged(self) -> bool:
        if self.base_note is None or self.proposed_note is None:
            return False
        return not self._has_changes()

    def _has_changes(self) -> bool:
        """Check if there are any differences between base and proposed."""
        if self.base_note is None or self.proposed_note is None:
            return True

        base_pitch = self.base_note.get("pitch")
        proposed_pitch = self.proposed_note.get("pitch")
        if base_pitch != proposed_pitch:
            return True

        base_start = self.base_note.get("start_beat", 0)
        proposed_start = self.proposed_note.get("start_beat", 0)
        if abs(base_start - proposed_start) > TIMING_TOLERANCE_BEATS:
            return True

        base_duration = self.base_note.get("duration_beats", 0.5)
        proposed_duration = self.proposed_note.get("duration_beats", 0.5)
        if abs(base_duration - proposed_duration) > TIMING_TOLERANCE_BEATS:
            return True

        base_velocity = self.base_note.get("velocity", 100)
        proposed_velocity = self.proposed_note.get("velocity", 100)
        if base_velocity != proposed_velocity:
            return True

        return False


def _get_note_key(note: dict[str, Any]) -> tuple[int, float]:
    """Get matching key for a note (pitch, start_beat)."""
    pitch = note.get("pitch", 60)
    start = note.get("start_beat", 0)
    return (pitch, start)


def _notes_match(base_note: dict[str, Any], proposed_note: dict[str, Any]) -> bool:
    """Check if two notes should be considered the same note."""
    base_pitch = base_note.get("pitch")
    proposed_pitch = proposed_note.get("pitch")
    if base_pitch is None or proposed_pitch is None:
        return False
    if abs(base_pitch - proposed_pitch) > PITCH_TOLERANCE:
        return False

    base_start = base_note.get("start_beat", 0)
    proposed_start = proposed_note.get("start_beat", 0)

    if abs(base_start - proposed_start) > TIMING_TOLERANCE_BEATS:
        return False

    return True


def match_notes(
    base_notes: list[dict[str, Any]],
    proposed_notes: list[dict[str, Any]],
) -> list[NoteMatch]:
    """Match notes between base and proposed states.

    Uses pitch + timing proximity to match notes. Unmatched base notes
    are marked as removed, unmatched proposed notes as added.

    Args:
        base_notes: Original notes
        proposed_notes: Notes after transformation

    Returns:
        list of NoteMatch objects representing the alignment
    """
    matches: list[NoteMatch] = []

    base_matched: set[int] = set()
    proposed_matched: set[int] = set()

    # First pass: exact matches (same pitch and timing)
    for bi, base_note in enumerate(base_notes):
        if bi in base_matched:
            continue

        for pi, proposed_note in enumerate(proposed_notes):
            if pi in proposed_matched:
                continue

            if _notes_match(base_note, proposed_note):
                matches.append(NoteMatch(
                    base_note=base_note,
                    proposed_note=proposed_note,
                    base_index=bi,
                    proposed_index=pi,
                ))
                base_matched.add(bi)
                proposed_matched.add(pi)
                break

    # Remaining base notes are removed
    for bi, base_note in enumerate(base_notes):
        if bi not in base_matched:
            matches.append(NoteMatch(
                base_note=base_note,
                proposed_note=None,
                base_index=bi,
                proposed_index=None,
            ))

    # Remaining proposed notes are added
    for pi, proposed_note in enumerate(proposed_notes):
        if pi not in proposed_matched:
            matches.append(NoteMatch(
                base_note=None,
                proposed_note=proposed_note,
                base_index=None,
                proposed_index=pi,
            ))

    return matches


# ── Controller event matching ─────────────────────────────────────────────


@dataclass
class EventMatch:
    """A matched pair of controller events (base and proposed)."""

    base_event: dict[str, Any] | None
    proposed_event: dict[str, Any] | None

    @property
    def is_added(self) -> bool:
        return self.base_event is None and self.proposed_event is not None

    @property
    def is_removed(self) -> bool:
        return self.base_event is not None and self.proposed_event is None

    @property
    def is_modified(self) -> bool:
        if self.base_event is None or self.proposed_event is None:
            return False
        return self.base_event.get("value") != self.proposed_event.get("value")

    @property
    def is_unchanged(self) -> bool:
        if self.base_event is None or self.proposed_event is None:
            return False
        return not self.is_modified


def _events_match_by_beat(base: dict[str, Any], proposed: dict[str, Any]) -> bool:
    """Two events are the same if they occur at the same beat (within tolerance)."""
    b_beat: float = base.get("beat", 0)
    p_beat: float = proposed.get("beat", 0)
    return abs(b_beat - p_beat) <= TIMING_TOLERANCE_BEATS


def _cc_events_match(base: dict[str, Any], proposed: dict[str, Any]) -> bool:
    """CC events match if same CC number and same beat."""
    if base.get("cc") != proposed.get("cc"):
        return False
    return _events_match_by_beat(base, proposed)


def _aftertouch_events_match(base: dict[str, Any], proposed: dict[str, Any]) -> bool:
    """Aftertouch events match if same pitch (if poly) and same beat."""
    if base.get("pitch") != proposed.get("pitch"):
        return False
    return _events_match_by_beat(base, proposed)


def _match_events(
    base_events: list[dict[str, Any]],
    proposed_events: list[dict[str, Any]],
    match_fn: Callable[[dict[str, Any], dict[str, Any]], bool],
) -> list[EventMatch]:
    """Generic event matcher using a pluggable identity function."""
    matches: list[EventMatch] = []
    base_matched: set[int] = set()
    proposed_matched: set[int] = set()

    for bi, base in enumerate(base_events):
        if bi in base_matched:
            continue
        for pi, proposed in enumerate(proposed_events):
            if pi in proposed_matched:
                continue
            if match_fn(base, proposed):
                matches.append(EventMatch(base_event=base, proposed_event=proposed))
                base_matched.add(bi)
                proposed_matched.add(pi)
                break

    for bi, base in enumerate(base_events):
        if bi not in base_matched:
            matches.append(EventMatch(base_event=base, proposed_event=None))

    for pi, proposed in enumerate(proposed_events):
        if pi not in proposed_matched:
            matches.append(EventMatch(base_event=None, proposed_event=proposed))

    return matches


def match_cc_events(
    base_events: list[dict[str, Any]],
    proposed_events: list[dict[str, Any]],
) -> list[EventMatch]:
    """Match CC events by CC number + beat timing."""
    return _match_events(base_events, proposed_events, _cc_events_match)


def match_pitch_bends(
    base_events: list[dict[str, Any]],
    proposed_events: list[dict[str, Any]],
) -> list[EventMatch]:
    """Match pitch bend events by beat timing."""
    return _match_events(base_events, proposed_events, _events_match_by_beat)


def match_aftertouch(
    base_events: list[dict[str, Any]],
    proposed_events: list[dict[str, Any]],
) -> list[EventMatch]:
    """Match aftertouch events by pitch (if poly) + beat timing."""
    return _match_events(base_events, proposed_events, _aftertouch_events_match)
