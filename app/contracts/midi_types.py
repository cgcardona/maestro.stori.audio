"""Canonical MIDI primitive type aliases.

Single source of truth for MIDI value ranges used throughout Maestro.

These ``Annotated`` aliases carry constraint metadata at every layer:

- **Pydantic BaseModel fields**: ``Field`` constraints are enforced at
  parse/validation time, so invalid values raise ``ValidationError``
  before they ever reach business logic.
- **TypedDicts**: mypy sees the annotation; the named alias
  self-documents the expected range without a prose comment.
- **Frozen dataclasses** (``contracts.py``): use
  ``_assert_midi_range`` in ``__post_init__`` for runtime checks.

MIDI standard ranges
--------------------
+-------------------+-------------------+----------------------------------+
| Primitive         | Range             | Notes                            |
+===================+===================+==================================+
| Pitch             | 0 – 127           | C-1=0, Middle C=60, G9=127       |
| Velocity          | 0 – 127           | 0 = note-off equivalent          |
| Channel           | 0 – 15            | 16 channels, drums = ch 9        |
| CC number         | 0 – 127           | Controller number                |
| CC value          | 0 – 127           | Controller value                 |
| Aftertouch        | 0 – 127           | Pressure value                   |
| Pitch bend        | −8192 – 8191      | 14-bit signed, 0 = centre        |
| GM program        | 0 – 127           | General MIDI patch number        |
| Tempo (BPM)       | 20 – 300          | Fractional BPM is not a concept  |
| Beat position     | ≥ 0.0             | Fractional; e.g. 1.5 = "and" 1  |
| Beat duration     | > 0.0             | Must be strictly positive        |
| Arrangement beat  | ≥ 0 (int)         | Bar-aligned section offset       |
| Arrangement dur.  | ≥ 1 (int)         | bars × time-sig numerator        |
| Bars              | ≥ 1               | Positive integer                 |
+-------------------+-------------------+----------------------------------+
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field


# ── MIDI byte values (7-bit, 0–127) ─────────────────────────────────────────

MidiPitch = Annotated[int, Field(ge=0, le=127)]
"""MIDI note number. C-1 = 0, Middle C = 60, G9 = 127."""

MidiVelocity = Annotated[int, Field(ge=0, le=127)]
"""Note velocity. 0 is the note-off equivalent; 1–127 is the audible range."""

MidiChannel = Annotated[int, Field(ge=0, le=15)]
"""Zero-indexed MIDI channel. Drums conventionally use channel 9."""

MidiCC = Annotated[int, Field(ge=0, le=127)]
"""MIDI Control Change controller number (0–127)."""

MidiCCValue = Annotated[int, Field(ge=0, le=127)]
"""MIDI Control Change value (0–127)."""

MidiAftertouchValue = Annotated[int, Field(ge=0, le=127)]
"""Channel or poly aftertouch pressure value (0–127)."""

MidiGMProgram = Annotated[int, Field(ge=0, le=127)]
"""General MIDI program (patch) number. 0-indexed; add 1 for GM display number."""

# ── 14-bit MIDI values ───────────────────────────────────────────────────────

MidiPitchBend = Annotated[int, Field(ge=-8192, le=8191)]
"""14-bit signed pitch bend. 0 = centre; ±8192 = full deflection (±2 semitones default)."""

# ── Tempo ────────────────────────────────────────────────────────────────────

MidiBPM = Annotated[int, Field(ge=20, le=300)]
"""Composition tempo in beats per minute.

Always an integer — fractional BPM is not a DAW concept.  Practical
compositions live in [40, 240]; the wider [20, 300] window accommodates
extreme styles without rejecting valid user input.
"""

# ── Beat-based timing (note level — fractional) ──────────────────────────────

BeatPosition = Annotated[float, Field(ge=0.0)]
"""Absolute beat position. Can be fractional (e.g. 1.5 = the 'and' of beat 1)."""

BeatDuration = Annotated[float, Field(gt=0.0)]
"""Duration in beats. Must be strictly positive (zero-length notes are invalid)."""

# ── Arrangement-level timing (section level — always integer, bar-aligned) ───

ArrangementBeat = Annotated[int, Field(ge=0)]
"""Bar-aligned beat offset. Sections always start at whole-beat positions."""

ArrangementDuration = Annotated[int, Field(ge=1)]
"""Section duration in beats. Derived from bars × time-signature numerator."""

Bars = Annotated[int, Field(ge=1)]
"""Bar count. Always a positive integer."""


# ── Validation helper for non-Pydantic contexts ─────────────────────────────

def _assert_range(value: int | float, lo: int | float, hi: int | float, name: str) -> None:
    """Raise ``ValueError`` when ``value`` is outside ``[lo, hi]``.

    Used in dataclass ``__post_init__`` methods where Pydantic's field
    validation is unavailable.  Prefer Pydantic ``Field(ge=..., le=...)``
    for BaseModel fields; use this only for frozen dataclasses.
    """
    if not (lo <= value <= hi):
        raise ValueError(f"{name} must be in [{lo}, {hi}], got {value!r}")
