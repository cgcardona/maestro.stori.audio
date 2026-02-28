"""Muse Hub Notation Service — MIDI-to-standard-notation conversion.

Converts MIDI note data (as stored in Muse commits) into quantized, structured
notation data suitable for rendering as sheet music.  The output is a typed
JSON payload consumed by the client-side SVG score renderer.

Why this exists
---------------
Musicians who read traditional notation need to visualize Muse compositions as
sheet music without exporting to MusicXML and opening a separate application.
This service bridges the gap by producing quantized ``NotationResult`` data that
the score page renders directly in the browser.

Design decisions
----------------
- Server-side quantization only.  The browser renderer is intentionally thin —
  it receives pre-computed beat-aligned note data and draws SVG, it does not
  re-quantize or re-interpret pitch.
- Deterministic stubs keyed on ``ref``.  Full Storpheus MIDI introspection will
  be wired in once the per-commit MIDI endpoint is stable.  Until then, stub
  data is musically realistic and internally consistent.
- No external I/O.  This module is pure data — no database, no network calls,
  no side effects.  Route handlers inject all inputs.

Boundary rules
--------------
- Must NOT import StateStore, EntityRegistry, or executor modules.
- Must NOT import LLM handlers or maestro_* pipeline modules.
- Must NOT import Storpheus service directly (data flows via route params).
"""
from __future__ import annotations

import hashlib
import logging
from typing import NamedTuple, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class NotationNote(TypedDict):
    """A single quantized note ready for SVG rendering.

    Fields are kept flat (no nested objects) to simplify JavaScript
    destructuring on the client side.

    pitch_name: e.g. "C", "F#", "Bb"
    octave:     MIDI octave number (4 = middle octave)
    duration:   note duration as a fraction string, e.g. "1/4", "1/8", "1/2"
    start_beat: beat position (0-indexed from the start of the piece)
    velocity:   MIDI velocity 0–127
    track_id:   source track index (matches NotationTrack.track_id)
    """

    pitch_name: str
    octave: int
    duration: str
    start_beat: float
    velocity: int
    track_id: int


class NotationTrack(TypedDict):
    """One instrument part, with clef/key/time signature metadata."""

    track_id: int
    clef: str
    key_signature: str
    time_signature: str
    instrument: str
    notes: list[NotationNote]


class NotationResult(NamedTuple):
    """Typed result returned by ``convert_ref_to_notation``.

    Attributes
    ----------
    tracks:
        List of ``NotationTrack`` dicts, one per instrument part.  Each track
        includes clef, key_signature, time_signature, and a list of
        ``NotationNote`` dicts ordered by start_beat.
    tempo:
        BPM as a positive integer.
    key:
        Key signature string, e.g. ``"C major"``, ``"F# minor"``.
    time_sig:
        Time signature string, e.g. ``"4/4"``, ``"3/4"``, ``"6/8"``.
    """

    tracks: list[NotationTrack]
    tempo: int
    key: str
    time_sig: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_KEY_POOL = [
    "C major",
    "G major",
    "D major",
    "A major",
    "F major",
    "Bb major",
    "Eb major",
    "A minor",
    "D minor",
    "E minor",
    "B minor",
    "G minor",
]

_CLEF_MAP = {
    "bass": "bass",
    "piano": "treble",
    "keys": "treble",
    "guitar": "treble",
    "strings": "treble",
    "violin": "treble",
    "cello": "bass",
    "trumpet": "treble",
    "sax": "treble",
    "default": "treble",
}

_TIME_SIGS = ["4/4", "3/4", "6/8", "2/4"]
_DURATIONS = ["1/4", "1/4", "1/4", "1/8", "1/8", "1/2", "1/4", "1/4"]
_ROLE_NAMES = ["piano", "bass", "guitar", "strings", "trumpet"]


def _seed_from_ref(ref: str) -> int:
    """Derive a deterministic integer seed from a commit ref string."""
    digest = hashlib.sha256(ref.encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _lcg(seed: int) -> int:
    """Minimal linear congruential generator step — returns updated state."""
    return (seed * 1664525 + 1013904223) & 0xFFFFFFFF


def _notes_for_track(
    seed: int,
    track_idx: int,
    time_sig: str,
    num_bars: int,
) -> list[NotationNote]:
    """Generate a list of quantized notation notes for one track.

    Uses a seeded pseudo-random sequence so that the same ref always produces
    the same notes.  The quantization grid matches the time signature — quarter
    notes for 4/4 and 3/4, eighth notes for 6/8.
    """
    beats_per_bar, _ = (int(x) for x in time_sig.split("/"))
    notes: list[NotationNote] = []

    s = seed ^ (track_idx * 0xDEAD)
    for bar in range(num_bars):
        beat = 0.0
        while beat < beats_per_bar:
            s = _lcg(s)
            # 30 % chance of a rest — skip this beat slot
            if (s % 10) < 3:
                beat += 1.0
                continue
            s = _lcg(s)
            pitch_idx = s % 12
            s = _lcg(s)
            octave = 3 + (s % 3)  # octaves 3, 4, 5
            s = _lcg(s)
            dur_idx = s % len(_DURATIONS)
            duration = _DURATIONS[dur_idx]
            s = _lcg(s)
            velocity = 60 + (s % 60)

            notes.append(
                NotationNote(
                    pitch_name=_PITCH_NAMES[pitch_idx],
                    octave=int(octave),
                    duration=duration,
                    start_beat=float(bar * beats_per_bar + beat),
                    velocity=int(velocity),
                    track_id=track_idx,
                )
            )
            # Advance beat by the duration value (quarter = 1, eighth = 0.5, half = 2)
            num, denom = (int(x) for x in duration.split("/"))
            beat_advance = 4.0 * num / denom
            beat += beat_advance

    return notes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def convert_ref_to_notation(
    ref: str,
    num_tracks: int = 3,
    num_bars: int = 8,
) -> NotationResult:
    """Convert a Muse commit ref to quantized notation data.

    Returns a ``NotationResult`` containing typed track data ready for the
    client-side SVG score renderer.

    Parameters
    ----------
    ref:
        Muse commit ref (branch name, tag, or commit SHA).  Used as a seed so
        that the same ref always returns the same notation.
    num_tracks:
        Number of instrument tracks to generate.  Clamped to [1, 8].
    num_bars:
        Number of bars of music to generate per track.  Clamped to [1, 32].
    """
    num_tracks = max(1, min(8, num_tracks))
    num_bars = max(1, min(32, num_bars))

    seed = _seed_from_ref(ref)

    key_idx = seed % len(_KEY_POOL)
    key = _KEY_POOL[key_idx]

    ts_seed = _lcg(seed)
    time_sig = _TIME_SIGS[ts_seed % len(_TIME_SIGS)]

    tempo_seed = _lcg(ts_seed)
    tempo = 80 + int(tempo_seed % 80)

    tracks: list[NotationTrack] = []
    for i in range(num_tracks):
        role = _ROLE_NAMES[i % len(_ROLE_NAMES)]
        clef = _CLEF_MAP.get(role, _CLEF_MAP["default"])
        notes = _notes_for_track(seed, i, time_sig, num_bars)
        tracks.append(
            NotationTrack(
                track_id=i,
                clef=clef,
                key_signature=key,
                time_signature=time_sig,
                instrument=role,
                notes=notes,
            )
        )

    logger.debug("✅ notation: ref=%s tracks=%d bars=%d tempo=%d", ref, num_tracks, num_bars, tempo)
    return NotationResult(tracks=tracks, tempo=tempo, key=key, time_sig=time_sig)


class NotationDict(TypedDict):
    """Serialized form of ``NotationResult`` for JSON API responses."""

    tracks: list[NotationTrack]
    tempo: int
    key: str
    timeSig: str


def notation_result_to_dict(result: NotationResult) -> NotationDict:
    """Serialise a ``NotationResult`` to a typed dict for JSON responses.

    The output shape is:
    ```json
    {
      "tracks": [...],
      "tempo": 120,
      "key": "C major",
      "timeSig": "4/4"
    }
    ```

    Note: ``time_sig`` is camelCase ``timeSig`` in the JSON output to match
    the JavaScript convention used by all other MuseHub API responses.
    """
    return NotationDict(
        tracks=result.tracks,
        tempo=result.tempo,
        key=result.key,
        timeSig=result.time_sig,
    )
