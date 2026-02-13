"""
Music Spec IR (Intermediate Representation).

Schema for plan → generate → edit/repair → judge pipeline.
See docs/MIDI_SPEC_IR_SCHEMA.md for full spec.
"""
from dataclasses import dataclass, field
from typing import Optional


# -----------------------------------------------------------------------------
# Global
# -----------------------------------------------------------------------------

@dataclass
class SectionMapEntry:
    """One section in the section map (intro, main, outro, etc.)."""
    bar_start: int
    bar_end: int
    section: str  # "intro" | "main" | "outro" | "bridge" etc.
    energy: float = 0.8  # 0..1


@dataclass
class GlobalSpec:
    """Global timing and structure."""
    tempo: int = 120
    key: str = "C"
    scale: str = "natural_minor"
    swing: float = 0.0  # 0 = straight, 0.5 = light swing, 1.0 = heavy shuffle
    bars: int = 16
    time_signature: tuple[int, int] = (4, 4)
    microtiming_jitter_ms: tuple[int, int] = (-15, 15)  # [min_ms, max_ms] for humanization
    humanize_profile: str = "tight"  # "laid_back" | "tight" | "pushed"
    section_map: list = field(default_factory=list)  # list of SectionMapEntry
    energy_curve: Optional[str] = None  # "rise_hold_fall" | "flat" | etc.


# -----------------------------------------------------------------------------
# Drum Spec
# -----------------------------------------------------------------------------

@dataclass
class DensityTarget:
    """Min/max hits per bar or per N bars."""
    min_hits_per_bar: Optional[int] = None
    max_hits_per_bar: Optional[int] = None
    min_hits_per_4_bars: Optional[int] = None
    max_hits_per_4_bars: Optional[int] = None
    min_hits_per_8_bars: Optional[int] = None
    max_hits_per_8_bars: Optional[int] = None


@dataclass
class DrumLayerSpec:
    """One drum layer (core, timekeepers, ghost, fills, etc.)."""
    role: str
    instruments: list[int]  # GM pitches
    density_target: DensityTarget
    velocity_range: tuple[int, int] = (70, 100)
    required: bool = True
    variation_rate: float = 0.0
    placement: Optional[str] = None  # e.g. "offbeats_and_before_backbeat"
    probability: float = 1.0  # for ear_candy
    fill_bars: Optional[list[int]] = None  # for fills layer
    max_fill_density_per_bar: Optional[int] = None


@dataclass
class DrumConstraints:
    """Hard constraints for drum renderer."""
    min_distinct_instruments_per_4_bars: int = 8
    max_simultaneous_voices_per_beat: int = 4
    max_salience_per_beat: float = 2.5
    max_repeat_ngram_length: int = 2
    fill_bars: list[int] = field(default_factory=lambda: [3, 7, 11, 15])


# Salience per layer (perceptual weight for clutter cap)
DEFAULT_SALIENCE_WEIGHT = {
    "core": 1.0,
    "timekeepers": 0.6,
    "cymbal_punctuation": 0.7,
    "ghost_layer": 0.3,
    "fills": 0.8,
    "ear_candy": 0.4,
}

# Groove templates: control kick placement, hat grid, syncopation
GROOVE_TEMPLATE_VALUES = ("trap_straight", "trap_triplet", "boom_bap_swing", "house_four_on_floor")


@dataclass
class DrumSpec:
    """Full drum spec for IR → notes renderer."""
    style: str = "trap"
    groove_template: str = "trap_straight"
    layers: dict = field(default_factory=dict)  # layer_name -> DrumLayerSpec
    salience_weight: dict = field(default_factory=lambda: dict(DEFAULT_SALIENCE_WEIGHT))
    constraints: DrumConstraints = field(default_factory=DrumConstraints)
    variation_plan: dict = field(default_factory=dict)  # bar_1: "establish", bar_2: "variation_hat", etc.


# -----------------------------------------------------------------------------
# Default drum spec (trap, 16-piece capable)
# -----------------------------------------------------------------------------

def default_drum_spec(style: str = "trap", bars: int = 4) -> DrumSpec:
    """Build a DrumSpec with full 16-piece layering and salience."""
    fill_bars = [b for b in range(3, bars, 4)]  # bar 3, 7, 11, ... (0-based, last bar of each 4-bar phrase)
    if not fill_bars and bars >= 1:
        fill_bars = [bars - 1]

    groove = "trap_straight"
    if style in ("boom_bap", "boom bap", "hip_hop", "hip hop"):
        groove = "boom_bap_swing"
    elif style in ("house", "techno"):
        groove = "house_four_on_floor"
    elif "trip" in style.lower() or "triplet" in style.lower():
        groove = "trap_triplet"

    return DrumSpec(
        style=style,
        groove_template=groove,
        layers={
            "core": DrumLayerSpec(
                role="kick_snare",
                instruments=[36, 38, 39],  # kick, snare, clap
                density_target=DensityTarget(min_hits_per_bar=2, max_hits_per_bar=4),
                velocity_range=(85, 110),
                required=True,
            ),
            "timekeepers": DrumLayerSpec(
                role="hats",
                instruments=[42, 44, 46],  # closed, pedal, open hi-hat
                density_target=DensityTarget(min_hits_per_bar=8, max_hits_per_bar=16),
                velocity_range=(60, 95),
                variation_rate=0.3,
                required=True,
            ),
            "cymbal_punctuation": DrumLayerSpec(
                role="crash_ride",
                instruments=[49, 51, 52],
                density_target=DensityTarget(min_hits_per_4_bars=1, max_hits_per_4_bars=4),
                placement="section_starts_and_fill_ends",
                required=False,
            ),
            "ghost_layer": DrumLayerSpec(
                role="ghost_snare_rim_perc",
                instruments=[37, 38, 40, 41, 43, 45, 47],  # rim, snare, e.snare, toms
                density_target=DensityTarget(min_hits_per_4_bars=2, max_hits_per_4_bars=12),
                velocity_range=(40, 70),
                placement="offbeats_and_before_backbeat",
                required=False,
            ),
            "fills": DrumLayerSpec(
                role="toms_rolls",
                instruments=[41, 43, 45, 47, 48, 50],  # toms
                density_target=DensityTarget(min_hits_per_bar=0, max_hits_per_bar=8),
                fill_bars=fill_bars,
                max_fill_density_per_bar=8,
                required=True,
            ),
            "ear_candy": DrumLayerSpec(
                role="perc",
                instruments=[54, 56, 69, 70, 75],  # tambourine, cowbell, cabasa, maracas, claves
                density_target=DensityTarget(min_hits_per_8_bars=0, max_hits_per_8_bars=4),
                probability=0.2,
                required=False,
            ),
        },
        salience_weight=dict(DEFAULT_SALIENCE_WEIGHT),
        constraints=DrumConstraints(
            min_distinct_instruments_per_4_bars=8,
            max_simultaneous_voices_per_beat=4,
            max_salience_per_beat=2.5,
            max_repeat_ngram_length=2,
            fill_bars=fill_bars,
        ),
        variation_plan={
            "bar_1": "establish",
            "bar_2": "variation_hat",
            "bar_3": "variation_kick_or_ghost",
            "bar_4": "turnaround_fill",
        },
    )


# -----------------------------------------------------------------------------
# Bass Spec IR (drum coupling: kick_follow, anticipation, octave_jump)
# -----------------------------------------------------------------------------

@dataclass
class BassDensityTarget:
    """Min/max notes per bar for bass."""
    min_notes_per_bar: int = 2
    max_notes_per_bar: int = 6


@dataclass
class BassNoteLength:
    """Bass note length in beats."""
    min_beats: float = 0.25
    max_beats: float = 1.5


@dataclass
class BassSpec:
    """Bass plan: rhythm lock to kick, register, 808 slides, chord follow."""
    style: str = "trap"
    register: str = "low"  # "low" | "mid" | "high"
    root_octave: int = 2  # MIDI octave for root
    rhythm_lock: str = "kick"  # "kick" | "clave" | "chord_rhythm" | "free"
    kick_follow_probability: float = 0.7
    anticipation_allowed: bool = True
    octave_jump_probability: float = 0.15
    note_length: BassNoteLength = field(default_factory=BassNoteLength)
    syncopation_allowed: bool = True
    slide_808_probability: float = 0.3
    chord_follow: str = "root_and_fifth"  # "root" | "root_and_fifth" | "arpeggio"
    density_target: BassDensityTarget = field(default_factory=BassDensityTarget)


def default_bass_spec(style: str = "trap", bars: int = 16) -> BassSpec:
    """Build BassSpec from style and bar count."""
    return BassSpec(style=style)


# -----------------------------------------------------------------------------
# Harmonic Spec IR (chord_schedule for melody resolution)
# -----------------------------------------------------------------------------

@dataclass
class ChordScheduleEntry:
    """One (bar, chord) in the chord schedule."""
    bar: int
    chord: str  # e.g. "Cm", "Eb", "Ab"


@dataclass
class HarmonicSpec:
    """Chord plan: chord_schedule (bar → chord), voicing, tension points."""
    chord_rhythm: str = "half_note"  # "whole" | "half_note" | "quarter" | "syncopated"
    chord_palette: list = field(default_factory=list)  # e.g. ["Cm", "Eb", "Ab", "Gb"]
    chord_schedule: list = field(default_factory=list)  # list of ChordScheduleEntry
    tension_points: list = field(default_factory=list)  # bar indices
    voicing: str = "root_third_seventh"  # "root" | "root_third" | "root_third_seventh"
    velocity_range: tuple[int, int] = (70, 95)


def default_harmonic_spec(
    key: str = "C",
    scale: str = "natural_minor",
    bars: int = 16,
    chords: Optional[list[str]] = None,
) -> HarmonicSpec:
    """Build HarmonicSpec from key, bars, and optional chord list."""
    palette = chords or _default_chord_palette(key, scale)
    # chord_schedule: one chord per 2 bars by default
    schedule = []
    for i in range(0, bars, 2):
        chord = palette[(i // 2) % len(palette)]
        schedule.append(ChordScheduleEntry(bar=i, chord=chord))
    return HarmonicSpec(
        chord_palette=palette,
        chord_schedule=schedule,
        tension_points=[b for b in range(7, bars, 8)],  # e.g. bar 7, 15
    )


def _default_chord_palette(key: str, scale: str) -> list[str]:
    """Default chord palette from key/scale (concrete chord names)."""
    k = (key or "C").strip().upper()
    root = k[0] if k else "C"
    is_minor = k.endswith("M") or (len(k) > 1 and k[1] == "M") or scale != "major"
    if is_minor:
        # Minor key: i, VI, III, VII (e.g. Am, F, C, G)
        palettes = {
            "C": ["Cm", "Ab", "Eb", "Bb"],
            "D": ["Dm", "Bb", "F", "C"],
            "E": ["Em", "C", "G", "D"],
            "F": ["Fm", "Db", "Ab", "Eb"],
            "G": ["Gm", "Eb", "Bb", "F"],
            "A": ["Am", "F", "C", "G"],
            "B": ["Bm", "G", "D", "A"],
        }
    else:
        palettes = {
            "C": ["C", "Am", "F", "G"],
            "D": ["D", "Bm", "G", "A"],
            "E": ["Em", "C", "G", "D"],
            "F": ["F", "Dm", "Bb", "C"],
            "G": ["G", "Em", "C", "D"],
            "A": ["A", "F#m", "D", "E"],
            "B": ["Bm", "G", "D", "A"],
        }
    return palettes.get(root, ["Cm", "Ab", "Eb", "Bb"])[:4]


# -----------------------------------------------------------------------------
# Melody Spec IR (uses chord_schedule for resolution)
# -----------------------------------------------------------------------------

@dataclass
class MelodySpec:
    """Melody plan: motif, phrase boundaries, contour, rest density."""
    motif_length_bars: int = 2
    call_response: bool = True
    phrase_boundaries: list = field(default_factory=list)  # e.g. [4, 8, 12, 16]
    contour: str = "arc"  # "arc" | "ascending" | "descending" | "wave"
    register: str = "mid_high"  # "low" | "mid" | "mid_high" | "high"
    rest_density: float = 0.3  # fraction of beats that are rest
    scale_lock: bool = True


def default_melody_spec(bars: int = 16) -> MelodySpec:
    """Build MelodySpec from bar count."""
    return MelodySpec(
        phrase_boundaries=[b for b in range(4, bars + 1, 4)],  # 4, 8, 12, 16
    )


# -----------------------------------------------------------------------------
# Full Music Spec IR (single structure for orchestrator)
# -----------------------------------------------------------------------------

@dataclass
class MusicSpec:
    """Full IR: global + drum_spec + bass_spec + harmonic_spec + melody_spec."""
    version: str = "1.0"
    global_spec: GlobalSpec = field(default_factory=GlobalSpec)
    drum_spec: Optional[DrumSpec] = None
    bass_spec: Optional[BassSpec] = None
    harmonic_spec: Optional[HarmonicSpec] = None
    melody_spec: Optional[MelodySpec] = None


def build_full_music_spec(
    style: str = "trap",
    tempo: int = 120,
    bars: int = 16,
    key: Optional[str] = None,
    chords: Optional[list[str]] = None,
    *,
    include_drums: bool = True,
    include_bass: bool = True,
    include_harmony: bool = True,
    include_melody: bool = True,
) -> MusicSpec:
    """Build full MusicSpec from intent (style, tempo, bars, key, chords)."""
    key = key or "C"
    scale = "natural_minor" if "m" in key.lower() else "major"
    global_spec = GlobalSpec(tempo=tempo, bars=bars, key=key, scale=scale)
    # Optional section map (bar_end exclusive)
    if bars >= 12:
        global_spec.section_map = [
            SectionMapEntry(0, 4, "intro", 0.5),
            SectionMapEntry(4, bars - 4, "main", 0.9),
            SectionMapEntry(bars - 4, bars, "outro", 0.4),
        ]
        global_spec.energy_curve = "rise_hold_fall"
    elif bars >= 8:
        global_spec.section_map = [
            SectionMapEntry(0, 4, "intro", 0.5),
            SectionMapEntry(4, bars, "main", 0.9),
        ]
        global_spec.energy_curve = "rise_hold_fall"

    drum_spec = default_drum_spec(style=style, bars=bars) if include_drums else None
    bass_spec = default_bass_spec(style=style, bars=bars) if include_bass else None
    harmonic_spec = default_harmonic_spec(key=key, scale=scale, bars=bars, chords=chords) if include_harmony else None
    melody_spec = default_melody_spec(bars=bars) if include_melody else None

    return MusicSpec(
        version="1.0",
        global_spec=global_spec,
        drum_spec=drum_spec,
        bass_spec=bass_spec,
        harmonic_spec=harmonic_spec,
        melody_spec=melody_spec,
    )


# -----------------------------------------------------------------------------
# Policy layer: intent → IR params (density, complexity, tension, brightness, groove)
# -----------------------------------------------------------------------------

def apply_policy_to_music_spec(
    spec: MusicSpec,
    *,
    density: float = 0.5,  # 0 = sparse, 1 = dense
    complexity: float = 0.5,  # syncopation + variation rate
    tension: float = 0.5,  # fill probability, cymbal density
    brightness: float = 0.5,  # cymbal vs tom emphasis
    groove: Optional[str] = None,  # swing/shuffle override
) -> MusicSpec:
    """
    Apply policy (density, complexity, tension, brightness, groove) to MusicSpec.
    Mutates IR params; returns same spec for chaining.
    """
    if spec.drum_spec:
        # Density → hits/bar per layer
        if density > 0.7:
            for layer in spec.drum_spec.layers.values():
                if hasattr(layer.density_target, "max_hits_per_bar") and layer.density_target.max_hits_per_bar:
                    layer.density_target.max_hits_per_bar = min(24, (layer.density_target.max_hits_per_bar or 8) + 4)
        elif density < 0.3:
            for layer in spec.drum_spec.layers.values():
                if hasattr(layer.density_target, "min_hits_per_bar") and layer.density_target.min_hits_per_bar:
                    layer.density_target.min_hits_per_bar = max(0, (layer.density_target.min_hits_per_bar or 0) - 2)
        # Complexity → variation rate
        for layer in spec.drum_spec.layers.values():
            layer.variation_rate = min(1.0, 0.2 + complexity * 0.5)
        # Groove override
        if groove and groove in GROOVE_TEMPLATE_VALUES:
            spec.drum_spec.groove_template = groove
    if spec.global_spec and groove:
        if groove == "boom_bap_swing":
            spec.global_spec.swing = 0.5
        elif "trip" in groove.lower():
            spec.global_spec.swing = 0.3
    return spec


# Quality presets: drive num_candidates and critic
QUALITY_PRESETS = {
    "fast": {"num_candidates": 1, "use_critic": False},
    "balanced": {"num_candidates": 2, "use_critic": True},
    "quality": {"num_candidates": 6, "use_critic": True},
}
