"""Server-side MIDI-to-JSON parser for MuseHub piano roll visualization.

Converts raw MIDI file bytes into a structured note representation consumed
by the Canvas-based piano roll renderer in the browser.  All timing is
expressed in beats (quarter-note units) so the renderer remains tempo-
agnostic — the client can choose whether to display wall-clock seconds or
musical beats.

Why this module exists:
    MIDI files on MuseHub are stored as opaque binary objects.  The browser
    cannot parse them natively at the precision required for a faithful piano
    roll (sustain pedal, program changes, fine-grained velocity).  Parsing
    server-side also lets us normalise multi-track SMF formats (type 0/1/2)
    into a unified per-channel model before transmission.
"""
from __future__ import annotations

import logging
from typing import TypedDict

import mido

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public type contract — registered in docs/reference/type_contracts.md
# ---------------------------------------------------------------------------


class MidiNote(TypedDict):
    """A single sounding note extracted from a MIDI track."""

    pitch: int
    """MIDI pitch number (0–127, 60 = middle C)."""

    start_beat: float
    """Note-on position in quarter-note beats from the beginning of the file."""

    duration_beats: float
    """Sustain length in quarter-note beats (note-on to note-off)."""

    velocity: int
    """Note-on velocity (0–127)."""

    track_id: int
    """Zero-based index of the source MIDI track."""

    channel: int
    """MIDI channel (0–15)."""


class MidiTrack(TypedDict):
    """A single logical track extracted from a MIDI file."""

    track_id: int
    """Zero-based track index matching ``MidiNote.track_id``."""

    channel: int
    """Dominant MIDI channel for this track (−1 when track has no notes)."""

    name: str
    """Track name from the ``track_name`` meta message, or auto-generated."""

    notes: list[MidiNote]
    """All notes in this track, sorted by ``start_beat``."""


class MidiParseResult(TypedDict):
    """Top-level result returned by :func:`parse_midi_bytes`.

    This is the canonical shape delivered to the browser by the
    ``GET /{owner}/{repo}/objects/{sha}/parse-midi`` endpoint and is
    registered as a type contract in ``docs/reference/type_contracts.md``.
    """

    tracks: list[MidiTrack]
    """Per-track note data, ordered by track index."""

    tempo_bpm: float
    """First tempo found in the file, in BPM (default 120.0 if absent)."""

    time_signature: str
    """First time signature as ``"{numerator}/{denominator}"`` (default ``"4/4"``)."""

    total_beats: float
    """Total duration of the piece in quarter-note beats."""


# ---------------------------------------------------------------------------
# MIDI pitch → note name helper
# ---------------------------------------------------------------------------

_PITCH_NAMES: list[str] = [
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
]


def pitch_to_name(pitch: int) -> str:
    """Convert a MIDI pitch number to a human-readable note name.

    Examples: 60 → "C4", 69 → "A4", 21 → "A0".
    """
    octave = (pitch // 12) - 1
    name = _PITCH_NAMES[pitch % 12]
    return f"{name}{octave}"


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

_DEFAULT_TEMPO_US: int = 500_000  # 120 BPM in microseconds per quarter note


def parse_midi_bytes(data: bytes) -> MidiParseResult:
    """Parse raw MIDI bytes into a structured :class:`MidiParseResult`.

    Supports SMF types 0, 1, and 2.  All absolute tick offsets are converted
    to quarter-note beats using the file's ``ticks_per_beat`` resolution and
    the first tempo event encountered.  If the file contains multiple tempo
    changes only the first is used for the overall ``tempo_bpm`` field; the
    note positions are computed against the initial tempo (accurate for the
    majority of single-tempo MIDI files common in DAW exports).

    Args:
        data: Raw bytes of a Standard MIDI File (.mid / .midi).

    Returns:
        A :class:`MidiParseResult` dict ready for JSON serialisation.

    Raises:
        ValueError: If ``data`` is not a valid MIDI file.
    """
    try:
        mid = mido.MidiFile(file=__import__("io").BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Could not parse MIDI data: {exc}") from exc

    ticks_per_beat: int = mid.ticks_per_beat or 480
    tempo_us: int = _DEFAULT_TEMPO_US
    tempo_bpm: float = mido.tempo2bpm(tempo_us)
    time_sig_num: int = 4
    time_sig_den: int = 4

    tracks: list[MidiTrack] = []

    for track_idx, track in enumerate(mid.tracks):
        track_name: str = f"Track {track_idx}"
        pending: dict[tuple[int, int], tuple[int, float]] = {}
        notes: list[MidiNote] = []
        abs_tick: int = 0
        channel_counts: dict[int, int] = {}

        for msg in track:
            abs_tick += msg.time

            if msg.is_meta:
                if msg.type == "set_tempo" and track_idx == 0:
                    tempo_us = msg.tempo
                    tempo_bpm = mido.tempo2bpm(tempo_us)
                elif msg.type == "time_signature" and track_idx == 0:
                    time_sig_num = msg.numerator
                    time_sig_den = msg.denominator
                elif msg.type == "track_name":
                    track_name = msg.name or track_name
                continue

            if not hasattr(msg, "channel"):
                continue

            ch: int = msg.channel
            beat_pos: float = abs_tick / ticks_per_beat

            if msg.type == "note_on" and msg.velocity > 0:
                pending[(ch, msg.note)] = (msg.velocity, beat_pos)
                channel_counts[ch] = channel_counts.get(ch, 0) + 1

            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                key = (ch, msg.note)
                if key in pending:
                    vel, start = pending.pop(key)
                    dur = beat_pos - start
                    if dur <= 0:
                        dur = 1.0 / ticks_per_beat
                    notes.append(
                        MidiNote(
                            pitch=msg.note,
                            start_beat=round(start, 6),
                            duration_beats=round(dur, 6),
                            velocity=vel,
                            track_id=track_idx,
                            channel=ch,
                        )
                    )

        # Close any dangling note-ons (file ended without note-off)
        beat_pos = abs_tick / ticks_per_beat
        for (ch, pitch), (vel, start) in pending.items():
            dur = max(beat_pos - start, 1.0 / ticks_per_beat)
            notes.append(
                MidiNote(
                    pitch=pitch,
                    start_beat=round(start, 6),
                    duration_beats=round(dur, 6),
                    velocity=vel,
                    track_id=track_idx,
                    channel=ch,
                )
            )

        notes.sort(key=lambda n: (n["start_beat"], n["pitch"]))

        dominant_channel: int = (
            max(channel_counts, key=lambda c: channel_counts[c])
            if channel_counts
            else -1
        )

        tracks.append(
            MidiTrack(
                track_id=track_idx,
                channel=dominant_channel,
                name=track_name,
                notes=notes,
            )
        )

    total_beats: float = max(
        (
            max((n["start_beat"] + n["duration_beats"] for n in t["notes"]), default=0.0)
            for t in tracks
            if t["notes"]
        ),
        default=0.0,
    )

    logger.debug(
        "✅ Parsed MIDI: %d tracks, %.1f beats, %.1f BPM",
        len(tracks),
        total_beats,
        tempo_bpm,
    )

    return MidiParseResult(
        tracks=tracks,
        tempo_bpm=round(tempo_bpm, 3),
        time_signature=f"{time_sig_num}/{time_sig_den}",
        total_beats=round(total_beats, 3),
    )
