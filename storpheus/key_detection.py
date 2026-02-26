"""Krumhansl-Schmuckler key detection for MIDI files.

Uses pitch-class distribution correlation against major/minor key
profiles to estimate the most likely key of a MIDI file or note list.
Pre-computes keys for seed library entries; also used at runtime to
verify generated output matches the intended key.

References:
    Krumhansl, C. L. (1990). *Cognitive Foundations of Musical Pitch*.
    Temperley, D. (2007). *Music and Probability*.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import mido

logger = logging.getLogger(__name__)

# ── Krumhansl-Kessler key profiles ──────────────────────────────────
# Empirically derived weights for each pitch class (C=0 … B=11) in
# major and minor keys rooted at C.  To test against other roots, we
# rotate the observed distribution rather than rotating the profiles.

MAJOR_PROFILE: tuple[float, ...] = (
    6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
    2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
)

MINOR_PROFILE: tuple[float, ...] = (
    6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
    2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
)

_PITCH_NAMES: tuple[str, ...] = (
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B",
)


def _pearson(x: list[float], y: tuple[float, ...]) -> float:
    """Pearson correlation coefficient between two equal-length sequences."""
    n = len(x)
    if n == 0:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    dx = [xi - mean_x for xi in x]
    dy = [yi - mean_y for yi in y]
    num = sum(a * b for a, b in zip(dx, dy))
    den_x = math.sqrt(sum(a * a for a in dx))
    den_y = math.sqrt(sum(b * b for b in dy))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def _rotate(distribution: list[float], n: int) -> list[float]:
    """Rotate a 12-element list by *n* positions to the left.

    Used to align pitch class index *n* with position 0 so we can
    compare against the reference profile rooted at C.
    """
    n = n % 12
    return distribution[n:] + distribution[:n]


def detect_key_from_pitches(
    pitches: list[int],
    *,
    min_notes: int = 8,
) -> tuple[str, str, float] | None:
    """Detect key from a list of MIDI pitch values.

    Returns ``(tonic_name, mode, correlation)`` — e.g. ``("A", "minor", 0.87)``
    — or ``None`` if too few notes to be reliable.
    """
    if len(pitches) < min_notes:
        return None

    pc_counts = [0.0] * 12
    for p in pitches:
        pc_counts[p % 12] += 1.0

    total = sum(pc_counts)
    if total == 0:
        return None

    best_key: str | None = None
    best_mode: str | None = None
    best_corr = -2.0

    for root in range(12):
        rotated = _rotate(pc_counts, root)
        corr_major = _pearson(rotated, MAJOR_PROFILE)
        corr_minor = _pearson(rotated, MINOR_PROFILE)

        if corr_major > best_corr:
            best_corr = corr_major
            best_key = _PITCH_NAMES[root]
            best_mode = "major"

        if corr_minor > best_corr:
            best_corr = corr_minor
            best_key = _PITCH_NAMES[root]
            best_mode = "minor"

    if best_key is None or best_mode is None:
        return None

    return (best_key, best_mode, round(best_corr, 4))


def detect_key(
    midi_path: str | Path,
    *,
    skip_drums: bool = True,
    min_notes: int = 8,
) -> tuple[str, str, float] | None:
    """Detect the key of a MIDI file.

    Args:
        midi_path: Path to a ``.mid`` file.
        skip_drums: Exclude channel 10 (GM drums) from analysis.
        min_notes: Minimum note count to attempt detection.

    Returns:
        ``(tonic_name, mode, correlation)`` or ``None``.
    """
    try:
        mid = mido.MidiFile(str(midi_path))
    except Exception:
        logger.warning("⚠️ Could not read MIDI file for key detection: %s", midi_path)
        return None

    pitches: list[int] = []
    for track in mid.tracks:
        for msg in track:
            if msg.type == "note_on" and msg.velocity > 0:
                if skip_drums and msg.channel == 9:
                    continue
                pitches.append(msg.note)

    return detect_key_from_pitches(pitches, min_notes=min_notes)


def key_to_semitones(tonic: str, mode: str) -> int:
    """Convert a key name to semitones above C.

    ``key_to_semitones("A", "minor")`` → 9
    ``key_to_semitones("C", "major")`` → 0
    """
    try:
        return _PITCH_NAMES.index(tonic.strip().upper()
                                   .replace("♯", "#")
                                   .replace("♭", "b"))
    except ValueError:
        # Handle flats by converting to enharmonic sharps
        flat_to_sharp = {
            "DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#",
        }
        normalised = tonic.strip().upper().replace("♭", "B")
        sharp = flat_to_sharp.get(normalised)
        if sharp is not None:
            return _PITCH_NAMES.index(sharp)
        raise ValueError(f"Unknown pitch name: {tonic!r}")


def transpose_distance(
    source_tonic: str,
    source_mode: str,
    target_tonic: str,
    target_mode: str,
) -> int:
    """Compute the shortest transposition in semitones from source to target key.

    Returns a value in [-6, +6].  Mode is informational — distance is
    purely based on tonic pitch class distance.
    """
    src = key_to_semitones(source_tonic, source_mode)
    tgt = key_to_semitones(target_tonic, target_mode)
    delta = (tgt - src) % 12
    if delta > 6:
        delta -= 12
    return delta


def parse_key_string(key_str: str) -> tuple[str, str] | None:
    """Parse a key string like ``"Am"``, ``"C major"``, ``"F# minor"`` into
    ``(tonic, mode)``."""
    key_str = key_str.strip()
    if not key_str:
        return None

    # "Am", "Cm", "F#m" — short minor notation
    if key_str.endswith("m") and len(key_str) <= 3:
        return (key_str[:-1], "minor")

    parts = key_str.split()
    if len(parts) == 2:
        tonic, mode = parts
        mode = mode.lower()
        if mode in ("major", "maj"):
            return (tonic, "major")
        if mode in ("minor", "min"):
            return (tonic, "minor")

    # Single letter — assume major
    if len(key_str) <= 2 and key_str[0].isalpha():
        return (key_str, "major")

    return None
