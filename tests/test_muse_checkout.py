"""Tests for the Muse Checkout Engine (Phase 9).

Verifies:
- No-op checkout when target == working.
- Note add checkout produces stori_add_notes.
- Controller restore produces correct tool calls.
- Large diff triggers region reset (clear + add).
- Determinism (same inputs → same plan hash).
- Boundary seal (AST).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteDict,
    PitchBendDict,
)
from app.services.muse_checkout import (
    CheckoutPlan,
    REGION_RESET_THRESHOLD,
    build_checkout_plan,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _note(pitch: int, start: float, dur: float = 1.0, vel: int = 100) -> NoteDict:
    return {"pitch": pitch, "start_beat": start, "duration_beats": dur, "velocity": vel, "channel": 0}


def _cc(cc_num: int, beat: float, value: int) -> CCEventDict:
    return {"cc": cc_num, "beat": beat, "value": value}


def _pb(beat: float, value: int) -> PitchBendDict:
    return {"beat": beat, "value": value}


def _at(beat: float, value: int, pitch: int | None = None) -> AftertouchDict:
    d: AftertouchDict = {"beat": beat, "value": value}
    if pitch is not None:
        d["pitch"] = pitch
    return d


def _empty_plan_args(
    *,
    target_notes: dict[str, list[NoteDict]] | None = None,
    working_notes: dict[str, list[NoteDict]] | None = None,
    target_cc: dict[str, list[CCEventDict]] | None = None,
    working_cc: dict[str, list[CCEventDict]] | None = None,
    target_pb: dict[str, list[PitchBendDict]] | None = None,
    working_pb: dict[str, list[PitchBendDict]] | None = None,
    target_at: dict[str, list[AftertouchDict]] | None = None,
    working_at: dict[str, list[AftertouchDict]] | None = None,
    track_regions: dict[str, str] | None = None,
) -> dict[str, Any]:
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

    def test_identical_state_produces_no_calls(self) -> None:

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

    def test_empty_state_is_noop(self) -> None:

        plan = build_checkout_plan(**_empty_plan_args())
        assert plan.is_noop

    def test_fingerprint_target_still_populated(self) -> None:

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

    def test_missing_note_produces_add(self) -> None:

        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
        ))
        assert not plan.is_noop
        add_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_notes"]
        assert len(add_calls) == 1
        notes = add_calls[0]["arguments"]["notes"]
        assert isinstance(notes, list)
        assert len(notes) == 1
        assert isinstance(notes[0], dict)
        assert notes[0]["pitch"] == 72

    def test_region_with_removals_triggers_reset(self) -> None:

        """Removing a note requires clear+add because no individual remove tool exists."""
        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            track_regions={"r1": "t1"},
        ))
        assert "r1" in plan.regions_reset
        clear_calls = [c for c in plan.tool_calls if c["tool"] == "stori_clear_notes"]
        assert len(clear_calls) == 1

    def test_modified_note_triggers_reset(self) -> None:

        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0, vel=80)]},
            working_notes={"r1": [_note(60, 0.0, vel=120)]},
            track_regions={"r1": "t1"},
        ))
        assert "r1" in plan.regions_reset

    def test_add_to_empty_region_no_clear(self) -> None:

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

    def test_missing_pb_produces_add_pitch_bend(self) -> None:

        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_pb={"r1": [_pb(1.0, 4096)]},
            working_pb={"r1": []},
            track_regions={"r1": "t1"},
        ))
        pb_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_pitch_bend"]
        assert len(pb_calls) == 1
        pb_events = pb_calls[0]["arguments"]["events"]
        assert isinstance(pb_events, list)
        assert isinstance(pb_events[0], dict)
        assert pb_events[0]["value"] == 4096

    def test_missing_cc_produces_add_midi_cc(self) -> None:

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

    def test_missing_at_produces_add_aftertouch(self) -> None:

        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_at={"r1": [_at(2.0, 80, pitch=60)]},
            working_at={"r1": []},
            track_regions={"r1": "t1"},
        ))
        at_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_aftertouch"]
        assert len(at_calls) == 1
        at_events = at_calls[0]["arguments"]["events"]
        assert isinstance(at_events, list)
        assert isinstance(at_events[0], dict)
        assert at_events[0]["pitch"] == 60

    def test_modified_cc_value_produces_call(self) -> None:

        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={"r1": [_cc(64, 0.0, 0)]},
            working_cc={"r1": [_cc(64, 0.0, 127)]},
            track_regions={"r1": "t1"},
        ))
        cc_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_midi_cc"]
        assert len(cc_calls) == 1
        cc_events = cc_calls[0]["arguments"]["events"]
        assert isinstance(cc_events, list)
        assert isinstance(cc_events[0], dict)
        assert cc_events[0]["value"] == 0

    def test_multiple_cc_numbers_grouped(self) -> None:

        plan = build_checkout_plan(**_empty_plan_args(
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={"r1": [_cc(1, 0.0, 64), _cc(64, 2.0, 127)]},
            working_cc={"r1": []},
            track_regions={"r1": "t1"},
        ))
        cc_calls = [c for c in plan.tool_calls if c["tool"] == "stori_add_midi_cc"]
        assert len(cc_calls) == 2
        cc_numbers = sorted(
            cc for c in cc_calls if isinstance((cc := c["arguments"]["cc"]), int)
        )
        assert cc_numbers == [1, 64]


# ---------------------------------------------------------------------------
# 6.4 — Large Drift Fallback
# ---------------------------------------------------------------------------


class TestLargeDriftFallback:

    def test_many_additions_trigger_reset(self) -> None:

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
        large_notes = add_calls[0]["arguments"]["notes"]
        assert isinstance(large_notes, list)
        assert len(large_notes) == len(target_notes)

    def test_below_threshold_pure_additions_no_reset(self) -> None:

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

    def test_same_inputs_produce_same_hash(self) -> None:

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

    def test_different_inputs_produce_different_hash(self) -> None:

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

    def test_tool_call_ordering_deterministic(self) -> None:

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

    def test_no_state_store_or_executor_import(self) -> None:

        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_checkout.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_checkout imports forbidden module: {node.module}"
                    )

    def test_no_forbidden_names(self) -> None:

        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_checkout.py"
        tree = ast.parse(filepath.read_text())
        forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"muse_checkout imports forbidden name: {alias.name}"
                    )

    def test_no_get_or_create_store_call(self) -> None:

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
