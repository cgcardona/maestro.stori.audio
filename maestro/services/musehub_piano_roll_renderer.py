"""Server-side MIDI-to-PNG piano roll renderer for MuseHub.

Converts raw MIDI bytes into a static piano roll image (PNG) without any
browser or external image library dependency. Uses ``mido`` (already a
project dependency) to parse MIDI and stdlib ``zlib``/``struct`` to encode
a minimal PNG.

The piano roll image layout:
  - Width : ``MAX_WIDTH_PX`` (clamped), representing the MIDI timeline.
  - Height: ``NOTE_ROWS`` (128), one row per MIDI pitch (0 = bottom, 127 = top).
  - Background: dark charcoal (``BG_COLOR``).
  - Octave boundary lines: slightly lighter horizontal rule at every C note.
  - Notes: colored rectangles, colour-coded by MIDI channel.

Design constraints:
  - Zero external image dependencies (no Pillow, no cairo, no Node).
  - Deterministic: same MIDI bytes → same PNG bytes for a given render width.
  - Graceful degradation: a blank canvas is returned when the MIDI has no
    note events, so callers always receive a valid PNG.

Result type: ``PianoRollRenderResult`` — registered in docs/reference/type_contracts.md.
"""
from __future__ import annotations

import io
import logging
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import mido

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOTE_ROWS: int = 128          # MIDI pitch range: 0–127
MAX_WIDTH_PX: int = 1920      # maximum render width in pixels
MIN_WIDTH_PX: int = 64        # minimum render width (very short clips)
NOTE_ROW_HEIGHT: int = 2      # height in px of each pitch row
IMAGE_HEIGHT: int = NOTE_ROWS * NOTE_ROW_HEIGHT   # total image height in pixels

# Background colour (dark charcoal)
BG_COLOR: tuple[int, int, int] = (28, 28, 34)
# Octave-C boundary line colour (slightly lighter)
BOUNDARY_COLOR: tuple[int, int, int] = (60, 60, 72)
# Per-channel note colours (MIDI channels 0–15); cycles if channel > 15.
_CHANNEL_COLORS: list[tuple[int, int, int]] = [
    (100, 220, 130),   # ch 0  — green (bass)
    (100, 160, 220),   # ch 1  — blue (keys)
    (220, 140, 100),   # ch 2  — orange (lead)
    (200, 100, 220),   # ch 3  — purple
    (220, 220, 100),   # ch 4  — yellow
    (100, 220, 220),   # ch 5  — cyan
    (220, 100, 100),   # ch 6  — red
    (140, 220, 100),   # ch 7  — lime
    (180, 120, 220),   # ch 8  — lavender
    (220, 180, 100),   # ch 9  — gold (often drums — colour differently)
    (100, 200, 180),   # ch 10 — teal
    (220, 120, 180),   # ch 11 — rose
    (180, 200, 100),   # ch 12 — olive
    (120, 180, 220),   # ch 13 — sky
    (220, 160, 120),   # ch 14 — peach
    (160, 120, 200),   # ch 15 — indigo
]

# Minimum note-render width so very short notes are always visible
_MIN_NOTE_PX: int = 1


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PianoRollRenderResult:
    """Outcome of a single piano roll render operation.

    Attributes:
        output_path: Absolute path of the PNG file written to disk.
        width_px: Actual render width in pixels.
        note_count: Number of MIDI note events rendered.
        track_index: Zero-based MIDI track index that was rendered.
        stubbed: True when the MIDI contained no note events and a blank
            canvas was returned.
    """

    output_path: Path
    width_px: int
    note_count: int
    track_index: int
    stubbed: bool


# ---------------------------------------------------------------------------
# MIDI parsing helpers
# ---------------------------------------------------------------------------


@dataclass
class _NoteEvent:
    """A resolved note-on / note-off pair in absolute tick time."""

    pitch: int
    channel: int
    start_tick: int
    end_tick: int


def _parse_note_events(midi: mido.MidiFile) -> list[_NoteEvent]:
    """Extract note-on/off pairs from all tracks, returning absolute-tick events.

    Pairs each note-on with the next note-off (or note-on with velocity 0) for
    the same pitch+channel combination. Orphaned note-ons (no matching note-off)
    are extended to the end of the track.

    Args:
        midi: Parsed ``mido.MidiFile`` object.

    Returns:
        List of ``_NoteEvent`` objects with resolved start/end tick positions.
    """
    events: list[_NoteEvent] = []

    for track in midi.tracks:
        # Track absolute tick alongside each message
        pending: dict[tuple[int, int], int] = {}  # (pitch, channel) → start_tick
        abs_tick = 0
        last_tick = 0

        for msg in track:
            abs_tick += msg.time
            last_tick = abs_tick

            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.note, msg.channel)
                if key not in pending:
                    pending[key] = abs_tick

            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                key = (msg.note, msg.channel)
                if key in pending:
                    start = pending.pop(key)
                    events.append(
                        _NoteEvent(
                            pitch=msg.note,
                            channel=msg.channel,
                            start_tick=start,
                            end_tick=abs_tick,
                        )
                    )

        # Close any orphaned note-ons at the track end
        for (pitch, channel), start in pending.items():
            events.append(
                _NoteEvent(
                    pitch=pitch,
                    channel=channel,
                    start_tick=start,
                    end_tick=last_tick if last_tick > start else start + 1,
                )
            )

    return events


# ---------------------------------------------------------------------------
# PNG encoder (pure stdlib — no Pillow)
# ---------------------------------------------------------------------------

# PNG magic bytes
_PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Encode a single PNG chunk (length + type + data + CRC)."""
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _encode_png(pixels: list[bytearray], width: int, height: int) -> bytes:
    """Encode a list of RGB scanlines as a minimal PNG byte string.

    Args:
        pixels: ``height`` bytearrays, each of length ``width * 3`` (RGB).
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        Complete PNG file bytes.
    """
    # IHDR: width, height, bit-depth=8, colour-type=2 (RGB), compression=0,
    # filter-method=0, interlace=0
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = _png_chunk(b"IHDR", ihdr_data)

    # IDAT: each scanline prefixed with filter byte 0 (None)
    raw_rows = b"".join(b"\x00" + row for row in pixels)
    idat = _png_chunk(b"IDAT", zlib.compress(raw_rows, 6))

    iend = _png_chunk(b"IEND", b"")

    return _PNG_SIGNATURE + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Render logic
# ---------------------------------------------------------------------------


def _build_canvas(width: int) -> list[bytearray]:
    """Allocate a blank RGB canvas of size ``width × IMAGE_HEIGHT``.

    Rows are stored bottom-first (MIDI pitch 0 at index 0) but will be
    written top-first (inverted) when encoding the PNG.

    Returns:
        ``IMAGE_HEIGHT`` bytearrays each of ``width * 3`` bytes filled with
        ``BG_COLOR``.
    """
    row_template = bytearray(BG_COLOR * width)

    # Draw octave-C boundary lines (every 12 semitones starting at C0 = pitch 0)
    rows: list[bytearray] = []
    for note in range(NOTE_ROWS):
        if note % 12 == 0:
            rows.append(bytearray(BOUNDARY_COLOR * width))
        else:
            rows.append(bytearray(row_template))

    return rows


def _draw_note(
    rows: list[bytearray],
    note: _NoteEvent,
    total_ticks: int,
    width: int,
) -> None:
    """Paint a single note event onto the canvas (in-place).

    Args:
        rows: Canvas from ``_build_canvas`` (bottom-first ordering).
        note: Note event with absolute tick positions.
        total_ticks: Total MIDI duration in ticks (used to map ticks → pixels).
        width: Canvas width in pixels.
    """
    if total_ticks <= 0:
        return

    color = _CHANNEL_COLORS[note.channel % len(_CHANNEL_COLORS)]

    # Map tick → pixel column
    x_start = int(note.start_tick / total_ticks * width)
    x_end = max(x_start + _MIN_NOTE_PX, int(note.end_tick / total_ticks * width))
    x_end = min(x_end, width)

    # Pitch row (bottom-first)
    row_base = note.pitch * NOTE_ROW_HEIGHT
    for dy in range(NOTE_ROW_HEIGHT):
        row_idx = row_base + dy
        if row_idx >= len(rows):
            continue
        row = rows[row_idx]
        for x in range(x_start, x_end):
            offset = x * 3
            row[offset] = color[0]
            row[offset + 1] = color[1]
            row[offset + 2] = color[2]


def render_piano_roll(
    midi_bytes: bytes,
    output_path: Path,
    track_index: int = 0,
    target_width: int = MAX_WIDTH_PX,
) -> PianoRollRenderResult:
    """Render raw MIDI bytes as a piano roll PNG image.

    Parses all tracks from the MIDI file, paints each note as a coloured
    rectangle proportional to its duration, and writes a PNG file.

    Args:
        midi_bytes: Raw bytes of a Standard MIDI File (.mid).
        output_path: Destination path for the output PNG file.
        track_index: Logical track index for the result metadata (informational
            only — all tracks are rendered into a single composite image).
        target_width: Desired render width in pixels. Clamped to
            ``[MIN_WIDTH_PX, MAX_WIDTH_PX]``.

    Returns:
        ``PianoRollRenderResult`` describing what was written.

    Raises:
        OSError: If the output directory cannot be created or the file written.
    """
    width = max(MIN_WIDTH_PX, min(target_width, MAX_WIDTH_PX))

    # Parse MIDI
    try:
        midi = mido.MidiFile(file=io.BytesIO(midi_bytes))
    except Exception as exc:
        logger.warning("⚠️ Failed to parse MIDI for piano roll: %s", exc)
        # Write blank canvas so callers always get a valid PNG
        canvas = _build_canvas(width)
        png_bytes = _encode_png(list(reversed(canvas)), width, IMAGE_HEIGHT)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(png_bytes)
        return PianoRollRenderResult(
            output_path=output_path,
            width_px=width,
            note_count=0,
            track_index=track_index,
            stubbed=True,
        )

    note_events = _parse_note_events(midi)

    if not note_events:
        logger.info("ℹ️ MIDI has no note events — writing blank piano roll at %s", output_path)
        canvas = _build_canvas(width)
        png_bytes = _encode_png(list(reversed(canvas)), width, IMAGE_HEIGHT)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(png_bytes)
        return PianoRollRenderResult(
            output_path=output_path,
            width_px=width,
            note_count=0,
            track_index=track_index,
            stubbed=True,
        )

    total_ticks = max(ev.end_tick for ev in note_events)
    canvas = _build_canvas(width)

    for ev in note_events:
        _draw_note(canvas, ev, total_ticks, width)

    # PNG rows are top-first; MIDI pitches are bottom-first, so reverse.
    png_bytes = _encode_png(list(reversed(canvas)), width, IMAGE_HEIGHT)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(png_bytes)

    logger.info(
        "✅ Piano roll rendered: %d notes → %s (%dx%d px)",
        len(note_events),
        output_path,
        width,
        IMAGE_HEIGHT,
    )

    return PianoRollRenderResult(
        output_path=output_path,
        width_px=width,
        note_count=len(note_events),
        track_index=track_index,
        stubbed=False,
    )
