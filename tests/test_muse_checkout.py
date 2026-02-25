"""Tests for the Muse Checkout Engine (Phase 9).

Verifies:
- No-op checkout when target == working.
- Note add checkout produces stori_add_notes.
- Controller restore produces correct tool calls.
- Large diff triggers region reset (clear + add).
- Determinism (same inputs → same plan hash).
- Boundary seal (AST).
"""

import ast
from pathlib import Path

import pytest

from app.services.muse_checkout import (
    CheckoutPlan,
    REGION_RESET_THRESHOLD,
    build_checkout_plan,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _note(pitch: int, start: float, dur: float = 1.0, vel: int = 100) -> dict:
    return {"pitch": pitch, "start_beat": start, "duration_beats": dur, "velocity": vel, "channel": 0}


def _cc(cc_num: int, beat: float, value: int) -> dict:
    return {"kind": "cc", "cc": cc_num, "beat": beat, "value": value}


def _pb(beat: float, value: int) -> dict:
    return {"kind": "pitch_bend", "beat": beat, "value": value}


def _at(beat: float, value: int, pitch: int | None = None) -> dict:
    d: dict = {"kind": "aftertouch", "beat": beat, "value": value}
    if pitch is not None:
        d["pitch"] = pitch
    return d


def _empty_plan_args(
    *,
    target_notes: dict | None = None,
    working_notes: dict | None = None,
    target_cc: dict | None = None,
    working_cc: dict | None = None,
    target_pb: dict | None = None,
    working_pb: dict | None = None,
    target_at: dict | None = None,
    working_at: dict | None = None,
    track_regions: dict | None = None,
) -> dict:
    return {
        "project_id": "proj-1",
        "target_variation_id": "var-1",
        "target_notes": target_notes or {},
        "working_notes": working_notes or {},
        "target_cc": target_cc or {},
        "working_cc": working_cc or {},
        "target_pb": target_pb or {},
        "working_pb": working_pb or {},
        "target_at": target_at or {},
        "working_at": working_at or {},
        "track_regions": track_regions or {},
    }


# ---------------------------------------------------------------------------
# 6.1 — No-Op Checkout
# ---------------------------------------------------------------------------


class TestNoOpCheckout:

    def test_identical_state_produces_no_calls(self):
        notes = {"r1": [_note(60, 0.0), _note(64, 1.0)]}
        cc = {"r1": [_cc(64, 0.0, 127)]}
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes=notes, working_notes=notes,
            target_cc=cc, working_cc=cc,
            track_regions={"r1": "t1"},
        ))
        assert plan.is_noop
        assert plan.tool_calls == ()
        assert plan.regions_reset == ()

    def test_empty_state_is_noop(self):
        plan = build_checkout_plan(**_empty_plan_args())
        assert plan.is_noop

    def test_fingerprint_target_still_populated(self):
        notes = {"r1": [_note(60, 0.0)]}
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes=notes, working_notes=notes,
            track_regions={"r1": "t1"},
        ))
        assert "r1" in plan.fingerprint_target
        assert len(plan.fingerprint_target["r1"]) == 16


# ---------------------------------------------------------------------------
# 6.2 — Note Add Checkout
# ---------------------------------------------------------------------------


class TestNoteAddCheckout:

    def test_missing_note_produces_add(self):
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
        ))
        assert not plan.is_noop
        add_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_notes"]
        assert len(add_calls) == 1
        assert len(add_calls[0]["arguments"]["notes"]) == 1
        assert add_calls[0]["arguments"]["notes"][0]["pitch"] == 72

    def test_region_with_removals_triggers_reset(self):
        """Removing a note requires clear+add because no individual remove tool exists."""
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            track_regions={"r1": "t1"},
        ))
        assert "r1" in plan.regions_reset
        clear_calls = [c for c in plan.tool_calls if c["tool"] == "stori_clear_notes"]
        assert len(clear_calls) == 1

    def test_modified_note_triggers_reset(self):
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0, vel=80)]},
            working_notes={"r1": [_note(60, 0.0, vel=120)]},
            track_regions={"r1": "t1"},
        ))
        assert "r1" in plan.regions_reset

    def test_add_to_empty_region_no_clear(self):
        """Adding notes to an empty region should not produce a clear call."""
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": []},
            track_regions={"r1": "t1"},
        ))
        clear_calls = [c for c in plan.tool_calls if c["tool"] == "stori_clear_notes"]
        add_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_notes"]
        assert len(clear_calls) == 0
        assert len(add_calls) == 1


# ---------------------------------------------------------------------------
# 6.3 — Controller Restore
# ---------------------------------------------------------------------------


class TestControllerRestore:

    def test_missing_pb_produces_add_pitch_bend(self):
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_pb={"r1": [_pb(1.0, 4096)]},
            working_pb={"r1": []},
            track_regions={"r1": "t1"},
        ))
        pb_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_pitch_bend"]
        assert len(pb_calls) == 1
        assert pb_calls[0]["arguments"]["events"][0]["value"] == 4096

    def test_missing_cc_produces_add_midi_cc(self):
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={"r1": [_cc(64, 0.0, 127)]},
            working_cc={"r1": []},
            track_regions={"r1": "t1"},
        ))
        cc_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_midi_cc"]
        assert len(cc_calls) == 1
        assert cc_calls[0]["arguments"]["cc"] == 64

    def test_missing_at_produces_add_aftertouch(self):
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_at={"r1": [_at(2.0, 80, pitch=60)]},
            working_at={"r1": []},
            track_regions={"r1": "t1"},
        ))
        at_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_aftertouch"]
        assert len(at_calls) == 1
        assert at_calls[0]["arguments"]["events"][0]["pitch"] == 60

    def test_modified_cc_value_produces_call(self):
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={"r1": [_cc(64, 0.0, 0)]},
            working_cc={"r1": [_cc(64, 0.0, 127)]},
            track_regions={"r1": "t1"},
        ))
        cc_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_midi_cc"]
        assert len(cc_calls) == 1
        assert cc_calls[0]["arguments"]["events"][0]["value"] == 0

    def test_multiple_cc_numbers_grouped(self):
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={"r1": [_cc(1, 0.0, 64), _cc(64, 2.0, 127)]},
            working_cc={"r1": []},
            track_regions={"r1": "t1"},
        ))
        cc_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_midi_cc"]
        assert len(cc_calls) == 2
        cc_numbers = sorted(c["arguments"]["cc"] for c in cc_calls)
        assert cc_numbers == [1, 64]


# ---------------------------------------------------------------------------
# 6.4 — Large Drift Fallback
# ---------------------------------------------------------------------------


class TestLargeDriftFallback:

    def test_many_additions_trigger_reset(self):
        target_notes = [_note(p, float(p - 40)) for p in range(40, 40 + REGION_RESET_THRESHOLD + 5)]
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": target_notes},
            working_notes={"r1": []},
            track_regions={"r1": "t1"},
        ))
        assert "r1" in plan.regions_reset
        clear_calls = [c for c in plan.tool_calls if c["tool"] == "stori_clear_notes"]
        add_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_notes"]
        assert len(clear_calls) == 1
        assert len(add_calls) == 1
        assert len(add_calls[0]["arguments"]["notes"]) == len(target_notes)

    def test_below_threshold_pure_additions_no_reset(self):
        target_notes = [_note(60, 0.0), _note(62, 1.0)]
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": target_notes},
            working_notes={"r1": []},
            track_regions={"r1": "t1"},
        ))
        assert "r1" not in plan.regions_reset


# ---------------------------------------------------------------------------
# 6.5 — Determinism Test
# ---------------------------------------------------------------------------


class TestDeterminism:

    def test_same_inputs_produce_same_hash(self):
        args = _empty_plan_args(
            target_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={"r1": [_cc(64, 0.0, 127)]},
            working_cc={"r1": []},
            track_regions={"r1": "t1"},
        )
        plan1 = build_checkout_plan(**args)
        plan2 = build_checkout_plan(**args)
        assert plan1.plan_hash() == plan2.plan_hash()

    def test_different_inputs_produce_different_hash(self):
        args1 = _empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": []},
            track_regions={"r1": "t1"},
        )
        args2 = _empty_plan_args(
            target_notes={"r1": [_note(72, 0.0)]},
            working_notes={"r1": []},
            track_regions={"r1": "t1"},
        )
        plan1 = build_checkout_plan(**args1)
        plan2 = build_checkout_plan(**args2)
        assert plan1.plan_hash() != plan2.plan_hash()

    def test_tool_call_ordering_deterministic(self):
        """Calls are ordered: clear → add_notes → cc → pb → at per region."""
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0, vel=80)]},
            working_notes={"r1": [_note(60, 0.0, vel=120)]},
            target_cc={"r1": [_cc(64, 0.0, 127)]},
            working_cc={"r1": []},
            target_pb={"r1": [_pb(1.0, 4096)]},
            working_pb={"r1": []},
            target_at={"r1": [_at(2.0, 80)]},
            working_at={"r1": []},
            track_regions={"r1": "t1"},
        ))
        tools = [c["tool"] for c in plan.tool_calls]
        assert tools == [
            "stori_clear_notes",
            "stori_add_notes",
            "stori_add_midi_cc",
            "stori_add_pitch_bend",
            "stori_add_aftertouch",
        ]


# ---------------------------------------------------------------------------
# 6.6 — Boundary Seal
# ---------------------------------------------------------------------------


class TestCheckoutBoundary:

    def test_no_state_store_or_executor_import(self):
        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_checkout.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_checkout imports forbidden module: {node.module}"
                    )

    def test_no_forbidden_names(self):
        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_checkout.py"
        tree = ast.parse(filepath.read_text())
        forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"muse_checkout imports forbidden name: {alias.name}"
                    )

    def test_no_get_or_create_store_call(self):
        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_checkout.py"
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
                    "muse_checkout.py calls get_or_create_store"
                )
