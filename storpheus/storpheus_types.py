"""Typed structures for the Storpheus music generation service.

Progressive generation types (``InstrumentTier``, ``ProgressiveTierResult``,
``ProgressiveGenerationResult``) define the dependency-ordered generation
pipeline introduced in issue #27 (drums → bass → harmony → melody).

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
from enum import Enum

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


# ---------------------------------------------------------------------------
# Progressive generation — dependency-ordered per-role pipeline (#27)
# ---------------------------------------------------------------------------


class InstrumentTier(str, Enum):
    """Musical dependency tier — determines generation order.

    Each tier is generated sequentially, with the previous tier's output MIDI
    used as the seed (prime) for the next tier (cascaded seeding):

        1. DRUMS   — independent; no harmonic context required
        2. BASS    — seeded from drums; establishes root motion
        3. HARMONY — seeded from drums+bass; chords, pads, piano
        4. MELODY  — seeded from drums+bass+harmony; lead, guitar, arp

    Ordering matches musical dependency: rhythm → foundation → colour → line.
    """

    DRUMS = "drums"
    BASS = "bass"
    HARMONY = "harmony"
    MELODY = "melody"


class ProgressiveTierResult(TypedDict):
    """Per-tier result emitted during a progressive generation run.

    One ``ProgressiveTierResult`` is produced for each ``InstrumentTier``
    that contains at least one requested instrument.
    """

    tier: str  # InstrumentTier.value
    instruments: list[str]
    notes: list[WireNoteDict]
    channel_notes: dict[str, list[WireNoteDict]] | None
    metadata: dict[str, object]
    elapsed_seconds: float


class ProgressiveGenerationResult(TypedDict):
    """Full result of a progressive per-role generation run.

    Aggregates all tier results.  ``all_notes`` is a flat union of every
    tier's notes for consumers that do not need per-tier resolution.

    Registered in ``docs/reference/type_contracts.md``.
    """

    success: bool
    composition_id: str
    tier_results: list[ProgressiveTierResult]
    all_notes: list[WireNoteDict]
    total_elapsed_seconds: float
    error: str | None


# ---------------------------------------------------------------------------
# Genre parameter priors and telemetry — per-genre quality tuning (#26)
# ---------------------------------------------------------------------------


@dataclass
class GenreParameterPrior:
    """Explicit per-genre parameter priors for the Orpheus model.

    These are tuned from listening tests and A/B experiments to produce
    genre-appropriate output.  All temperature/top_p values override the
    defaults derived from the control-vector mapping; density_offset biases
    the GenerationControlVector before token-budget allocation.

    Ranges:
        temperature     0.7–1.0   Orpheus safe range (default 0.9)
        top_p           0.90–0.98 Orpheus safe range (default 0.96)
        density_offset  -0.3–0.3  Additive offset on GenerationControlVector.density
        prime_ratio     0.5–1.0   Fraction of max prime tokens to supply
    """

    temperature: float
    top_p: float
    density_offset: float = 0.0
    prime_ratio: float = 1.0


class GenerationTelemetryRecord(TypedDict, total=False):
    """One telemetry record emitted for every completed generation.

    Logged at INFO level in JSON-serialisable form so that external
    consumers (log aggregators, dashboards) can ingest without parsing.

    Input fields (always present):
        genre           Musical style string
        tempo           BPM
        bars            Requested bar count
        instruments     List of requested instrument roles
        quality_preset  "fast" | "balanced" | "quality"
        temperature     Orpheus model temperature used
        top_p           Orpheus model top_p used
        num_prime_tokens  Prime context tokens supplied to the model
        num_gen_tokens    Generation tokens requested from the model
        genre_prior_applied  Whether a genre-specific prior overrode defaults

    Output fields (present on success):
        note_count          Total notes in the selected candidate
        pitch_range         Max MIDI pitch - min MIDI pitch
        velocity_variation  Coefficient of variation for note velocities
        quality_score       Composite quality score 0–1
        rejection_score     Rejection-sampling score of selected candidate
        candidate_count     How many candidates were evaluated
        generation_ok       True = at least one candidate was accepted
    """

    genre: Required[str]
    tempo: Required[int]
    bars: Required[int]
    instruments: list[str]
    quality_preset: str
    temperature: float
    top_p: float
    num_prime_tokens: int
    num_gen_tokens: int
    genre_prior_applied: bool
    note_count: int
    pitch_range: int
    velocity_variation: float
    quality_score: float
    rejection_score: float
    candidate_count: int
    generation_ok: bool


class ParameterSweepResult(TypedDict):
    """Quality metrics plus the parameter set used to produce them."""

    temperature: float
    top_p: float
    quality_score: float
    note_count: int
    pitch_range: int
    velocity_variation: float
    rejection_score: float


class SweepABTestResult(TypedDict):
    """Statistical summary of a parameter sweep A/B test."""

    genre: str
    tempo: int
    bars: int
    sweep_results: list[ParameterSweepResult]
    best_temperature: float
    best_top_p: float
    best_quality_score: float
    score_range: float
    significant: bool  # True when max-min quality gap ≥ 0.05


# ---------------------------------------------------------------------------
# Chunked generation — sliding window for long compositions (#25)
# ---------------------------------------------------------------------------


class ChunkMetadata(TypedDict):
    """Per-chunk metadata emitted during a chunked generation run.

    Field ranges:
        chunk        ≥ 0    zero-based chunk index
        bars         ≥ 1    bar count for this chunk (last chunk may be smaller)
        notes        ≥ 0    notes produced by this chunk after beat-trimming
        beat_offset  ≥ 0.0  beat position of chunk start in the final timeline
        rejection_score  0.0–1.0  candidate rejection score for this chunk; None if unavailable
    """

    chunk: int
    bars: int
    notes: int
    beat_offset: float
    rejection_score: float | None


class ChunkedGenerationResult(TypedDict):
    """Aggregated result of a sliding window chunked generation.

    Produced when ``request.bars > STORPHEUS_CHUNKED_THRESHOLD_BARS``.
    The ``notes`` list spans the full requested bar count with sequential
    beat offsets applied across all chunks.

    Registered in ``docs/reference/type_contracts.md`` under Storpheus Types.
    """

    success: bool
    notes: list[WireNoteDict]
    chunk_count: int
    total_bars: int
    chunk_metadata: list[ChunkMetadata]
    total_elapsed_seconds: float
    error: str | None
