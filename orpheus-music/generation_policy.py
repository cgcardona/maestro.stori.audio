"""
Generation Policy Layer for Stori Music AI

This is the "soul" of Stori - the policy that translates human musical intent
into concrete generation parameters. This is where UX philosophy, musical taste,
and product differentiation live.

Architecture:
    User Prompt → LLM → IntentVector (semantic space)
                       ↓
                  Policy Function (this module)
                       ↓
              Generator Control Vector
                       ↓
                  Orpheus Params
                       ↓
                  Music Model

Key principles:
- Pure functions (deterministic, testable, tunable)
- Compositional (apply transforms independently)
- Backend-agnostic (abstract musical controls, not model params)
- A/B testable (swap policies, measure results)
"""

from dataclasses import dataclass
from typing import Optional, List, Literal, Tuple
from enum import Enum
import os


# =============================================================================
# Musical Control Space (Backend-Agnostic)
# =============================================================================

@dataclass
class GenerationControlVector:
    """
    Abstract musical control parameters that work across any backend.
    
    This is the interface between musical intent and model parameters.
    Values are normalized 0-1 for consistency.
    """
    # Core continuous controls (0-1)
    creativity: float = 0.5          # How much variation/surprise (→ temperature)
    density: float = 0.5             # Note/event density (→ tokens_per_bar)
    complexity: float = 0.5          # Harmonic/rhythmic complexity (→ token count, seed choice)
    brightness: float = 0.5          # Pitch range, tonal color (→ seed patterns)
    tension: float = 0.5             # Dissonance, unstable tones (→ seed harmony)
    groove: float = 0.5              # Swing/humanization (→ velocity variation, timing)
    
    # Structural controls
    section_type: Optional[str] = None      # "intro", "verse", "drop", "bridge"
    loopable: bool = True                   # Should it loop seamlessly?
    build_intensity: bool = False           # Should it build/escalate?
    
    # Quality preset
    quality_preset: Literal["fast", "balanced", "quality"] = "balanced"
    
    def clamp(self):
        """Ensure all values are in valid ranges."""
        for field in ["creativity", "density", "complexity", "brightness", "tension", "groove"]:
            value = getattr(self, field)
            setattr(self, field, max(0.0, min(1.0, value)))


# =============================================================================
# Intent Vector → Control Vector (Semantic → Control)
# =============================================================================

def intent_to_controls(
    genre: str,
    tempo: int,
    musical_goals: Optional[List[str]] = None,
    tone_brightness: float = 0.0,      # -1 to 1 from IntentVector
    tone_warmth: float = 0.0,
    energy_intensity: float = 0.0,
    energy_excitement: float = 0.0,
    complexity_hint: float = 0.5,      # 0-1 from user or inferred
    quality_preset: str = "balanced",
) -> GenerationControlVector:
    """
    Convert semantic musical intent into abstract control parameters.
    
    This is the core policy function - where musical taste lives.
    
    Args:
        genre: Base musical style
        tempo: BPM
        musical_goals: List like ["dark", "energetic", "minimal"]
        tone_brightness: -1 (dark) to +1 (bright)
        tone_warmth: -1 (cold) to +1 (warm)
        energy_intensity: -1 (calm) to +1 (intense)
        energy_excitement: -1 (laid back) to +1 (exciting)
        complexity_hint: 0 (simple) to 1 (complex)
        quality_preset: "fast", "balanced", or "quality"
    
    Returns:
        GenerationControlVector with normalized 0-1 values
    """
    # Start with neutral defaults
    controls = GenerationControlVector(quality_preset=quality_preset)
    
    # Apply each factor compositionally
    controls = apply_genre_baseline(controls, genre)
    controls = apply_tone_vector(controls, tone_brightness, tone_warmth)
    controls = apply_energy_vector(controls, energy_intensity, energy_excitement)
    controls = apply_complexity(controls, complexity_hint)
    controls = apply_musical_goals(controls, musical_goals or [])
    controls = apply_tempo_adjustments(controls, tempo)
    
    # Clamp to valid ranges
    controls.clamp()
    
    return controls


# =============================================================================
# Compositional Transform Functions
# =============================================================================

def apply_genre_baseline(controls: GenerationControlVector, genre: str) -> GenerationControlVector:
    """
    Apply genre-specific baseline adjustments.
    
    Uses fuzzy matching to understand genre variations without brittle lookups.
    """
    genre_lower = genre.lower()
    
    # Repetitive/minimal genres → lower creativity, higher density
    if any(x in genre_lower for x in ["techno", "house", "trance", "minimal"]):
        controls.creativity *= 0.85      # More deterministic
        controls.density *= 1.1          # More regular patterns
        controls.complexity *= 0.9       # Simpler structures
        controls.loopable = True
        
    # Complex/improvisational genres → higher creativity
    elif any(x in genre_lower for x in ["jazz", "fusion", "prog", "experimental"]):
        controls.creativity *= 1.2       # More variation
        controls.complexity *= 1.3       # More complex harmony/rhythm
        controls.density *= 1.15         # More notes
        controls.loopable = False        # Through-composed
        
    # Trap/modern hip-hop → high rhythmic variation, moderate creativity
    elif any(x in genre_lower for x in ["trap", "drill", "plugg"]):
        controls.creativity *= 1.05      # Some variation for hi-hats
        controls.density *= 1.2          # Dense hi-hat patterns
        controls.groove *= 1.1           # Rhythmic complexity
        
    # Classic boom bap → moderate everything, groove emphasis
    elif any(x in genre_lower for x in ["boom", "bap", "hip", "hop"]):
        controls.creativity *= 0.95      # Relatively stable
        controls.groove *= 1.25          # Strong swing/feel
        controls.density *= 0.95         # Not too dense
        
    # Lo-fi/chill → warm, simple, groovy
    elif any(x in genre_lower for x in ["lofi", "lo-fi", "chill", "ambient"]):
        controls.creativity *= 0.9
        controls.complexity *= 0.8       # Simpler
        controls.groove *= 1.3           # Heavy swing
        controls.brightness *= 0.85      # Darker, warmer
        
    return controls


def apply_tone_vector(
    controls: GenerationControlVector,
    brightness: float,  # -1 to 1
    warmth: float,      # -1 to 1
) -> GenerationControlVector:
    """
    Apply tonal characteristics to controls.
    
    Brightness affects creativity and note range.
    Warmth affects complexity and groove.
    """
    # Brightness: brighter = more creative, higher range
    # Map -1..1 to multiplier
    brightness_factor = 1.0 + brightness * 0.15  # ±15%
    controls.creativity *= brightness_factor
    controls.brightness = normalize_signed(brightness)
    
    # Warmth: warmer = more groove, less harsh complexity
    warmth_factor = 1.0 + warmth * 0.1
    controls.groove *= warmth_factor
    if warmth < 0:  # Cold = more sparse
        controls.density *= (1.0 + warmth * 0.15)  # Up to -15%
    
    return controls


def apply_energy_vector(
    controls: GenerationControlVector,
    intensity: float,   # -1 to 1
    excitement: float,  # -1 to 1
) -> GenerationControlVector:
    """
    Apply energy characteristics to controls.
    
    Intensity affects density and tension.
    Excitement affects creativity and groove.
    """
    # Intensity: high intensity = more dense, more tense
    intensity_normalized = normalize_signed(intensity)
    controls.density *= (0.8 + intensity_normalized * 0.4)  # 0.8x to 1.2x
    controls.tension = intensity_normalized
    
    # Excitement: high excitement = more variation, more groove
    excitement_normalized = normalize_signed(excitement)
    controls.creativity *= (0.9 + excitement_normalized * 0.2)
    controls.groove *= (0.9 + excitement_normalized * 0.2)
    
    return controls


def apply_complexity(controls: GenerationControlVector, complexity: float) -> GenerationControlVector:
    """
    Apply complexity adjustments (0-1).
    
    Affects creativity, density, and structural decisions.
    """
    controls.complexity = complexity
    
    # More complex = more creative, denser, more variation
    controls.creativity *= (0.85 + complexity * 0.3)  # 0.85x to 1.15x
    controls.density *= (0.9 + complexity * 0.2)      # 0.9x to 1.1x
    
    return controls


def apply_musical_goals(controls: GenerationControlVector, goals: List[str]) -> GenerationControlVector:
    """
    Apply musical goal modifiers.
    
    These are product differentiation - "club_ready", "cinematic", etc.
    """
    goals_lower = [g.lower() for g in goals]
    
    # Dark → less bright, more tense
    if any(x in goals_lower for x in ["dark", "moody", "ominous"]):
        controls.brightness *= 0.7
        controls.tension *= 1.3
        controls.creativity *= 1.05  # Slightly more variation
        
    # Bright → more brightness, less tension
    if any(x in goals_lower for x in ["bright", "happy", "uplifting"]):
        controls.brightness *= 1.3
        controls.tension *= 0.7
        
    # Energetic → high density, high creativity
    if any(x in goals_lower for x in ["energetic", "intense", "aggressive"]):
        controls.density *= 1.2
        controls.creativity *= 1.1
        controls.tension *= 1.2
        
    # Calm/chill → low density, low tension
    if any(x in goals_lower for x in ["calm", "chill", "relaxed", "peaceful"]):
        controls.density *= 0.8
        controls.tension *= 0.6
        controls.creativity *= 0.9
        
    # Minimal → low complexity, low density
    if "minimal" in goals_lower:
        controls.complexity *= 0.7
        controls.density *= 0.75
        
    # Dense/maximal → high everything
    if any(x in goals_lower for x in ["dense", "maximal", "thick", "full"]):
        controls.density *= 1.3
        controls.complexity *= 1.2
        
    # Cinematic → high creativity, moderate density, builds
    if "cinematic" in goals_lower:
        controls.creativity *= 1.15
        controls.build_intensity = True
        controls.loopable = False
        
    # Club ready → tight, loopable, punchy
    if any(x in goals_lower for x in ["club", "dancefloor", "party"]):
        controls.groove *= 1.2
        controls.loopable = True
        controls.density *= 1.1
        
    return controls


def apply_tempo_adjustments(controls: GenerationControlVector, tempo: int) -> GenerationControlVector:
    """
    Adjust controls based on tempo.
    
    Faster tempos need simpler patterns (cognitive load).
    Slower tempos can be more complex.
    """
    if tempo < 80:
        # Slow tempo: can be more complex
        controls.complexity *= 1.1
    elif tempo > 140:
        # Fast tempo: keep it simpler
        controls.complexity *= 0.9
        controls.density *= 0.95  # Don't oversaturate
        
    return controls


# =============================================================================
# Control Vector → Orpheus Params (Control → Generator)
# =============================================================================

# Parameter ranges for Orpheus
ORPHEUS_RANGES = {
    "temperature": (0.70, 1.10),
    "top_p": (0.90, 0.99),
    "tokens_per_bar": (32, 96),
    "num_prime_tokens": (512, 4096),
}

# Orpheus Music Transformer context window (prime + gen tokens)
_CONTEXT_WINDOW = 8192
_MAX_GEN_TOKENS = int(os.environ.get("ORPHEUS_MAX_GEN_TOKENS", "4096"))
_MIN_PRIME_TOKENS = 256


def allocate_token_budget(
    bars: int,
    tokens_per_bar: int,
    prime_from_policy: int,
    *,
    context_window: int = _CONTEXT_WINDOW,
    max_gen: int = _MAX_GEN_TOKENS,
    min_prime: int = _MIN_PRIME_TOKENS,
) -> Tuple[int, int]:
    """
    Split the model's context window between prime and generation tokens.

    Short sections get generous priming (better musical coherence).
    Long sections shift budget toward generation while keeping a useful prime floor.

    Returns:
        (num_prime_tokens, num_gen_tokens)
    """
    raw_gen = bars * tokens_per_bar
    gen = min(raw_gen, max_gen)

    remaining = context_window - gen
    prime = max(min_prime, min(prime_from_policy, remaining))

    # If gen + prime still exceeds the window, trim gen to fit
    if gen + prime > context_window:
        gen = context_window - prime

    return (prime, gen)


def controls_to_orpheus_params(controls: GenerationControlVector) -> dict:
    """
    Convert abstract control vector to concrete Orpheus parameters.
    
    This is the adapter layer - if we switch backends, we write a new adapter,
    not a new policy.
    """
    # Temperature: maps creativity → exploration
    temperature = lerp(
        ORPHEUS_RANGES["temperature"][0],
        ORPHEUS_RANGES["temperature"][1],
        controls.creativity
    )
    
    # Top-p: higher creativity = higher top_p (more diverse sampling)
    top_p = lerp(
        ORPHEUS_RANGES["top_p"][0],
        ORPHEUS_RANGES["top_p"][1],
        controls.creativity * 0.8 + 0.2  # Bias toward higher values
    )
    
    # Tokens per bar: maps density + complexity
    token_factor = (controls.density * 0.6 + controls.complexity * 0.4)
    tokens_per_bar = int(lerp(
        ORPHEUS_RANGES["tokens_per_bar"][0],
        ORPHEUS_RANGES["tokens_per_bar"][1],
        token_factor
    ))
    
    # Prime tokens: more complex = more context needed
    num_prime_tokens = int(lerp(
        ORPHEUS_RANGES["num_prime_tokens"][0],
        ORPHEUS_RANGES["num_prime_tokens"][1],
        controls.complexity
    ))
    
    # Apply quality preset modifiers
    if controls.quality_preset == "fast":
        tokens_per_bar = int(tokens_per_bar * 0.85)
        num_prime_tokens = int(num_prime_tokens * 0.75)
    elif controls.quality_preset == "quality":
        tokens_per_bar = int(tokens_per_bar * 1.15)
        num_prime_tokens = int(num_prime_tokens * 1.0)  # Already balanced
        
    return {
        "model_temperature": round(temperature, 3),
        "model_top_p": round(top_p, 3),
        "num_gen_tokens_per_bar": tokens_per_bar,
        "num_prime_tokens": num_prime_tokens,
        "velocity_variation": controls.groove * 0.3,  # 0-30% variation
        "seed_brightness_hint": controls.brightness,
        "seed_tension_hint": controls.tension,
    }


# =============================================================================
# Utilities
# =============================================================================

def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation from a to b by factor t (0-1)."""
    return a + (b - a) * t


def normalize_signed(value: float) -> float:
    """Convert -1..1 to 0..1."""
    return (value + 1.0) / 2.0


def denormalize_signed(value: float) -> float:
    """Convert 0..1 to -1..1."""
    return value * 2.0 - 1.0


# =============================================================================
# Policy Versioning (for A/B testing)
# =============================================================================

POLICY_VERSION = "v1.1"

def get_policy_version() -> str:
    """Return current policy version for logging/analytics."""
    return POLICY_VERSION


# =============================================================================
# Example Usage & Testing
# =============================================================================

if __name__ == "__main__":
    # Example 1: Dark energetic trap
    controls = intent_to_controls(
        genre="trap",
        tempo=140,
        musical_goals=["dark", "energetic"],
        tone_brightness=-0.7,
        energy_intensity=0.8,
        complexity_hint=0.6,
    )
    
    params = controls_to_orpheus_params(controls)
    print("Dark energetic trap:")
    print(f"  Controls: {controls}")
    print(f"  Orpheus params: {params}")
    print()
    
    # Example 2: Bright chill lo-fi
    controls = intent_to_controls(
        genre="lofi",
        tempo=85,
        musical_goals=["bright", "chill"],
        tone_brightness=0.5,
        tone_warmth=0.7,
        energy_intensity=-0.4,
        complexity_hint=0.3,
    )
    
    params = controls_to_orpheus_params(controls)
    print("Bright chill lo-fi:")
    print(f"  Controls: {controls}")
    print(f"  Orpheus params: {params}")
