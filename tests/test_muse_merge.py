"""Tests for Muse Merge Engine (Phase 12).

Verifies:
- Merge base detection.
- Auto merge (non-overlapping regions).
- Conflict detection (same note modified on both sides).
- Merge checkout plan determinism.
- Merge commit graph (two parents).
- Boundary seal (AST).
"""

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.db import muse_models  # noqa: F401
from app.models.variation import (
    MidiNoteSnapshot,
    NoteChange,
    Phrase,
    Variation,
)
from app.services import muse_repository
from app.services.muse_merge import (
    MergeConflict,
    MergeResult,
    ThreeWaySnapshot,
    build_merge_result,
    build_merge_checkout_plan,
)
from app.services.muse_merge_base import find_merge_base
from app.services.muse_replay import HeadSnapshot


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────


def _note(pitch: int, start: float, dur: float = 1.0, vel: int = 100) -> dict[str, Any]:
    return {"pitch": pitch, "start_beat": start, "duration_beats": dur, "velocity": vel, "channel": 0}


def _cc(cc_num: int, beat: float, value: int) -> dict[str, Any]:
    return {"kind": "cc", "cc": cc_num, "beat": beat, "value": value}


def _snap(
    vid: str,
    notes: dict[str, list[dict[str, Any]]] | None = None,
    cc: dict[str, list[dict[str, Any]]] | None = None,
    pb: dict[str, list[dict[str, Any]]] | None = None,
    at: dict[str, list[dict[str, Any]]] | None = None,
    track_regions: dict[str, str] | None = None,
) -> HeadSnapshot:
    return HeadSnapshot(
        variation_id=vid,
        notes=notes or {},
        cc=cc or {},
        pitch_bends=pb or {},
        aftertouch=at or {},
        track_regions=track_regions or {},
        region_start_beats={},
    )


def _make_variation(
    notes: list[dict[str, Any]],
    controllers: list[dict[str, Any]] | None = None,
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
                controller_changes=controllers or [],
            ),
        ],
    )


async def _save(
    session: AsyncSession,
    var: Variation,
    project_id: str,
    parent: str | None = None,
    parent2: str | None = None,
) -> str:
    await muse_repository.save_variation(
        session, var,
        project_id=project_id,
        base_state_id="s1",
        conversation_id="c",
        region_metadata={},
        parent_variation_id=parent,
        parent2_variation_id=parent2,
    )
    return var.variation_id


# ---------------------------------------------------------------------------
# 9.1 — Merge Base Detection
# ---------------------------------------------------------------------------


class TestMergeBase:

    @pytest.mark.anyio
    async def test_two_branches_find_common_root(self, async_session: AsyncSession):
        """
        root ─── left_branch
          └───── right_branch
        merge_base(left, right) = root
        """
        root = _make_variation([_note(60, 0.0)])
        root_id = await _save(async_session, root, "proj-1")

        left = _make_variation([_note(60, 0.0), _note(64, 2.0)])
        left_id = await _save(async_session, left, "proj-1", parent=root_id)

        right = _make_variation([_note(60, 0.0), _note(67, 4.0)])
        right_id = await _save(async_session, right, "proj-1", parent=root_id)

        await async_session.commit()

        base = await find_merge_base(async_session, left_id, right_id)
        assert base == root_id

    @pytest.mark.anyio
    async def test_deeper_branch_finds_correct_ancestor(self, async_session: AsyncSession):
        """
        A ── B ── left
          └────── right
        merge_base(left, right) = A
        """
        a = _make_variation([_note(60, 0.0)])
        a_id = await _save(async_session, a, "proj-2")

        b = _make_variation([_note(60, 0.0), _note(62, 1.0)])
        b_id = await _save(async_session, b, "proj-2", parent=a_id)

        left = _make_variation([_note(60, 0.0), _note(62, 1.0), _note(64, 2.0)])
        left_id = await _save(async_session, left, "proj-2", parent=b_id)

        right = _make_variation([_note(60, 0.0), _note(67, 4.0)])
        right_id = await _save(async_session, right, "proj-2", parent=a_id)

        await async_session.commit()

        base = await find_merge_base(async_session, left_id, right_id)
        assert base == a_id

    @pytest.mark.anyio
    async def test_no_common_ancestor_returns_none(self, async_session: AsyncSession):
        a = _make_variation([_note(60, 0.0)])
        a_id = await _save(async_session, a, "proj-3a")

        b = _make_variation([_note(67, 0.0)])
        b_id = await _save(async_session, b, "proj-3b")

        await async_session.commit()

        base = await find_merge_base(async_session, a_id, b_id)
        assert base is None


# ---------------------------------------------------------------------------
# 9.2 — Auto Merge (Non-overlapping Regions)
# ---------------------------------------------------------------------------


class TestAutoMerge:

    def test_disjoint_regions_merge_cleanly(self):
        base = _snap("base", notes={"r1": [_note(60, 0.0)]})
        left = _snap("left", notes={"r1": [_note(60, 0.0)], "r2": [_note(72, 0.0)]})
        right = _snap("right", notes={"r1": [_note(60, 0.0)], "r3": [_note(48, 0.0)]})

        result = build_merge_result(base=base, left=left, right=right)
        assert not result.has_conflicts
        assert result.merged_snapshot is not None
        assert "r1" in result.merged_snapshot.notes
        assert "r2" in result.merged_snapshot.notes
        assert "r3" in result.merged_snapshot.notes

    def test_one_side_modifies_other_untouched(self):
        base = _snap("base", notes={"r1": [_note(60, 0.0)]})
        left = _snap("left", notes={"r1": [_note(60, 0.0), _note(64, 2.0)]})
        right = _snap("right", notes={"r1": [_note(60, 0.0)]})

        result = build_merge_result(base=base, left=left, right=right)
        assert not result.has_conflicts
        merged = result.merged_snapshot
        assert merged is not None
        assert len(merged.notes["r1"]) == 2

    def test_both_add_to_different_beats(self):
        base = _snap("base", notes={"r1": [_note(60, 0.0)]})
        left = _snap("left", notes={"r1": [_note(60, 0.0), _note(64, 2.0)]})
        right = _snap("right", notes={"r1": [_note(60, 0.0), _note(67, 4.0)]})

        result = build_merge_result(base=base, left=left, right=right)
        assert not result.has_conflicts
        merged = result.merged_snapshot
        assert merged is not None
        assert len(merged.notes["r1"]) == 3

    def test_controller_merge_from_one_side(self):
        base = _snap("base", notes={"r1": [_note(60, 0.0)]})
        left = _snap("left",
                      notes={"r1": [_note(60, 0.0)]},
                      cc={"r1": [_cc(64, 0.0, 127)]})
        right = _snap("right", notes={"r1": [_note(60, 0.0)]})

        result = build_merge_result(base=base, left=left, right=right)
        assert not result.has_conflicts
        merged = result.merged_snapshot
        assert merged is not None
        assert len(merged.cc.get("r1", [])) == 1


# ---------------------------------------------------------------------------
# 9.3 — Conflict Detection
# ---------------------------------------------------------------------------


class TestConflictDetection:

    def test_same_note_modified_both_sides(self):
        base = _snap("base", notes={"r1": [_note(60, 0.0)]})
        left = _snap("left", notes={"r1": [_note(60, 0.0, vel=50)]})
        right = _snap("right", notes={"r1": [_note(60, 0.0, vel=80)]})

        result = build_merge_result(base=base, left=left, right=right)
        assert result.has_conflicts
        assert len(result.conflicts) >= 1
        assert result.conflicts[0].type == "note"
        assert result.merged_snapshot is None

    def test_one_removed_other_modified(self):
        base = _snap("base", notes={"r1": [_note(60, 0.0), _note(64, 2.0)]})
        left = _snap("left", notes={"r1": [_note(64, 2.0)]})
        right = _snap("right", notes={"r1": [_note(60, 0.0, vel=50), _note(64, 2.0)]})

        result = build_merge_result(base=base, left=left, right=right)
        assert result.has_conflicts
        assert any("removed" in c.description.lower() or "modified" in c.description.lower()
                    for c in result.conflicts)

    def test_controller_conflict(self):
        base = _snap("base",
                      notes={"r1": [_note(60, 0.0)]},
                      cc={"r1": [_cc(64, 0.0, 100)]})
        left = _snap("left",
                      notes={"r1": [_note(60, 0.0)]},
                      cc={"r1": [_cc(64, 0.0, 50)]})
        right = _snap("right",
                       notes={"r1": [_note(60, 0.0)]},
                       cc={"r1": [_cc(64, 0.0, 80)]})

        result = build_merge_result(base=base, left=left, right=right)
        assert result.has_conflicts
        assert any(c.type == "cc" for c in result.conflicts)

    def test_no_conflicts_both_unchanged(self):
        base = _snap("base", notes={"r1": [_note(60, 0.0)]})
        left = _snap("left", notes={"r1": [_note(60, 0.0)]})
        right = _snap("right", notes={"r1": [_note(60, 0.0)]})

        result = build_merge_result(base=base, left=left, right=right)
        assert not result.has_conflicts
        assert result.merged_snapshot is not None


# ---------------------------------------------------------------------------
# 9.4 — Merge CheckoutPlan Determinism
# ---------------------------------------------------------------------------


class TestMergeDeterminism:

    def test_same_merge_produces_same_hash(self):
        base = _snap("base", notes={"r1": [_note(60, 0.0)]})
        left = _snap("left", notes={"r1": [_note(60, 0.0), _note(64, 2.0)]})
        right = _snap("right", notes={"r1": [_note(60, 0.0)]})

        r1 = build_merge_result(base=base, left=left, right=right)
        r2 = build_merge_result(base=base, left=left, right=right)

        assert r1.merged_snapshot is not None
        assert r2.merged_snapshot is not None
        assert r1.merged_snapshot.notes == r2.merged_snapshot.notes

    @pytest.mark.anyio
    async def test_merge_plan_deterministic(self, async_session: AsyncSession):
        root = _make_variation([_note(60, 0.0)])
        root_id = await _save(async_session, root, "proj-det")

        left = _make_variation([_note(60, 0.0), _note(64, 2.0)])
        left_id = await _save(async_session, left, "proj-det", parent=root_id)

        right = _make_variation([_note(60, 0.0), _note(67, 4.0)])
        right_id = await _save(async_session, right, "proj-det", parent=root_id)

        await async_session.commit()

        plan1 = await build_merge_checkout_plan(
            async_session, "proj-det", left_id, right_id,
        )
        plan2 = await build_merge_checkout_plan(
            async_session, "proj-det", left_id, right_id,
        )

        assert not plan1.is_conflict
        assert not plan2.is_conflict
        assert plan1.checkout_plan is not None
        assert plan2.checkout_plan is not None
        assert plan1.checkout_plan.plan_hash() == plan2.checkout_plan.plan_hash()


# ---------------------------------------------------------------------------
# 9.5 — Merge Commit Graph (Two Parents)
# ---------------------------------------------------------------------------


class TestMergeCommitGraph:

    @pytest.mark.anyio
    async def test_merge_commit_has_two_parents(self, async_session: AsyncSession):
        root = _make_variation([_note(60, 0.0)])
        root_id = await _save(async_session, root, "proj-graph")

        left = _make_variation([_note(60, 0.0), _note(64, 2.0)])
        left_id = await _save(async_session, left, "proj-graph", parent=root_id)

        right = _make_variation([_note(60, 0.0), _note(67, 4.0)])
        right_id = await _save(async_session, right, "proj-graph", parent=root_id)

        merge = _make_variation([])
        merge_id = await _save(
            async_session, merge, "proj-graph",
            parent=left_id, parent2=right_id,
        )

        await async_session.commit()

        from sqlalchemy import select
        from app.db import muse_models as db
        stmt = select(db.Variation).where(db.Variation.variation_id == merge_id)
        result = await async_session.execute(stmt)
        row = result.scalar_one()

        assert row.parent_variation_id == left_id
        assert row.parent2_variation_id == right_id

    @pytest.mark.anyio
    async def test_parent2_nullable_for_non_merge(self, async_session: AsyncSession):
        var = _make_variation([_note(60, 0.0)])
        vid = await _save(async_session, var, "proj-null")
        await async_session.commit()

        from sqlalchemy import select
        from app.db import muse_models as db
        stmt = select(db.Variation).where(db.Variation.variation_id == vid)
        result = await async_session.execute(stmt)
        row = result.scalar_one()

        assert row.parent2_variation_id is None


# ---------------------------------------------------------------------------
# 9.6 — Boundary Seal
# ---------------------------------------------------------------------------


class TestMergeBoundary:

    def test_merge_no_state_store_import(self):
        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_merge.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"state_store", "executor", "maestro_handlers", "maestro_editing", "mcp"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_merge imports forbidden module: {node.module}"
                    )

    def test_merge_base_no_state_store_import(self):
        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_merge_base.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"state_store", "executor", "maestro_handlers", "mcp"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_merge_base imports forbidden module: {node.module}"
                    )

    def test_merge_no_forbidden_names(self):
        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_merge.py"
        tree = ast.parse(filepath.read_text())
        forbidden_names = {"StateStore", "get_or_create_store", "VariationService"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"muse_merge imports forbidden name: {alias.name}"
                    )
