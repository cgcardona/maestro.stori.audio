"""Typed structures for the Storpheus music generation service.

Defines the MIDI event shapes, parsed result types, and scoring entities
used throughout the Storpheus codebase.  These mirror ``app/contracts/json_types.py``
in the Maestro service but are defined independently to avoid
cross-container imports.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from typing_extensions import Required, TypedDict


class StorpheusNoteDict(TypedDict, total=False):
    """A single MIDI note as parsed from a MIDI file."""

    pitch: Required[int]
    start_beat: Required[float]
    duration_beats: Required[float]
    velocity: Required[int]


class StorpheusCCEvent(TypedDict):
    """A MIDI Control Change event."""

    cc: int
    beat: float
    value: int


class StorpheusPitchBend(TypedDict):
    """A MIDI Pitch Bend event."""

    beat: float
    value: int


class StorpheusAftertouch(TypedDict, total=False):
    """A MIDI Aftertouch event (channel or poly)."""

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
    """

    bars: int
    target_key: str | None
    expected_channels: int
    target_density: float | None = None
    register_center: int | None = None
    register_spread: int | None = None
    velocity_floor: int | None = None
    velocity_ceiling: int | None = None


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
