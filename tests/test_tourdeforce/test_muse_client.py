"""Tests for MUSE client result types."""

from __future__ import annotations

from tourdeforce.clients.muse import CheckoutResult, MergeResult


class TestCheckoutResult:

    def test_successful_checkout(self) -> None:

        r = CheckoutResult(
            success=True,
            blocked=False,
            target="abc123",
            head_moved=True,
            executed=5,
            plan_hash="deadbeefcafe1234",
        )
        assert r.success is True
        assert r.blocked is False
        assert r.head_moved is True
        d = r.to_dict()
        assert d["success"] is True
        assert d["plan_hash"] == "deadbeefcafe"  # truncated to 12

    def test_blocked_checkout(self) -> None:

        r = CheckoutResult(
            success=False,
            blocked=True,
            target="abc123",
            drift_severity="DIRTY",
            drift_total_changes=3,
            status_code=409,
        )
        assert r.success is False
        assert r.blocked is True
        assert r.drift_severity == "DIRTY"
        assert r.drift_total_changes == 3

    def test_force_recovery_checkout(self) -> None:

        r = CheckoutResult(
            success=True,
            blocked=False,
            target="abc123",
            head_moved=True,
            executed=10,
        )
        assert r.success is True
        assert r.blocked is False


class TestMergeResult:

    def test_successful_merge(self) -> None:

        r = MergeResult(
            success=True,
            merge_variation_id="merge_abc",
            executed=8,
        )
        assert r.success is True
        d = r.to_dict()
        assert d["conflict_count"] == 0

    def test_conflict_merge(self) -> None:

        r = MergeResult(
            success=False,
            conflicts=[
                {"region_id": "r_keys", "type": "note", "description": "Both sides modified note at pitch=48 beat=4.0"},
                {"region_id": "r_keys", "type": "cc", "description": "Both sides modified cc event at beat=0.0"},
            ],
            status_code=409,
        )
        assert r.success is False
        d = r.to_dict()
        assert d["conflict_count"] == 2
        assert len(d["conflicts"]) == 2

    def test_no_common_ancestor(self) -> None:

        r = MergeResult(
            success=False,
            conflicts=[{"region_id": "*", "type": "note", "description": "No common ancestor"}],
            status_code=409,
        )
        assert not r.success
        assert r.conflicts[0]["region_id"] == "*"
