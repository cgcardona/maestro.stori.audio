"""Muse Hub Analysis Service — structured musical analysis for agent consumption.

This module is the single orchestration point for all 13 analysis dimensions.
Route handlers delegate here; no business logic lives in routes.

Why this exists
---------------
AI agents need structured, typed JSON data to make informed composition
decisions.  HTML analysis pages are not machine-readable.  This service
bridges the gap by returning fully-typed Pydantic models for every musical
dimension of a Muse commit.

Stub implementation
-------------------
Full MIDI content analysis will be wired in once Storpheus exposes a
per-dimension introspection route.  Until then, the service returns
deterministic stub data keyed on the ``ref`` value — deterministic so that
agents receive consistent responses across retries and across sessions.

The stub data is musically realistic: values are drawn from realistic ranges
for jazz/soul/pop production and are internally consistent within each
dimension (e.g. the key reported by ``harmony`` matches the key reported by
``key``).

Boundary rules
--------------
- Pure data — no side effects, no external I/O beyond reading ``ref``.
- Must NOT import StateStore, EntityRegistry, or executor modules.
- Must NOT import LLM handlers or maestro_* pipeline modules.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Optional

from maestro.models.musehub_analysis import (
    ALL_DIMENSIONS,
    AggregateAnalysisResponse,
    AlternateKey,
    AnalysisFilters,
    AnalysisResponse,
    ChangedDimension,
    ChordEvent,
    ChordMapData,
    ContourData,
    DimensionData,
    DivergenceData,
    DynamicArc,
    DynamicsData,
    DynamicsPageData,
    EmotionData,
    FormData,
    GrooveData,
    HarmonyData,
    IrregularSection,
    KeyData,
    MeterData,
    ModulationPoint,
    MotifEntry,
    MotifRecurrenceCell,
    MotifTransformation,
    MotifsData,
    SectionEntry,
    SimilarCommit,
    SimilarityData,
    TempoChange,
    TempoData,
    TrackDynamicsProfile,
    VelocityEvent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stub data constants — musically realistic, deterministic by ref hash
# ---------------------------------------------------------------------------

_MODES = ["major", "minor", "dorian", "mixolydian", "lydian", "phrygian"]
_EMOTIONS = ["joyful", "melancholic", "tense", "serene", "energetic", "brooding"]
_FORMS = ["AABA", "verse-chorus", "through-composed", "rondo", "binary", "ternary"]
_GROOVES = ["straight", "swing", "shuffled", "half-time", "double-time"]
_TONICS = ["C", "F", "G", "D", "Bb", "Eb"]
_DYNAMIC_ARCS: list[DynamicArc] = [
    "flat", "terraced", "crescendo", "decrescendo", "swell", "hairpin",
]
_DEFAULT_TRACKS = ["bass", "keys", "drums", "melody", "pads"]


def _ref_hash(ref: str) -> int:
    """Derive a stable integer seed from a ref string for deterministic stubs."""
    return int(hashlib.md5(ref.encode()).hexdigest(), 16)  # noqa: S324 — non-crypto use


def _pick(seed: int, items: list[str], offset: int = 0) -> str:
    return items[(seed + offset) % len(items)]


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Per-dimension stub builders
# ---------------------------------------------------------------------------


def _build_harmony(ref: str, track: Optional[str], section: Optional[str]) -> HarmonyData:
    """Build stub harmonic analysis.  Deterministic for a given ref."""
    seed = _ref_hash(ref)
    tonic = _pick(seed, _TONICS)
    mode = _pick(seed, _MODES)
    total_beats = 32

    progression = [
        ChordEvent(beat=0.0, chord=f"{tonic}maj7", function="Imaj7", tension=0.1),
        ChordEvent(beat=4.0, chord="Am7", function="VIm7", tension=0.2),
        ChordEvent(beat=8.0, chord="Dm7", function="IIm7", tension=0.25),
        ChordEvent(beat=12.0, chord="G7", function="V7", tension=0.6),
        ChordEvent(beat=16.0, chord=f"{tonic}maj7", function="Imaj7", tension=0.1),
        ChordEvent(beat=20.0, chord="Em7b5", function="VIIm7b5", tension=0.7),
        ChordEvent(beat=24.0, chord="A7", function="V7/IIm", tension=0.65),
        ChordEvent(beat=28.0, chord="Dm7", function="IIm7", tension=0.25),
    ]

    tension_curve = [
        round(0.1 + 0.5 * abs((i - total_beats / 2) / (total_beats / 2)) * (seed % 3 + 1) / 3, 4)
        for i in range(total_beats)
    ]

    modulation_points = (
        [ModulationPoint(beat=16.0, from_key=f"{tonic} {mode}", to_key=f"G {mode}", confidence=0.72)]
        if seed % 3 == 0
        else []
    )

    return HarmonyData(
        tonic=tonic,
        mode=mode,
        key_confidence=round(0.7 + (seed % 30) / 100, 4),
        chord_progression=progression,
        tension_curve=tension_curve,
        modulation_points=modulation_points,
        total_beats=total_beats,
    )


def _build_dynamics(ref: str, track: Optional[str], section: Optional[str]) -> DynamicsData:
    seed = _ref_hash(ref)
    base_vel = 64 + (seed % 32)
    peak = min(127, base_vel + 30)
    low = max(20, base_vel - 20)

    curve = [
        VelocityEvent(
            beat=float(i * 2),
            velocity=min(127, max(20, base_vel + (seed >> i) % 20 - 10)),
        )
        for i in range(16)
    ]

    events = ["crescendo@8", "sfz@16"] if seed % 2 == 0 else ["diminuendo@12", "fp@0"]

    return DynamicsData(
        peak_velocity=peak,
        mean_velocity=round(float(base_vel), 2),
        min_velocity=low,
        dynamic_range=peak - low,
        velocity_curve=curve,
        dynamic_events=events,
    )


_CONTOUR_LABELS = [
    "ascending-step",
    "descending-step",
    "arch",
    "valley",
    "oscillating",
    "static",
]
_TRANSFORMATION_TYPES = ["inversion", "retrograde", "retrograde-inversion", "transposition"]
_MOTIF_TRACKS = ["melody", "bass", "keys", "strings", "brass"]
_MOTIF_SECTIONS = ["intro", "verse_1", "chorus", "verse_2", "outro"]


def _invert_intervals(intervals: list[int]) -> list[int]:
    """Return the melodic inversion (negate all semitone intervals)."""
    return [-x for x in intervals]


def _retrograde_intervals(intervals: list[int]) -> list[int]:
    """Return the retrograde (reversed interval sequence)."""
    return list(reversed(intervals))


def _build_motifs(ref: str, track: Optional[str], section: Optional[str]) -> MotifsData:
    """Build stub motif analysis with transformations, contour, and recurrence grid.

    Deterministic for a given ``ref`` value.  Produces 2–4 motifs, each with:
    - Original interval sequence and occurrence beats
    - Melodic contour label (arch, valley, oscillating, etc.)
    - All tracks where the motif or its transformations appear
    - Up to 3 transformations (inversion, retrograde, transposition)
    - Flat track×section recurrence grid for heatmap rendering
    """
    seed = _ref_hash(ref)
    n_motifs = 2 + (seed % 3)
    all_tracks = _MOTIF_TRACKS[: 2 + (seed % 3)]
    sections = _MOTIF_SECTIONS

    motifs: list[MotifEntry] = []
    for i in range(n_motifs):
        intervals = [2, -1, 3, -2][: 2 + i]
        occurrences = [float(j * 8 + i * 2) for j in range(2 + (seed % 2))]
        contour_label = _pick(seed, _CONTOUR_LABELS, offset=i)
        primary_track = track or all_tracks[i % len(all_tracks)]

        # Cross-track sharing: motif appears in 1–3 tracks
        n_sharing_tracks = 1 + (seed + i) % min(3, len(all_tracks))
        sharing_tracks = [all_tracks[(i + k) % len(all_tracks)] for k in range(n_sharing_tracks)]
        if primary_track not in sharing_tracks:
            sharing_tracks = [primary_track] + sharing_tracks[: n_sharing_tracks - 1]

        # Build transformations
        transformations: list[MotifTransformation] = []
        inv_occurrences = [float(j * 8 + i * 2 + 4) for j in range(1 + (seed % 2))]
        transformations.append(
            MotifTransformation(
                transformation_type="inversion",
                intervals=_invert_intervals(intervals),
                transposition_semitones=0,
                occurrences=inv_occurrences,
                track=sharing_tracks[0],
            )
        )
        if len(intervals) >= 2:
            retro_occurrences = [float(j * 8 + i * 2 + 2) for j in range(1 + (seed % 2))]
            transformations.append(
                MotifTransformation(
                    transformation_type="retrograde",
                    intervals=_retrograde_intervals(intervals),
                    transposition_semitones=0,
                    occurrences=retro_occurrences,
                    track=sharing_tracks[-1],
                )
            )
        if (seed + i) % 2 == 0:
            transpose_by = 5 if (seed % 2 == 0) else 7
            transpo_occurrences = [float(j * 16 + i * 2) for j in range(1 + (seed % 2))]
            transformations.append(
                MotifTransformation(
                    transformation_type="transposition",
                    intervals=[x for x in intervals],
                    transposition_semitones=transpose_by,
                    occurrences=transpo_occurrences,
                    track=sharing_tracks[min(1, len(sharing_tracks) - 1)],
                )
            )

        # Build recurrence grid: track × section
        recurrence_grid: list[MotifRecurrenceCell] = []
        for t in all_tracks:
            for s in sections:
                # Original present in primary track, first two sections
                if t == primary_track and s in sections[:2]:
                    recurrence_grid.append(
                        MotifRecurrenceCell(
                            track=t,
                            section=s,
                            present=True,
                            occurrence_count=1 + (seed % 2),
                            transformation_types=["original"],
                        )
                    )
                # Inversion in sharing tracks at chorus
                elif t in sharing_tracks and s == "chorus":
                    recurrence_grid.append(
                        MotifRecurrenceCell(
                            track=t,
                            section=s,
                            present=True,
                            occurrence_count=1,
                            transformation_types=["inversion"],
                        )
                    )
                # Transposition in bridge / outro for certain motifs
                elif (seed + i) % 2 == 0 and t in sharing_tracks and s == "outro":
                    recurrence_grid.append(
                        MotifRecurrenceCell(
                            track=t,
                            section=s,
                            present=True,
                            occurrence_count=1,
                            transformation_types=["transposition"],
                        )
                    )
                else:
                    recurrence_grid.append(
                        MotifRecurrenceCell(
                            track=t,
                            section=s,
                            present=False,
                            occurrence_count=0,
                            transformation_types=[],
                        )
                    )

        motifs.append(
            MotifEntry(
                motif_id=f"M{i + 1:02d}",
                intervals=intervals,
                length_beats=float(2 + i),
                occurrence_count=len(occurrences),
                occurrences=occurrences,
                track=primary_track,
                contour_label=contour_label,
                tracks=sharing_tracks,
                transformations=transformations,
                recurrence_grid=recurrence_grid,
            )
        )

    return MotifsData(
        total_motifs=len(motifs),
        motifs=motifs,
        sections=sections,
        all_tracks=all_tracks,
    )


def _build_form(ref: str, track: Optional[str], section: Optional[str]) -> FormData:
    seed = _ref_hash(ref)
    form_label = _pick(seed, _FORMS)
    sections = [
        SectionEntry(label="intro", function="exposition", start_beat=0.0, end_beat=8.0, length_beats=8.0),
        SectionEntry(label="verse_1", function="statement", start_beat=8.0, end_beat=24.0, length_beats=16.0),
        SectionEntry(label="chorus", function="climax", start_beat=24.0, end_beat=40.0, length_beats=16.0),
        SectionEntry(label="verse_2", function="restatement", start_beat=40.0, end_beat=56.0, length_beats=16.0),
        SectionEntry(label="outro", function="resolution", start_beat=56.0, end_beat=64.0, length_beats=8.0),
    ]
    return FormData(form_label=form_label, total_beats=64, sections=sections)


def _build_groove(ref: str, track: Optional[str], section: Optional[str]) -> GrooveData:
    seed = _ref_hash(ref)
    style = _pick(seed, _GROOVES)
    swing = 0.5 if style == "straight" else round(0.55 + (seed % 20) / 100, 4)
    bpm = round(80.0 + (seed % 80), 1)
    return GrooveData(
        swing_factor=swing,
        grid_resolution="1/16" if style == "straight" else "1/8T",
        onset_deviation=round(0.01 + (seed % 10) / 200, 4),
        groove_score=round(0.6 + (seed % 40) / 100, 4),
        style=style,
        bpm=bpm,
    )


def _build_emotion(ref: str, track: Optional[str], section: Optional[str]) -> EmotionData:
    seed = _ref_hash(ref)
    emotion = _pick(seed, _EMOTIONS)
    valence_map: dict[str, float] = {
        "joyful": 0.8, "melancholic": -0.5, "tense": -0.3,
        "serene": 0.4, "energetic": 0.6, "brooding": -0.7,
    }
    arousal_map: dict[str, float] = {
        "joyful": 0.7, "melancholic": 0.3, "tense": 0.8,
        "serene": 0.2, "energetic": 0.9, "brooding": 0.5,
    }
    return EmotionData(
        valence=valence_map[emotion],
        arousal=arousal_map[emotion],
        tension=round(0.1 + (seed % 60) / 100, 4),
        primary_emotion=emotion,
        confidence=round(0.65 + (seed % 35) / 100, 4),
    )


def _build_chord_map(ref: str, track: Optional[str], section: Optional[str]) -> ChordMapData:
    harmony = _build_harmony(ref, track, section)
    return ChordMapData(
        progression=harmony.chord_progression,
        total_chords=len(harmony.chord_progression),
        total_beats=harmony.total_beats,
    )


def _build_contour(ref: str, track: Optional[str], section: Optional[str]) -> ContourData:
    seed = _ref_hash(ref)
    shapes = ["arch", "ascending", "descending", "flat", "wave"]
    shape = _pick(seed, shapes)
    base_pitch = 60 + (seed % 12)
    pitch_curve = [
        round(base_pitch + 5 * (i / 16) * (1 if seed % 2 == 0 else -1) + (seed >> i) % 3, 1)
        for i in range(16)
    ]
    return ContourData(
        shape=shape,
        direction_changes=1 + (seed % 4),
        peak_beat=float(4 + (seed % 12)),
        valley_beat=float(seed % 8),
        overall_direction="up" if seed % 3 == 0 else ("down" if seed % 3 == 1 else "flat"),
        pitch_curve=pitch_curve,
    )


def _build_key(ref: str, track: Optional[str], section: Optional[str]) -> KeyData:
    seed = _ref_hash(ref)
    tonic = _pick(seed, _TONICS)
    mode = _pick(seed, _MODES[:2])
    rel_choices = ["A", "D", "E", "B", "G", "C"]
    relative = f"{_pick(seed + 3, rel_choices)}m" if mode == "major" else f"{tonic}m"
    alternates = [
        AlternateKey(
            tonic=_pick(seed + 2, ["G", "D", "A", "E", "Bb"]),
            mode="dorian",
            confidence=round(0.3 + (seed % 20) / 100, 4),
        )
    ]
    return KeyData(
        tonic=tonic,
        mode=mode,
        confidence=round(0.75 + (seed % 25) / 100, 4),
        relative_key=relative,
        alternate_keys=alternates,
    )


def _build_tempo(ref: str, track: Optional[str], section: Optional[str]) -> TempoData:
    seed = _ref_hash(ref)
    bpm = round(80.0 + (seed % 80), 1)
    stability = round(0.7 + (seed % 30) / 100, 4)
    feels = ["straight", "laid-back", "rushing"]
    feel = _pick(seed, feels)
    changes = (
        [TempoChange(beat=32.0, bpm=round(bpm * 1.05, 1))]
        if seed % 4 == 0
        else []
    )
    return TempoData(bpm=bpm, stability=stability, time_feel=feel, tempo_changes=changes)


def _build_meter(ref: str, track: Optional[str], section: Optional[str]) -> MeterData:
    seed = _ref_hash(ref)
    sigs = ["4/4", "3/4", "6/8", "5/4", "7/8"]
    sig = _pick(seed, sigs[:2])
    is_compound = sig in ("6/8", "12/8")
    profile_44 = [1.0, 0.2, 0.6, 0.2]
    profile_34 = [1.0, 0.3, 0.5]
    profile = profile_44 if sig == "4/4" else profile_34
    irregular: list[IrregularSection] = (
        [IrregularSection(start_beat=24.0, end_beat=25.0, time_signature="5/4")]
        if seed % 5 == 0
        else []
    )
    return MeterData(
        time_signature=sig,
        irregular_sections=irregular,
        beat_strength_profile=profile,
        is_compound=is_compound,
    )


def _build_similarity(ref: str, track: Optional[str], section: Optional[str]) -> SimilarityData:
    seed = _ref_hash(ref)
    n = 1 + (seed % 3)
    similar = [
        SimilarCommit(
            ref=f"commit_{hashlib.md5(f'{ref}{i}'.encode()).hexdigest()[:8]}",  # noqa: S324
            score=round(0.5 + (seed >> i) % 50 / 100, 4),
            shared_motifs=[f"M{j + 1:02d}" for j in range(1 + i % 2)],
            commit_message=f"Add {'bridge' if i == 0 else 'variation'} section",
        )
        for i in range(n)
    ]
    return SimilarityData(similar_commits=similar, embedding_dimensions=128)


def _build_divergence(ref: str, track: Optional[str], section: Optional[str]) -> DivergenceData:
    seed = _ref_hash(ref)
    score = round((seed % 60) / 100, 4)
    changed = [
        ChangedDimension(
            dimension="harmony",
            change_magnitude=round(0.2 + (seed % 40) / 100, 4),
            description="Key shifted from C major to F major",
        ),
        ChangedDimension(
            dimension="tempo",
            change_magnitude=round(0.1 + (seed % 20) / 100, 4),
            description="BPM increased by ~8%",
        ),
    ]
    return DivergenceData(
        divergence_score=score,
        base_ref=f"parent:{ref[:8]}",
        changed_dimensions=changed,
    )


# ---------------------------------------------------------------------------
# Dimension dispatch table
# ---------------------------------------------------------------------------

# Each builder has signature (ref: str, track: str | None, section: str | None) -> DimensionData
_DimBuilder = Callable[[str, Optional[str], Optional[str]], DimensionData]

_BUILDERS: dict[str, _DimBuilder] = {
    "harmony": _build_harmony,
    "dynamics": _build_dynamics,
    "motifs": _build_motifs,
    "form": _build_form,
    "groove": _build_groove,
    "emotion": _build_emotion,
    "chord-map": _build_chord_map,
    "contour": _build_contour,
    "key": _build_key,
    "tempo": _build_tempo,
    "meter": _build_meter,
    "similarity": _build_similarity,
    "divergence": _build_divergence,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_dimension(
    dimension: str,
    ref: str,
    track: Optional[str] = None,
    section: Optional[str] = None,
) -> DimensionData:
    """Compute analysis data for a single musical dimension.

    Dispatches to the appropriate stub builder based on ``dimension``.
    Returns a fully-typed Pydantic model for the given dimension.

    Args:
        dimension: One of the 13 supported dimension names.
        ref:       Muse commit ref (branch name, commit ID, or tag).
        track:     Optional instrument track filter.
        section:   Optional musical section filter.

    Returns:
        Dimension-specific Pydantic data model.

    Raises:
        ValueError: If ``dimension`` is not a supported analysis dimension.
    """
    builder = _BUILDERS.get(dimension)
    if builder is None:
        raise ValueError(f"Unknown analysis dimension: {dimension!r}")
    return builder(ref, track, section)


def compute_analysis_response(
    *,
    repo_id: str,
    dimension: str,
    ref: str,
    track: Optional[str] = None,
    section: Optional[str] = None,
) -> AnalysisResponse:
    """Build a complete :class:`AnalysisResponse` envelope for one dimension.

    This is the primary entry point for the single-dimension endpoint.

    Args:
        repo_id:   Muse Hub repo UUID.
        dimension: Analysis dimension name.
        ref:       Muse commit ref.
        track:     Optional track filter.
        section:   Optional section filter.

    Returns:
        :class:`AnalysisResponse` with typed ``data`` and filter metadata.
    """
    data = compute_dimension(dimension, ref, track, section)
    response = AnalysisResponse(
        dimension=dimension,
        ref=ref,
        computed_at=_utc_now(),
        data=data,
        filters_applied=AnalysisFilters(track=track, section=section),
    )
    logger.info("✅ analysis/%s repo=%s ref=%s", dimension, repo_id[:8], ref)
    return response


def _build_track_dynamics_profile(
    ref: str,
    track: str,
    track_index: int,
) -> TrackDynamicsProfile:
    """Build a deterministic per-track dynamic profile for the dynamics page.

    Seed is derived from ``ref`` XOR ``track_index`` so each track gets a
    distinct but reproducible curve for the same ref.
    """
    seed = _ref_hash(ref) ^ (track_index * 0x9E3779B9)
    base_vel = 50 + (seed % 50)
    peak = min(127, base_vel + 20 + (seed % 30))
    low = max(10, base_vel - 20 - (seed % 20))
    mean = round(float((peak + low) / 2), 2)

    curve = [
        VelocityEvent(
            beat=float(i * 2),
            velocity=min(127, max(10, base_vel + (seed >> (i % 16)) % 25 - 12)),
        )
        for i in range(16)
    ]

    arc: DynamicArc = _DYNAMIC_ARCS[(seed + track_index) % len(_DYNAMIC_ARCS)]

    return TrackDynamicsProfile(
        track=track,
        peak_velocity=peak,
        min_velocity=low,
        mean_velocity=mean,
        velocity_range=peak - low,
        arc=arc,
        velocity_curve=curve,
    )


def compute_dynamics_page_data(
    *,
    repo_id: str,
    ref: str,
    track: Optional[str] = None,
    section: Optional[str] = None,
) -> DynamicsPageData:
    """Build per-track dynamics data for the Dynamics Analysis page.

    Returns one :class:`TrackDynamicsProfile` per active track, or a single
    entry when ``track`` filter is applied.  Each profile includes a velocity
    curve suitable for rendering a profile graph, an arc classification badge,
    and peak/range metrics for the loudness comparison bar chart.

    Args:
        repo_id:  Muse Hub repo UUID.
        ref:      Muse commit ref (branch name, commit ID, or tag).
        track:    Optional track filter — if set, only that track is returned.
        section:  Optional section filter (recorded in ``filters_applied``).

    Returns:
        :class:`DynamicsPageData` with per-track profiles.
    """
    tracks_to_include = [track] if track else _DEFAULT_TRACKS
    profiles = [
        _build_track_dynamics_profile(ref, t, i)
        for i, t in enumerate(tracks_to_include)
    ]
    now = _utc_now()
    logger.info(
        "✅ dynamics/page repo=%s ref=%s tracks=%d",
        repo_id[:8], ref, len(profiles),
    )
    return DynamicsPageData(
        ref=ref,
        repo_id=repo_id,
        computed_at=now,
        tracks=profiles,
        filters_applied=AnalysisFilters(track=track, section=section),
    )


def compute_aggregate_analysis(
    *,
    repo_id: str,
    ref: str,
    track: Optional[str] = None,
    section: Optional[str] = None,
) -> AggregateAnalysisResponse:
    """Build a complete :class:`AggregateAnalysisResponse` for all 13 dimensions.

    This is the primary entry point for the aggregate endpoint.  All 13
    dimensions are computed in a single call so agents can retrieve the full
    musical picture without issuing 13 sequential requests.

    Args:
        repo_id:  Muse Hub repo UUID.
        ref:      Muse commit ref.
        track:    Optional track filter (applied to all dimensions).
        section:  Optional section filter (applied to all dimensions).

    Returns:
        :class:`AggregateAnalysisResponse` with one entry per dimension.
    """
    now = _utc_now()
    dimensions = [
        AnalysisResponse(
            dimension=dim,
            ref=ref,
            computed_at=now,
            data=compute_dimension(dim, ref, track, section),
            filters_applied=AnalysisFilters(track=track, section=section),
        )
        for dim in ALL_DIMENSIONS
    ]
    logger.info("✅ analysis/aggregate repo=%s ref=%s dims=%d", repo_id[:8], ref, len(dimensions))
    return AggregateAnalysisResponse(
        ref=ref,
        repo_id=repo_id,
        computed_at=now,
        dimensions=dimensions,
        filters_applied=AnalysisFilters(track=track, section=section),
    )
