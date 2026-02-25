"""Tests for Muse Log Graph Serialization (Phase 13).

Verifies:
- Linear history ordering.
- Branch + merge DAG with parent2 and HEAD.
- Deterministic JSON output.
- Boundary seal (AST).
"""
from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from collections.abc import AsyncGenerator
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
from app.services.muse_log_graph import (
    MuseLogGraph,
    MuseLogNode,
    build_muse_log_graph,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


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


def _note(pitch: int, start: float) -> dict[str, Any]:

    return {"pitch": pitch, "start_beat": start, "duration_beats": 1.0, "velocity": 100, "channel": 0}


def _make_variation(
    notes: list[dict[str, Any]],
    region_id: str = "region-1",
    track_id: str = "track-1",
    intent: str = "test",
) -> Variation:
    vid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    return Variation(
        variation_id=vid,
        intent=intent,
        ai_explanation=intent,
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
                label=intent,
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


async def _save(
    session: AsyncSession,
    var: Variation,
    project_id: str,
    parent: str | None = None,
    parent2: str | None = None,
    is_head: bool = False,
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
    if is_head:
        await muse_repository.set_head(session, var.variation_id)
    return var.variation_id


# ---------------------------------------------------------------------------
# 6.1 — Linear History
# ---------------------------------------------------------------------------


class TestLinearHistory:

    @pytest.mark.anyio
    async def test_linear_order_preserved(self, async_session: AsyncSession) -> None:

        """C0 -> C1 -> C2  — nodes must appear in that order."""
        c0 = _make_variation([_note(60, 0.0)], intent="init")
        c0_id = await _save(async_session, c0, "proj-lin")

        c1 = _make_variation([_note(64, 2.0)], intent="add chord")
        c1_id = await _save(async_session, c1, "proj-lin", parent=c0_id)

        c2 = _make_variation([_note(67, 4.0)], intent="add melody")
        c2_id = await _save(async_session, c2, "proj-lin", parent=c1_id, is_head=True)

        await async_session.commit()

        graph = await build_muse_log_graph(async_session, "proj-lin")

        assert len(graph.nodes) == 3
        ids = [n.variation_id for n in graph.nodes]
        assert ids == [c0_id, c1_id, c2_id]

    @pytest.mark.anyio
    async def test_head_detected(self, async_session: AsyncSession) -> None:

        c0 = _make_variation([_note(60, 0.0)])
        c0_id = await _save(async_session, c0, "proj-head", is_head=True)
        await async_session.commit()

        graph = await build_muse_log_graph(async_session, "proj-head")
        assert graph.head == c0_id
        assert graph.nodes[0].is_head

    @pytest.mark.anyio
    async def test_empty_project(self, async_session: AsyncSession) -> None:

        graph = await build_muse_log_graph(async_session, "proj-empty")
        assert graph.head is None
        assert len(graph.nodes) == 0

    @pytest.mark.anyio
    async def test_parent_field_set(self, async_session: AsyncSession) -> None:

        c0 = _make_variation([_note(60, 0.0)])
        c0_id = await _save(async_session, c0, "proj-par")
        c1 = _make_variation([_note(64, 2.0)])
        c1_id = await _save(async_session, c1, "proj-par", parent=c0_id)
        await async_session.commit()

        graph = await build_muse_log_graph(async_session, "proj-par")
        assert graph.nodes[0].parent is None
        assert graph.nodes[1].parent == c0_id


# ---------------------------------------------------------------------------
# 6.2 — Branch + Merge Graph
# ---------------------------------------------------------------------------


class TestBranchMergeGraph:

    @pytest.mark.anyio
    async def test_merge_parent2_serialized(self, async_session: AsyncSession) -> None:

        """
        C0
        ├── C1 (bass)
        ├── C2 (piano)
        └── C3 merge(C1,C2) ← HEAD
        """
        c0 = _make_variation([_note(60, 0.0)], intent="root")
        c0_id = await _save(async_session, c0, "proj-merge")

        c1 = _make_variation([_note(36, 0.0)], region_id="r-bass", intent="add bass")
        c1_id = await _save(async_session, c1, "proj-merge", parent=c0_id)

        c2 = _make_variation([_note(72, 0.0)], region_id="r-piano", intent="add piano")
        c2_id = await _save(async_session, c2, "proj-merge", parent=c0_id)

        c3 = _make_variation([], intent="merge")
        c3_id = await _save(
            async_session, c3, "proj-merge",
            parent=c1_id, parent2=c2_id, is_head=True,
        )
        await async_session.commit()

        graph = await build_muse_log_graph(async_session, "proj-merge")

        assert len(graph.nodes) == 4
        assert graph.head == c3_id

        merge_node = [n for n in graph.nodes if n.variation_id == c3_id][0]
        assert merge_node.parent == c1_id
        assert merge_node.parent2 == c2_id
        assert merge_node.is_head

        c0_idx = next(i for i, n in enumerate(graph.nodes) if n.variation_id == c0_id)
        c1_idx = next(i for i, n in enumerate(graph.nodes) if n.variation_id == c1_id)
        c2_idx = next(i for i, n in enumerate(graph.nodes) if n.variation_id == c2_id)
        c3_idx = next(i for i, n in enumerate(graph.nodes) if n.variation_id == c3_id)
        assert c0_idx < c1_idx
        assert c0_idx < c2_idx
        assert c1_idx < c3_idx
        assert c2_idx < c3_idx

    @pytest.mark.anyio
    async def test_regions_extracted(self, async_session: AsyncSession) -> None:

        c0 = _make_variation([_note(60, 0.0)], region_id="r-drums", intent="drums")
        await _save(async_session, c0, "proj-reg")
        await async_session.commit()

        graph = await build_muse_log_graph(async_session, "proj-reg")
        assert graph.nodes[0].affected_regions == ("r-drums",)


# ---------------------------------------------------------------------------
# 6.3 — Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:

    @pytest.mark.anyio
    async def test_repeated_calls_identical_json(self, async_session: AsyncSession) -> None:

        c0 = _make_variation([_note(60, 0.0)], intent="root")
        c0_id = await _save(async_session, c0, "proj-det")

        c1 = _make_variation([_note(64, 2.0)], intent="branch-a")
        await _save(async_session, c1, "proj-det", parent=c0_id)

        c2 = _make_variation([_note(67, 4.0)], intent="branch-b")
        await _save(async_session, c2, "proj-det", parent=c0_id)

        await async_session.commit()

        g1 = await build_muse_log_graph(async_session, "proj-det")
        g2 = await build_muse_log_graph(async_session, "proj-det")

        j1 = json.dumps(g1.to_dict(), sort_keys=True)
        j2 = json.dumps(g2.to_dict(), sort_keys=True)
        assert j1 == j2

    @pytest.mark.anyio
    async def test_to_dict_field_names(self, async_session: AsyncSession) -> None:

        c0 = _make_variation([_note(60, 0.0)], intent="init")
        await _save(async_session, c0, "proj-fields", is_head=True)
        await async_session.commit()

        graph = await build_muse_log_graph(async_session, "proj-fields")
        d = graph.to_dict()

        assert "projectId" in d
        assert "head" in d
        assert "nodes" in d

        node = d["nodes"][0]
        assert "id" in node
        assert "parent" in node
        assert "parent2" in node
        assert "isHead" in node
        assert "timestamp" in node
        assert "intent" in node
        assert "regions" in node


# ---------------------------------------------------------------------------
# 6.4 — Boundary Seal
# ---------------------------------------------------------------------------


class TestLogGraphBoundary:

    def test_no_state_store_import(self) -> None:

        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_log_graph.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {
            "state_store", "executor", "maestro_handlers", "maestro_editing",
            "muse_drift", "muse_merge", "muse_checkout", "muse_replay",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_log_graph imports forbidden module: {node.module}"
                    )

    def test_no_forbidden_names(self) -> None:

        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "muse_log_graph.py"
        tree = ast.parse(filepath.read_text())
        forbidden_names = {"StateStore", "get_or_create_store", "VariationService"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"muse_log_graph imports forbidden name: {alias.name}"
                    )
