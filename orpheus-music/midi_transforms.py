"""MIDI transformation utilities for the Orpheus expressiveness layer.

Provides lossless MIDI transposition (preserving drums on channel 10)
and future-proof hooks for other transforms (time-stretch, velocity
scaling, etc.).

All functions write transformed output to temp files so the original
seed library is never mutated.
"""

from __future__ import annotations

import copy
import logging
import tempfile
from pathlib import Path
from typing import Any

import mido

logger = logging.getLogger(__name__)


def transpose_midi(
    midi_path: str | Path,
    semitones: int,
    *,
    skip_drums: bool = True,
    output_dir: str | Path | None = None,
) -> Path:
    """Transpose all note events in a MIDI file by *semitones*.

    - Channel 10 (index 9) is skipped when *skip_drums* is True.
    - The original file is never modified; a new file is written to
      *output_dir* (defaults to ``/tmp``).
    - Pitches are clamped to the MIDI range [0, 127].

    Returns the path to the transposed MIDI file.
    """
    if semitones == 0:
        return Path(midi_path)

    mid = mido.MidiFile(str(midi_path))
    transposed = copy.deepcopy(mid)

    notes_shifted = 0
    notes_clamped = 0

    for track in transposed.tracks:
        for msg in track:
            if msg.type in ("note_on", "note_off"):
                if skip_drums and msg.channel == 9:
                    continue
                new_pitch = msg.note + semitones
                if new_pitch < 0:
                    new_pitch = 0
                    notes_clamped += 1
                elif new_pitch > 127:
                    new_pitch = 127
                    notes_clamped += 1
                msg.note = new_pitch
                notes_shifted += 1

    out_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(midi_path).stem
    direction = "up" if semitones > 0 else "down"
    out_name = f"{stem}_t{direction}{abs(semitones)}.mid"
    out_path = out_dir / out_name

    transposed.save(str(out_path))

    logger.info(
        "🎵 Transposed %s by %+d semitones → %s "
        "(%d notes shifted, %d clamped)",
        Path(midi_path).name, semitones, out_path.name,
        notes_shifted, notes_clamped,
    )

    return out_path


def transpose_notes(
    notes: list[dict[str, Any]],
    semitones: int,
    *,
    is_drums: bool = False,
) -> list[dict[str, Any]]:
    """Transpose a list of parsed note dicts in-place.

    Operates on the ``{"pitch": int, ...}`` dicts produced by
    ``parse_midi_to_notes``.  Drum notes are left untouched.
    """
    if semitones == 0 or is_drums:
        return notes

    for note in notes:
        p = note.get("pitch", 60) + semitones
        note["pitch"] = max(0, min(127, p))

    return notes
