"""
Expressiveness Post-Processor

Enriches raw Orpheus-generated notes with performance-quality dynamics,
CC automation, pitch bends, and timing humanization.

Informed by analysis of 200 MAESTRO classical performances + orchestral
reference MIDI:
  - CC density target: ~15-27 CC events per bar
  - CC 64 (sustain pedal) dominates in keyboard; CC 11 (expression) in
    orchestral/ensemble
  - Velocity stdev ~17, full 5-127 range
  - 92.7% of notes off 16th grid (0.06 beat mean deviation)
  - Duration range: grace notes (0.008 beats) to sustained pads (28 beats)
"""
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExpressivenessProfile:
    """
    Style-specific parameters controlling the post-processor.

    Each genre gets a tuned profile.  Profiles are composable —
    start from a base and override selectively.
    """
    # Velocity shaping
    velocity_arc: bool = True
    velocity_arc_shape: str = "phrase"  # "phrase" | "bar" | "crescendo" | "none"
    velocity_stdev_target: float = 17.0
    accent_beats: list[float] = field(default_factory=lambda: [0.0, 2.0])
    accent_strength: int = 12
    ghost_probability: float = 0.0
    ghost_velocity_range: tuple[int, int] = (25, 45)

    # CC automation
    cc_expression_enabled: bool = False
    cc_expression_density: int = 8      # events per bar
    cc_expression_range: tuple[int, int] = (70, 120)
    cc_sustain_enabled: bool = False
    cc_sustain_changes_per_bar: float = 2.0
    cc_mod_enabled: bool = False
    cc_mod_depth: int = 40

    # Pitch bend
    pitch_bend_enabled: bool = False
    pitch_bend_probability: float = 0.0
    pitch_bend_range: int = 4096        # ±1 semitone

    # Timing humanization
    humanize_timing: bool = True
    timing_jitter_beats: float = 0.02   # random ± offset
    timing_late_bias: float = 0.005     # slight laid-back feel


# ---------------------------------------------------------------------------
# Genre profiles (informed by 200-file MAESTRO analysis + concerto)
# ---------------------------------------------------------------------------

PROFILES: dict[str, ExpressivenessProfile] = {
    "classical": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="phrase",
        velocity_stdev_target=20.0,
        accent_beats=[0.0, 2.0],
        accent_strength=15,
        cc_expression_enabled=True,
        cc_expression_density=10,
        cc_expression_range=(60, 115),
        cc_sustain_enabled=True,
        cc_sustain_changes_per_bar=2.0,
        cc_mod_enabled=False,
        humanize_timing=True,
        timing_jitter_beats=0.06,
        timing_late_bias=0.0,
    ),
    "cinematic": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="crescendo",
        velocity_stdev_target=22.0,
        accent_beats=[0.0],
        accent_strength=18,
        cc_expression_enabled=True,
        cc_expression_density=12,
        cc_expression_range=(50, 127),
        cc_sustain_enabled=True,
        cc_sustain_changes_per_bar=1.0,
        cc_mod_enabled=True,
        cc_mod_depth=60,
        pitch_bend_enabled=True,
        pitch_bend_probability=0.08,
        pitch_bend_range=2048,
        humanize_timing=True,
        timing_jitter_beats=0.04,
    ),
    "jazz": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="phrase",
        velocity_stdev_target=18.0,
        accent_beats=[1.0, 3.0],
        accent_strength=10,
        ghost_probability=0.15,
        ghost_velocity_range=(25, 40),
        cc_sustain_enabled=True,
        cc_sustain_changes_per_bar=3.0,
        cc_mod_enabled=True,
        cc_mod_depth=25,
        humanize_timing=True,
        timing_jitter_beats=0.05,
        timing_late_bias=0.015,
    ),
    "neo_soul": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="phrase",
        velocity_stdev_target=16.0,
        accent_beats=[1.0, 3.0],
        accent_strength=8,
        ghost_probability=0.1,
        cc_expression_enabled=True,
        cc_expression_density=6,
        cc_expression_range=(60, 100),
        cc_sustain_enabled=True,
        cc_sustain_changes_per_bar=2.0,
        humanize_timing=True,
        timing_jitter_beats=0.04,
        timing_late_bias=0.02,
    ),
    "boom_bap": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="bar",
        velocity_stdev_target=14.0,
        accent_beats=[0.0, 2.0],
        accent_strength=10,
        ghost_probability=0.08,
        ghost_velocity_range=(30, 50),
        cc_sustain_enabled=False,
        humanize_timing=True,
        timing_jitter_beats=0.03,
        timing_late_bias=0.01,
    ),
    "trap": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="bar",
        velocity_stdev_target=15.0,
        accent_beats=[0.0],
        accent_strength=14,
        cc_expression_enabled=True,
        cc_expression_density=4,
        cc_expression_range=(80, 127),
        pitch_bend_enabled=True,
        pitch_bend_probability=0.12,
        pitch_bend_range=8192,
        humanize_timing=True,
        timing_jitter_beats=0.015,
    ),
    "house": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="bar",
        velocity_stdev_target=10.0,
        accent_beats=[0.0, 1.0, 2.0, 3.0],
        accent_strength=8,
        cc_expression_enabled=False,
        humanize_timing=True,
        timing_jitter_beats=0.01,
    ),
    "techno": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="bar",
        velocity_stdev_target=8.0,
        accent_beats=[0.0, 1.0, 2.0, 3.0],
        accent_strength=6,
        cc_expression_enabled=True,
        cc_expression_density=4,
        cc_expression_range=(80, 120),
        humanize_timing=True,
        timing_jitter_beats=0.008,
    ),
    "ambient": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="crescendo",
        velocity_stdev_target=12.0,
        accent_beats=[],
        accent_strength=0,
        cc_expression_enabled=True,
        cc_expression_density=10,
        cc_expression_range=(30, 90),
        cc_sustain_enabled=True,
        cc_sustain_changes_per_bar=0.5,
        cc_mod_enabled=True,
        cc_mod_depth=70,
        humanize_timing=True,
        timing_jitter_beats=0.07,
    ),
    "funk": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="bar",
        velocity_stdev_target=18.0,
        accent_beats=[0.0, 1.0, 2.0, 3.0],
        accent_strength=14,
        ghost_probability=0.2,
        ghost_velocity_range=(25, 45),
        humanize_timing=True,
        timing_jitter_beats=0.02,
        timing_late_bias=0.005,
    ),
    "lofi": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="phrase",
        velocity_stdev_target=12.0,
        accent_beats=[0.0, 2.0],
        accent_strength=6,
        cc_sustain_enabled=True,
        cc_sustain_changes_per_bar=1.0,
        cc_expression_enabled=True,
        cc_expression_density=4,
        cc_expression_range=(40, 80),
        humanize_timing=True,
        timing_jitter_beats=0.05,
        timing_late_bias=0.02,
    ),
    "drum_and_bass": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="bar",
        velocity_stdev_target=14.0,
        accent_beats=[0.0],
        accent_strength=12,
        cc_expression_enabled=True,
        cc_expression_density=6,
        cc_expression_range=(70, 127),
        humanize_timing=True,
        timing_jitter_beats=0.012,
    ),
    "reggae": ExpressivenessProfile(
        velocity_arc=True,
        velocity_arc_shape="bar",
        velocity_stdev_target=12.0,
        accent_beats=[1.0, 3.0],
        accent_strength=10,
        humanize_timing=True,
        timing_jitter_beats=0.03,
        timing_late_bias=0.02,
    ),
}

# Aliases
PROFILES["hip_hop"] = PROFILES["boom_bap"]
PROFILES["r_and_b"] = PROFILES["neo_soul"]
PROFILES["dnb"] = PROFILES["drum_and_bass"]
PROFILES["lo-fi"] = PROFILES["lofi"]
PROFILES["lo_fi"] = PROFILES["lofi"]
PROFILES["drill"] = PROFILES["trap"]
PROFILES["dubstep"] = PROFILES["drum_and_bass"]


def get_profile(style: str) -> ExpressivenessProfile:
    """Look up profile by style, with fuzzy fallback."""
    key = style.lower().replace(" ", "_").replace("-", "_")
    if key in PROFILES:
        return PROFILES[key]
    for pk in PROFILES:
        if pk in key or key in pk:
            return PROFILES[pk]
    return ExpressivenessProfile()


# ---------------------------------------------------------------------------
# Velocity curves
# ---------------------------------------------------------------------------

def add_velocity_curves(
    notes: list[dict],
    style: str,
    bars: int,
    rng: Optional[random.Random] = None,
) -> list[dict]:
    """
    Apply phrase-level velocity arcs, accent patterns, and ghost notes.

    Mutates notes in-place and returns the same list.
    """
    if not notes:
        return notes

    prof = get_profile(style)
    if not prof.velocity_arc:
        return notes

    rng = rng or random.Random(42)
    beats_total = bars * 4

    for note in notes:
        beat = note.get("start_beat", 0)
        vel = note.get("velocity", 80)
        frac = beat / max(beats_total, 1)

        # Phrase shape
        if prof.velocity_arc_shape == "phrase":
            # Triangle: build to 2/3, then fall
            shape = 1.0 - abs(3.0 * frac - 2.0) / 2.0
            vel = int(vel * (0.85 + 0.30 * shape))
        elif prof.velocity_arc_shape == "crescendo":
            vel = int(vel * (0.80 + 0.40 * frac))
        elif prof.velocity_arc_shape == "bar":
            bar_frac = (beat % 4) / 4
            shape = 1.0 - abs(2.0 * bar_frac - 1.0)
            vel = int(vel * (0.90 + 0.20 * shape))

        # Accents on strong beats
        beat_in_bar = beat % 4
        for accent_beat in prof.accent_beats:
            if abs(beat_in_bar - accent_beat) < 0.05:
                vel += prof.accent_strength
                break

        # Slight random humanization
        vel += rng.randint(-3, 3)
        note["velocity"] = max(1, min(127, vel))

    # Ghost note insertion
    if prof.ghost_probability > 0 and len(notes) > 2:
        new_ghosts: list[dict] = []
        for note in notes:
            if rng.random() < prof.ghost_probability:
                ghost_beat = note["start_beat"] - 0.25
                if ghost_beat >= 0:
                    gv = rng.randint(*prof.ghost_velocity_range)
                    new_ghosts.append({
                        "pitch": note["pitch"],
                        "start_beat": round(ghost_beat, 3),
                        "duration_beats": 0.1,
                        "velocity": gv,
                    })
        notes.extend(new_ghosts)
        notes.sort(key=lambda n: (n.get("start_beat", 0), n.get("pitch", 0)))

    return notes


# ---------------------------------------------------------------------------
# CC automation
# ---------------------------------------------------------------------------

def add_cc_automation(
    notes: list[dict],
    style: str,
    bars: int,
    instrument_role: str = "melody",
) -> list[dict]:
    """
    Generate CC events (expression, sustain pedal, mod wheel) based on style
    and instrument role.

    Returns a list of CC event dicts: {cc, beat, value}.
    """
    prof = get_profile(style)
    cc_events: list[dict] = []
    is_keys = instrument_role in ("chords", "piano", "keys", "pads")

    # CC 11 — Expression
    if prof.cc_expression_enabled:
        lo, hi = prof.cc_expression_range
        for bar in range(bars):
            base = bar * 4
            steps = prof.cc_expression_density
            for i in range(steps):
                t = i / max(steps - 1, 1)
                if prof.velocity_arc_shape == "crescendo":
                    shape = t
                else:
                    shape = 1.0 - abs(2.0 * t - 1.0)
                val = int(lo + (hi - lo) * shape)
                cc_events.append({
                    "cc": 11,
                    "beat": round(base + i * 4 / steps, 3),
                    "value": min(val, 127),
                })

    # CC 64 — Sustain pedal
    if prof.cc_sustain_enabled and is_keys:
        changes = max(1, int(prof.cc_sustain_changes_per_bar))
        interval = 4.0 / changes
        for bar in range(bars):
            base = bar * 4
            for c in range(changes):
                down_beat = base + c * interval
                up_beat = down_beat + interval - 0.25
                cc_events.append({"cc": 64, "beat": round(down_beat, 3), "value": 127})
                cc_events.append({"cc": 64, "beat": round(up_beat, 3), "value": 0})

    # CC 1 — Mod wheel (vibrato)
    if prof.cc_mod_enabled:
        depth = prof.cc_mod_depth
        for bar in range(bars):
            base = bar * 4
            cc_events.append({"cc": 1, "beat": base, "value": 0})
            cc_events.append({"cc": 1, "beat": round(base + 1, 3), "value": depth // 3})
            cc_events.append({"cc": 1, "beat": round(base + 2, 3), "value": depth})
            cc_events.append({"cc": 1, "beat": round(base + 3, 3), "value": depth // 2})

    cc_events.sort(key=lambda e: (e["beat"], e["cc"]))
    return cc_events


# ---------------------------------------------------------------------------
# Pitch bends
# ---------------------------------------------------------------------------

def add_pitch_bend_phrasing(
    notes: list[dict],
    style: str,
    instrument_role: str = "melody",
    rng: Optional[random.Random] = None,
) -> list[dict]:
    """
    Add subtle pitch bends for slides, approach notes, and phrase endings.

    Returns a list of pitch bend event dicts: {beat, value}.
    """
    prof = get_profile(style)
    if not prof.pitch_bend_enabled:
        return []

    rng = rng or random.Random(42)
    bends: list[dict] = []
    pb_range = prof.pitch_bend_range

    for note in notes:
        if rng.random() >= prof.pitch_bend_probability:
            continue

        beat = note["start_beat"]
        if instrument_role in ("bass", "lead", "melody"):
            # Slide up from below
            bends.append({"beat": round(beat - 0.15, 3), "value": -pb_range})
            bends.append({"beat": round(beat, 3), "value": 0})
        elif instrument_role in ("guitar",):
            # Blues bend
            bends.append({"beat": round(beat, 3), "value": 0})
            bends.append({"beat": round(beat + 0.3, 3), "value": pb_range})
            bends.append({"beat": round(beat + 0.6, 3), "value": 0})

    bends.sort(key=lambda e: e["beat"])
    return bends


# ---------------------------------------------------------------------------
# Timing humanization
# ---------------------------------------------------------------------------

def add_timing_humanization(
    notes: list[dict],
    style: str,
    rng: Optional[random.Random] = None,
) -> list[dict]:
    """
    Add micro-timing offsets to push notes slightly off-grid.

    Mutates notes in-place and returns the same list.
    Target: ~92% of notes off the 16th grid (matching pro MIDI analysis).
    """
    prof = get_profile(style)
    if not prof.humanize_timing:
        return notes

    rng = rng or random.Random(42)
    jitter = prof.timing_jitter_beats
    bias = prof.timing_late_bias

    for note in notes:
        offset = rng.gauss(bias, jitter)
        new_beat = note["start_beat"] + offset
        note["start_beat"] = round(max(0, new_beat), 4)

    return notes


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def apply_expressiveness(
    notes: list[dict],
    style: str,
    bars: int,
    instrument_role: str = "melody",
    seed: int = 42,
) -> dict:
    """
    Full expressiveness post-processing pipeline.

    Args:
        notes: Raw note dicts from Orpheus.
        style: Genre/style string.
        bars: Number of bars.
        instrument_role: "drums", "bass", "melody", "chords", etc.
        seed: RNG seed for reproducibility.

    Returns:
        Dict with keys: notes, cc_events, pitch_bends.
        notes is mutated in-place with velocity curves and timing humanization.
    """
    rng = random.Random(seed)

    # Skip drums — they have their own groove engine
    if instrument_role == "drums":
        return {"notes": notes, "cc_events": [], "pitch_bends": []}

    add_velocity_curves(notes, style, bars, rng)
    add_timing_humanization(notes, style, rng)
    cc_events = add_cc_automation(notes, style, bars, instrument_role)
    pitch_bends = add_pitch_bend_phrasing(notes, style, instrument_role, rng)

    logger.info(
        f"Expressiveness applied ({style}/{instrument_role}): "
        f"{len(notes)} notes, {len(cc_events)} CC, {len(pitch_bends)} PB"
    )
    return {"notes": notes, "cc_events": cc_events, "pitch_bends": pitch_bends}
