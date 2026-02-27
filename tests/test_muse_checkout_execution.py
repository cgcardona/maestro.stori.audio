"""Tests for Muse Checkout Execution (Phase 10).

Verifies:
- No-op execution.
- Undo execution (checkout to parent).
- Redo execution (forward checkout).
- Drift safety block.
- Force override.
- Boundary seal (AST).
"""
from __future__ import annotations

import ast
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contracts.json_types import CCEventDict, NoteDict
from app.core.state_store import StateStore
from app.core.tracing import TraceContext
from app.core.tool_names import ToolName
from app.db.database import Base
from app.db import muse_models  # noqa: F401 — register tables
from app.models.variation import (
    MidiNoteSnapshot,
    NoteChange,
    Phrase,
    Variation,
)
from app.services import muse_repository
from app.services.muse_checkout import build_checkout_plan
from app.services.muse_checkout_executor import (
    CheckoutExecutionResult,
    execute_checkout_plan,
)
from app.services.muse_history_controller import (
    CheckoutBlockedError,
    CheckoutSummary,
    checkout_to_variation,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def store() -> StateStore:
    return StateStore(conversation_id="test-conv", project_id="test-proj")


@pytest.fixture
def trace() -> TraceContext:
    return TraceContext(trace_id="test-trace-001")


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────


def _note(pitch: int, start: float, dur: float = 1.0, vel: int = 100) -> NoteDict:

    return {"pitch": pitch, "start_beat": start, "duration_beats": dur, "velocity": vel, "channel": 0}


def _cc(cc_num: int, beat: float, value: int) -> CCEventDict:

    return {"cc": cc_num, "beat": beat, "value": value}


def _make_variation(
    notes: list[NoteDict],
    region_id: str = "region-1",
    track_id: str = "track-1",
) -> Variation:
    vid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    return Variation(
        variation_id=vid,
        intent="test",
        ai_explanation="test",
        affected_tracks=[track_id],
        affected_regions=[region_id],
        beat_range=(0.0, 8.0),
        phrases=[
            Phrase(
                phrase_id=pid,
                track_id=track_id,
                region_id=region_id,
                start_beat=0.0,
                end_beat=8.0,
                label="Test",
                note_changes=[
                    NoteChange(
                        note_id=str(uuid.uuid4()),
                        change_type="added",
                        after=MidiNoteSnapshot.from_note_dict(n),
                    )
                    for n in notes
                ],
            ),
        ],
    )


def _setup_region(store: StateStore, region_id: str = "region-1", track_id: str = "track-1") -> None:

    """Create track + region in StateStore so add_notes works."""
    txn = store.begin_transaction("setup")
    store.create_track("Track", track_id=track_id, transaction=txn)
    store.create_region("Region", parent_track_id=track_id, region_id=region_id, transaction=txn)
    store.commit(txn)


# ---------------------------------------------------------------------------
# 6.1 — No-op Checkout Execution
# ---------------------------------------------------------------------------


class TestNoOpExecution:

    def test_noop_plan_executes_zero_calls(self, store: StateStore, trace: TraceContext) -> None:

        plan = build_checkout_plan(
            project_id="p1", target_variation_id="v1",
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={}, working_cc={},
            target_pb={}, working_pb={},
            target_at={}, working_at={},
            track_regions={"r1": "t1"},
        )
        assert plan.is_noop

        result = execute_checkout_plan(
            checkout_plan=plan, store=store, trace=trace,
        )
        assert result.is_noop
        assert result.executed == 0
        assert result.failed == 0


# ---------------------------------------------------------------------------
# 6.2 — Undo Execution
# ---------------------------------------------------------------------------


class TestUndoExecution:

    def test_clear_and_add_notes_applied(self, store: StateStore, trace: TraceContext) -> None:

        _setup_region(store, "r1", "t1")
        txn = store.begin_transaction("add-working")
        store.add_notes("r1", [_note(60, 0.0), _note(72, 2.0)], transaction=txn)
        store.commit(txn)
        assert len(store.get_region_notes("r1")) == 2

        plan = build_checkout_plan(
            project_id="p1", target_variation_id="parent-var",
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            target_cc={}, working_cc={},
            target_pb={}, working_pb={},
            target_at={}, working_at={},
            track_regions={"r1": "t1"},
        )
        assert not plan.is_noop
        assert "r1" in plan.regions_reset

        result = execute_checkout_plan(
            checkout_plan=plan, store=store, trace=trace,
        )
        assert result.success
        assert result.executed > 0

        final_notes = store.get_region_notes("r1")
        assert len(final_notes) == 1
        assert final_notes[0]["pitch"] == 60

    def test_sse_events_emitted(self, store: StateStore, trace: TraceContext) -> None:

        _setup_region(store, "r1", "t1")
        txn = store.begin_transaction("add")
        store.add_notes("r1", [_note(60, 0.0), _note(72, 2.0)], transaction=txn)
        store.commit(txn)

        plan = build_checkout_plan(
            project_id="p1", target_variation_id="v1",
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            target_cc={}, working_cc={},
            target_pb={}, working_pb={},
            target_at={}, working_at={},
            track_regions={"r1": "t1"},
        )

        result = execute_checkout_plan(
            checkout_plan=plan, store=store, trace=trace, emit_sse=True,
        )
        assert len(result.events) > 0
        tool_types = [e["tool"] for e in result.events]
        assert ToolName.CLEAR_NOTES.value in tool_types
        assert ToolName.ADD_NOTES.value in tool_types


# ---------------------------------------------------------------------------
# 6.3 — Redo Execution
# ---------------------------------------------------------------------------


class TestRedoExecution:

    def test_redo_produces_same_plan_hash(self, store: StateStore, trace: TraceContext) -> None:

        plan1 = build_checkout_plan(
            project_id="p1", target_variation_id="v2",
            target_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={}, working_cc={},
            target_pb={}, working_pb={},
            target_at={}, working_at={},
            track_regions={"r1": "t1"},
        )
        plan2 = build_checkout_plan(
            project_id="p1", target_variation_id="v2",
            target_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={}, working_cc={},
            target_pb={}, working_pb={},
            target_at={}, working_at={},
            track_regions={"r1": "t1"},
        )
        assert plan1.plan_hash() == plan2.plan_hash()

    def test_redo_adds_missing_notes(self, store: StateStore, trace: TraceContext) -> None:

        _setup_region(store, "r1", "t1")
        txn = store.begin_transaction("initial")
        store.add_notes("r1", [_note(60, 0.0)], transaction=txn)
        store.commit(txn)

        plan = build_checkout_plan(
            project_id="p1", target_variation_id="v2",
            target_notes={"r1": [_note(60, 0.0), _note(72, 2.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={}, working_cc={},
            target_pb={}, working_pb={},
            target_at={}, working_at={},
            track_regions={"r1": "t1"},
        )

        result = execute_checkout_plan(
            checkout_plan=plan, store=store, trace=trace,
        )
        assert result.success
        final = store.get_region_notes("r1")
        assert len(final) == 2

    def test_controller_restore(self, store: StateStore, trace: TraceContext) -> None:

        _setup_region(store, "r1", "t1")
        txn = store.begin_transaction("initial")
        store.add_notes("r1", [_note(60, 0.0)], transaction=txn)
        store.commit(txn)

        plan = build_checkout_plan(
            project_id="p1", target_variation_id="v2",
            target_notes={"r1": [_note(60, 0.0)]},
            working_notes={"r1": [_note(60, 0.0)]},
            target_cc={"r1": [_cc(64, 0.0, 127)]},
            working_cc={"r1": []},
            target_pb={}, working_pb={},
            target_at={}, working_at={},
            track_regions={"r1": "t1"},
        )
        result = execute_checkout_plan(
            checkout_plan=plan, store=store, trace=trace,
        )
        assert result.success
        assert result.executed == 1


# ---------------------------------------------------------------------------
# 6.4 — Drift Block
# ---------------------------------------------------------------------------


class TestDriftBlock:

    @pytest.mark.anyio
    async def test_dirty_working_tree_blocks_checkout(self, async_session: AsyncSession) -> None:

        store = StateStore(conversation_id="cb-test", project_id="proj-cb")
        _setup_region(store, "region-1", "track-1")
        trace = TraceContext(trace_id="test-drift-block")

        var1 = _make_variation([_note(60, 0.0)])
        await muse_repository.save_variation(
            async_session, var1,
            project_id="proj-cb", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(async_session, var1.variation_id, commit_state_id="s1")
        await async_session.commit()

        txn = store.begin_transaction("user-edit")
        store.add_notes("region-1", [_note(60, 0.0), _note(72, 2.0)], transaction=txn)
        store.commit(txn)

        with pytest.raises(CheckoutBlockedError) as exc_info:
            await checkout_to_variation(
                session=async_session,
                project_id="proj-cb",
                target_variation_id=var1.variation_id,
                store=store,
                trace=trace,
                force=False,
            )
        assert "dirty" in str(exc_info.value).lower() or exc_info.value.total_changes > 0


# ---------------------------------------------------------------------------
# 6.5 — Force Override
# ---------------------------------------------------------------------------


class TestForceOverride:

    @pytest.mark.anyio
    async def test_force_bypasses_drift_check(self, async_session: AsyncSession) -> None:

        store = StateStore(conversation_id="force-test", project_id="proj-force")
        _setup_region(store, "region-1", "track-1")
        trace = TraceContext(trace_id="test-force")

        var1 = _make_variation([_note(60, 0.0)])
        await muse_repository.save_variation(
            async_session, var1,
            project_id="proj-force", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(async_session, var1.variation_id, commit_state_id="s1")
        await async_session.commit()

        txn = store.begin_transaction("user-edit")
        store.add_notes("region-1", [_note(60, 0.0), _note(72, 2.0)], transaction=txn)
        store.commit(txn)

        summary = await checkout_to_variation(
            session=async_session,
            project_id="proj-force",
            target_variation_id=var1.variation_id,
            store=store,
            trace=trace,
            force=True,
        )
        assert summary.head_moved
        assert summary.execution.failed == 0


# ---------------------------------------------------------------------------
# 6.6 — Boundary Seal
# ---------------------------------------------------------------------------


class TestCheckoutExecutorBoundary:

    def test_no_handler_imports(self) -> None:

        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_checkout_executor.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"maestro_handlers", "maestro_editing", "maestro_composing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_checkout_executor imports forbidden: {node.module}"
                    )

    def test_no_variation_service_import(self) -> None:

        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_checkout_executor.py"
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name != "VariationService", (
                        "muse_checkout_executor imports VariationService"
                    )

    def test_no_replay_internals_import(self) -> None:

        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_checkout_executor.py"
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "muse_replay" not in node.module, (
                    "muse_checkout_executor imports replay internals"
                )
