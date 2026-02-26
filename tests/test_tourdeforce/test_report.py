"""Tests for report builder — verifies MUSE permutation KPIs appear."""

from __future__ import annotations

import tempfile
from pathlib import Path

from stori_tourdeforce.analyzers.run import RunAnalyzer
from stori_tourdeforce.models import RunResult, RunStatus


class TestRunAnalyzerMuseKPIs:

    def _make_result(self, run_id: str, **kwargs: object) -> RunResult:

        r = RunResult(run_id=run_id, status=RunStatus.SUCCESS, duration_ms=5000.0)
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r

    def _make_analyzer(self, *results: RunResult) -> RunAnalyzer:

        with tempfile.TemporaryDirectory() as tmpdir:
            return RunAnalyzer(list(results), Path(tmpdir))

    def test_muse_conflict_kpis(self) -> None:

        a = self._make_analyzer(
            self._make_result("r_000001", muse_conflict_count=2, muse_checkout_count=4),
            self._make_result("r_000002", muse_conflict_count=1, muse_checkout_count=3),
        )
        kpis = a.compute_kpis()
        assert kpis["total_muse_conflicts"] == 3
        assert kpis["total_muse_checkouts"] == 7

    def test_muse_drift_kpis(self) -> None:

        a = self._make_analyzer(
            self._make_result("r_000001", muse_drift_detected=True, muse_force_recoveries=1),
            self._make_result("r_000002", muse_drift_detected=False, muse_force_recoveries=0),
        )
        kpis = a.compute_kpis()
        assert kpis["total_muse_drift_detected"] == 1
        assert kpis["total_muse_force_recoveries"] == 1

    def test_muse_checkout_blocked_kpis(self) -> None:

        a = self._make_analyzer(
            self._make_result("r_000001", muse_checkout_blocked=2),
            self._make_result("r_000002", muse_checkout_blocked=0),
        )
        kpis = a.compute_kpis()
        assert kpis["total_muse_checkout_blocked"] == 2

    def test_muse_branch_kpis(self) -> None:

        a = self._make_analyzer(
            self._make_result(
                "r_000001",
                muse_branch_names=["bass_tighten", "drums_variation"],
                muse_commit_ids=["c1", "c2", "c3"],
                muse_merge_ids=["m1"],
            ),
        )
        kpis = a.compute_kpis()
        assert kpis["total_muse_branches"] == 2
        assert kpis["total_muse_commits"] == 3
        assert kpis["total_muse_merges"] == 1

    def test_empty_analyzer(self) -> None:

        a = self._make_analyzer()
        kpis = a.compute_kpis()
        assert kpis["total_runs"] == 0
        assert kpis["total_muse_conflicts"] == 0
        assert kpis["total_muse_checkouts"] == 0
        assert kpis["total_muse_drift_detected"] == 0

    def test_all_failure_types_tracked(self) -> None:

        a = self._make_analyzer(
            self._make_result("r_000001", status=RunStatus.MAESTRO_ERROR),
            self._make_result("r_000002", status=RunStatus.STORPHEUS_ERROR),
            self._make_result("r_000003", status=RunStatus.MUSE_ERROR),
            self._make_result("r_000004", status=RunStatus.MERGE_CONFLICT),
        )
        kpis = a.compute_kpis()
        assert kpis["total_runs"] == 4
        assert kpis["successful_runs"] == 0
        assert "maestro_error" in kpis["failure_breakdown"]
        assert "storpheus_error" in kpis["failure_breakdown"]
        assert "muse_error" in kpis["failure_breakdown"]
        assert "merge_conflict" in kpis["failure_breakdown"]
