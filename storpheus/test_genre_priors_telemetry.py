"""Tests for per-genre parameter priors and generation telemetry.

Covers:
- ``get_genre_prior`` fuzzy matching and prior values
- ``apply_controls_to_params`` with and without genre priors
- ``GenerationTelemetryRecord`` field coverage
- ``SweepABTestResult`` / ``ParameterSweepResult`` structure
"""
from __future__ import annotations

import pytest

from generation_policy import (
    GenerationControlVector,
    apply_controls_to_params,
    get_genre_prior,
    _GENRE_PRIORS,
    _TEMP_MIN,
    _TEMP_MAX,
    _TOP_P_MIN,
    _TOP_P_MAX,
)
from storpheus_types import (
    GenerationTelemetryRecord,
    GenreParameterPrior,
    ParameterSweepResult,
    StorpheusNoteDict,
    SweepABTestResult,
)


# =============================================================================
# Helpers
# =============================================================================


def _note(pitch: int, start_beat: float, duration_beats: float, velocity: int) -> StorpheusNoteDict:
    return StorpheusNoteDict(
        pitch=pitch,
        start_beat=start_beat,
        duration_beats=duration_beats,
        velocity=velocity,
    )


def _controls(**kwargs: float) -> GenerationControlVector:
    return GenerationControlVector(**kwargs)  # type: ignore[arg-type]


# =============================================================================
# get_genre_prior — fuzzy matching
# =============================================================================


class TestGetGenrePrior:
    """``get_genre_prior`` should resolve genre strings to priors or None."""

    def test_exact_canonical_names_resolve(self) -> None:
        """Every canonical genre in _GENRE_PRIORS resolves."""
        for canonical in _GENRE_PRIORS:
            result = get_genre_prior(canonical)
            assert result is not None, f"Canonical genre '{canonical}' did not resolve"

    def test_jazz_resolves(self) -> None:
        prior = get_genre_prior("jazz")
        assert prior is not None
        assert prior.temperature >= 0.90

    def test_techno_resolves_lower_temp(self) -> None:
        prior = get_genre_prior("techno")
        jazz_prior = get_genre_prior("jazz")
        assert prior is not None
        assert jazz_prior is not None
        assert prior.temperature < jazz_prior.temperature

    def test_ambient_resolves_negative_density_offset(self) -> None:
        prior = get_genre_prior("ambient")
        assert prior is not None
        assert prior.density_offset < 0

    def test_trap_resolves_positive_density_offset(self) -> None:
        prior = get_genre_prior("trap")
        assert prior is not None
        assert prior.density_offset > 0

    def test_fuzzy_compound_genre_matches(self) -> None:
        """'dark minimal techno' should resolve to the techno prior."""
        prior = get_genre_prior("dark minimal techno")
        assert prior is not None
        techno_prior = get_genre_prior("techno")
        assert prior is techno_prior

    def test_case_insensitive(self) -> None:
        assert get_genre_prior("JAZZ") == get_genre_prior("jazz")
        assert get_genre_prior("Techno") == get_genre_prior("techno")

    def test_unknown_genre_returns_none(self) -> None:
        # "xylophone_baroque_polka" contains none of the alias tokens
        assert get_genre_prior("xylophone_baroque_polka") is None

    def test_lofi_aliases(self) -> None:
        """Both 'lofi' and 'lo-fi' and 'chill' should resolve."""
        assert get_genre_prior("lofi") is not None
        assert get_genre_prior("lo-fi") is not None
        assert get_genre_prior("chill vibes") is not None

    def test_boom_bap_aliases(self) -> None:
        assert get_genre_prior("boom bap") is not None
        assert get_genre_prior("hip hop") is not None

    def test_rnb_aliases(self) -> None:
        assert get_genre_prior("rnb") is not None
        assert get_genre_prior("r&b soul") is not None

    def test_prior_values_in_safe_range(self) -> None:
        """All priors must stay within the Orpheus safe parameter ranges."""
        for name, prior in _GENRE_PRIORS.items():
            assert 0.7 <= prior.temperature <= 1.0, (
                f"Genre '{name}': temperature {prior.temperature} outside safe range [0.7, 1.0]"
            )
            assert 0.90 <= prior.top_p <= 0.98, (
                f"Genre '{name}': top_p {prior.top_p} outside safe range [0.90, 0.98]"
            )
            assert -0.3 <= prior.density_offset <= 0.3, (
                f"Genre '{name}': density_offset {prior.density_offset} outside [-0.3, 0.3]"
            )
            assert 0.5 <= prior.prime_ratio <= 1.0, (
                f"Genre '{name}': prime_ratio {prior.prime_ratio} outside [0.5, 1.0]"
            )


# =============================================================================
# apply_controls_to_params with genre_prior
# =============================================================================


class TestApplyControlsWithPrior:
    """Genre priors should override temperature/top_p derived from the control vector."""

    def test_no_prior_uses_control_vector(self) -> None:
        """Without a prior, temperature is derived from creativity."""
        controls = _controls(creativity=1.0, groove=1.0)
        params = apply_controls_to_params(controls, bars=4, genre_prior=None)
        # Max creativity → max temperature
        assert params["temperature"] == round(_TEMP_MAX, 3)
        assert params["top_p"] == round(_TOP_P_MAX, 3)

    def test_prior_overrides_temperature(self) -> None:
        """A prior with fixed temperature replaces the CV-derived value."""
        controls = _controls(creativity=1.0, groove=1.0)  # would produce high temp
        prior = GenreParameterPrior(temperature=0.78, top_p=0.92)
        params = apply_controls_to_params(controls, bars=4, genre_prior=prior)
        assert params["temperature"] == 0.78
        assert params["top_p"] == 0.92

    def test_prior_density_offset_affects_gen_tokens(self) -> None:
        """Negative density_offset reduces gen tokens vs. no offset."""
        controls = _controls(density=0.5)
        prior_neg = GenreParameterPrior(temperature=0.9, top_p=0.96, density_offset=-0.3)
        prior_none = GenreParameterPrior(temperature=0.9, top_p=0.96, density_offset=0.0)
        params_neg = apply_controls_to_params(controls, bars=4, genre_prior=prior_neg)
        params_none = apply_controls_to_params(controls, bars=4, genre_prior=prior_none)
        assert params_neg["num_gen_tokens"] <= params_none["num_gen_tokens"]

    def test_prior_prime_ratio_reduces_prime_tokens(self) -> None:
        """prime_ratio < 1.0 reduces the prime token budget."""
        controls = _controls(complexity=1.0)
        full = GenreParameterPrior(temperature=0.9, top_p=0.96, prime_ratio=1.0)
        reduced = GenreParameterPrior(temperature=0.9, top_p=0.96, prime_ratio=0.7)
        params_full = apply_controls_to_params(controls, bars=4, genre_prior=full)
        params_reduced = apply_controls_to_params(controls, bars=4, genre_prior=reduced)
        assert params_reduced["num_prime_tokens"] <= params_full["num_prime_tokens"]

    def test_density_clamped_after_offset(self) -> None:
        """Effective density is clamped to [0, 1] even with extreme offset."""
        controls = _controls(density=0.1)
        # Extreme negative offset would go below zero
        prior = GenreParameterPrior(temperature=0.9, top_p=0.96, density_offset=-0.5)
        params = apply_controls_to_params(controls, bars=4, genre_prior=prior)
        # Should not crash and gen tokens should be at floor
        assert params["num_gen_tokens"] >= 512

    def test_jazz_prior_gives_higher_temp_than_techno(self) -> None:
        """Jazz prior must be warmer than techno — validated by listening tests."""
        controls = _controls(creativity=0.5)
        jazz_prior = get_genre_prior("jazz")
        techno_prior = get_genre_prior("techno")
        assert jazz_prior is not None
        assert techno_prior is not None
        jazz_params = apply_controls_to_params(controls, bars=4, genre_prior=jazz_prior)
        techno_params = apply_controls_to_params(controls, bars=4, genre_prior=techno_prior)
        assert jazz_params["temperature"] > techno_params["temperature"]

    def test_ambient_prior_gives_fewer_gen_tokens_than_trap(self) -> None:
        """Ambient's negative density_offset produces fewer gen tokens than trap's positive."""
        controls = _controls(density=0.5, complexity=0.5)
        ambient_prior = get_genre_prior("ambient")
        trap_prior = get_genre_prior("trap")
        assert ambient_prior is not None
        assert trap_prior is not None
        ambient_params = apply_controls_to_params(controls, bars=4, genre_prior=ambient_prior)
        trap_params = apply_controls_to_params(controls, bars=4, genre_prior=trap_prior)
        assert ambient_params["num_gen_tokens"] <= trap_params["num_gen_tokens"]


# =============================================================================
# GenreParameterPrior dataclass
# =============================================================================


class TestGenreParameterPrior:
    """GenreParameterPrior has sensible defaults and is typed correctly."""

    def test_defaults(self) -> None:
        prior = GenreParameterPrior(temperature=0.9, top_p=0.96)
        assert prior.density_offset == 0.0
        assert prior.prime_ratio == 1.0

    def test_all_fields_assignable(self) -> None:
        prior = GenreParameterPrior(
            temperature=0.85,
            top_p=0.94,
            density_offset=-0.1,
            prime_ratio=0.8,
        )
        assert prior.temperature == 0.85
        assert prior.top_p == 0.94
        assert prior.density_offset == -0.1
        assert prior.prime_ratio == 0.8


# =============================================================================
# GenerationTelemetryRecord structure
# =============================================================================


class TestGenerationTelemetryRecord:
    """GenerationTelemetryRecord can be constructed with required fields."""

    def test_minimal_required_fields(self) -> None:
        record: GenerationTelemetryRecord = {
            "genre": "jazz",
            "tempo": 120,
            "bars": 4,
        }
        assert record["genre"] == "jazz"
        assert record["tempo"] == 120
        assert record["bars"] == 4

    def test_full_record(self) -> None:
        record: GenerationTelemetryRecord = {
            "genre": "techno",
            "tempo": 128,
            "bars": 8,
            "instruments": ["drums", "bass"],
            "quality_preset": "balanced",
            "temperature": 0.78,
            "top_p": 0.92,
            "num_prime_tokens": 5000,
            "num_gen_tokens": 768,
            "genre_prior_applied": True,
            "note_count": 64,
            "pitch_range": 24,
            "velocity_variation": 0.12,
            "quality_score": 0.75,
            "rejection_score": 0.82,
            "candidate_count": 3,
            "generation_ok": True,
        }
        assert record["genre_prior_applied"] is True
        assert record["quality_score"] == 0.75
        assert record["generation_ok"] is True


# =============================================================================
# SweepABTestResult / ParameterSweepResult structure
# =============================================================================


class TestSweepResultStructure:
    """SweepABTestResult and ParameterSweepResult can be constructed correctly."""

    def test_parameter_sweep_result(self) -> None:
        result: ParameterSweepResult = {
            "temperature": 0.87,
            "top_p": 0.95,
            "quality_score": 0.72,
            "note_count": 48,
            "pitch_range": 18,
            "velocity_variation": 0.14,
            "rejection_score": 0.65,
        }
        assert result["temperature"] == 0.87
        assert result["quality_score"] == 0.72

    def test_sweep_ab_test_result_significant_flag(self) -> None:
        sweep: SweepABTestResult = {
            "genre": "jazz",
            "tempo": 120,
            "bars": 4,
            "sweep_results": [],
            "best_temperature": 0.95,
            "best_top_p": 0.97,
            "best_quality_score": 0.81,
            "score_range": 0.07,
            "significant": True,
        }
        assert sweep["significant"] is True
        assert sweep["score_range"] >= 0.05

    def test_sweep_ab_test_result_not_significant(self) -> None:
        sweep: SweepABTestResult = {
            "genre": "techno",
            "tempo": 130,
            "bars": 8,
            "sweep_results": [],
            "best_temperature": 0.78,
            "best_top_p": 0.92,
            "best_quality_score": 0.68,
            "score_range": 0.03,
            "significant": False,
        }
        assert sweep["significant"] is False
        assert sweep["score_range"] < 0.05


# =============================================================================
# Regression: genre priors do not break existing control-vector flow
# =============================================================================


class TestPriorRegressions:
    """Ensure adding genre priors does not regress existing non-prior behaviour."""

    def test_unknown_genre_falls_back_to_cv(self) -> None:
        """Genres with no prior still produce valid params from control vector."""
        controls = _controls(creativity=0.6, groove=0.5, density=0.5, complexity=0.5)
        params = apply_controls_to_params(controls, bars=4, genre_prior=None)
        assert _TEMP_MIN <= params["temperature"] <= _TEMP_MAX
        assert _TOP_P_MIN <= params["top_p"] <= _TOP_P_MAX
        assert params["num_gen_tokens"] >= 512
        assert params["num_prime_tokens"] >= 2048

    def test_all_canonical_priors_produce_valid_params(self) -> None:
        """Sanity sweep: every known prior produces params in acceptable ranges."""
        controls = _controls(creativity=0.5, groove=0.5, density=0.5, complexity=0.5)
        for genre in _GENRE_PRIORS:
            prior = get_genre_prior(genre)
            assert prior is not None
            params = apply_controls_to_params(controls, bars=4, genre_prior=prior)
            assert 0.7 <= params["temperature"] <= 1.0, f"{genre}: temp {params['temperature']}"
            assert 0.90 <= params["top_p"] <= 0.98, f"{genre}: top_p {params['top_p']}"
            assert params["num_gen_tokens"] >= 512, f"{genre}: gen_tokens {params['num_gen_tokens']}"
            assert params["num_prime_tokens"] >= 2048, f"{genre}: prime_tokens {params['num_prime_tokens']}"
