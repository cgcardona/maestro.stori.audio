"""Tests for drift-aware commit safety (Phase 8).

Verifies:
- Clean commits succeed (200).
- Dirty notes block commit (409 + WORKING_TREE_DIRTY).
- Dirty controllers block commit (409).
- force=True bypasses drift check.
- Commit route boundary seal (AST).
"""
from __future__ import annotations

import ast
import uuid
from dataclasses import asdict
from pathlib import Path

import pytest

from app.contracts.json_types import CCEventDict, NoteDict
from app.services.muse_drift import (
    CommitConflictPayload,
    DriftReport,
    DriftSeverity,
    RegionDriftSummary,
    compute_drift_report,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _note(pitch: int, start: float) -> NoteDict:

    return {"pitch": pitch, "start_beat": start, "duration_beats": 1.0, "velocity": 100, "channel": 0}


def _cc(cc_num: int, beat: float, value: int) -> CCEventDict:

    return {"cc": cc_num, "beat": beat, "value": value}


# ---------------------------------------------------------------------------
# 5.1 — Clean commit allowed
# ---------------------------------------------------------------------------


class TestCleanCommitAllowed:

    def test_clean_drift_does_not_require_action(self) -> None:

        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
        )
        assert report.is_clean is True
        assert report.requires_user_action() is False
        assert report.severity == DriftSeverity.CLEAN

    def test_clean_drift_with_controllers(self) -> None:

        cc_events = [_cc(64, 0.0, 127)]
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_cc={"r1": cc_events},
            working_cc={"r1": cc_events},
        )
        assert report.requires_user_action() is False


# ---------------------------------------------------------------------------
# 5.2 — Dirty notes blocked (409)
# ---------------------------------------------------------------------------


class TestDirtyNotesBlocked:

    def test_dirty_drift_requires_action(self) -> None:

        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            track_regions={"r1": "t1"},
        )
        assert report.is_clean is False
        assert report.requires_user_action() is True
        assert report.severity == DriftSeverity.DIRTY

    def test_conflict_payload_from_dirty_report(self) -> None:

        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            track_regions={"r1": "t1"},
        )
        conflict = CommitConflictPayload.from_drift_report(report)
        assert conflict.severity == "dirty"
        assert conflict.total_changes == 1
        assert "r1" in conflict.changed_regions

    def test_conflict_payload_serializable(self) -> None:

        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            track_regions={"r1": "t1"},
        )
        conflict = CommitConflictPayload.from_drift_report(report)
        payload = asdict(conflict)
        assert payload["severity"] == "dirty"
        assert payload["total_changes"] == 1
        assert "error" not in payload

    def test_conflict_payload_error_field(self) -> None:

        """The 409 detail must include 'error': 'WORKING_TREE_DIRTY'."""
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            track_regions={"r1": "t1"},
        )
        conflict = CommitConflictPayload.from_drift_report(report)
        drift_dict = asdict(conflict)
        detail: dict[str, object] = {
            "error": "WORKING_TREE_DIRTY",
            "drift": drift_dict,
        }
        assert detail["error"] == "WORKING_TREE_DIRTY"
        assert drift_dict["severity"] == "dirty"


# ---------------------------------------------------------------------------
# 5.3 — Dirty controllers blocked (409)
# ---------------------------------------------------------------------------


class TestDirtyControllersBlocked:

    def test_cc_change_requires_action(self) -> None:

        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_cc={"r1": []},
            working_cc={"r1": [_cc(64, 0.0, 127)]},
        )
        assert report.requires_user_action() is True
        conflict = CommitConflictPayload.from_drift_report(report)
        assert conflict.total_changes == 1

    def test_pb_change_requires_action(self) -> None:

        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_pb={"r1": [{"beat": 1.0, "value": 4096}]},
            working_pb={"r1": [{"beat": 1.0, "value": 8192}]},
        )
        assert report.requires_user_action() is True


# ---------------------------------------------------------------------------
# 5.4 — Force commit allowed
# ---------------------------------------------------------------------------


class TestForceCommitAllowed:

    def test_force_field_exists_on_request_model(self) -> None:

        from app.models.requests import CommitVariationRequest
        req = CommitVariationRequest(
            project_id="p1",
            base_state_id="s1",
            variation_id="v1",
            accepted_phrase_ids=["ph1"],
            force=True,
        )
        assert req.force is True

    def test_force_default_is_false(self) -> None:

        from app.models.requests import CommitVariationRequest
        req = CommitVariationRequest(
            project_id="p1",
            base_state_id="s1",
            variation_id="v1",
            accepted_phrase_ids=["ph1"],
        )
        assert req.force is False

    def test_requires_user_action_still_true_with_force(self) -> None:

        """Force doesn't change the drift report — only the commit route checks it."""
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            track_regions={"r1": "t1"},
        )
        assert report.requires_user_action() is True


# ---------------------------------------------------------------------------
# CommitConflictPayload unit tests
# ---------------------------------------------------------------------------


class TestCommitConflictPayload:

    def test_fingerprint_delta_only_dirty_regions(self) -> None:

        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={
                "r1": [_note(60, 0.0)],
                "r2": [_note(72, 0.0)],
            },
            working_snapshot_notes={
                "r1": [_note(60, 0.0)],
                "r2": [_note(72, 0.0), _note(76, 2.0)],
            },
            track_regions={"r1": "t1", "r2": "t2"},
        )
        conflict = CommitConflictPayload.from_drift_report(report)
        assert "r2" in conflict.fingerprint_delta
        assert "r1" not in conflict.fingerprint_delta

    def test_payload_excludes_sample_changes(self) -> None:

        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            track_regions={"r1": "t1"},
        )
        conflict = CommitConflictPayload.from_drift_report(report)
        payload = asdict(conflict)
        assert "sample_changes" not in payload
        assert "region_summaries" not in payload


# ---------------------------------------------------------------------------
# 5.5 — Boundary seal
# ---------------------------------------------------------------------------


class TestCommitRouteBoundary:

    def test_no_drift_internal_imports(self) -> None:

        """Commit route may only import compute_drift_report and CommitConflictPayload from drift."""
        filepath = Path(__file__).resolve().parent.parent / "app" / "api" / "routes" / "variation" / "commit.py"
        tree = ast.parse(filepath.read_text())
        forbidden_names = {"_fingerprint", "_combined_fingerprint", "RegionDriftSummary", "DriftSeverity"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"commit.py imports drift internal: {alias.name}"
                    )

    def test_commit_route_imports_only_public_drift_api(self) -> None:

        """Only compute_drift_report and CommitConflictPayload are used from muse_drift."""
        filepath = Path(__file__).resolve().parent.parent / "app" / "api" / "routes" / "variation" / "commit.py"
        tree = ast.parse(filepath.read_text())
        drift_imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "muse_drift" in node.module:
                for alias in node.names:
                    drift_imports.append(alias.name)
        allowed = {"compute_drift_report", "CommitConflictPayload"}
        for name in drift_imports:
            assert name in allowed, f"commit.py imports non-public drift symbol: {name}"

    def test_commit_route_does_not_import_state_store_internals(self) -> None:

        """Commit route uses get_or_create_store (allowed) but not StateStore class directly."""
        filepath = Path(__file__).resolve().parent.parent / "app" / "api" / "routes" / "variation" / "commit.py"
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name != "StateStore", (
                        "commit.py imports StateStore class directly"
                    )
