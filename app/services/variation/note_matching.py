"""Note and controller event matching between base and proposed states."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from typing import Callable, Generic, TypeVar

from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteDict,
    PitchBendDict,
)

# Matching tolerances
TIMING_TOLERANCE_BEATS = 0.05  # Notes within 0.05 beats are considered same timing
PITCH_TOLERANCE = 0  # Exact pitch match required


@dataclass
class NoteMatch:
    """A matched pair of notes (base and proposed) from a diff alignment.

    Produced by ``match_notes`` — one instance per note in either state.
    Inspecting the four mutually-exclusive status properties tells callers
    what happened to the note between the two snapshots.

    Attributes:
        base_note: Note from the HEAD (pre-execution) snapshot; ``None``
            when the note exists only in the proposed state (added).
        proposed_note: Note from the post-execution snapshot; ``None``
            when the note exists only in the base state (removed).
        base_index: Position of the note in the base list, or ``None`` if added.
        proposed_index: Position of the note in the proposed list, or ``None`` if removed.
    """

    base_note: NoteDict | None
    proposed_note: NoteDict | None
    base_index: int | None
    proposed_index: int | None

    @property
    def is_added(self) -> bool:
        """``True`` when the note is present only in the proposed state."""
        return self.base_note is None and self.proposed_note is not None

    @property
    def is_removed(self) -> bool:
        """``True`` when the note is present only in the base (HEAD) state."""
        return self.base_note is not None and self.proposed_note is None

    @property
    def is_modified(self) -> bool:
        """``True`` when both notes are present but differ in pitch, timing, or velocity."""
        if self.base_note is None or self.proposed_note is None:
            return False
        return self._has_changes()

    @property
    def is_unchanged(self) -> bool:
        """``True`` when both notes are present and identical within tolerance."""
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


def _get_note_key(note: NoteDict) -> tuple[int, float]:
    """Get matching key for a note (pitch, start_beat)."""
    pitch = note.get("pitch", 60)
    start = note.get("start_beat", 0)
    return (pitch, start)


def _notes_match(base_note: NoteDict, proposed_note: NoteDict) -> bool:
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
    base_notes: list[NoteDict],
    proposed_notes: list[NoteDict],
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


EventDict = CCEventDict | PitchBendDict | AftertouchDict

# Constrained to the three concrete event types so callers get the exact
# element type back (e.g. list[CCEventDict] in → list[CCEventDict] out).
_EV = TypeVar("_EV", CCEventDict, PitchBendDict, AftertouchDict)


@dataclass
class EventMatch(Generic[_EV]):
    """A matched pair of controller events (base and proposed).

    Generic over the concrete event type (``CCEventDict``, ``PitchBendDict``,
    ``AftertouchDict``) so match functions preserve the element type end-to-end
    without requiring ``cast`` or overloaded signatures.

    Attributes:
        base_event: Event from the HEAD snapshot; ``None`` if the event is new.
        proposed_event: Event from the post-execution snapshot; ``None`` if removed.
    """

    base_event: _EV | None
    proposed_event: _EV | None

    @property
    def is_added(self) -> bool:
        """``True`` when the event exists only in the proposed (new) state."""
        return self.base_event is None and self.proposed_event is not None

    @property
    def is_removed(self) -> bool:
        """``True`` when the event exists only in the base (HEAD) state."""
        return self.base_event is not None and self.proposed_event is None

    @property
    def is_modified(self) -> bool:
        """``True`` when both events are present but their ``value`` fields differ."""
        if self.base_event is None or self.proposed_event is None:
            return False
        return self.base_event.get("value") != self.proposed_event.get("value")

    @property
    def is_unchanged(self) -> bool:
        """``True`` when both events are present and have the same ``value``."""
        if self.base_event is None or self.proposed_event is None:
            return False
        return not self.is_modified


def _events_match_by_beat(base: EventDict, proposed: EventDict) -> bool:
    """Two events are the same if they occur at the same beat (within tolerance)."""
    b_beat: float = base.get("beat", 0)
    p_beat: float = proposed.get("beat", 0)
    return abs(b_beat - p_beat) <= TIMING_TOLERANCE_BEATS


def _cc_events_match(base: EventDict, proposed: EventDict) -> bool:
    """CC events match if same CC number and same beat."""
    if base.get("cc") != proposed.get("cc"):
        return False
    return _events_match_by_beat(base, proposed)


def _aftertouch_events_match(base: EventDict, proposed: EventDict) -> bool:
    """Aftertouch events match if same pitch (if poly) and same beat."""
    if base.get("pitch") != proposed.get("pitch"):
        return False
    return _events_match_by_beat(base, proposed)


def _match_events(
    base_events: Sequence[_EV],
    proposed_events: Sequence[_EV],
    match_fn: Callable[[EventDict, EventDict], bool],
) -> list[EventMatch[_EV]]:
    """Generic event matcher using a pluggable identity function.

    The match_fn takes the broader EventDict union (each concrete event type
    is a member of that union) so the same helpers can be reused across all
    event kinds without duplicating them.
    """
    matches: list[EventMatch[_EV]] = []
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
    base_events: list[CCEventDict],
    proposed_events: list[CCEventDict],
) -> list[EventMatch[CCEventDict]]:
    """Match CC events by CC number + beat timing."""
    return _match_events(base_events, proposed_events, _cc_events_match)


def match_pitch_bends(
    base_events: list[PitchBendDict],
    proposed_events: list[PitchBendDict],
) -> list[EventMatch[PitchBendDict]]:
    """Match pitch bend events by beat timing."""
    return _match_events(base_events, proposed_events, _events_match_by_beat)


def match_aftertouch(
    base_events: list[AftertouchDict],
    proposed_events: list[AftertouchDict],
) -> list[EventMatch[AftertouchDict]]:
    """Match aftertouch events by pitch (if poly) + beat timing."""
    return _match_events(base_events, proposed_events, _aftertouch_events_match)
