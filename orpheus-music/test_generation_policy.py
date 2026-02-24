"""
Tests for generation policy layer.

The policy layer is the "soul" of Stori - it translates musical intent
into generation parameters. These tests ensure the mapping is correct.
"""
import pytest
from generation_policy import (
    intent_to_controls,
    controls_to_orpheus_params,
    allocate_token_budget,
    apply_genre_baseline,
    apply_tone_vector,
    apply_energy_vector,
    apply_musical_goals,
    GenerationControlVector,
    ORPHEUS_RANGES,
    _CONTEXT_WINDOW,
    _MIN_PRIME_TOKENS,
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


def test_controls_to_orpheus_params():
    """Test control vector converts to valid Orpheus parameters."""
    controls = GenerationControlVector(
        creativity=0.6,
        density=0.7,
        complexity=0.5,
        quality_preset="balanced"
    )
    
    params = controls_to_orpheus_params(controls)
    
    tpb_lo, tpb_hi = ORPHEUS_RANGES["tokens_per_bar"]
    prime_lo, prime_hi = ORPHEUS_RANGES["num_prime_tokens"]

    assert 0.7 <= params["model_temperature"] <= 1.1
    assert 0.9 <= params["model_top_p"] <= 0.99
    assert tpb_lo <= params["num_gen_tokens_per_bar"] <= tpb_hi
    assert prime_lo <= params["num_prime_tokens"] <= prime_hi
    assert 0.0 <= params["velocity_variation"] <= 0.3


def test_fast_quality_preset():
    """Test fast preset reduces token counts relative to balanced."""
    base_controls = GenerationControlVector(
        creativity=0.5, density=0.5, complexity=0.5, quality_preset="balanced"
    )
    fast_controls = GenerationControlVector(
        creativity=0.5, density=0.5, complexity=0.5, quality_preset="fast"
    )

    base_params = controls_to_orpheus_params(base_controls)
    fast_params = controls_to_orpheus_params(fast_controls)

    assert fast_params["num_gen_tokens_per_bar"] < base_params["num_gen_tokens_per_bar"]
    assert fast_params["num_prime_tokens"] < base_params["num_prime_tokens"]


def test_quality_preset():
    """Test quality preset increases token density relative to balanced."""
    base_controls = GenerationControlVector(
        creativity=0.5, density=0.5, complexity=0.5, quality_preset="balanced"
    )
    quality_controls = GenerationControlVector(
        creativity=0.5, density=0.5, complexity=0.5, quality_preset="quality"
    )

    base_params = controls_to_orpheus_params(base_controls)
    quality_params = controls_to_orpheus_params(quality_controls)

    assert quality_params["num_gen_tokens_per_bar"] >= base_params["num_gen_tokens_per_bar"]


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


def test_orpheus_params_valid_ranges():
    """Test Orpheus params stay within valid ranges for all control values."""
    tpb_lo, tpb_hi = ORPHEUS_RANGES["tokens_per_bar"]
    prime_lo, prime_hi = ORPHEUS_RANGES["num_prime_tokens"]

    for creativity in [0.0, 0.5, 1.0]:
        for density in [0.0, 0.5, 1.0]:
            for complexity in [0.0, 0.5, 1.0]:
                controls = GenerationControlVector(
                    creativity=creativity,
                    density=density,
                    complexity=complexity,
                )

                params = controls_to_orpheus_params(controls)

                assert 0.7 <= params["model_temperature"] <= 1.1
                assert 0.9 <= params["model_top_p"] <= 0.99
                # Allow a small margin below tpb_lo for "fast" quality preset
                # (balanced preset stays within nominal range)
                assert params["num_gen_tokens_per_bar"] >= tpb_lo * 0.8
                assert params["num_gen_tokens_per_bar"] <= tpb_hi * 1.2
                assert params["num_prime_tokens"] >= prime_lo * 0.7
                assert params["num_prime_tokens"] <= prime_hi * 1.1


# =============================================================================
# Token Budget Allocator Tests
# =============================================================================


class TestAllocateTokenBudget:
    """Tests for the 8k context window budget allocator."""

    def test_short_section_gets_generous_prime(self):
        """2-bar section should get maximum prime tokens."""
        prime, gen = allocate_token_budget(
            bars=2, tokens_per_bar=64, prime_from_policy=4096
        )
        assert prime == 4096
        assert gen == 128  # 2 * 64
        assert prime + gen <= _CONTEXT_WINDOW

    def test_medium_section_balanced(self):
        """4-bar section at mid density fits comfortably."""
        prime, gen = allocate_token_budget(
            bars=4, tokens_per_bar=64, prime_from_policy=4096
        )
        assert prime == 4096
        assert gen == 256  # 4 * 64
        assert prime + gen <= _CONTEXT_WINDOW

    def test_long_section_trims_prime_to_fit(self):
        """48-bar section at high density needs prime trimmed."""
        prime, gen = allocate_token_budget(
            bars=48, tokens_per_bar=96, prime_from_policy=4096
        )
        # 48 * 96 = 4608, capped at max_gen=4096
        assert gen == 4096
        # prime = min(4096, 8192 - 4096) = 4096
        assert prime == 4096
        assert prime + gen == _CONTEXT_WINDOW

    def test_gen_capped_at_max(self):
        """Generation tokens should never exceed ORPHEUS_MAX_GEN_TOKENS."""
        prime, gen = allocate_token_budget(
            bars=100, tokens_per_bar=96, prime_from_policy=2048, max_gen=4096
        )
        assert gen == 4096
        assert prime + gen <= _CONTEXT_WINDOW

    def test_prime_never_below_minimum(self):
        """Even at maximum gen, prime stays above the floor."""
        prime, gen = allocate_token_budget(
            bars=200, tokens_per_bar=96, prime_from_policy=4096, max_gen=8000
        )
        assert prime >= _MIN_PRIME_TOKENS
        assert prime + gen <= _CONTEXT_WINDOW

    def test_total_never_exceeds_context_window(self):
        """Sweep across bar counts — total always fits in 8192."""
        for bars in [1, 2, 4, 8, 16, 24, 32, 48, 64]:
            for tpb in [32, 64, 96]:
                prime, gen = allocate_token_budget(
                    bars=bars, tokens_per_bar=tpb, prime_from_policy=4096
                )
                assert prime + gen <= _CONTEXT_WINDOW, (
                    f"bars={bars}, tpb={tpb}: prime={prime} + gen={gen} "
                    f"= {prime + gen} > {_CONTEXT_WINDOW}"
                )
                assert prime >= _MIN_PRIME_TOKENS
                assert gen > 0

    def test_custom_context_window(self):
        """Allocator respects a custom context window size."""
        prime, gen = allocate_token_budget(
            bars=4, tokens_per_bar=64, prime_from_policy=2048,
            context_window=4096, max_gen=2048,
        )
        assert prime + gen <= 4096

    def test_single_bar_maximises_prime(self):
        """1-bar generation should leave nearly all budget for priming."""
        prime, gen = allocate_token_budget(
            bars=1, tokens_per_bar=64, prime_from_policy=4096
        )
        assert gen == 64
        assert prime == 4096
        assert prime + gen <= _CONTEXT_WINDOW
