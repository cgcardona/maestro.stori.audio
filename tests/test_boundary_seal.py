"""Boundary seal tests — verify architectural contracts are enforced.

These tests fail if internal module boundaries are violated, ensuring
the Maestro/Muse separation survives future changes.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.models.variation import (
    Variation,
    Phrase,
    NoteChange,
    MidiNoteSnapshot,
)

ROOT = Path(__file__).resolve().parent.parent


# ── 1.1  Muse compute boundary is pure ──


class TestMuseComputeBoundary:
    """compute_variation_from_context must be a pure function of data."""

    def test_signature_has_no_store_param(self):
        """The function must not accept a StateStore parameter."""
        from app.core.executor.variation import compute_variation_from_context

        sig = inspect.signature(compute_variation_from_context)
        param_names = set(sig.parameters.keys())
        assert "store" not in param_names

    def test_variation_service_has_no_state_store_import(self):
        """app/services/variation/ must not import StateStore or EntityRegistry."""
        variation_dir = ROOT / "app" / "services" / "variation"
        forbidden = {"app.core.state_store", "app.core.entity_registry"}

        violations: list[str] = []
        for py_file in variation_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for fb in forbidden:
                        if node.module.startswith(fb):
                            violations.append(f"{py_file.name}: {node.module}")

        assert violations == [], f"Forbidden imports found: {violations}"

    def test_compute_function_body_has_no_store_imports(self):
        """The function body must not contain lazy imports of StateStore."""
        filepath = ROOT / "app" / "core" / "executor" / "variation.py"
        tree = ast.parse(filepath.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "compute_variation_from_context":
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom) and child.module:
                        assert "state_store" not in child.module, (
                            f"compute_variation_from_context lazily imports {child.module}"
                        )
                        assert "entity_registry" not in child.module, (
                            f"compute_variation_from_context lazily imports {child.module}"
                        )


# ── 1.2  Commit path doesn't call get_or_create_store ──


class TestApplyVariationBoundary:
    """apply_variation_phrases must never call get_or_create_store."""

    @pytest.mark.anyio
    async def test_apply_never_calls_get_or_create_store(self):
        """Monkeypatch get_or_create_store to raise; apply must still succeed."""
        from app.core.executor import apply_variation_phrases

        store = MagicMock()
        store.begin_transaction.return_value = MagicMock()
        store.add_notes = MagicMock()
        store.remove_notes = MagicMock()
        store.commit = MagicMock()
        store.get_region_notes = MagicMock(return_value=[])
        store.get_region_track_id = MagicMock(return_value="t1")
        store.get_region_cc = MagicMock(return_value=[])
        store.get_region_pitch_bends = MagicMock(return_value=[])
        store.get_region_aftertouch = MagicMock(return_value=[])

        variation = Variation(
            variation_id="v1",
            intent="test",
            beat_range=(0.0, 4.0),
            phrases=[
                Phrase(
                    phrase_id="p1",
                    track_id="t1",
                    region_id="r1",
                    start_beat=0.0,
                    end_beat=4.0,
                    label="Test",
                    note_changes=[
                        NoteChange(
                            note_id="n1",
                            change_type="added",
                            after=MidiNoteSnapshot(pitch=60, start_beat=0.0, duration_beats=1.0),
                        ),
                    ],
                ),
            ],
        )

        def _boom(*a: Any, **kw: Any) -> None:
            raise AssertionError("apply_variation_phrases called get_or_create_store!")

        with patch("app.core.state_store.get_or_create_store", side_effect=_boom):
            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=["p1"],
                project_state={},
                store=store,
            )

        assert result.success is True

    def test_apply_module_does_not_import_get_or_create_store(self):
        """The apply module must not import get_or_create_store at all."""
        filepath = ROOT / "app" / "core" / "executor" / "apply.py"
        source = filepath.read_text()

        for i, line in enumerate(source.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            assert "get_or_create_store" not in line, (
                f"apply.py:{i} references get_or_create_store"
            )

    def test_apply_does_not_access_store_registry(self):
        """apply.py must not contain store.registry references."""
        filepath = ROOT / "app" / "core" / "executor" / "apply.py"
        source = filepath.read_text()

        for i, line in enumerate(source.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            assert "store.registry" not in line, (
                f"apply.py:{i} accesses store.registry"
            )


# ── 3.3  Golden shape tests ──


class TestGoldenShapes:
    """Lock down key payload structures to prevent silent contract drift."""

    def test_updated_region_shape(self):
        """updated_regions dicts must contain the expected keys."""
        required_keys = {
            "region_id", "track_id", "notes",
            "cc_events", "pitch_bends", "aftertouch",
            "start_beat", "duration_beats", "name",
        }

        from app.models.variation import UpdatedRegionPayload
        model_fields = set(UpdatedRegionPayload.model_fields.keys())
        for key in required_keys:
            assert key in model_fields, f"UpdatedRegionPayload missing field: {key}"

    def test_tool_call_outcome_shape(self):
        """_ToolCallOutcome must have the expected fields."""
        from app.core.maestro_plan_tracker.models import _ToolCallOutcome

        expected = {
            "enriched_params", "tool_result", "sse_events",
            "msg_call", "msg_result", "skipped", "extra_tool_calls",
        }
        actual = {f.name for f in _ToolCallOutcome.__dataclass_fields__.values()}
        assert expected.issubset(actual), f"Missing fields: {expected - actual}"

    def test_orpheus_normalization_output_shape(self):
        """normalize_orpheus_tool_calls must return the canonical key set."""
        from app.services.orpheus import normalize_orpheus_tool_calls

        result = normalize_orpheus_tool_calls([])
        assert set(result.keys()) == {"notes", "cc_events", "pitch_bends", "aftertouch"}
        for v in result.values():
            assert isinstance(v, list)

    def test_orpheus_normalization_with_data(self):
        """Verify normalization parses all four data types correctly."""
        from app.services.orpheus import normalize_orpheus_tool_calls

        tool_calls = [
            {"tool": "addNotes", "params": {"notes": [{"pitch": 60}]}},
            {"tool": "addMidiCC", "params": {"cc": 64, "events": [{"beat": 0, "value": 127}]}},
            {"tool": "addPitchBend", "params": {"events": [{"beat": 1, "value": 4096}]}},
            {"tool": "addAftertouch", "params": {"events": [{"beat": 2, "value": 80}]}},
        ]
        result = normalize_orpheus_tool_calls(tool_calls)

        assert len(result["notes"]) == 1
        assert result["notes"][0]["pitch"] == 60
        assert len(result["cc_events"]) == 1
        assert result["cc_events"][0]["cc"] == 64
        assert len(result["pitch_bends"]) == 1
        assert result["pitch_bends"][0]["value"] == 4096
        assert len(result["aftertouch"]) == 1
        assert result["aftertouch"][0]["value"] == 80

    def test_store_snapshot_shape(self):
        """capture_base_snapshot must return the canonical key set."""
        from app.core.executor.snapshots import capture_base_snapshot
        from app.core.state_store import StateStore

        store = StateStore(conversation_id="shape-test")
        snapshot = capture_base_snapshot(store)

        assert set(snapshot.keys()) == {
            "region_notes", "region_cc", "region_pitch_bends", "region_aftertouch",
        }
        for v in snapshot.values():
            assert isinstance(v, dict)
