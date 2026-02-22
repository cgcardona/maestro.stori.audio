"""Data-driven instrument role profiles from 222K MIDI compositions.

Loads heuristic statistics (median values) for each instrument role
(lead, bass, chords, pads, drums) from analysis of 222,497 multi-instrument
compositions totaling 1,844,218 tracks.  Used by:

- Instrument agent prompts (Musical DNA blocks)
- Orpheus conditioning (complexity, density, musical_goals)
- Expressiveness post-processor (role-aware profiles)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HEURISTICS_PATH = Path(__file__).parent / "heuristics_v2.json"

# Fuzzy aliases for role lookup
_ROLE_ALIASES: dict[str, str] = {
    "melody": "lead",
    "vocal": "lead",
    "vocals": "lead",
    "synth": "lead",
    "guitar": "lead",
    "trumpet": "lead",
    "flute": "lead",
    "clarinet": "lead",
    "saxophone": "lead",
    "violin": "lead",
    "piano": "chords",
    "keys": "chords",
    "organ": "chords",
    "pad": "pads",
    "strings": "pads",
    "string": "pads",
    "choir": "pads",
    "drum": "drums",
    "percussion": "drums",
    "perc": "drums",
    "chord": "chords",
    "arp": "chords",
    "fx": "other",
}


def _safe_median(stat: object) -> float:
    """Extract median from a stat dict, falling back to mean, then 0."""
    if isinstance(stat, dict):
        return float(stat.get("median", stat.get("mean", 0.0)))
    if isinstance(stat, (int, float)):
        return float(stat)
    return 0.0


@dataclass(frozen=True)
class RoleProfile:
    """Aggregate statistics for one instrument role.

    All numeric fields are median values from the 222K-file analysis unless
    otherwise noted.  ``p25`` / ``p75`` fields carry interquartile bounds
    for key metrics so downstream code can express natural variation.
    """

    role: str
    track_count: int

    # ── Silence & density ──
    rest_ratio: float
    notes_per_bar: float

    # ── Phrasing ──
    phrase_length_beats: float
    notes_per_phrase: float
    phrase_regularity_cv: float
    note_length_entropy: float

    # ── Rhythm ──
    syncopation_ratio: float
    swing_ratio: float
    rhythm_trigram_repeat: float
    ioi_cv: float

    # ── Melody & pitch ──
    step_ratio: float
    leap_ratio: float
    repeat_ratio: float
    pitch_class_entropy: float
    contour_complexity: float
    interval_entropy: float
    pitch_gravity: float
    climax_position: float
    pitch_range_semitones: float

    # ── Register ──
    register_mean_pitch: float
    register_low_ratio: float
    register_mid_ratio: float
    register_high_ratio: float

    # ── Dynamics ──
    velocity_mean: float
    velocity_range: float
    velocity_stdev: float
    velocity_entropy: float
    velocity_pitch_correlation: float
    phrase_velocity_slope: float

    # ── Tempo tendency ──
    accelerando_ratio: float
    ritardando_ratio: float

    # ── Articulation ──
    staccato_ratio: float
    legato_ratio: float
    sustained_ratio: float

    # ── Polyphony ──
    polyphony_mean: float
    pct_monophonic: float

    # ── Motif ──
    motif_pitch_trigram_repeat: float
    motif_direction_trigram_repeat: float

    # ── Derived Orpheus conditioning targets ──
    orpheus_complexity: float
    orpheus_density_hint: str

    def prompt_block(self) -> str:
        """Render a compact Musical DNA block for LLM system prompts."""
        poly_desc = (
            "mostly monophonic (single notes)"
            if self.pct_monophonic > 0.8
            else "mixed mono/polyphonic"
            if self.pct_monophonic > 0.4
            else "predominantly polyphonic (chords/clusters)"
        )
        return (
            f"MUSICAL DNA — {self.role.upper()} "
            f"(from {self.track_count:,} professional tracks):\n"
            f"- DENSITY: ~{self.notes_per_bar:.1f} notes/bar, "
            f"~{self.rest_ratio:.0%} silence between phrases\n"
            f"- PHRASING: phrases of ~{self.phrase_length_beats:.0f} beats "
            f"with breathing gaps (regularity CV {self.phrase_regularity_cv:.2f})\n"
            f"- INTERVALS: {self.step_ratio:.0%} stepwise, "
            f"{self.leap_ratio:.0%} leaps, "
            f"{self.repeat_ratio:.0%} repeated notes\n"
            f"- ARTICULATION: {self.legato_ratio:.0%} legato, "
            f"{self.staccato_ratio:.0%} staccato, "
            f"{self.sustained_ratio:.0%} sustained\n"
            f"- DYNAMICS: velocity mean ~{self.velocity_mean:.0f}, "
            f"range ~{self.velocity_range:.0f}, stdev ~{self.velocity_stdev:.0f}\n"
            f"- TEXTURE: {poly_desc} "
            f"({self.pct_monophonic:.0%} monophonic)\n"
            f"- MOTIF: {self.motif_pitch_trigram_repeat:.0%} pitch-pattern repetition "
            f"— repeat melodic fragments, then vary\n"
            f"- REGISTER: mean pitch ~{self.register_mean_pitch:.0f} "
            f"(low {self.register_low_ratio:.0%} / mid {self.register_mid_ratio:.0%} "
            f"/ high {self.register_high_ratio:.0%})\n"
            f"- SYNCOPATION: {self.syncopation_ratio:.0%} of onsets are syncopated\n"
        )


def _build_profile(role: str, data: dict) -> RoleProfile:
    """Construct a RoleProfile from the raw heuristics JSON for one role."""
    track_count = data.get("track_count", 0)

    rest_ratio = _safe_median(data.get("rest_ratio"))
    notes_per_bar = _safe_median(data.get("notes_per_bar"))
    contour_complexity = _safe_median(data.get("contour_complexity"))

    if notes_per_bar > 6:
        density_hint = "dense"
    elif notes_per_bar > 2.5:
        density_hint = "moderate"
    else:
        density_hint = "sparse"

    return RoleProfile(
        role=role,
        track_count=track_count,
        rest_ratio=rest_ratio,
        notes_per_bar=notes_per_bar,
        phrase_length_beats=_safe_median(data.get("phrase_length.mean")),
        notes_per_phrase=_safe_median(data.get("notes_per_phrase.mean")),
        phrase_regularity_cv=_safe_median(data.get("phrase_regularity_cv")),
        note_length_entropy=_safe_median(data.get("note_length_entropy")),
        syncopation_ratio=_safe_median(data.get("syncopation_ratio")),
        swing_ratio=_safe_median(data.get("swing_ratio")),
        rhythm_trigram_repeat=_safe_median(data.get("rhythm_pattern.trigram_repeat")),
        ioi_cv=_safe_median(data.get("ioi.cv")),
        step_ratio=_safe_median(data.get("intervals.step_ratio")),
        leap_ratio=_safe_median(data.get("intervals.leap_ratio")),
        repeat_ratio=_safe_median(data.get("intervals.repeat_ratio")),
        pitch_class_entropy=_safe_median(data.get("pitch_class_entropy")),
        contour_complexity=contour_complexity,
        interval_entropy=_safe_median(data.get("interval_entropy")),
        pitch_gravity=_safe_median(data.get("pitch_gravity")),
        climax_position=_safe_median(data.get("climax_position")),
        pitch_range_semitones=_safe_median(data.get("pitch_range.range_semitones")),
        register_mean_pitch=_safe_median(data.get("register.mean_pitch")),
        register_low_ratio=_safe_median(data.get("register.low_ratio")),
        register_mid_ratio=_safe_median(data.get("register.mid_ratio")),
        register_high_ratio=_safe_median(data.get("register.high_ratio")),
        velocity_mean=_safe_median(data.get("velocity.mean")),
        velocity_range=_safe_median(data.get("velocity.range")),
        velocity_stdev=_safe_median(data.get("velocity.stdev")),
        velocity_entropy=_safe_median(data.get("velocity_entropy")),
        velocity_pitch_correlation=_safe_median(data.get("velocity_pitch_correlation")),
        phrase_velocity_slope=_safe_median(data.get("phrase_velocity_slope.mean")),
        accelerando_ratio=_safe_median(data.get("tempo_tendency.accelerando_ratio")),
        ritardando_ratio=_safe_median(data.get("tempo_tendency.ritardando_ratio")),
        staccato_ratio=_safe_median(data.get("staccato_ratio")),
        legato_ratio=_safe_median(data.get("legato_ratio")),
        sustained_ratio=_safe_median(data.get("sustained_ratio")),
        polyphony_mean=_safe_median(data.get("polyphony.mean")),
        pct_monophonic=_safe_median(data.get("polyphony.pct_monophonic")),
        motif_pitch_trigram_repeat=_safe_median(data.get("motif.pitch_trigram_repeat")),
        motif_direction_trigram_repeat=_safe_median(data.get("motif.direction_trigram_repeat")),
        orpheus_complexity=round(min(1.0, contour_complexity), 3),
        orpheus_density_hint=density_hint,
    )


def _load_profiles() -> dict[str, RoleProfile]:
    """Load and parse heuristics JSON into RoleProfile instances."""
    try:
        raw = json.loads(_HEURISTICS_PATH.read_text())
    except FileNotFoundError:
        logger.warning(
            "Heuristics file not found at %s — role profiles will be empty. "
            "Run scripts/analyze_midi.py to generate.",
            _HEURISTICS_PATH,
        )
        return {}
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in heuristics file: %s", exc)
        return {}

    profiles: dict[str, RoleProfile] = {}
    by_role = raw.get("by_role", {})
    for role_name, role_data in by_role.items():
        if role_name == "other":
            continue
        try:
            profiles[role_name] = _build_profile(role_name, role_data)
        except Exception as exc:
            logger.error("Failed to build profile for role '%s': %s", role_name, exc)

    if profiles:
        total_tracks = sum(p.track_count for p in profiles.values())
        logger.info(
            "Loaded %d role profiles (%s) from %s (%d total tracks)",
            len(profiles),
            ", ".join(sorted(profiles)),
            _HEURISTICS_PATH.name,
            total_tracks,
        )

    return profiles


ROLE_PROFILES: dict[str, RoleProfile] = _load_profiles()


def get_role_profile(role: str) -> Optional[RoleProfile]:
    """Look up a role profile with fuzzy matching.

    Accepts canonical names (``"lead"``, ``"bass"``, ``"chords"``,
    ``"pads"``, ``"drums"``) as well as common aliases
    (``"melody"`` → ``"lead"``, ``"piano"`` → ``"chords"``, etc.).

    Returns ``None`` if no matching profile is found.
    """
    key = role.lower().strip()
    if not key:
        return None
    if key in ROLE_PROFILES:
        return ROLE_PROFILES[key]
    canonical = _ROLE_ALIASES.get(key)
    if canonical and canonical in ROLE_PROFILES:
        return ROLE_PROFILES[canonical]
    for alias, canon in _ROLE_ALIASES.items():
        if alias in key or key in alias:
            if canon in ROLE_PROFILES:
                return ROLE_PROFILES[canon]
    return None
