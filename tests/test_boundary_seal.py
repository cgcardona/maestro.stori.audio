"""Boundary seal tests — verify architectural contracts are enforced.

These tests fail if internal module boundaries are violated, ensuring
the Maestro/Muse separation survives future changes.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maestro.models.variation import (
    Variation,
    Phrase,
    NoteChange,
    MidiNoteSnapshot,
)

ROOT = Path(__file__).resolve().parent.parent


# ── 1.1  Muse compute boundary is pure ──


class TestMuseComputeBoundary:
    """compute_variation_from_context must be a pure function of data."""

    def test_signature_has_no_store_param(self) -> None:

        """The function must not accept a StateStore parameter."""
        from maestro.core.executor.variation import compute_variation_from_context

        sig = inspect.signature(compute_variation_from_context)
        param_names = set(sig.parameters.keys())
        assert "store" not in param_names

    def test_variation_service_has_no_state_store_import(self) -> None:

        """maestro/services/variation/ must not import StateStore or EntityRegistry."""
        variation_dir = ROOT / "maestro" / "services" / "variation"
        forbidden = {"maestro.core.state_store", "maestro.core.entity_registry"}

        violations: list[str] = []
        for py_file in variation_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for fb in forbidden:
                        if node.module.startswith(fb):
                            violations.append(f"{py_file.name}: {node.module}")

        assert violations == [], f"Forbidden imports found: {violations}"

    def test_compute_function_body_has_no_store_imports(self) -> None:

        """The function body must not contain lazy imports of StateStore."""
        filepath = ROOT / "maestro" / "core" / "executor" / "variation.py"
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


# ── 1.2  VariationContext is data-only ──


class TestVariationContextDataOnly:
    """VariationContext must not contain a StateStore reference."""

    def test_variation_context_has_no_store_field(self) -> None:

        from maestro.core.executor.models import VariationContext
        field_names = {f.name for f in VariationContext.__dataclass_fields__.values()}
        assert "store" not in field_names, "VariationContext must not have a 'store' field"

    def test_variation_context_uses_snapshot_bundle(self) -> None:

        from maestro.core.executor.models import VariationContext, SnapshotBundle
        ctx = VariationContext.__dataclass_fields__
        assert "base" in {f for f in ctx}, "VariationContext must have 'base' field"
        assert "proposed" in {f for f in ctx}, "VariationContext must have 'proposed' field"
        # With `from __future__ import annotations`, .type is a string
        assert ctx["base"].type in (SnapshotBundle, "SnapshotBundle")
        assert ctx["proposed"].type in (SnapshotBundle, "SnapshotBundle")


# ── 1.3  Commit path doesn't call get_or_create_store ──


class TestApplyVariationBoundary:
    """apply_variation_phrases must never call get_or_create_store."""

    @pytest.mark.anyio
    async def test_apply_never_calls_get_or_create_store(self) -> None:

        """Monkeypatch get_or_create_store to raise; apply must still succeed."""
        from maestro.core.executor import apply_variation_phrases

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

        def _boom(conversation_id: str, project_id: str | None = None) -> None:

            raise AssertionError("apply_variation_phrases called get_or_create_store!")

        with patch("maestro.core.state_store.get_or_create_store", side_effect=_boom):
            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=["p1"],
                project_state={},
                store=store,
            )

        assert result.success is True

    def test_apply_module_does_not_import_get_or_create_store(self) -> None:

        """The apply module must not import get_or_create_store at all."""
        filepath = ROOT / "maestro" / "core" / "executor" / "apply.py"
        source = filepath.read_text()

        for i, line in enumerate(source.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            assert "get_or_create_store" not in line, (
                f"apply.py:{i} references get_or_create_store"
            )

    def test_apply_does_not_access_store_registry(self) -> None:

        """apply.py must not contain store.registry references."""
        filepath = ROOT / "maestro" / "core" / "executor" / "apply.py"
        source = filepath.read_text()

        for i, line in enumerate(source.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            assert "store.registry" not in line, (
                f"apply.py:{i} accesses store.registry"
            )


# ── 1.4  muse_repository boundary ──


class TestMuseRepositoryBoundary:
    """muse_repository must not import StateStore or executor."""

    def test_no_state_store_import(self) -> None:

        filepath = ROOT / "maestro" / "services" / "muse_repository.py"
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "state_store" not in node.module
                assert "executor" not in node.module

    def test_no_variation_service_import(self) -> None:

        filepath = ROOT / "maestro" / "services" / "muse_repository.py"
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "variation.service" not in node.module
                assert "services.variation" not in node.module


class TestMuseReplayBoundary:
    """muse_replay must not import StateStore, executor, or LLM handlers."""

    def test_no_state_store_or_executor_import(self) -> None:

        filepath = ROOT / "maestro" / "services" / "muse_replay.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_replay imports forbidden module: {node.module}"
                    )

    def test_no_forbidden_names(self) -> None:

        filepath = ROOT / "maestro" / "services" / "muse_replay.py"
        tree = ast.parse(filepath.read_text())
        forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"muse_replay imports forbidden name: {alias.name}"
                    )


class TestMuseDriftBoundary:
    """muse_drift must not import StateStore, executor, or LLM handlers."""

    def test_no_state_store_or_executor_import(self) -> None:

        filepath = ROOT / "maestro" / "services" / "muse_drift.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_drift imports forbidden module: {node.module}"
                    )

    def test_no_forbidden_names(self) -> None:

        filepath = ROOT / "maestro" / "services" / "muse_drift.py"
        tree = ast.parse(filepath.read_text())
        forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"muse_drift imports forbidden name: {alias.name}"
                    )

    def test_no_get_or_create_store_call(self) -> None:

        filepath = ROOT / "maestro" / "services" / "muse_drift.py"
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                assert name != "get_or_create_store", (
                    "muse_drift calls get_or_create_store"
                )


# ── 3.3  Golden shape tests ──


class TestGoldenShapes:
    """Lock down key payload structures to prevent silent contract drift."""

    def test_updated_region_shape(self) -> None:

        """updated_regions dicts must contain the expected keys."""
        required_keys = {
            "region_id", "track_id", "notes",
            "cc_events", "pitch_bends", "aftertouch",
            "start_beat", "duration_beats", "name",
        }

        from maestro.models.variation import UpdatedRegionPayload
        model_fields = set(UpdatedRegionPayload.model_fields.keys())
        for key in required_keys:
            assert key in model_fields, f"UpdatedRegionPayload missing field: {key}"

    def test_tool_call_outcome_shape(self) -> None:

        """_ToolCallOutcome must have the expected fields."""
        from maestro.core.maestro_plan_tracker.models import _ToolCallOutcome

        expected = {
            "enriched_params", "tool_result", "sse_events",
            "msg_call", "msg_result", "skipped", "extra_tool_calls",
        }
        actual = {f.name for f in _ToolCallOutcome.__dataclass_fields__.values()}
        assert expected.issubset(actual), f"Missing fields: {expected - actual}"

    def test_snapshot_bundle_shape(self) -> None:

        """SnapshotBundle must expose the canonical attribute set."""
        from maestro.core.executor.models import SnapshotBundle

        bundle = SnapshotBundle()
        expected_attrs = {"notes", "cc", "pitch_bends", "aftertouch", "track_regions", "region_start_beats"}
        actual_attrs = set(SnapshotBundle.__dataclass_fields__.keys())
        assert expected_attrs == actual_attrs, f"Mismatch: {expected_attrs.symmetric_difference(actual_attrs)}"
        for attr in expected_attrs:
            assert isinstance(getattr(bundle, attr), dict)

    def test_snapshot_bundle_from_capture(self) -> None:

        """capture_base_snapshot must return a SnapshotBundle."""
        from maestro.core.executor.snapshots import capture_base_snapshot
        from maestro.core.executor.models import SnapshotBundle
        from maestro.core.state_store import StateStore

        store = StateStore(conversation_id="shape-test")
        snapshot = capture_base_snapshot(store)

        assert isinstance(snapshot, SnapshotBundle)
        assert isinstance(snapshot.notes, dict)
        assert isinstance(snapshot.cc, dict)
        assert isinstance(snapshot.pitch_bends, dict)
        assert isinstance(snapshot.aftertouch, dict)
