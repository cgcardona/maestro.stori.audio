"""
Tests for generation policy layer.

The policy layer is the "soul" of Stori - it translates musical intent
into generation parameters. These tests ensure the mapping is correct.
"""
import pytest
from generation_policy import (
    intent_to_controls,
    allocate_token_budget,
    apply_genre_baseline,
    apply_tone_vector,
    apply_energy_vector,
    apply_musical_goals,
    GenerationControlVector,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    _MAX_PRIME_TOKENS,
    _MAX_GEN_TOKENS,
    _TOKENS_PER_BAR,
)


def test_dark_energetic_trap():
    """Test dark energetic trap produces expected control vector."""
    controls = intent_to_controls(
        genre="trap",
        tempo=140,
        musical_goals=["dark", "energetic"],
        tone_brightness=-0.7,
        energy_intensity=0.8,
    )
    
    # Dark → low brightness, high tension
    assert controls.brightness < 0.3
    assert controls.tension > 0.7
    
    # Energetic → high density, high creativity
    assert controls.density > 0.7
    assert controls.creativity > 0.5
    
    # Trap → moderate complexity
    assert 0.4 < controls.complexity < 0.7


def test_bright_chill_lofi():
    """Test bright chill lo-fi produces expected control vector."""
    controls = intent_to_controls(
        genre="lofi",
        tempo=85,
        musical_goals=["bright", "chill"],
        tone_brightness=0.6,
        energy_intensity=-0.4,
    )
    
    # Bright → high brightness
    assert controls.brightness > 0.7
    
    # Chill → low density, low tension
    assert controls.density < 0.5
    assert controls.tension < 0.4
    
    # Lo-fi → high groove
    assert controls.groove > 0.6


def test_minimal_techno():
    """Test minimal techno produces low creativity."""
    controls = intent_to_controls(
        genre="techno",
        tempo=128,
        musical_goals=["minimal"],
    )
    
    # Techno + minimal → very low creativity, low complexity
    assert controls.creativity < 0.5
    assert controls.complexity < 0.6
    assert controls.loopable is True


def test_complex_jazz():
    """Test jazz produces high creativity and complexity."""
    controls = intent_to_controls(
        genre="jazz",
        tempo=110,
        complexity_hint=0.8,
    )
    
    # Jazz → elevated creativity and complexity
    assert controls.creativity > 0.6
    assert controls.complexity > 0.7


def test_genre_baseline_techno():
    """Test techno reduces creativity."""
    controls = GenerationControlVector()
    result = apply_genre_baseline(controls, "techno")
    
    # Techno → repetitive
    assert result.creativity < 0.5
    assert result.loopable is True


def test_genre_baseline_jazz():
    """Test jazz increases creativity and complexity."""
    controls = GenerationControlVector()
    result = apply_genre_baseline(controls, "jazz")
    
    # Jazz → creative and complex
    assert result.creativity > 0.5
    assert result.complexity > 0.5
    assert result.loopable is False


def test_tone_vector_brightness():
    """Test tone brightness affects creativity."""
    controls = GenerationControlVector(creativity=0.5)
    
    # Bright
    result = apply_tone_vector(controls, brightness=0.8, warmth=0.0)
    assert result.creativity > 0.5
    assert result.brightness > 0.7
    
    # Dark
    controls2 = GenerationControlVector(creativity=0.5)
    result2 = apply_tone_vector(controls2, brightness=-0.8, warmth=0.0)
    assert result2.creativity < 0.5
    assert result2.brightness < 0.3


def test_energy_vector_intensity():
    """Test energy intensity affects density and tension."""
    controls = GenerationControlVector(density=0.5, tension=0.5)
    result = apply_energy_vector(controls, intensity=0.8, excitement=0.0)
    
    # High intensity → high density, high tension
    assert result.density > 0.5
    assert result.tension > 0.7


def test_musical_goals_dark():
    """Test 'dark' goal reduces brightness and increases tension."""
    controls = GenerationControlVector()
    result = apply_musical_goals(controls, ["dark"])
    
    assert result.brightness < 0.5  # Should be reduced from default
    assert result.tension > 0.5     # Should be increased from default


def test_musical_goals_energetic():
    """Test 'energetic' goal increases density."""
    controls = GenerationControlVector()
    result = apply_musical_goals(controls, ["energetic"])
    
    assert result.density > 0.5
    assert result.creativity > 0.5


def test_musical_goals_minimal():
    """Test 'minimal' goal reduces complexity and density."""
    controls = GenerationControlVector()
    result = apply_musical_goals(controls, ["minimal"])
    
    assert result.complexity < 1.0
    assert result.density < 1.0


def test_musical_goals_cinematic():
    """Test 'cinematic' goal enables build."""
    controls = GenerationControlVector()
    result = apply_musical_goals(controls, ["cinematic"])
    
    assert result.build_intensity is True
    assert result.loopable is False
    assert result.creativity > 0.5


def test_controls_clamp():
    """Test control values are clamped to 0-1."""
    controls = GenerationControlVector(
        creativity=1.5,   # Over limit
        density=-0.2,     # Under limit
        complexity=0.5
    )
    
    controls.clamp()
    
    assert 0.0 <= controls.creativity <= 1.0
    assert 0.0 <= controls.density <= 1.0
    assert 0.0 <= controls.complexity <= 1.0


def test_compound_genres():
    """Test compound genre handling (e.g., 'dark trap')."""
    controls = intent_to_controls(
        genre="trap",  # Base genre
        tempo=140,
        musical_goals=["dark", "minimal"],  # Modifiers
        tone_brightness=-0.6,
    )
    
    # Should combine trap baseline + dark + minimal modifiers
    assert controls.brightness < 0.4  # Dark effect
    assert controls.complexity < 0.6  # Minimal effect
    assert controls.density > 0.4     # Trap baseline, reduced by minimal


def test_policy_is_deterministic():
    """Test that same inputs always produce same outputs."""
    controls1 = intent_to_controls(
        genre="trap",
        tempo=140,
        musical_goals=["dark"],
        tone_brightness=-0.7,
        energy_intensity=0.8,
    )
    
    controls2 = intent_to_controls(
        genre="trap",
        tempo=140,
        musical_goals=["dark"],
        tone_brightness=-0.7,
        energy_intensity=0.8,
    )
    
    # Should produce identical results
    assert controls1.creativity == controls2.creativity
    assert controls1.density == controls2.density
    assert controls1.complexity == controls2.complexity
    assert controls1.brightness == controls2.brightness
    assert controls1.tension == controls2.tension


def test_default_temperature_and_top_p():
    """Simplified policy uses HF-proven defaults."""
    assert DEFAULT_TEMPERATURE == 0.9
    assert DEFAULT_TOP_P == 0.96


# =============================================================================
# Token Budget Allocator Tests
# =============================================================================


class TestAllocateTokenBudget:
    """Tests for the simplified token budget allocator.

    allocate_token_budget(bars) returns (prime, gen) where prime is always
    _MAX_PRIME_TOKENS and gen = min(bars * _TOKENS_PER_BAR, _MAX_GEN_TOKENS).
    """

    def test_short_section_gets_max_prime(self):
        """2-bar section should get maximum prime tokens."""
        prime, gen = allocate_token_budget(bars=2)
        assert prime == _MAX_PRIME_TOKENS
        assert gen == 2 * _TOKENS_PER_BAR

    def test_medium_section(self):
        """4-bar section at default density."""
        prime, gen = allocate_token_budget(bars=4)
        assert prime == _MAX_PRIME_TOKENS
        assert gen == 4 * _TOKENS_PER_BAR

    def test_8_bar_section(self):
        """8-bar section — common loop length."""
        prime, gen = allocate_token_budget(bars=8)
        assert prime == _MAX_PRIME_TOKENS
        assert gen == 8 * _TOKENS_PER_BAR

    def test_gen_capped_at_max(self):
        """Very long sections cap generation tokens."""
        prime, gen = allocate_token_budget(bars=100)
        assert gen == _MAX_GEN_TOKENS
        assert prime == _MAX_PRIME_TOKENS

    def test_single_bar(self):
        """1-bar generation leaves nearly all budget for priming."""
        prime, gen = allocate_token_budget(bars=1)
        assert gen == _TOKENS_PER_BAR
        assert prime == _MAX_PRIME_TOKENS

    def test_gen_never_exceeds_max(self):
        """Sweep across bar counts — gen never exceeds _MAX_GEN_TOKENS."""
        for bars in [1, 2, 4, 8, 16, 24, 32, 48, 64, 128]:
            prime, gen = allocate_token_budget(bars=bars)
            assert gen <= _MAX_GEN_TOKENS, (
                f"bars={bars}: gen={gen} > {_MAX_GEN_TOKENS}"
            )
            assert prime == _MAX_PRIME_TOKENS
            assert gen > 0
