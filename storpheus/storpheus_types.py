"""Typed structures for the Storpheus music generation service.

Defines the MIDI event shapes, parsed result types, and scoring entities
used throughout the Storpheus codebase.  These mirror ``app/contracts/json_types.py``
in the Maestro service but are defined independently to avoid
cross-container imports.

MIDI primitive ranges (enforced in dataclass ``__post_init__`` methods;
documented here for TypedDicts which have no runtime enforcement):

    pitch         0–127   MIDI note number
    velocity      0–127   0 = note-off equivalent; 1–127 audible
    channel       0–15    16 MIDI channels, zero-indexed
    cc            0–127   CC controller number
    cc value      0–127   CC value
    pitch bend    −8192–8191  14-bit signed; 0 = centre
    aftertouch    0–127   pressure value
    tempo (BPM)   20–300  always an integer
    bars          ≥ 1     positive integer
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from typing_extensions import Required, TypedDict

# ── MIDI range constants (mirrors app/contracts/midi_types.py) ───────────────
# Duplicated from Maestro contracts to avoid cross-container imports.
_MIDI_MIN: int = 0
_MIDI_MAX: int = 127
_MIDI_CHANNEL_MAX: int = 15
_MIDI_PITCH_BEND_MIN: int = -8192
_MIDI_PITCH_BEND_MAX: int = 8191
_BPM_MIN: int = 20
_BPM_MAX: int = 300
_BARS_MIN: int = 1


def _assert_range(value: int | float, lo: int | float, hi: int | float, name: str) -> None:
    """Raise ``ValueError`` when ``value`` is outside ``[lo, hi]``."""
    if not (lo <= value <= hi):
        raise ValueError(f"{name} must be in [{lo}, {hi}], got {value!r}")


class StorpheusNoteDict(TypedDict, total=False):
    """A single MIDI note as parsed from a MIDI file.

    Field ranges:
        pitch         0–127   MIDI note number
        start_beat    ≥ 0.0   beat position (fractional allowed)
        duration_beats > 0.0  beat duration (fractional allowed)
        velocity      0–127   note velocity
    """

    pitch: Required[int]
    start_beat: Required[float]
    duration_beats: Required[float]
    velocity: Required[int]


class StorpheusCCEvent(TypedDict):
    """A MIDI Control Change event.

    Field ranges:
        cc    0–127   controller number
        beat  ≥ 0.0   beat position (fractional allowed)
        value 0–127   controller value
    """

    cc: int
    beat: float
    value: int


class StorpheusPitchBend(TypedDict):
    """A MIDI Pitch Bend event.

    Field ranges:
        beat  ≥ 0.0          beat position (fractional allowed)
        value −8192–8191     14-bit signed; 0 = centre
    """

    beat: float
    value: int


class StorpheusAftertouch(TypedDict, total=False):
    """A MIDI Aftertouch event (channel or poly).

    Field ranges:
        beat  ≥ 0.0   beat position (fractional allowed)
        value 0–127   pressure value
        pitch 0–127   note number (poly aftertouch only; omit for channel pressure)
    """

    beat: Required[float]
    value: Required[int]
    pitch: int


class ParsedMidiResult(TypedDict):
    """Return type of ``parse_midi_to_notes``."""

    notes: dict[int, list[StorpheusNoteDict]]
    cc_events: dict[int, list[StorpheusCCEvent]]
    pitch_bends: dict[int, list[StorpheusPitchBend]]
    aftertouch: dict[int, list[StorpheusAftertouch]]
    program_changes: dict[int, int]


class CacheKeyData(TypedDict):
    """Canonical request fields used for cache key generation."""

    genre: str
    tempo: int
    key: str
    instruments: list[str]
    bars: int
    intent_goals: list[str]
    energy: float
    valence: float
    tension: float
    intimacy: float
    motion: float
    quality_preset: str


class FulfillmentReport(TypedDict):
    """Constraint-fulfillment report produced after candidate selection."""

    goal_scores: dict[str, float]
    constraint_violations: list[str]
    coverage_pct: float


class GradioGenerationParams(TypedDict):
    """Concrete Gradio API parameters derived from the generation control vector."""

    temperature: float
    top_p: float
    num_prime_tokens: int
    num_gen_tokens: int


class WireNoteDict(TypedDict):
    """A single MIDI note in the camelCase wire format sent to Maestro.

    ``StorpheusNoteDict`` is used internally (snake_case).  This type is
    used only in ``GenerateResponse`` fields that cross the API boundary.
    """

    pitch: int
    startBeat: float
    durationBeats: float
    velocity: int


class GenerationComparison(TypedDict):
    """Result of comparing two generation candidates."""

    generation_a: dict[str, float]
    generation_b: dict[str, float]
    winner: str  # "a" | "b" | "tie"
    confidence: float


class QualityEvalParams(TypedDict, total=False):
    """Parameters for a tool call inside a quality evaluation request.

    Only ``addNotes`` is currently scored; other tool types are ignored.
    """

    notes: list[StorpheusNoteDict]


class QualityEvalToolCall(TypedDict):
    """A single tool call as submitted to the ``/quality/evaluate`` endpoint."""

    tool: str
    params: QualityEvalParams


@dataclass
class ScoringParams:
    """All scoring parameters passed to ``score_candidate``.

    Extracted from the generation request and policy controls before
    the candidate-selection loop so each call is explicit and typed.

    Field ranges:
        bars              ≥ 1       positive integer
        expected_channels ≥ 1       number of MIDI channels expected
        register_center   0–127     target pitch center (MIDI note number)
        register_spread   0–64      semitone spread around ``register_center``
        velocity_floor    0–127     minimum acceptable note velocity
        velocity_ceiling  0–127     maximum acceptable note velocity
    """

    bars: int
    target_key: str | None
    expected_channels: int
    target_density: float | None = None
    register_center: int | None = None
    register_spread: int | None = None
    velocity_floor: int | None = None
    velocity_ceiling: int | None = None

    def __post_init__(self) -> None:
        """Validate MIDI primitive ranges at construction time."""
        _assert_range(self.bars, _BARS_MIN, 65536, "bars")
        _assert_range(self.expected_channels, 1, 16, "expected_channels")
        if self.register_center is not None:
            _assert_range(self.register_center, _MIDI_MIN, _MIDI_MAX, "register_center")
        if self.register_spread is not None:
            _assert_range(self.register_spread, 0, 64, "register_spread")
        if self.velocity_floor is not None:
            _assert_range(self.velocity_floor, _MIDI_MIN, _MIDI_MAX, "velocity_floor")
        if self.velocity_ceiling is not None:
            _assert_range(self.velocity_ceiling, _MIDI_MIN, _MIDI_MAX, "velocity_ceiling")
        if (
            self.velocity_floor is not None
            and self.velocity_ceiling is not None
            and self.velocity_floor > self.velocity_ceiling
        ):
            raise ValueError(
                f"velocity_floor ({self.velocity_floor}) must be ≤ "
                f"velocity_ceiling ({self.velocity_ceiling})"
            )


@dataclass
class BestCandidate:
    """The winning candidate retained after rejection-sampling evaluation.

    Wraps everything needed to continue post-processing without carrying
    a loosely-typed ``dict`` through the generation pipeline.
    """

    midi_result: Sequence[object]  # Gradio response: [audio, plot, midi_path, …]
    midi_path: str
    parsed: ParsedMidiResult
    flat_notes: list[StorpheusNoteDict]
    batch_idx: int
