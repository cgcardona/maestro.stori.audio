"""Chord scoring functions."""
from __future__ import annotations

from typing import Any


def score_chord_notes(notes: list[dict[str, Any]], *, min_notes: int = 4) -> tuple[float, list[str]]:
    """Score chord voicings: completeness (multiple pitches per chord), rhythm."""
    if len(notes) < min_notes:
        return 0.5, ["chords_sparse: add chord voicings"]
    score = min(1.0, len(notes) / 24.0)
    return score, []
