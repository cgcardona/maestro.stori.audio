"""
Emotion Vector Schema for neural music generation conditioning.

The emotion vector is a 5-dimensional continuous control signal that:
1. Conditions generation models
2. Guides refinement commands
3. Enables precise emotional control

See NEURAL_MIDI_ROADMAP.md for full specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class EmotionVector:
    """
    5-axis emotional control vector.
    
    All axes are continuous floats. Models consume these directly
    as conditioning signals.
    
    Attributes:
        energy: 0.0 (stillness) to 1.0 (explosive)
        valence: -1.0 (dark/sad) to +1.0 (bright/joyful)
        tension: 0.0 (resolved) to 1.0 (unresolved/anxious)
        intimacy: 0.0 (distant/epic) to 1.0 (close/personal)
        motion: 0.0 (static/sustained) to 1.0 (driving/rhythmic)
    """
    energy: float = 0.5
    valence: float = 0.0
    tension: float = 0.3
    intimacy: float = 0.5
    motion: float = 0.5
    
    def __post_init__(self):
        """Validate and clamp values to valid ranges."""
        self.energy = self._clamp(self.energy, 0.0, 1.0)
        self.valence = self._clamp(self.valence, -1.0, 1.0)
        self.tension = self._clamp(self.tension, 0.0, 1.0)
        self.intimacy = self._clamp(self.intimacy, 0.0, 1.0)
        self.motion = self._clamp(self.motion, 0.0, 1.0)
    
    @staticmethod
    def _clamp(value: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(max_val, value))
    
    def to_conditioning_vector(self) -> list[float]:
        """Return as a list for model input."""
        return [self.energy, self.valence, self.tension, self.intimacy, self.motion]
    
    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        return {
            "energy": self.energy,
            "valence": self.valence,
            "tension": self.tension,
            "intimacy": self.intimacy,
            "motion": self.motion,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> EmotionVector:
        """Deserialize from dict."""
        return cls(
            energy=data.get("energy", 0.5),
            valence=data.get("valence", 0.0),
            tension=data.get("tension", 0.3),
            intimacy=data.get("intimacy", 0.5),
            motion=data.get("motion", 0.5),
        )
    
    def apply_delta(self, delta: dict) -> EmotionVector:
        """
        Apply a mutation delta and return new EmotionVector.
        
        Used for refinement commands like "make it sadder" → {"valence": -0.3}
        """
        return EmotionVector(
            energy=self.energy + delta.get("energy", 0.0),
            valence=self.valence + delta.get("valence", 0.0),
            tension=self.tension + delta.get("tension", 0.0),
            intimacy=self.intimacy + delta.get("intimacy", 0.0),
            motion=self.motion + delta.get("motion", 0.0),
        )
    
    def distance(self, other: EmotionVector) -> float:
        """Euclidean distance to another emotion vector."""
        d2 = (
            (self.energy - other.energy) ** 2
            + (self.valence - other.valence) ** 2
            + (self.tension - other.tension) ** 2
            + (self.intimacy - other.intimacy) ** 2
            + (self.motion - other.motion) ** 2
        )
        return float(d2**0.5)
    
    def __repr__(self) -> str:
        return (
            f"EmotionVector(energy={self.energy:.2f}, valence={self.valence:.2f}, "
            f"tension={self.tension:.2f}, intimacy={self.intimacy:.2f}, motion={self.motion:.2f})"
        )


# =============================================================================
# Preset Emotion Vectors (for quick reference / defaults)
# =============================================================================

EMOTION_PRESETS: dict[str, EmotionVector] = {
    # Basic emotions
    "neutral": EmotionVector(energy=0.5, valence=0.0, tension=0.3, intimacy=0.5, motion=0.5),
    "happy": EmotionVector(energy=0.7, valence=0.7, tension=0.2, intimacy=0.5, motion=0.6),
    "sad": EmotionVector(energy=0.3, valence=-0.6, tension=0.3, intimacy=0.7, motion=0.3),
    "angry": EmotionVector(energy=0.9, valence=-0.4, tension=0.8, intimacy=0.3, motion=0.8),
    "peaceful": EmotionVector(energy=0.2, valence=0.3, tension=0.1, intimacy=0.6, motion=0.2),
    "anxious": EmotionVector(energy=0.6, valence=-0.2, tension=0.9, intimacy=0.4, motion=0.7),
    "triumphant": EmotionVector(energy=0.95, valence=0.8, tension=0.5, intimacy=0.3, motion=0.7),
    "melancholic": EmotionVector(energy=0.25, valence=-0.4, tension=0.4, intimacy=0.8, motion=0.2),
    "euphoric": EmotionVector(energy=0.95, valence=0.9, tension=0.3, intimacy=0.4, motion=0.9),
    
    # Musical contexts
    "verse": EmotionVector(energy=0.4, valence=0.0, tension=0.3, intimacy=0.7, motion=0.4),
    "chorus": EmotionVector(energy=0.8, valence=0.3, tension=0.4, intimacy=0.5, motion=0.7),
    "bridge": EmotionVector(energy=0.5, valence=-0.1, tension=0.6, intimacy=0.6, motion=0.5),
    "intro": EmotionVector(energy=0.3, valence=0.1, tension=0.2, intimacy=0.6, motion=0.3),
    "outro": EmotionVector(energy=0.25, valence=0.2, tension=0.1, intimacy=0.7, motion=0.2),
    "breakdown": EmotionVector(energy=0.2, valence=0.0, tension=0.5, intimacy=0.8, motion=0.2),
    "buildup": EmotionVector(energy=0.6, valence=0.2, tension=0.7, intimacy=0.4, motion=0.6),
    "drop": EmotionVector(energy=1.0, valence=0.5, tension=0.3, intimacy=0.2, motion=1.0),
    
    # Genre defaults
    "indie_folk": EmotionVector(energy=0.4, valence=0.1, tension=0.3, intimacy=0.8, motion=0.4),
    "edm": EmotionVector(energy=0.85, valence=0.4, tension=0.4, intimacy=0.2, motion=0.9),
    "jazz": EmotionVector(energy=0.5, valence=0.2, tension=0.5, intimacy=0.6, motion=0.5),
    "metal": EmotionVector(energy=0.95, valence=-0.3, tension=0.8, intimacy=0.2, motion=0.85),
    "ambient": EmotionVector(energy=0.15, valence=0.2, tension=0.2, intimacy=0.7, motion=0.1),
    "hip_hop": EmotionVector(energy=0.7, valence=0.1, tension=0.4, intimacy=0.4, motion=0.75),
    "classical": EmotionVector(energy=0.5, valence=0.3, tension=0.4, intimacy=0.5, motion=0.4),
}


def get_emotion_preset(name: str) -> EmotionVector:
    """Get a preset emotion vector by name, or neutral if not found."""
    return EMOTION_PRESETS.get(name.lower(), EMOTION_PRESETS["neutral"])


# =============================================================================
# Refinement Command Mappings
# =============================================================================

REFINEMENT_DELTAS: dict[str, dict[str, float]] = {
    # Valence adjustments
    "sadder": {"valence": -0.3},
    "happier": {"valence": 0.3},
    "brighter": {"valence": 0.25},
    "darker": {"valence": -0.25},
    
    # Energy adjustments
    "more intense": {"energy": 0.2, "tension": 0.15},
    "less intense": {"energy": -0.2, "tension": -0.1},
    "bigger": {"energy": 0.25, "intimacy": -0.15},
    "smaller": {"energy": -0.2, "intimacy": 0.15},
    "calmer": {"energy": -0.25, "tension": -0.2, "motion": -0.15},
    "more energetic": {"energy": 0.3, "motion": 0.15},
    
    # Tension adjustments
    "more tension": {"tension": 0.3},
    "less tension": {"tension": -0.25},
    "resolve it": {"tension": -0.4, "valence": 0.1},
    "build up": {"tension": 0.25, "energy": 0.15},
    
    # Intimacy adjustments
    "more intimate": {"intimacy": 0.3, "energy": -0.1},
    "more epic": {"intimacy": -0.3, "energy": 0.15},
    "closer": {"intimacy": 0.25},
    "more distant": {"intimacy": -0.25},
    
    # Motion adjustments
    "more driving": {"motion": 0.3, "energy": 0.1},
    "less driving": {"motion": -0.25},
    "more rhythmic": {"motion": 0.25},
    "more sustained": {"motion": -0.3},
    "busier": {"motion": 0.25, "energy": 0.1},
    "sparser": {"motion": -0.2, "energy": -0.1},
}


def get_refinement_delta(command: str) -> Optional[dict[str, float]]:
    """
    Get the emotion delta for a refinement command.
    
    Returns None if no exact match. The LLM should handle
    fuzzy matching and complex commands.
    """
    return REFINEMENT_DELTAS.get(command.lower().strip())


# =============================================================================
# STORI PROMPT → EmotionVector Parser
# =============================================================================

# Keyword → partial emotion axis overrides derived from STORI PROMPT Vibe field.
# Values are targets; parsing blends multiple keywords by averaging.
_VIBE_KEYWORD_MAP: dict[str, dict[str, float]] = {
    # Valence (dark ↔ bright)
    "melancholic": {"valence": -0.5, "energy": 0.25, "intimacy": 0.7},
    "sad": {"valence": -0.6, "energy": 0.3},
    "dark": {"valence": -0.45, "tension": 0.5},
    "moody": {"valence": -0.3, "tension": 0.45},
    "brooding": {"valence": -0.35, "tension": 0.5, "energy": 0.3},
    "nostalgic": {"valence": -0.2, "intimacy": 0.6},
    "bittersweet": {"valence": -0.1, "intimacy": 0.6, "tension": 0.35},
    "happy": {"valence": 0.6, "energy": 0.6},
    "joyful": {"valence": 0.7, "energy": 0.65},
    "bright": {"valence": 0.5, "energy": 0.5},
    "uplifting": {"valence": 0.6, "energy": 0.65},
    "triumphant": {"valence": 0.7, "energy": 0.85, "tension": 0.4},
    "euphoric": {"valence": 0.9, "energy": 0.9, "motion": 0.9},
    # Energy
    "energetic": {"energy": 0.8, "motion": 0.7},
    "intense": {"energy": 0.85, "tension": 0.7},
    "explosive": {"energy": 1.0, "tension": 0.8, "motion": 0.9},
    "peaceful": {"energy": 0.2, "tension": 0.1},
    "calm": {"energy": 0.2, "tension": 0.1, "motion": 0.2},
    "relaxed": {"energy": 0.25, "tension": 0.1, "motion": 0.2},
    "mellow": {"energy": 0.3, "tension": 0.15, "motion": 0.3},
    "aggressive": {"energy": 0.9, "tension": 0.8, "motion": 0.8},
    "laid-back": {"energy": 0.3, "tension": 0.1, "motion": 0.35},
    # Intimacy / space
    "intimate": {"intimacy": 0.8, "energy": 0.3},
    "personal": {"intimacy": 0.75},
    "warm": {"valence": 0.3, "intimacy": 0.65},
    "cozy": {"intimacy": 0.7, "energy": 0.2, "valence": 0.2},
    "epic": {"intimacy": 0.1, "energy": 0.85},
    "distant": {"intimacy": 0.15},
    "atmospheric": {"intimacy": 0.5, "tension": 0.3, "energy": 0.2},
    "cinematic": {"intimacy": 0.3, "tension": 0.5, "energy": 0.6},
    # Motion / rhythm
    "driving": {"motion": 0.8, "energy": 0.7},
    "groovy": {"motion": 0.75, "energy": 0.6},
    "sparse": {"motion": 0.2, "energy": 0.2},
    "minimal": {"motion": 0.15, "energy": 0.15},
    "dense": {"motion": 0.7, "energy": 0.7},
    "flowing": {"motion": 0.6, "tension": 0.2},
    "bouncy": {"motion": 0.7, "energy": 0.6, "valence": 0.3},
    # Tension / harmonic colour
    "anxious": {"tension": 0.8, "energy": 0.6},
    "tense": {"tension": 0.75},
    "resolved": {"tension": 0.1, "valence": 0.2},
    "dreamy": {"tension": 0.2, "intimacy": 0.7, "energy": 0.2},
    "mysterious": {"tension": 0.6, "valence": -0.2, "energy": 0.3},
    "haunting": {"tension": 0.65, "valence": -0.4, "energy": 0.25},
    "eerie": {"tension": 0.7, "valence": -0.45},
}

# "Energy: Low/Medium/High" levels map to axis values.
_ENERGY_LEVEL_MAP: dict[str, dict[str, float]] = {
    "very low": {"energy": 0.1, "motion": 0.1},
    "low": {"energy": 0.2, "motion": 0.2},
    "medium": {"energy": 0.5, "motion": 0.5},
    "mid": {"energy": 0.5, "motion": 0.5},
    "high": {"energy": 0.8, "motion": 0.7},
    "very high": {"energy": 0.95, "motion": 0.9},
}

# Genre/style hints → closest preset name
_GENRE_PRESET_MAP: dict[str, str] = {
    "lofi": "lofi",
    "lo-fi": "lofi",
    "hip-hop": "hip_hop",
    "hip hop": "hip_hop",
    "trap": "hip_hop",
    "jazz": "jazz",
    "ambient": "ambient",
    "edm": "edm",
    "electronic": "edm",
    "metal": "metal",
    "rock": "metal",
    "folk": "indie_folk",
    "indie": "indie_folk",
    "classical": "classical",
}

# Add lofi to EMOTION_PRESETS for completeness (not already there)
EMOTION_PRESETS.setdefault(
    "lofi",
    EmotionVector(energy=0.3, valence=-0.1, tension=0.2, intimacy=0.75, motion=0.35),
)


def emotion_vector_from_stori_prompt(text: str) -> EmotionVector:
    """
    Derive an EmotionVector from a STORI PROMPT YAML block.

    Parses Vibe, Energy, Section, and Style fields and blends their
    contributions into a 5-axis emotion vector.  Falls back to the
    "neutral" preset when no recognisable fields are found.

    This is the bridge between the natural-language creative brief and
    the numeric conditioning signal sent to Orpheus.
    """
    if not text:
        return EMOTION_PRESETS["neutral"]

    vibe_text = ""
    energy_text = ""
    section_text = ""
    style_text = ""

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip().lower()
        if key == "vibe":
            vibe_text = value
        elif key == "energy":
            energy_text = value
        elif key == "section":
            section_text = value
        elif key == "style":
            style_text = value

    # Start from neutral and accumulate contributions as a running average.
    base = EMOTION_PRESETS["neutral"]
    acc: dict[str, float] = {
        "energy": base.energy,
        "valence": base.valence,
        "tension": base.tension,
        "intimacy": base.intimacy,
        "motion": base.motion,
    }
    n: float = 1  # weight of the neutral baseline

    def _blend(overrides: dict[str, float], weight: float = 1.0) -> None:
        nonlocal n
        for axis, target in overrides.items():
            acc[axis] = (acc[axis] * n + target * weight) / (n + weight)
        n += weight

    # 1. Section preset (verse/chorus/bridge/etc.) as coarse baseline.
    if section_text:
        preset_name = section_text.split()[0]  # "Verse 1" → "verse"
        preset = EMOTION_PRESETS.get(preset_name)
        if preset:
            _blend(
                {
                    "energy": preset.energy,
                    "valence": preset.valence,
                    "tension": preset.tension,
                    "intimacy": preset.intimacy,
                    "motion": preset.motion,
                },
                weight=1.5,
            )

    # 2. Genre/style preset (half weight — less specific than explicit vibe).
    if style_text:
        for genre_kw, preset_name in _GENRE_PRESET_MAP.items():
            if genre_kw in style_text:
                preset = EMOTION_PRESETS.get(preset_name)
                if preset:
                    _blend(
                        {
                            "energy": preset.energy,
                            "valence": preset.valence,
                            "tension": preset.tension,
                            "intimacy": preset.intimacy,
                            "motion": preset.motion,
                        },
                        weight=0.5,
                    )
                break

    # 3. Explicit vibe keywords (highest specificity; each kw is full weight).
    vibe_keywords = [
        kw.strip() for kw in vibe_text.replace(",", " ").split() if kw.strip()
    ]
    for kw in vibe_keywords:
        overrides = _VIBE_KEYWORD_MAP.get(kw)
        if overrides:
            _blend(overrides, weight=1.0)

    # 4. Explicit energy level (overrides energy + motion).
    for level_kw, overrides in _ENERGY_LEVEL_MAP.items():
        if level_kw in energy_text:
            _blend(overrides, weight=1.5)
            break

    return EmotionVector(
        energy=acc["energy"],
        valence=acc["valence"],
        tension=acc["tension"],
        intimacy=acc["intimacy"],
        motion=acc["motion"],
    )


# =============================================================================
# Emotion to Generation Constraints Mapping
# =============================================================================

@dataclass
class GenerationConstraints:
    """
    Constraints derived from emotion vector for conditioning generators.
    
    These are the actual parameters that affect note generation.
    """
    # Rhythm
    drum_density: float = 0.5  # 0-1, affects note count
    subdivision: int = 8  # 8 = 8th notes, 16 = 16th notes
    swing_amount: float = 0.0  # 0-0.3
    
    # Melody
    register_center: int = 60  # MIDI note number
    register_spread: int = 12  # Semitones above/below center
    rest_density: float = 0.3  # Fraction of time as rest
    leap_probability: float = 0.2  # Probability of large intervals
    
    # Harmony
    chord_extensions: bool = False  # Use 7ths, 9ths, etc.
    borrowed_chord_probability: float = 0.0
    harmonic_rhythm_bars: float = 1.0  # Chord changes per bar
    
    # Dynamics
    velocity_floor: int = 60
    velocity_ceiling: int = 100
    dynamic_range: int = 25


def emotion_to_constraints(ev: EmotionVector) -> GenerationConstraints:
    """
    Map emotion vector to concrete generation constraints.
    
    This is the core translation from emotion → music.
    """
    def lerp(a: float, b: float, t: float) -> float:
        """Linear interpolation."""
        return a + (b - a) * t
    
    # Normalize valence from [-1, 1] to [0, 1] for lerp
    valence_01 = (ev.valence + 1) / 2
    
    return GenerationConstraints(
        # Rhythm: energy and motion drive activity
        drum_density=lerp(0.2, 1.0, ev.energy * ev.motion),
        subdivision=16 if ev.motion > 0.6 else 8,
        swing_amount=lerp(0.0, 0.25, 1.0 - ev.tension),
        
        # Melody: valence affects register, energy affects spread
        register_center=int(lerp(48, 72, valence_01)),
        register_spread=int(lerp(6, 18, ev.energy)),
        rest_density=lerp(0.4, 0.1, ev.motion),
        leap_probability=lerp(0.1, 0.4, ev.tension),
        
        # Harmony: tension drives complexity
        chord_extensions=ev.tension > 0.5,
        borrowed_chord_probability=lerp(0.0, 0.3, ev.tension),
        harmonic_rhythm_bars=lerp(2.0, 0.5, ev.energy),
        
        # Dynamics: energy and intimacy
        velocity_floor=int(lerp(40, 80, ev.energy)),
        velocity_ceiling=int(lerp(80, 120, ev.energy)),
        dynamic_range=int(lerp(10, 40, 1.0 - ev.intimacy)),
    )
