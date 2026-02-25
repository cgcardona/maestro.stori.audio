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
    controls = GenerationControlVector(quality_preset=quality_preset)  # type: ignore[arg-type]  # validated by caller
    
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
# Canonical control builder — consumes structured Maestro intent directly
# =============================================================================


def build_controls(
    *,
    genre: str,
    tempo: int,
    emotion_vector: Optional[dict] = None,
    role_profile_summary: Optional[dict] = None,
    generation_constraints: Optional[dict] = None,
    intent_goals: Optional[List[dict]] = None,
    quality_preset: str = "balanced",
) -> GenerationControlVector:
    """Build a ``GenerationControlVector`` from the canonical Maestro payload.

    Consumes structured blocks (emotion_vector, role_profile_summary,
    generation_constraints) computed by Maestro and only fills gaps when
    a block is absent.

    Fallback order:
        1. generation_constraints (hard controls — authoritative)
        2. emotion_vector (continuous axes — used to derive missing controls)
        3. role_profile_summary (data-driven priors)
        4. genre/tempo heuristic baseline (last resort)
    """
    ev = emotion_vector or {}
    rp = role_profile_summary or {}
    gc = generation_constraints or {}
    goals = intent_goals or []

    controls = GenerationControlVector(quality_preset=quality_preset)  # type: ignore[arg-type]

    # ── Tension: from emotion_vector (Maestro is authoritative) ──
    if "tension" in ev:
        controls.tension = ev["tension"]

    # ── Density: from generation_constraints first, else emotion-derived ──
    if "drum_density" in gc:
        controls.density = gc["drum_density"]
    elif "energy" in ev and "motion" in ev:
        controls.density = ev["energy"] * ev["motion"]
    elif "rest_ratio" in rp:
        controls.density = max(0.0, 1.0 - rp["rest_ratio"])

    # ── Complexity: from role profile first ──
    if "contour_complexity" in rp:
        controls.complexity = rp["contour_complexity"]
    elif gc:
        controls.complexity = 0.5  # neutral when constraints drive everything

    # ── Brightness: from emotion valence ──
    if "valence" in ev:
        controls.brightness = normalize_signed(ev["valence"])

    # ── Groove: from role profile swing ──
    if "swing_ratio" in rp:
        controls.groove = rp["swing_ratio"]
    elif "swing_amount" in gc:
        controls.groove = min(1.0, gc["swing_amount"] * 4.0)  # 0-0.25 → 0-1

    # ── Creativity: emotion-derived ──
    if "motion" in ev:
        controls.creativity = normalize_signed(ev.get("motion", 0.5) * 2 - 1)

    # ── Apply weighted goal modifiers ──
    for goal in goals:
        name = goal.get("name", "") if isinstance(goal, dict) else str(goal)
        weight = goal.get("weight", 1.0) if isinstance(goal, dict) else 1.0
        name_lower = name.lower()

        if name_lower in ("dark", "moody", "ominous"):
            controls.brightness *= max(0.5, 1.0 - 0.3 * weight)
            controls.tension *= min(1.5, 1.0 + 0.3 * weight)
        elif name_lower in ("bright", "happy", "uplifting"):
            controls.brightness *= min(1.5, 1.0 + 0.3 * weight)
            controls.tension *= max(0.5, 1.0 - 0.3 * weight)
        elif name_lower in ("energetic", "intense", "aggressive"):
            controls.density *= min(1.5, 1.0 + 0.2 * weight)
            controls.tension *= min(1.5, 1.0 + 0.2 * weight)
        elif name_lower in ("calm", "chill", "relaxed", "peaceful"):
            controls.density *= max(0.5, 1.0 - 0.2 * weight)
            controls.tension *= max(0.4, 1.0 - 0.4 * weight)
        elif name_lower == "minimal":
            controls.complexity *= max(0.5, 1.0 - 0.3 * weight)
            controls.density *= max(0.5, 1.0 - 0.25 * weight)
        elif name_lower in ("dense", "maximal", "thick", "full"):
            controls.density *= min(1.5, 1.0 + 0.3 * weight)
        elif name_lower == "cinematic":
            controls.build_intensity = True
            controls.loopable = False
        elif name_lower in ("tense",):
            controls.tension *= min(1.5, 1.0 + 0.3 * weight)
        elif name_lower in ("driving",):
            controls.groove *= min(1.5, 1.0 + 0.2 * weight)
        elif name_lower in ("sustained",):
            controls.density *= max(0.6, 1.0 - 0.15 * weight)
        elif name_lower in ("syncopated",):
            controls.groove *= min(1.5, 1.0 + 0.25 * weight)

    # Only apply genre/tempo as a final light adjustment, not a full derivation
    controls = apply_genre_baseline(controls, genre)
    controls = apply_tempo_adjustments(controls, tempo)
    controls.clamp()
    return controls


def build_fulfillment_report(
    notes: list,
    bars: int,
    controls: GenerationControlVector,
    generation_constraints: Optional[dict] = None,
) -> dict:
    """Compute a fulfillment report comparing output against intent.

    Uses the existing ``rejection_score`` infrastructure and adds
    constraint violation detection.
    """
    gc = generation_constraints or {}
    violations: list[str] = []
    goal_scores: dict[str, float] = {}

    if not notes:
        return {
            "goal_scores": {},
            "constraint_violations": ["no_notes_generated"],
            "coverage_pct": 0.0,
        }

    pitches = [n.get("pitch", 60) for n in notes]
    velocities = [n.get("velocity", 80) for n in notes]

    # Pitch range constraint check
    if "register_center" in gc and "register_spread" in gc:
        center = gc["register_center"]
        spread = gc["register_spread"]
        low, high = center - spread, center + spread
        out_of_range = sum(1 for p in pitches if p < low or p > high)
        pct = out_of_range / len(pitches) if pitches else 0
        goal_scores["register_compliance"] = round(1.0 - pct, 3)
        if pct > 0.3:
            violations.append(f"register_violation: {pct:.0%} notes outside [{low}, {high}]")

    # Velocity range constraint check
    if "velocity_floor" in gc and "velocity_ceiling" in gc:
        floor_v, ceil_v = gc["velocity_floor"], gc["velocity_ceiling"]
        out_vel = sum(1 for v in velocities if v < floor_v or v > ceil_v)
        pct_v = out_vel / len(velocities) if velocities else 0
        goal_scores["velocity_compliance"] = round(1.0 - pct_v, 3)
        if pct_v > 0.3:
            violations.append(f"velocity_violation: {pct_v:.0%} notes outside [{floor_v}, {ceil_v}]")

    # Density check
    density_score = len(notes) / max(bars * 4, 1)
    goal_scores["density"] = round(min(1.0, density_score / 4.0), 3)

    # Overall coverage: how many constraint fields were satisfiable
    total_checks = max(len(goal_scores), 1)
    passing = sum(1 for s in goal_scores.values() if s >= 0.7)
    coverage_pct = round(passing / total_checks * 100, 1)

    return {
        "goal_scores": goal_scores,
        "constraint_violations": violations,
        "coverage_pct": coverage_pct,
    }


# =============================================================================
# Control Vector → Orpheus Params (Control → Generator)
# =============================================================================

# Orpheus Music Transformer proven-good defaults (from HF Space).
# The model produces best results at these values; only deviate on
# explicit user override.
DEFAULT_TEMPERATURE = 0.9
DEFAULT_TOP_P = 0.96

# HF Space UI caps gen tokens at 1024; prime at 6656.
_MAX_PRIME_TOKENS = 6656
_MAX_GEN_TOKENS = 1024
_TOKENS_PER_BAR = 64


def allocate_token_budget(bars: int) -> Tuple[int, int]:
    """Return (num_prime_tokens, num_gen_tokens) for a generation request.

    Strategy: maximise prime context (the model is a continuation engine),
    keep gen tokens within the HF Space's proven range.
    """
    gen = min(bars * _TOKENS_PER_BAR, _MAX_GEN_TOKENS)
    prime = _MAX_PRIME_TOKENS
    return (prime, gen)


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
# Policy Identity (for A/B testing and analytics)
# =============================================================================

POLICY_VERSION = "canonical-1.0"

def get_policy_version() -> str:
    """Return current policy version for logging/analytics."""
    return POLICY_VERSION


# =============================================================================
# Example Usage & Testing
# =============================================================================

if __name__ == "__main__":
    prime, gen = allocate_token_budget(bars=8)
    print(f"8-bar budget: prime={prime}, gen={gen}")
    prime, gen = allocate_token_budget(bars=16)
    print(f"16-bar budget: prime={prime}, gen={gen}")
    prime, gen = allocate_token_budget(bars=32)
    print(f"32-bar budget: prime={prime}, gen={gen}")
    print(f"Defaults: temp={DEFAULT_TEMPERATURE}, top_p={DEFAULT_TOP_P}")
    print(f"Policy: {get_policy_version()}")
