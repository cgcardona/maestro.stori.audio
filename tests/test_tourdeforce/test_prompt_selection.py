"""Tests for deterministic prompt selection."""

from __future__ import annotations

from tourdeforce.models import stable_hash


class TestStableHash:

    def test_deterministic(self) -> None:

        result1 = stable_hash(["p1", "p2", "p3", "p4"], 1337)
        result2 = stable_hash(["p1", "p2", "p3", "p4"], 1337)
        assert result1 == result2

    def test_seed_changes_result(self) -> None:

        result1 = stable_hash(["p1", "p2", "p3", "p4"], 1337)
        result2 = stable_hash(["p1", "p2", "p3", "p4"], 42)
        assert result1 != result2

    def test_order_matters(self) -> None:

        result1 = stable_hash(["p1", "p2", "p3", "p4"], 1337)
        result2 = stable_hash(["p4", "p3", "p2", "p1"], 1337)
        assert result1 != result2

    def test_selection_index_distribution(self) -> None:

        """Ensure selection distributes across all 4 options over many seeds."""
        seen = set()
        for seed in range(100):
            idx = stable_hash(["p1", "p2", "p3", "p4"], seed) % 4
            seen.add(idx)
        assert seen == {0, 1, 2, 3}, "All indices should be reachable"

    def test_modular_selection(self) -> None:

        ids = sorted(["p_alpha", "p_beta", "p_gamma", "p_delta"])
        idx = stable_hash(ids, 1337) % 4
        assert 0 <= idx <= 3

        # Same inputs produce same selection
        idx2 = stable_hash(ids, 1337) % 4
        assert idx == idx2
