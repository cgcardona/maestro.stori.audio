"""Pydantic v2 models for the Muse Hub Analysis API and Dynamics Page.

Each musical dimension has a dedicated typed data model.  All models are
consumed by AI agents and must be fully described so agents can reason
about musical properties programmatically.

Dimensions supported (13 total):
  harmony, dynamics, motifs, form, groove, emotion, chord-map,
  contour, key, tempo, meter, similarity, divergence

Every endpoint returns an :class:`AnalysisResponse` envelope whose ``data``
field is one of the dimension-specific ``*Data`` models below.  The
aggregate endpoint returns :class:`AggregateAnalysisResponse` containing
one ``AnalysisResponse`` per dimension.

Design contract:
- CamelCase on the wire (via :class:`~maestro.models.base.CamelModel`).
- All float fields are rounded to 4 decimal places in the service layer.
- Stub data is deterministic for a given ``ref`` value.
- ``filters_applied`` records which query-param filters were active so
  agents can tell whether the result is narrowed or full-spectrum.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from maestro.models.base import CamelModel

# Arc classification labels for dynamic contour per track.
# Each label describes the overall shape of the velocity curve:
#   flat       — velocity is nearly constant throughout
#   terraced   — abrupt step changes between dynamic levels
#   crescendo  — steady or gradual increase in velocity
#   decrescendo — steady or gradual decrease in velocity
#   swell      — rises then falls (arch shape)
#   hairpin    — falls then rises (valley shape)
DynamicArc = Literal["flat", "terraced", "crescendo", "decrescendo", "swell", "hairpin"]

# ---------------------------------------------------------------------------
# Filter envelope (shared across all dimension responses)
# ---------------------------------------------------------------------------


class AnalysisFilters(CamelModel):
    """Query-param filters applied to the analysis computation.

    ``None`` means the filter was not applied (full-spectrum result).
    Agents can inspect this to decide whether to re-query with a specific
    track or section scope.
    """

    track: str | None = Field(None, description="Track/instrument filter, e.g. 'bass'")
    section: str | None = Field(None, description="Musical section filter, e.g. 'chorus'")


# ---------------------------------------------------------------------------
# Per-dimension data models
# ---------------------------------------------------------------------------


class ChordEvent(CamelModel):
    """A single chord occurrence in a chord progression.

    ``beat`` is the onset position in beats from the top of the ref.
    ``chord`` is a standard chord symbol (e.g. 'Cmaj7', 'Am7b5').
    ``function`` is the Roman-numeral harmonic function (e.g. 'I', 'IIm7', 'V7').
    ``tension`` is a 0–1 score where 1 is maximally dissonant.
    """

    beat: float
    chord: str
    function: str
    tension: float = Field(..., ge=0.0, le=1.0)


class ModulationPoint(CamelModel):
    """A detected key change in the harmonic analysis."""

    beat: float
    from_key: str
    to_key: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class HarmonyData(CamelModel):
    """Structured harmonic analysis for a Muse commit.

    Provides the detected key, chord progression, tension curve, and any
    modulation points.  Agents use this to compose harmonically coherent
    continuations or variations.

    ``tension_curve`` is sampled at one-beat intervals; its length equals
    ``total_beats``.
    """

    tonic: str = Field(..., description="Detected tonic pitch class, e.g. 'C', 'F#'")
    mode: str = Field(..., description="Detected mode, e.g. 'major', 'dorian', 'mixolydian'")
    key_confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in key detection")
    chord_progression: list[ChordEvent]
    tension_curve: list[float] = Field(
        ..., description="Per-beat tension scores (0–1); length == total_beats"
    )
    modulation_points: list[ModulationPoint]
    total_beats: int


class VelocityEvent(CamelModel):
    """MIDI velocity measurement at a specific beat position."""

    beat: float
    velocity: int = Field(..., ge=0, le=127)


class DynamicsData(CamelModel):
    """Structured dynamic (loudness/velocity) analysis for a Muse commit.

    Agents use this to match dynamic contour when generating continuation
    material — e.g. avoid a ff outro after a pp intro.
    """

    peak_velocity: int = Field(..., ge=0, le=127)
    mean_velocity: float = Field(..., ge=0.0, le=127.0)
    min_velocity: int = Field(..., ge=0, le=127)
    dynamic_range: int = Field(..., ge=0, le=127, description="peak_velocity - min_velocity")
    velocity_curve: list[VelocityEvent]
    dynamic_events: list[str] = Field(
        ..., description="Detected articulations, e.g. ['crescendo@4', 'sfz@12']"
    )


class MotifTransformation(CamelModel):
    """A single transformation of a motif (inversion, retrograde, transposition).

    Each transformation is a variant of the parent motif that has been detected
    in the piece.  ``intervals`` is the transformed interval sequence.
    ``transposition_semitones`` is non-zero only for transposition transformations.
    ``occurrences`` are the beat positions where this specific transformation appears.
    """

    transformation_type: str = Field(
        ...,
        description="One of: inversion, retrograde, retrograde-inversion, transposition",
    )
    intervals: list[int] = Field(
        ..., description="Transformed interval sequence in semitones"
    )
    transposition_semitones: int = Field(
        0, description="Semitones of transposition (0 for non-transposition variants)"
    )
    occurrences: list[float] = Field(
        ..., description="Beat positions where this transformation appears"
    )
    track: str = Field(..., description="Track where this transformation was found")


class MotifRecurrenceCell(CamelModel):
    """A single cell in the motif recurrence grid (track x section).

    Encodes whether a motif (or one of its transformations) appears in a
    specific track at a specific formal section.  Used by the motif browser
    to render the recurrence heatmap.
    """

    track: str
    section: str
    present: bool
    occurrence_count: int = Field(
        0, description="How many times the motif appears in this cell"
    )
    transformation_types: list[str] = Field(
        default_factory=list,
        description="Which transformation types are present ('original', 'inversion', etc.)",
    )


class MotifEntry(CamelModel):
    """A detected melodic or rhythmic motif with transformation and recurrence data.

    ``intervals`` is the interval sequence in semitones (signed).
    ``occurrences`` lists the beat positions where the original motif starts.
    ``contour_label`` classifies the melodic shape for human and agent readability.
    ``tracks`` lists every instrument track where this motif or its transformations appear.
    ``transformations`` captures inversion, retrograde, and transposition variants.
    ``recurrence_grid`` provides a flat list of track×section cells for heatmap rendering.
    """

    motif_id: str
    intervals: list[int] = Field(..., description="Melodic intervals in semitones")
    length_beats: float
    occurrence_count: int
    occurrences: list[float] = Field(
        ..., description="Beat positions of each original occurrence"
    )
    track: str = Field(..., description="Primary instrument track where this motif was detected")
    contour_label: str = Field(
        ...,
        description=(
            "Melodic contour shape: ascending-step, descending-step, arch, "
            "valley, oscillating, or static"
        ),
    )
    tracks: list[str] = Field(
        ...,
        description="All tracks where this motif or its transformations appear (cross-track sharing)",
    )
    transformations: list[MotifTransformation] = Field(
        ..., description="Detected transformations of this motif"
    )
    recurrence_grid: list[MotifRecurrenceCell] = Field(
        ...,
        description="Flat track×section recurrence grid for heatmap rendering",
    )


class MotifsData(CamelModel):
    """All detected melodic/rhythmic motifs in a Muse commit.

    Agents use this to identify recurring themes and decide whether to
    develop, vary, or contrast a motif in the next section.

    The ``sections`` field lists the formal section labels present in this
    ref so the motif browser can render the recurrence grid column headers.
    The ``all_tracks`` field lists every active instrument track so the
    browser can render row headers.
    """

    total_motifs: int
    motifs: list[MotifEntry]
    sections: list[str] = Field(
        ..., description="Formal section labels (column headers for the recurrence grid)"
    )
    all_tracks: list[str] = Field(
        ..., description="All active instrument tracks (row headers for the recurrence grid)"
    )


class SectionEntry(CamelModel):
    """A single formal section (e.g. intro, verse, chorus, bridge, outro)."""

    label: str = Field(..., description="Section label, e.g. 'intro', 'verse_1', 'chorus'")
    function: str = Field(
        ..., description="Formal function, e.g. 'exposition', 'development', 'recapitulation'"
    )
    start_beat: float
    end_beat: float
    length_beats: float


class FormData(CamelModel):
    """High-level formal structure of a Muse commit.

    Agents use this to understand where they are in the compositional arc
    and what macro-form conventions the piece is following.
    """

    form_label: str = Field(
        ..., description="Detected macro form, e.g. 'AABA', 'verse-chorus', 'through-composed'"
    )
    total_beats: int
    sections: list[SectionEntry]


class GrooveData(CamelModel):
    """Rhythmic groove analysis for a Muse commit.

    ``onset_deviation`` measures the mean absolute deviation of note onsets
    from the quantization grid in beats.  Lower = tighter quantization.
    ``swing_factor`` is 0.5 for straight time, ~0.67 for triplet swing.
    """

    swing_factor: float = Field(
        ..., ge=0.0, le=1.0, description="0.5=straight, 0.67=hard swing"
    )
    grid_resolution: str = Field(
        ..., description="Quantization grid, e.g. '1/16', '1/8T'"
    )
    onset_deviation: float = Field(
        ..., ge=0.0, description="Mean absolute note onset deviation from grid (beats)"
    )
    groove_score: float = Field(
        ..., ge=0.0, le=1.0, description="Aggregate rhythmic tightness (1=very tight)"
    )
    style: str = Field(..., description="Detected groove style, e.g. 'straight', 'swing', 'shuffled'")
    bpm: float


class EmotionData(CamelModel):
    """Affective/emotional profile of a Muse commit.

    Uses the valence-arousal model.  ``valence`` is -1 (sad/tense) to +1
    (happy/bright).  ``arousal`` is 0 (calm) to 1 (energetic).
    ``tension`` is 0 (relaxed) to 1 (tense/dissonant).
    Agents use this to maintain emotional continuity or introduce contrast.
    """

    valence: float = Field(..., ge=-1.0, le=1.0, description="-1=sad/dark, +1=happy/bright")
    arousal: float = Field(..., ge=0.0, le=1.0, description="0=calm, 1=energetic")
    tension: float = Field(..., ge=0.0, le=1.0, description="0=relaxed, 1=tense/dissonant")
    primary_emotion: str = Field(
        ..., description="Dominant emotion label, e.g. 'joyful', 'melancholic', 'tense', 'serene'"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Emotion map models (issue #227)
# ---------------------------------------------------------------------------


class EmotionVector(CamelModel):
    """Four-axis emotion vector, all dimensions normalised to [0, 1].

    - ``energy``   — 0 (passive/still) to 1 (active/driving)
    - ``valence``  — 0 (dark/negative) to 1 (bright/positive)
    - ``tension``  — 0 (relaxed) to 1 (tense/dissonant)
    - ``darkness`` — 0 (luminous) to 1 (brooding/ominous)

    Note that ``valence`` here is re-normalised relative to :class:`EmotionData`
    (which uses –1…+1) so that all four axes share the same visual scale in charts.
    """

    energy: float = Field(..., ge=0.0, le=1.0)
    valence: float = Field(..., ge=0.0, le=1.0)
    tension: float = Field(..., ge=0.0, le=1.0)
    darkness: float = Field(..., ge=0.0, le=1.0)


class EmotionMapPoint(CamelModel):
    """Emotion vector sample at a specific beat position within a ref.

    Used to render the intra-ref emotion evolution chart (x=beat, y=0–1 per dimension).
    """

    beat: float
    vector: EmotionVector


class CommitEmotionSnapshot(CamelModel):
    """Summary emotion vector for a single commit in the trajectory view.

    Used to render the cross-commit emotion trajectory chart (x=commit index, y=0–1).
    """

    commit_id: str
    message: str
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp")
    vector: EmotionVector
    primary_emotion: str = Field(
        ..., description="Dominant emotion label for this commit, e.g. 'serene', 'tense'"
    )


class EmotionDrift(CamelModel):
    """Emotion drift distance between two consecutive commits.

    ``drift`` is the Euclidean distance in the four-dimensional emotion space (0–√4≈1.41).
    A drift near 0 means the emotional character was stable; near 1 means a large shift.
    """

    from_commit: str
    to_commit: str
    drift: float = Field(..., ge=0.0, description="Euclidean distance in emotion space (0–√4)")
    dominant_change: str = Field(
        ..., description="Which axis changed most, e.g. 'energy', 'tension'"
    )


class EmotionMapResponse(CamelModel):
    """Full emotion map for a Muse repo ref.

    Combines intra-ref per-beat evolution, cross-commit trajectory,
    drift distances, narrative text, and source attribution.

    Returned by ``GET /musehub/repos/{repo_id}/analysis/{ref}/emotion-map``.
    Agents and the MuseHub UI use this to render emotion arc visualisations.
    """

    repo_id: str
    ref: str
    computed_at: datetime
    filters_applied: AnalysisFilters

    # Intra-ref: how the emotion evolves beat-by-beat within this ref
    evolution: list[EmotionMapPoint] = Field(
        ..., description="Per-beat emotion samples within this ref"
    )
    # Aggregate vector for this ref (mean of evolution points)
    summary_vector: EmotionVector

    # Cross-commit: emotion snapshots for recent ancestor commits + this ref
    trajectory: list[CommitEmotionSnapshot] = Field(
        ...,
        description="Emotion snapshot per commit in the recent history (oldest first, head last)",
    )
    drift: list[EmotionDrift] = Field(
        ..., description="Drift distances between consecutive commits in the trajectory"
    )

    # Human-readable narrative
    narrative: str = Field(
        ..., description="Textual description of the emotional journey across the trajectory"
    )

    # Attribution
    source: Literal["explicit", "inferred", "mixed"] = Field(
        ...,
        description="How emotion was determined: 'explicit' (tags), 'inferred' (model), or 'mixed'",
    )


class ChordMapData(CamelModel):
    """Full chord-by-chord map for a Muse commit.

    Equivalent to a lead-sheet chord chart.  Agents use this to generate
    harmonically idiomatic accompaniment or improvisation.
    ``progression`` is time-ordered, covering the full duration of the ref.
    """

    progression: list[ChordEvent]
    total_chords: int
    total_beats: int


class ContourData(CamelModel):
    """Melodic contour analysis for the primary melodic voice.

    ``shape`` is a coarse descriptor; ``pitch_curve`` is sampled at
    quarter-note intervals and gives the predominant pitch in MIDI note
    numbers.  Agents use contour to match or contrast melodic shape
    in continuation material.
    """

    shape: str = Field(
        ..., description="Coarse shape label, e.g. 'arch', 'ascending', 'descending', 'flat', 'wave'"
    )
    direction_changes: int = Field(
        ..., description="Number of times the melodic direction reverses"
    )
    peak_beat: float = Field(..., description="Beat position of the melodic peak")
    valley_beat: float = Field(..., description="Beat position of the melodic valley")
    overall_direction: str = Field(
        ..., description="Net direction from first to last note, e.g. 'up', 'down', 'flat'"
    )
    pitch_curve: list[float] = Field(
        ..., description="MIDI pitch sampled at quarter-note intervals"
    )


class AlternateKey(CamelModel):
    """A secondary key candidate with its confidence score."""

    tonic: str
    mode: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class KeyData(CamelModel):
    """Key detection result for a Muse commit.

    ``alternate_keys`` lists other plausible keys ranked by confidence,
    which is useful when the piece is tonally ambiguous.
    Agents use this to select compatible scale material for generation.
    """

    tonic: str
    mode: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    relative_key: str = Field(..., description="Relative major/minor key, e.g. 'Am' for 'C major'")
    alternate_keys: list[AlternateKey]


class TempoChange(CamelModel):
    """A tempo change event at a specific beat position."""

    beat: float
    bpm: float


class TempoData(CamelModel):
    """Tempo and time-feel analysis for a Muse commit.

    ``stability`` is 0 (widely varying tempo) to 1 (perfectly metronomic).
    ``tempo_changes`` is empty for a constant-tempo piece.
    Agents use this to generate rhythmically coherent continuation material
    and to detect rubato or accelerando passages.
    """

    bpm: float = Field(..., description="Primary (mean) BPM")
    stability: float = Field(..., ge=0.0, le=1.0, description="0=free tempo, 1=metronomic")
    time_feel: str = Field(
        ..., description="Perceived time feel, e.g. 'straight', 'laid-back', 'rushing'"
    )
    tempo_changes: list[TempoChange]


class IrregularSection(CamelModel):
    """A section where the time signature differs from the primary meter."""

    start_beat: float
    end_beat: float
    time_signature: str


class MeterData(CamelModel):
    """Metric analysis for a Muse commit.

    ``beat_strength_profile`` is the per-beat strength across one bar
    (e.g. [1.0, 0.2, 0.6, 0.2] for 4/4).  Agents use this to place
    accents and avoid metrically naïve generation.
    """

    time_signature: str = Field(..., description="Primary time signature, e.g. '4/4', '6/8'")
    irregular_sections: list[IrregularSection]
    beat_strength_profile: list[float] = Field(
        ..., description="Relative beat strengths across one bar (sums to 1.0 approximately)"
    )
    is_compound: bool = Field(..., description="True for compound meters like 6/8, 12/8")


class SimilarCommit(CamelModel):
    """A commit that is harmonically/rhythmically similar to the queried ref.

    ``score`` is 0–1 cosine similarity.  ``shared_motifs`` lists motif IDs
    that appear in both commits.
    """

    ref: str
    score: float = Field(..., ge=0.0, le=1.0)
    shared_motifs: list[str]
    commit_message: str


class SimilarityData(CamelModel):
    """Cross-commit similarity analysis for a Muse ref.

    Agents use this to find the most musically relevant commit to base a
    variation or continuation on, rather than always using HEAD.
    """

    similar_commits: list[SimilarCommit]
    embedding_dimensions: int = Field(
        ..., description="Dimensionality of the musical embedding used"
    )


class ChangedDimension(CamelModel):
    """A musical dimension that changed significantly relative to the base ref."""

    dimension: str
    change_magnitude: float = Field(..., ge=0.0, le=1.0)
    description: str


class DivergenceData(CamelModel):
    """Divergence analysis between a ref and its parent (or a baseline).

    Agents use this to understand how much a commit changed the musical
    character of a piece — useful for deciding whether to accept or revert
    a generative commit.
    """

    divergence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Aggregate divergence from parent (0=identical, 1=completely different)"
    )
    base_ref: str = Field(..., description="The ref this divergence was computed against")
    changed_dimensions: list[ChangedDimension]


# Form and structure page models (issue #225)
# ---------------------------------------------------------------------------


class SectionMapEntry(CamelModel):
    """A formal section rendered in bars for the section map visualisation.

    Bar numbers are 1-indexed.  ``color_hint`` is a CSS colour string agents
    and UIs can use for colour-coding the section timeline without computing
    a palette themselves.
    """

    label: str = Field(..., description="Section label, e.g. 'intro', 'verse_1', 'chorus'")
    function: str = Field(..., description="Formal function, e.g. 'exposition', 'climax'")
    start_bar: int = Field(..., ge=1, description="First bar of this section (1-indexed)")
    end_bar: int = Field(..., ge=1, description="Last bar of this section (1-indexed, inclusive)")
    bar_count: int = Field(..., ge=1, description="Number of bars in this section")
    color_hint: str = Field(..., description="CSS colour string for this section type")


class RepetitionEntry(CamelModel):
    """A group of sections that share the same structural role (repeat).

    ``occurrences`` lists the 1-indexed bar positions where this pattern
    starts.  ``similarity_score`` is 1.0 for exact repeats, lower for
    varied repetitions.
    """

    pattern_label: str = Field(..., description="Canonical name for this repeated pattern, e.g. 'chorus'")
    occurrences: list[int] = Field(..., description="1-indexed start bars of each occurrence")
    occurrence_count: int = Field(..., ge=1)
    similarity_score: float = Field(
        ..., ge=0.0, le=1.0, description="Mean pairwise similarity across occurrences (1=exact repeat)"
    )


class SectionSimilarityHeatmap(CamelModel):
    """Pairwise similarity matrix between all formal sections.

    ``labels`` lists section labels in the same order as ``matrix`` rows/cols.
    ``matrix`` is a square, symmetric matrix where ``matrix[i][j]`` is the
    0–1 cosine similarity between section i and section j.  Diagonal is 1.0.

    Agents use this to identify musical cousins (verses that sound similar
    to each other) and contrasting sections (bridge vs. chorus).
    """

    labels: list[str] = Field(..., description="Section labels, ordered to match matrix rows/cols")
    matrix: list[list[float]] = Field(
        ..., description="Square symmetric similarity matrix; matrix[i][j] in [0, 1]"
    )


class FormStructureResponse(CamelModel):
    """Combined form and structure analysis for a Muse commit ref.

    Returned by ``GET /api/v1/musehub/repos/{repo_id}/form-structure/{ref}``.
    Combines three complementary views of the piece's formal architecture:

    - ``section_map``: timeline of sections with bar ranges and colour hints
    - ``repetition_structure``: which sections repeat and how often
    - ``section_comparison``: pairwise similarity heatmap for all sections

    Agents use this as the structural context document before generating
    a new section — it answers "where am I in the form?" and "what sounds
    like what?" without requiring multiple analysis requests.
    """

    repo_id: str
    ref: str
    form_label: str = Field(..., description="Detected macro form, e.g. 'AABA', 'verse-chorus'")
    time_signature: str = Field(..., description="Primary time signature, e.g. '4/4'")
    beats_per_bar: int = Field(..., ge=1)
    total_bars: int = Field(..., ge=1)
    section_map: list[SectionMapEntry]
    repetition_structure: list[RepetitionEntry]
    section_comparison: SectionSimilarityHeatmap


# ---------------------------------------------------------------------------
# Per-track dynamics models (used by the Dynamics Analysis Page)
# ---------------------------------------------------------------------------


class TrackDynamicsProfile(CamelModel):
    """Dynamic analysis for a single instrument track.

    Used exclusively by the dynamics page endpoint so that per-track data
    can be visualised independently. Agents that need the aggregate
    (cross-track average) should use :class:`DynamicsData` instead.

    ``arc`` classifies the overall shape of the velocity curve:
    - ``flat``       — velocity nearly constant
    - ``terraced``   — abrupt step changes between levels
    - ``crescendo``  — gradual increase
    - ``decrescendo`` — gradual decrease
    - ``swell``      — rises then falls (arch)
    - ``hairpin``    — falls then rises (valley)

    ``velocity_curve`` samples the track at 2-beat intervals so a
    velocity profile graph can be drawn without requiring MIDI file access.
    """

    track: str = Field(..., description="Instrument track name, e.g. 'bass', 'keys', 'drums'")
    peak_velocity: int = Field(..., ge=0, le=127)
    min_velocity: int = Field(..., ge=0, le=127)
    mean_velocity: float = Field(..., ge=0.0, le=127.0)
    velocity_range: int = Field(..., ge=0, le=127, description="peak_velocity - min_velocity")
    arc: DynamicArc = Field(..., description="Dynamic arc classification for this track")
    velocity_curve: list[VelocityEvent] = Field(
        ..., description="Velocity sampled at 2-beat intervals for graphing"
    )


class DynamicsPageData(CamelModel):
    """Enriched per-track dynamics data for the Dynamics Analysis page.

    Returned by ``GET /musehub/repos/{repo_id}/analysis/{ref}/dynamics/page``.
    Contains one :class:`TrackDynamicsProfile` per active instrument track,
    plus the cross-track loudness comparison list (peak velocities sorted
    descending) so the bar chart can be drawn directly from ``tracks``.

    Agent use case: when orchestrating a mix, an agent inspects this to
    identify dynamic imbalances — e.g. bass consistently louder than keys —
    and adjusts generation accordingly.
    """

    ref: str
    repo_id: str
    computed_at: datetime
    tracks: list[TrackDynamicsProfile]
    filters_applied: AnalysisFilters


# ---------------------------------------------------------------------------
# Dimension enum and union
# ---------------------------------------------------------------------------

AnalysisDimension = Literal[
    "harmony",
    "dynamics",
    "motifs",
    "form",
    "groove",
    "emotion",
    "chord-map",
    "contour",
    "key",
    "tempo",
    "meter",
    "similarity",
    "divergence",
]

ALL_DIMENSIONS: list[str] = [
    "harmony",
    "dynamics",
    "motifs",
    "form",
    "groove",
    "emotion",
    "chord-map",
    "contour",
    "key",
    "tempo",
    "meter",
    "similarity",
    "divergence",
]

DimensionData = (
    HarmonyData
    | DynamicsData
    | MotifsData
    | FormData
    | GrooveData
    | EmotionData
    | ChordMapData
    | ContourData
    | KeyData
    | TempoData
    | MeterData
    | SimilarityData
    | DivergenceData
)

# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


class AnalysisResponse(CamelModel):
    """Envelope for a single-dimension analysis result.

    ``data`` contains dimension-specific structured data.  The envelope is
    consistent across all 13 dimensions so agents can process responses
    uniformly without branching on ``dimension``.

    Cache semantics: the ``computed_at`` timestamp drives ETag generation.
    Two responses with the same ``computed_at`` carry the same ``data``.
    """

    dimension: str
    ref: str
    computed_at: datetime
    data: DimensionData
    filters_applied: AnalysisFilters


class AggregateAnalysisResponse(CamelModel):
    """Aggregate response containing all 13 dimension analyses for a ref.

    Returned by ``GET /musehub/repos/{repo_id}/analysis/{ref}``.
    Agents that need a full musical picture of a commit can fetch this
    once rather than making 13 sequential requests.
    """

    ref: str
    repo_id: str
    computed_at: datetime
    dimensions: list[AnalysisResponse]
    filters_applied: AnalysisFilters
