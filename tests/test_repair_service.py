"""Tests for the drum repair service (app/services/repair.py).

Covers apply_drum_repair instructions and repair_drum_if_needed threshold logic.
"""
import random
import pytest
from app.services.repair import apply_drum_repair, repair_drum_if_needed
from app.core.music_spec_ir import DrumSpec, DrumConstraints, DensityTarget, DrumLayerSpec, default_drum_spec


def _make_drum_spec(**overrides) -> DrumSpec:
    """Build a minimal DrumSpec for testing."""
    return default_drum_spec(style="boom_bap", bars=8)


def _basic_kick_snare_hats(bars=4):
    """Generate a basic kick/snare/hat pattern."""
    notes = []
    for bar in range(bars):
        base = bar * 4.0
        notes.append({"pitch": 36, "startBeat": base, "duration": 0.25, "velocity": 100})
        notes.append({"pitch": 38, "startBeat": base + 2.0, "duration": 0.25, "velocity": 100})
        for i in range(8):
            notes.append({"pitch": 42, "startBeat": base + i * 0.5, "duration": 0.25, "velocity": 80})
    return notes


class TestApplyDrumRepair:

    def test_instrument_coverage_low_ghost(self):
        """instrument_coverage_low with ghost adds ghost notes."""
        notes = _basic_kick_snare_hats(8)
        spec = _make_drum_spec()
        repaired = apply_drum_repair(
            notes, spec,
            ["instrument_coverage_low: ghost"],
            rng=random.Random(42),
        )
        # Should have added ghost notes (pitches 37, 40, 41)
        ghost_notes = [n for n in repaired if n["pitch"] in (37, 40, 41)]
        assert len(ghost_notes) > 0

    def test_no_fill_adds_fills(self):
        """no_fill instruction adds fill notes in fill bars."""
        notes = _basic_kick_snare_hats(8)
        spec = _make_drum_spec()
        original_len = len(notes)
        repaired = apply_drum_repair(
            notes, spec,
            ["no_fill"],
            rng=random.Random(42),
        )
        # Fill bars should have added tom notes (41, 43, 45, 47)
        fill_notes = [n for n in repaired if n["pitch"] in (41, 43, 45, 47)]
        assert len(fill_notes) > 0

    def test_hats_repetitive_adds_open_hat(self):
        """hats_repetitive adds open hat (46) on beat 4."""
        notes = _basic_kick_snare_hats(8)
        spec = _make_drum_spec()
        repaired = apply_drum_repair(
            notes, spec,
            ["hats_repetitive: add open hat variation"],
            rng=random.Random(42),
        )
        open_hats = [n for n in repaired if n["pitch"] == 46]
        assert len(open_hats) > 0

    def test_velocity_flat_adds_dynamics(self):
        """velocity_flat adjusts velocities: accent beats 1/3, reduce upbeats."""
        notes = _basic_kick_snare_hats(4)
        spec = _make_drum_spec()
        repaired = apply_drum_repair(
            notes, spec,
            ["velocity_flat: need more dynamics"],
            rng=random.Random(42),
        )
        # Beat 1 kick should have higher velocity
        kick_on_beat1 = [n for n in repaired if n["pitch"] == 36 and abs(n["startBeat"] % 4) < 0.2]
        assert any(n["velocity"] > 100 for n in kick_on_beat1)

    def test_empty_instructions_no_change(self):
        """No repair instructions leaves notes unchanged (except salience cap + sort)."""
        notes = _basic_kick_snare_hats(4)
        spec = _make_drum_spec()
        repaired = apply_drum_repair(notes, spec, [])
        assert len(repaired) == len(notes)

    def test_sorted_output(self):
        """Output is sorted by (startBeat, pitch)."""
        notes = _basic_kick_snare_hats(4)
        spec = _make_drum_spec()
        repaired = apply_drum_repair(
            notes, spec,
            ["no_fill", "instrument_coverage_low: ghost"],
            rng=random.Random(42),
        )
        for i in range(len(repaired) - 1):
            a, b = repaired[i], repaired[i + 1]
            assert (a["startBeat"], a["pitch"]) <= (b["startBeat"], b["pitch"])


class TestRepairDrumIfNeeded:

    def test_above_threshold_no_repair(self):
        """Score above threshold returns original notes."""
        notes = _basic_kick_snare_hats(4)
        spec = _make_drum_spec()
        result, was_repaired = repair_drum_if_needed(
            notes, spec, score=0.8, repair_instructions=["no_fill"],
        )
        assert was_repaired is False
        assert result is notes

    def test_below_threshold_triggers_repair(self):
        """Score below threshold triggers repair."""
        notes = _basic_kick_snare_hats(4)
        spec = _make_drum_spec()
        result, was_repaired = repair_drum_if_needed(
            notes, spec, score=0.3, repair_instructions=["no_fill"],
        )
        assert was_repaired is True
        assert result is not notes

    def test_no_instructions_no_repair(self):
        """Empty instructions means no repair even if score is low."""
        notes = _basic_kick_snare_hats(4)
        spec = _make_drum_spec()
        result, was_repaired = repair_drum_if_needed(
            notes, spec, score=0.1, repair_instructions=[],
        )
        assert was_repaired is False
