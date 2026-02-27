"""RunAnalyzer — aggregate KPIs across all runs."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from tourdeforce.models import RunResult, RunStatus


class RunAnalyzer:
    """Aggregates results from all runs into KPIs and a normalized SQLite DB."""

    def __init__(self, results: list[RunResult], output_dir: Path) -> None:
        self._results = results
        self._output_dir = output_dir
        self._db_path = output_dir / "tourdeforce.db"

    @property
    def total_runs(self) -> int:
        return len(self._results)

    @property
    def successful_runs(self) -> int:
        return sum(1 for r in self._results if r.status == RunStatus.SUCCESS)

    @property
    def success_rate(self) -> float:
        return self.successful_runs / max(self.total_runs, 1)

    @property
    def failure_breakdown(self) -> dict[str, int]:
        breakdown: dict[str, int] = {}
        for r in self._results:
            if r.status != RunStatus.SUCCESS:
                key = r.status.value
                breakdown[key] = breakdown.get(key, 0) + 1
        return breakdown

    def compute_kpis(self) -> dict[str, Any]:
        """Compute aggregate KPIs."""
        durations = [r.duration_ms for r in self._results if r.duration_ms > 0]
        storpheus_totals = [r.storpheus_total_ms for r in self._results if r.storpheus_total_ms > 0]
        quality_scores = [
            r.midi_metrics.get("quality_score", 0)
            for r in self._results
            if r.midi_metrics
        ]
        note_counts = [
            r.midi_metrics.get("note_count_total", 0)
            for r in self._results
            if r.midi_metrics
        ]

        return {
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "success_rate": round(self.success_rate * 100, 1),
            "failure_breakdown": self.failure_breakdown,
            "duration_stats": _stats(durations) if durations else {},
            "storpheus_latency_stats": _stats(storpheus_totals) if storpheus_totals else {},
            "quality_score_stats": _stats(quality_scores) if quality_scores else {},
            "note_count_stats": _stats(note_counts) if note_counts else {},
            "total_tool_calls": sum(len(r.tool_calls) for r in self._results),
            "total_midi_notes": sum(r.storpheus_note_count for r in self._results),
            # MUSE VCS KPIs
            "total_muse_commits": sum(len(r.muse_commit_ids) for r in self._results),
            "total_muse_merges": sum(len(r.muse_merge_ids) for r in self._results),
            "total_muse_conflicts": sum(r.muse_conflict_count for r in self._results),
            "total_muse_checkouts": sum(r.muse_checkout_count for r in self._results),
            "total_muse_checkout_blocked": sum(r.muse_checkout_blocked for r in self._results),
            "total_muse_drift_detected": sum(1 for r in self._results if r.muse_drift_detected),
            "total_muse_force_recoveries": sum(r.muse_force_recoveries for r in self._results),
            "total_muse_branches": sum(len(r.muse_branch_names) for r in self._results),
            # Artifacts
            "total_artifacts": sum(len(r.artifact_files) for r in self._results),
            "artifact_breakdown": self._artifact_breakdown(),
            # Timing breakdown
            "total_duration_ms": sum(r.duration_ms for r in self._results),
            "total_storpheus_ms": sum(r.storpheus_total_ms for r in self._results),
        }

    def _artifact_breakdown(self) -> dict[str, int]:
        """Count artifact files by extension."""
        counts: dict[str, int] = {}
        for r in self._results:
            for f in r.artifact_files:
                ext = f.rsplit(".", 1)[-1].lower() if "." in f else "other"
                counts[ext] = counts.get(ext, 0) + 1
        return counts

    def build_sqlite(self) -> Path:
        """Build a normalized SQLite DB from run data."""
        conn = sqlite3.connect(str(self._db_path))
        c = conn.cursor()

        c.execute("""CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            prompt_id TEXT,
            status TEXT,
            start_ts TEXT,
            end_ts TEXT,
            duration_ms REAL,
            seed INTEGER,
            scenario TEXT
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS storpheus_calls (
            run_id TEXT,
            job_id TEXT,
            queue_wait_ms REAL,
            infer_ms REAL,
            total_ms REAL,
            retries INTEGER,
            output_bytes INTEGER,
            note_count INTEGER,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS maestro_steps (
            run_id TEXT,
            step_name TEXT,
            duration_ms REAL,
            outcome TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS midi_metrics (
            run_id TEXT,
            track_count INTEGER,
            note_count INTEGER,
            pitch_class_entropy REAL,
            velocity_mean REAL,
            velocity_stdev REAL,
            quality_score REAL,
            duration_sec REAL,
            tempo REAL,
            polyphony_estimate REAL,
            zero_length_notes INTEGER,
            extreme_pitches INTEGER,
            note_spam_regions INTEGER,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS muse_commits (
            run_id TEXT,
            commit_id TEXT,
            parent_ids TEXT,
            branch TEXT,
            message TEXT,
            ts TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS muse_merges (
            run_id TEXT,
            merge_id TEXT,
            left_id TEXT,
            right_id TEXT,
            outcome TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        )""")

        for r in self._results:
            c.execute(
                "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (r.run_id, r.prompt.id if r.prompt else "", r.status.value,
                 r.start_ts, r.end_ts, r.duration_ms, r.seed, r.scenario),
            )

            if r.storpheus_job_id:
                c.execute(
                    "INSERT INTO storpheus_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (r.run_id, r.storpheus_job_id, r.storpheus_queue_wait_ms,
                     r.storpheus_infer_ms, r.storpheus_total_ms, r.storpheus_retries,
                     r.storpheus_output_bytes, r.storpheus_note_count),
                )

            if r.midi_metrics:
                mm = r.midi_metrics
                c.execute(
                    "INSERT INTO midi_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r.run_id, mm.get("track_count", 0), mm.get("note_count_total", 0),
                     mm.get("pitch_class_entropy", 0), mm.get("velocity_mean", 0),
                     mm.get("velocity_stdev", 0), mm.get("quality_score", 0),
                     mm.get("duration_sec", 0), mm.get("tempo", 0),
                     mm.get("polyphony_estimate", 0), mm.get("zero_length_notes", 0),
                     mm.get("extreme_pitches", 0), mm.get("note_spam_regions", 0)),
                )

            for cid in r.muse_commit_ids:
                c.execute(
                    "INSERT INTO muse_commits VALUES (?, ?, ?, ?, ?, ?)",
                    (r.run_id, cid, "", "", "", r.start_ts),
                )

            for mid in r.muse_merge_ids:
                c.execute(
                    "INSERT INTO muse_merges VALUES (?, ?, ?, ?, ?)",
                    (r.run_id, mid, "", "", "success"),
                )

        conn.commit()
        conn.close()
        return self._db_path

    def find_best_run(self) -> RunResult | None:
        """Find the run with the highest quality score."""
        best = None
        best_score = -1.0
        for r in self._results:
            score = r.midi_metrics.get("quality_score", 0) if r.midi_metrics else 0
            if score > best_score:
                best_score = score
                best = r
        return best

    def find_worst_run(self) -> RunResult | None:
        """Find the worst run (failed or lowest quality)."""
        failed = [r for r in self._results if r.status != RunStatus.SUCCESS]
        if failed:
            return failed[0]
        worst = None
        worst_score = float("inf")
        for r in self._results:
            score = r.midi_metrics.get("quality_score", 0) if r.midi_metrics else 0
            if score < worst_score:
                worst_score = score
                worst = r
        return worst

    def get_outliers(self, percentile: float = 0.05) -> list[RunResult]:
        """Find the bottom N% of runs by quality score."""
        scored = sorted(
            self._results,
            key=lambda r: r.midi_metrics.get("quality_score", 0) if r.midi_metrics else 0,
        )
        n = max(1, int(len(scored) * percentile))
        return scored[:n]


def _stats(values: list[float]) -> dict[str, float]:
    """Compute summary statistics."""
    if not values:
        return {}
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    variance = sum((x - mean) ** 2 for x in s) / max(n - 1, 1)
    return {
        "count": n,
        "min": s[0],
        "max": s[-1],
        "mean": round(mean, 2),
        "median": s[n // 2],
        "stdev": round(math.sqrt(variance), 2),
        "p95": s[int(n * 0.95)] if n > 1 else s[0],
        "p99": s[int(n * 0.99)] if n > 1 else s[0],
    }
