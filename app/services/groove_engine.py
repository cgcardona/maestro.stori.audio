"""
Groove Engine: Style-specific microtiming, velocity, and articulation.

Provides instrument-role based timing offsets, swing grids, accent maps, and 
hat articulation rules. This is the key to "played" vs "mechanized" feel.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from app.contracts.json_types import NoteDict

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Groove Profile: per-style timing + velocity + articulation
# -----------------------------------------------------------------------------

@dataclass
class GrooveProfile:
    """
    Style-specific groove parameters for microtiming, velocity, and articulation.
    
    Attributes:
        name: Profile identifier (e.g., "boom_bap", "trap", "house")
        role_offset_ms: Per-role (min_ms, max_ms) timing offset. Positive = late, negative = early.
        swing_amount: 0..1 swing intensity (0 = straight, 1 = heavy shuffle)
        swing_grid: "8th" or "16th" - which subdivisions get swing delay
        accent_map: Beat position -> velocity multiplier (e.g., {0.0: 1.1, 1.0: 1.25} for backbeat)
        hat_arc: (min_mul, max_mul) for hat velocity arc over bar (hand motion simulation)
        ghost_timing_late_ms: How late ghosts should be (anticipate/answer backbeat)
        fill_timing_variance_ms: Timing variance range for fill notes
        velocity_humanize_range: (min, max) random velocity adjustment
    """
    name: str
    role_offset_ms: dict[str, tuple[int, int]] = field(default_factory=dict)
    swing_amount: float = 0.0
    swing_grid: str = "8th"  # "8th" or "16th"
    accent_map: dict[float, float] = field(default_factory=dict)
    hat_arc: tuple[float, float] = (0.95, 1.05)
    ghost_timing_late_ms: int = 15
    fill_timing_variance_ms: tuple[int, int] = (-5, 10)
    velocity_humanize_range: tuple[int, int] = (-3, 3)


# -----------------------------------------------------------------------------
# Built-in Groove Profiles by Style
# -----------------------------------------------------------------------------

BOOM_BAP = GrooveProfile(
    name="boom_bap",
    role_offset_ms={
        "kick": (-8, -2),      # Kick slightly early (punchy)
        "snare": (8, 16),      # Snare laid back (pocket)
        "hat": (4, 12),        # Hats slightly late (lazy feel)
        "ghost": (10, 20),     # Ghosts very late (behind the beat)
        "fill": (0, 8),        # Fills slightly late
        "cymbal": (0, 5),      # Cymbals on/slightly late
    },
    swing_amount=0.55,         # Medium swing (8th note shuffle)
    swing_grid="8th",
    accent_map={
        0.0: 1.10,  # Beat 1 (downbeat) - accented
        1.0: 1.25,  # Beat 2 (backbeat) - strong accent
        2.0: 1.05,  # Beat 3 - light accent
        3.0: 1.25,  # Beat 4 (backbeat) - strong accent
    },
    hat_arc=(0.90, 1.05),      # Hats get quieter mid-bar, louder at ends
    ghost_timing_late_ms=18,
    fill_timing_variance_ms=(0, 12),
    velocity_humanize_range=(-5, 5),
)

TRAP_STRAIGHT = GrooveProfile(
    name="trap_straight",
    role_offset_ms={
        "kick": (-5, 2),       # Kick slightly early to on-beat
        "snare": (0, 8),       # Snare on/slightly late
        "hat": (-3, 6),        # Hats tight with slight variance
        "ghost": (5, 15),      # Ghosts late
        "fill": (-2, 5),       # Fills tight
        "cymbal": (-2, 3),     # Cymbals tight
    },
    swing_amount=0.0,          # Straight 16ths
    swing_grid="16th",
    accent_map={
        0.0: 1.15,  # Beat 1 - strong
        1.0: 1.20,  # Beat 2 - backbeat
        2.0: 1.08,  # Beat 3
        3.0: 1.20,  # Beat 4 - backbeat
    },
    hat_arc=(0.92, 1.08),      # Subtle arc
    ghost_timing_late_ms=10,
    fill_timing_variance_ms=(-3, 8),
    velocity_humanize_range=(-4, 4),
)

TRAP_TRIPLET = GrooveProfile(
    name="trap_triplet",
    role_offset_ms={
        "kick": (-6, 0),       # Kick early/on-beat
        "snare": (2, 10),      # Snare slightly late
        "hat": (-2, 8),        # Hats with triplet feel variance
        "ghost": (8, 18),      # Ghosts late
        "fill": (-2, 6),
        "cymbal": (-2, 4),
    },
    swing_amount=0.33,         # Triplet feel (not quite swing)
    swing_grid="16th",
    accent_map={
        0.0: 1.12,
        1.0: 1.18,
        2.0: 1.05,
        3.0: 1.18,
    },
    hat_arc=(0.90, 1.06),
    ghost_timing_late_ms=12,
    fill_timing_variance_ms=(-2, 8),
    velocity_humanize_range=(-4, 5),
)

HOUSE_FOUR_ON_FLOOR = GrooveProfile(
    name="house",
    role_offset_ms={
        "kick": (-3, 3),       # Kick tight (machine-like with slight humanization)
        "snare": (2, 8),       # Clap/snare slightly late (groove)
        "hat": (0, 6),         # Hats tight to slightly late
        "ghost": (5, 12),      # Ghosts late
        "fill": (0, 5),
        "cymbal": (0, 4),
    },
    swing_amount=0.15,         # Light shuffle (house groove)
    swing_grid="16th",
    accent_map={
        0.0: 1.20,  # All 4 kicks accented
        1.0: 1.15,  # Clap on 2
        2.0: 1.20,  # Kick
        3.0: 1.15,  # Clap on 4
    },
    hat_arc=(0.93, 1.03),      # Subtle arc (more mechanical)
    ghost_timing_late_ms=8,
    fill_timing_variance_ms=(-2, 5),
    velocity_humanize_range=(-3, 3),
)

# Tight profile for when user wants minimal humanization
TIGHT = GrooveProfile(
    name="tight",
    role_offset_ms={
        "kick": (-2, 2),
        "snare": (-1, 3),
        "hat": (-1, 2),
        "ghost": (2, 6),
        "fill": (-1, 2),
        "cymbal": (-1, 2),
    },
    swing_amount=0.0,
    swing_grid="8th",
    accent_map={
        0.0: 1.05,
        1.0: 1.10,
        2.0: 1.02,
        3.0: 1.10,
    },
    hat_arc=(0.98, 1.02),
    ghost_timing_late_ms=4,
    fill_timing_variance_ms=(-2, 2),
    velocity_humanize_range=(-2, 2),
)

# Pushed profile for aggressive forward feel
PUSHED = GrooveProfile(
    name="pushed",
    role_offset_ms={
        "kick": (-10, -3),     # Kick very early (aggressive)
        "snare": (-5, 2),      # Snare early/on-beat
        "hat": (-6, 0),        # Hats early (driving)
        "ghost": (0, 8),       # Ghosts still slightly late
        "fill": (-5, 2),
        "cymbal": (-5, 0),
    },
    swing_amount=0.0,
    swing_grid="8th",
    accent_map={
        0.0: 1.15,
        1.0: 1.12,
        2.0: 1.10,
        3.0: 1.12,
    },
    hat_arc=(0.95, 1.08),
    ghost_timing_late_ms=5,
    fill_timing_variance_ms=(-5, 3),
    velocity_humanize_range=(-3, 5),
)

# Laid back profile for relaxed feel
LAID_BACK = GrooveProfile(
    name="laid_back",
    role_offset_ms={
        "kick": (0, 5),        # Kick on/slightly late
        "snare": (12, 22),     # Snare very late (deep pocket)
        "hat": (8, 16),        # Hats lazy
        "ghost": (15, 25),     # Ghosts very late
        "fill": (5, 15),
        "cymbal": (3, 10),
    },
    swing_amount=0.45,
    swing_grid="8th",
    accent_map={
        0.0: 1.08,
        1.0: 1.20,  # Strong backbeat
        2.0: 1.02,
        3.0: 1.20,
    },
    hat_arc=(0.88, 1.02),
    ghost_timing_late_ms=20,
    fill_timing_variance_ms=(5, 15),
    velocity_humanize_range=(-6, 4),
)

# Profile registry
GROOVE_PROFILES: dict[str, GrooveProfile] = {
    "boom_bap": BOOM_BAP,
    "boom_bap_swing": BOOM_BAP,
    "hip_hop": BOOM_BAP,
    "hip hop": BOOM_BAP,
    "trap": TRAP_STRAIGHT,
    "trap_straight": TRAP_STRAIGHT,
    "trap_triplet": TRAP_TRIPLET,
    "house": HOUSE_FOUR_ON_FLOOR,
    "house_four_on_floor": HOUSE_FOUR_ON_FLOOR,
    "techno": HOUSE_FOUR_ON_FLOOR,
    "tight": TIGHT,
    "pushed": PUSHED,
    "laid_back": LAID_BACK,
}


def get_groove_profile(style: str, humanize_profile: str | None = None) -> GrooveProfile:
    """
    Get groove profile for a style, optionally modified by humanize_profile.
    
    Args:
        style: Music style (e.g., "boom_bap", "trap", "house")
        humanize_profile: Optional override ("tight", "laid_back", "pushed")
    
    Returns:
        GrooveProfile for the style/feel combination
    """
    # If humanize_profile is explicitly set, use that
    if humanize_profile and humanize_profile in GROOVE_PROFILES:
        return GROOVE_PROFILES[humanize_profile]
    
    # Otherwise use style-based profile
    style_lower = style.lower().replace("-", "_").replace(" ", "_")
    if style_lower in GROOVE_PROFILES:
        return GROOVE_PROFILES[style_lower]
    
    # Default to trap
    return TRAP_STRAIGHT


# -----------------------------------------------------------------------------
# Role Detection: pitch → role for groove offset lookup
# -----------------------------------------------------------------------------

# GM drum map → role
PITCH_TO_ROLE = {
    # Kick
    35: "kick", 36: "kick",
    # Snare / Clap
    38: "snare", 39: "snare", 40: "snare",
    # Rim / Ghost instruments (treated as ghost when velocity low)
    37: "ghost",
    # Hi-hats
    42: "hat", 44: "hat", 46: "hat",
    # Toms (often used in fills)
    41: "fill", 43: "fill", 45: "fill", 47: "fill", 48: "fill", 50: "fill",
    # Cymbals
    49: "cymbal", 51: "cymbal", 52: "cymbal", 53: "cymbal", 55: "cymbal", 57: "cymbal", 59: "cymbal",
    # Percussion (ear candy)
    54: "ghost", 56: "ghost", 69: "ghost", 70: "ghost", 75: "ghost",
}


def get_role_for_pitch(pitch: int, velocity: int = 80, layer: str | None = None) -> str:
    """
    Determine the role of a drum hit for groove offset lookup.
    
    Uses pitch, velocity, and optional layer hint to determine role.
    Ghost notes are detected by low velocity on snare/rim pitches.
    """
    # If layer is explicitly provided, use it
    if layer:
        if layer == "ghost_layer":
            return "ghost"
        if layer == "fills":
            return "fill"
        if layer == "cymbal_punctuation":
            return "cymbal"
        if layer == "timekeepers":
            return "hat"
        if layer == "core":
            # Core layer contains kick and snare
            if pitch in (35, 36):
                return "kick"
            return "snare"
    
    # Velocity-based ghost detection
    if velocity < 60 and pitch in (37, 38, 40):
        return "ghost"
    
    # Pitch-based role
    return PITCH_TO_ROLE.get(pitch, "hat")


# -----------------------------------------------------------------------------
# Swing Calculation
# -----------------------------------------------------------------------------

def is_offbeat_for_grid(beat_position: float, swing_grid: str) -> bool:
    """
    Determine if a beat position is an offbeat for the given swing grid.
    
    For 8th grid: offbeats are at 0.5, 1.5, 2.5, 3.5 (the "ands")
    For 16th grid: offbeats are at 0.25, 0.75, 1.25, 1.75, etc. (every other 16th)
    """
    beat_in_bar = beat_position % 4.0
    
    if swing_grid == "8th":
        # Offbeats for 8ths: 0.5, 1.5, 2.5, 3.5
        frac = beat_in_bar % 1.0
        return abs(frac - 0.5) < 0.05
    else:  # 16th
        # Offbeats for 16ths: 0.25, 0.75, 1.25, etc.
        frac = (beat_in_bar * 4) % 2
        return abs(frac - 1) < 0.2


def calculate_swing_offset(
    beat_position: float,
    profile: GrooveProfile,
    tempo: int,
) -> float:
    """
    Calculate swing timing offset in beats.
    
    Swing delays offbeat subdivisions by a percentage of the subdivision length.
    8th swing: delay "ands" by up to ~50ms at 120bpm
    16th swing: delay odd 16ths by up to ~25ms at 120bpm
    """
    if profile.swing_amount <= 0:
        return 0.0
    
    if not is_offbeat_for_grid(beat_position, profile.swing_grid):
        return 0.0
    
    # Calculate max swing delay in beats
    # 8th swing: up to 1/3 of an 8th note (makes triplet feel)
    # 16th swing: up to 1/3 of a 16th note
    if profile.swing_grid == "8th":
        max_swing_beats = 0.5 * 0.33 * profile.swing_amount  # Up to ~0.08 beats
    else:
        max_swing_beats = 0.25 * 0.33 * profile.swing_amount  # Up to ~0.04 beats
    
    return max_swing_beats


# -----------------------------------------------------------------------------
# Main Groove Application Function
# -----------------------------------------------------------------------------

def apply_groove_map(
    notes: list[NoteDict],
    tempo: int,
    style: str = "trap",
    humanize_profile: str | None = None,
    layer_map: dict[int, str] | None = None,
    rng: random.Random | None = None,
) -> list[NoteDict]:
    """
    Apply style-specific groove to notes: microtiming, swing, velocity shaping.
    
    Core Groove Engine function providing instrument-role-aware timing
    and velocity curves.
    
    Args:
        notes: list of {pitch, start_beat, duration_beats, velocity, ...}
        tempo: BPM
        style: Music style (e.g., "boom_bap", "trap", "house")
        humanize_profile: Optional feel override ("tight", "laid_back", "pushed")
        layer_map: Optional dict mapping note index -> layer name
        rng: Random number generator for reproducibility
    
    Returns:
        list of notes with groove applied (timing + velocity adjusted)
    """
    if not notes:
        return notes
    
    rng = rng or random.Random()
    profile = get_groove_profile(style, humanize_profile)
    ms_per_beat = 60_000 / tempo
    
    out: list[NoteDict] = []
    for idx, n in enumerate(notes):
        nn: NoteDict = n.copy()
        pitch = nn.get("pitch", 36)
        velocity = nn.get("velocity", 80)
        start = nn.get("start_beat", 0.0)
        
        # Get layer from layer_map or note metadata
        layer = None
        if layer_map and idx in layer_map:
            layer = layer_map[idx]
        elif "layer" in nn:
            layer = nn["layer"]
        
        # Determine role for this note
        role = get_role_for_pitch(pitch, velocity, layer)
        
        # Get beat position in bar
        beat_in_bar = start % 4.0
        
        # 1. Apply swing
        swing_offset = calculate_swing_offset(start, profile, tempo)
        
        # 2. Apply role-specific timing offset
        offset_range = profile.role_offset_ms.get(role, (-3, 3))
        role_offset_ms = rng.randint(offset_range[0], offset_range[1])
        role_offset_beats = role_offset_ms / ms_per_beat
        
        # Combine offsets
        total_offset = swing_offset + role_offset_beats
        new_start = start + total_offset
        
        # Quantize to 1/32 grid for stability (0.125 beats = 32nd note)
        new_start = round(new_start * 32) / 32
        nn["start_beat"] = max(0.0, new_start)
        
        # 3. Apply velocity shaping
        # a. Beat accent
        accent_mul = profile.accent_map.get(float(int(beat_in_bar)), 1.0)
        
        # b. Hat arc (velocity curve over bar)
        if role == "hat":
            # Arc: quieter in middle of bar, louder at start/end
            t = beat_in_bar / 4.0
            arc_pos = 1 - abs(2 * t - 1)  # 0 at ends, 1 in middle
            arc_mul = profile.hat_arc[0] + (profile.hat_arc[1] - profile.hat_arc[0]) * arc_pos
        else:
            arc_mul = 1.0
        
        # c. Humanize variation
        humanize_lo, humanize_hi = profile.velocity_humanize_range
        humanize_offset = rng.randint(humanize_lo, humanize_hi)
        
        # Combine velocity adjustments
        new_vel = int(velocity * accent_mul * arc_mul) + humanize_offset
        nn["velocity"] = max(1, min(127, new_vel))
        
        out.append(nn)
    
    # Sort by start_beat for stable output
    out.sort(key=lambda x: (x["start_beat"], x["pitch"]))
    logger.debug(f"Groove Engine: applied {profile.name} profile to {len(out)} notes")
    return out


# -----------------------------------------------------------------------------
# Kick/Snare Onset Extraction (for bass coupling)
# -----------------------------------------------------------------------------

KICK_PITCHES = {35, 36}
SNARE_PITCHES = {38, 39, 40}
HAT_PITCHES = {42, 44, 46}


def extract_onsets(notes: list[NoteDict], pitch_set: set[int]) -> list[float]:
    """
    Extract onset times for notes matching the given pitch set.
    
    Used to get kick/snare/hat onsets for bass coupling.
    """
    return sorted({round(n["start_beat"], 4) for n in notes if n.get("pitch") in pitch_set})


def extract_kick_onsets(notes: list[NoteDict]) -> list[float]:
    """Extract kick drum onset times from drum notes."""
    return extract_onsets(notes, KICK_PITCHES)


def extract_snare_onsets(notes: list[NoteDict]) -> list[float]:
    """Extract snare/clap onset times from drum notes."""
    return extract_onsets(notes, SNARE_PITCHES)


def extract_hat_grid(notes: list[NoteDict]) -> list[float]:
    """Extract hi-hat onset times from drum notes."""
    return extract_onsets(notes, HAT_PITCHES)


# -----------------------------------------------------------------------------
# Rhythm Spine: shared rhythmic backbone for coupled generation
# -----------------------------------------------------------------------------

@dataclass
class RhythmSpine:
    """
    Shared rhythmic backbone derived from drum output.
    
    Used to couple bass, melody, and other parts to the drum groove.
    """
    kick_onsets: list[float] = field(default_factory=list)
    snare_onsets: list[float] = field(default_factory=list)
    hat_grid: list[float] = field(default_factory=list)
    tempo: int = 120
    bars: int = 16
    groove_profile: GrooveProfile | None = None
    
    @classmethod
    def from_drum_notes(
        cls,
        notes: list[NoteDict],
        tempo: int = 120,
        bars: int = 16,
        style: str = "trap",
    ) -> "RhythmSpine":
        """Create RhythmSpine from rendered drum notes."""
        return cls(
            kick_onsets=extract_kick_onsets(notes),
            snare_onsets=extract_snare_onsets(notes),
            hat_grid=extract_hat_grid(notes),
            tempo=tempo,
            bars=bars,
            groove_profile=get_groove_profile(style),
        )
    
    def get_anticipation_slots(self, beat_before: float = 0.125) -> list[float]:
        """
        Get anticipation slots: positions slightly before strong kicks.
        
        Used for bass anticipation (playing slightly before the kick).
        """
        return [round(k - beat_before, 4) for k in self.kick_onsets if k >= beat_before]
    
    def get_response_slots(self, snare_offset: float = 0.25) -> list[float]:
        """
        Get response slots: positions after snare hits.
        
        Used for call-response patterns in bass/melody.
        """
        return [round(s + snare_offset, 4) for s in self.snare_onsets]
