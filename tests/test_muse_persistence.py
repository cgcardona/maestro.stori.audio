"""Tests for Muse persistent variation storage, lineage, and replay.

Verifies:
- Variation → DB → domain model roundtrip fidelity.
- Commit-from-DB produces identical results to commit-from-memory.
- Lineage graph formation and HEAD tracking.
- Replay plan construction and determinism.
- muse_repository and muse_replay module boundary rules.
"""
from __future__ import annotations

import ast
import uuid

import pytest
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contracts.json_types import RegionMetadataWire
from app.db.database import Base
from app.db import muse_models  # noqa: F401 — register tables
from app.models.variation import (
    MidiNoteSnapshot,
    NoteChange,
    Phrase,
    Variation,
)
from app.services import muse_repository, muse_replay
from app.services.muse_repository import HistoryNode
from app.services.muse_replay import ReplayPlan


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an in-memory SQLite async session for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def _make_variation() -> Variation:
    """Build a realistic test variation."""
    vid = str(uuid.uuid4())
    pid1 = str(uuid.uuid4())
    pid2 = str(uuid.uuid4())

    note_added = NoteChange(
        note_id=str(uuid.uuid4()),
        change_type="added",
        before=None,
        after=MidiNoteSnapshot(
            pitch=60, start_beat=0.0, duration_beats=1.0, velocity=100, channel=0,
        ),
    )
    note_removed = NoteChange(
        note_id=str(uuid.uuid4()),
        change_type="removed",
        before=MidiNoteSnapshot(
            pitch=64, start_beat=2.0, duration_beats=0.5, velocity=80, channel=0,
        ),
        after=None,
    )
    note_modified = NoteChange(
        note_id=str(uuid.uuid4()),
        change_type="modified",
        before=MidiNoteSnapshot(
            pitch=67, start_beat=4.0, duration_beats=2.0, velocity=90, channel=0,
        ),
        after=MidiNoteSnapshot(
            pitch=67, start_beat=4.0, duration_beats=3.0, velocity=110, channel=0,
        ),
    )

    phrase1 = Phrase(
        phrase_id=pid1,
        track_id="track-1",
        region_id="region-1",
        start_beat=0.0,
        end_beat=4.0,
        label="Phrase A",
        note_changes=[note_added, note_removed],
        cc_events=[{"cc": 64, "beat": 0.0, "value": 127}],
        explanation="first phrase",
        tags=["intro"],
    )
    phrase2 = Phrase(
        phrase_id=pid2,
        track_id="track-2",
        region_id="region-2",
        start_beat=4.0,
        end_beat=8.0,
        label="Phrase B",
        note_changes=[note_modified],
        cc_events=[],
        explanation="second phrase",
        tags=["verse"],
    )

    return Variation(
        variation_id=vid,
        intent="test composition",
        ai_explanation="test explanation",
        affected_tracks=["track-1", "track-2"],
        affected_regions=["region-1", "region-2"],
        beat_range=(0.0, 8.0),
        phrases=[phrase1, phrase2],
    )


# ---------------------------------------------------------------------------
# 3.1 — Variation roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_variation_roundtrip(async_session: AsyncSession) -> None:

    """Persist a variation, reload it, assert equality."""
    original = _make_variation()
    region_metadata: dict[str, RegionMetadataWire] = {
        "region-1": {"startBeat": 0, "durationBeats": 16, "name": "Intro Region"},
        "region-2": {"startBeat": 16, "durationBeats": 16, "name": "Verse Region"},
    }

    await muse_repository.save_variation(
        async_session,
        original,
        project_id="proj-1",
        base_state_id="state-42",
        conversation_id="conv-1",
        region_metadata=region_metadata,
    )
    await async_session.commit()

    loaded = await muse_repository.load_variation(async_session, original.variation_id)
    assert loaded is not None

    assert loaded.variation_id == original.variation_id
    assert loaded.intent == original.intent
    assert loaded.ai_explanation == original.ai_explanation
    assert loaded.affected_tracks == original.affected_tracks
    assert loaded.affected_regions == original.affected_regions
    assert loaded.beat_range == original.beat_range
    assert len(loaded.phrases) == len(original.phrases)

    for orig_p, load_p in zip(original.phrases, loaded.phrases):
        assert load_p.phrase_id == orig_p.phrase_id
        assert load_p.track_id == orig_p.track_id
        assert load_p.region_id == orig_p.region_id
        assert load_p.start_beat == orig_p.start_beat
        assert load_p.end_beat == orig_p.end_beat
        assert load_p.label == orig_p.label
        assert load_p.explanation == orig_p.explanation
        assert load_p.tags == orig_p.tags
        assert load_p.cc_events == orig_p.cc_events
        assert load_p.pitch_bends == orig_p.pitch_bends
        assert load_p.aftertouch == orig_p.aftertouch
        assert len(load_p.note_changes) == len(orig_p.note_changes)

        for orig_nc, load_nc in zip(orig_p.note_changes, load_p.note_changes):
            assert load_nc.change_type == orig_nc.change_type
            if orig_nc.before:
                assert load_nc.before is not None
                assert load_nc.before.pitch == orig_nc.before.pitch
                assert load_nc.before.start_beat == orig_nc.before.start_beat
                assert load_nc.before.duration_beats == orig_nc.before.duration_beats
                assert load_nc.before.velocity == orig_nc.before.velocity
            else:
                assert load_nc.before is None
            if orig_nc.after:
                assert load_nc.after is not None
                assert load_nc.after.pitch == orig_nc.after.pitch
                assert load_nc.after.start_beat == orig_nc.after.start_beat
                assert load_nc.after.duration_beats == orig_nc.after.duration_beats
                assert load_nc.after.velocity == orig_nc.after.velocity
            else:
                assert load_nc.after is None


@pytest.mark.anyio
async def test_variation_status_lifecycle(async_session: AsyncSession) -> None:

    """Persist → mark committed → verify status transition."""
    var = _make_variation()
    await muse_repository.save_variation(
        async_session, var,
        project_id="p", base_state_id="s", conversation_id="c",
        region_metadata={},
    )
    await async_session.commit()

    status = await muse_repository.get_status(async_session, var.variation_id)
    assert status == "ready"

    await muse_repository.mark_committed(async_session, var.variation_id)
    await async_session.commit()

    status = await muse_repository.get_status(async_session, var.variation_id)
    assert status == "committed"


@pytest.mark.anyio
async def test_variation_discard(async_session: AsyncSession) -> None:

    """Persist → mark discarded → verify."""
    var = _make_variation()
    await muse_repository.save_variation(
        async_session, var,
        project_id="p", base_state_id="s", conversation_id="c",
        region_metadata={},
    )
    await async_session.commit()

    await muse_repository.mark_discarded(async_session, var.variation_id)
    await async_session.commit()

    status = await muse_repository.get_status(async_session, var.variation_id)
    assert status == "discarded"


@pytest.mark.anyio
async def test_load_nonexistent_returns_none(async_session: AsyncSession) -> None:

    """Load with unknown ID returns None."""
    result = await muse_repository.load_variation(async_session, "nonexistent-id")
    assert result is None


@pytest.mark.anyio
async def test_region_metadata_roundtrip(async_session: AsyncSession) -> None:

    """Region metadata stored on phrases is retrievable."""
    var = _make_variation()
    region_metadata: dict[str, RegionMetadataWire] = {
        "region-1": {"startBeat": 0, "durationBeats": 16, "name": "Intro"},
        "region-2": {"startBeat": 16, "durationBeats": 8, "name": "Verse"},
    }
    await muse_repository.save_variation(
        async_session, var,
        project_id="p", base_state_id="s", conversation_id="c",
        region_metadata=region_metadata,
    )
    await async_session.commit()

    loaded_meta = await muse_repository.get_region_metadata(
        async_session, var.variation_id,
    )
    assert "region-1" in loaded_meta
    assert loaded_meta["region-1"]["name"] == "Intro"
    assert loaded_meta["region-1"]["start_beat"] == 0
    assert loaded_meta["region-1"]["duration_beats"] == 16


@pytest.mark.anyio
async def test_phrase_ids_in_order(async_session: AsyncSession) -> None:

    """Phrase IDs returned in sequence order."""
    var = _make_variation()
    await muse_repository.save_variation(
        async_session, var,
        project_id="p", base_state_id="s", conversation_id="c",
        region_metadata={},
    )
    await async_session.commit()

    ids = await muse_repository.get_phrase_ids(async_session, var.variation_id)
    assert len(ids) == 2
    assert ids == [p.phrase_id for p in var.phrases]


# ---------------------------------------------------------------------------
# 3.2 — Commit replay safety
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_replay_from_db(async_session: AsyncSession) -> None:

    """Simulate memory loss: persist variation, reload, verify commit-ready data."""
    original = _make_variation()
    region_metadata: dict[str, RegionMetadataWire] = {
        "region-1": {"startBeat": 0, "durationBeats": 16, "name": "R1"},
        "region-2": {"startBeat": 16, "durationBeats": 16, "name": "R2"},
    }

    await muse_repository.save_variation(
        async_session, original,
        project_id="proj-1", base_state_id="state-42", conversation_id="c",
        region_metadata=region_metadata,
    )
    await async_session.commit()

    loaded = await muse_repository.load_variation(async_session, original.variation_id)
    assert loaded is not None

    base_state = await muse_repository.get_base_state_id(
        async_session, original.variation_id,
    )
    assert base_state == "state-42"

    phrase_ids = await muse_repository.get_phrase_ids(
        async_session, original.variation_id,
    )
    assert phrase_ids == [p.phrase_id for p in original.phrases]

    assert len(loaded.phrases) == len(original.phrases)
    for orig_p, loaded_p in zip(original.phrases, loaded.phrases):
        assert loaded_p.phrase_id == orig_p.phrase_id
        assert len(loaded_p.note_changes) == len(orig_p.note_changes)
        for orig_nc, load_nc in zip(orig_p.note_changes, loaded_p.note_changes):
            assert load_nc.change_type == orig_nc.change_type
            assert load_nc.before == orig_nc.before
            assert load_nc.after == orig_nc.after


# ---------------------------------------------------------------------------
# Phase 5 — Lineage graph tests
# ---------------------------------------------------------------------------


def _make_child_variation(parent_id: str, intent: str = "child") -> Variation:

    """Build a simple variation for lineage tests."""
    vid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    return Variation(
        variation_id=vid,
        intent=intent,
        ai_explanation=f"explanation for {intent}",
        affected_tracks=["track-1"],
        affected_regions=["region-1"],
        beat_range=(0.0, 4.0),
        phrases=[
            Phrase(
                phrase_id=pid,
                track_id="track-1",
                region_id="region-1",
                start_beat=0.0,
                end_beat=4.0,
                label=f"Phrase for {intent}",
                note_changes=[
                    NoteChange(
                        note_id=str(uuid.uuid4()),
                        change_type="added",
                        after=MidiNoteSnapshot(
                            pitch=60, start_beat=0.0, duration_beats=1.0,
                            velocity=100, channel=0,
                        ),
                    ),
                ],
            ),
        ],
    )


@pytest.mark.anyio
async def test_set_and_get_head(async_session: AsyncSession) -> None:

    """set_head marks a variation as HEAD, get_head retrieves it."""
    var = _make_variation()
    await muse_repository.save_variation(
        async_session, var,
        project_id="proj-head", base_state_id="s1", conversation_id="c",
        region_metadata={},
    )
    await async_session.commit()

    head = await muse_repository.get_head(async_session, "proj-head")
    assert head is None

    await muse_repository.set_head(
        async_session, var.variation_id, commit_state_id="state-99",
    )
    await async_session.commit()

    head = await muse_repository.get_head(async_session, "proj-head")
    assert head is not None
    assert head.variation_id == var.variation_id
    assert head.commit_state_id == "state-99"


@pytest.mark.anyio
async def test_set_head_clears_previous(async_session: AsyncSession) -> None:

    """Setting HEAD on one variation clears HEAD from another."""
    var_a = _make_variation()
    var_b = _make_child_variation(var_a.variation_id, "second")

    for var in [var_a, var_b]:
        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-swap", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
    await async_session.commit()

    await muse_repository.set_head(async_session, var_a.variation_id)
    await async_session.commit()

    head = await muse_repository.get_head(async_session, "proj-swap")
    assert head is not None
    assert head.variation_id == var_a.variation_id

    await muse_repository.set_head(async_session, var_b.variation_id)
    await async_session.commit()

    head = await muse_repository.get_head(async_session, "proj-swap")
    assert head is not None
    assert head.variation_id == var_b.variation_id


@pytest.mark.anyio
async def test_move_head(async_session: AsyncSession) -> None:

    """move_head moves HEAD pointer without any StateStore involvement."""
    var_a = _make_variation()
    var_b = _make_child_variation(var_a.variation_id, "b")

    for var in [var_a, var_b]:
        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-move", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
    await async_session.commit()

    await muse_repository.set_head(async_session, var_a.variation_id)
    await async_session.commit()

    await muse_repository.move_head(async_session, "proj-move", var_b.variation_id)
    await async_session.commit()

    head = await muse_repository.get_head(async_session, "proj-move")
    assert head is not None
    assert head.variation_id == var_b.variation_id


@pytest.mark.anyio
async def test_get_children(async_session: AsyncSession) -> None:

    """get_children returns child variations."""
    parent = _make_variation()
    child_a = _make_child_variation(parent.variation_id, "child-a")
    child_b = _make_child_variation(parent.variation_id, "child-b")

    await muse_repository.save_variation(
        async_session, parent,
        project_id="proj-c", base_state_id="s1", conversation_id="c",
        region_metadata={},
    )
    for child in [child_a, child_b]:
        await muse_repository.save_variation(
            async_session, child,
            project_id="proj-c", base_state_id="s1", conversation_id="c",
            region_metadata={},
            parent_variation_id=parent.variation_id,
        )
    await async_session.commit()

    children = await muse_repository.get_children(async_session, parent.variation_id)
    assert len(children) == 2
    child_ids = {c.variation_id for c in children}
    assert child_a.variation_id in child_ids
    assert child_b.variation_id in child_ids


@pytest.mark.anyio
async def test_get_lineage(async_session: AsyncSession) -> None:

    """get_lineage returns root-first path."""
    root = _make_variation()
    mid = _make_child_variation(root.variation_id, "mid")
    leaf = _make_child_variation(mid.variation_id, "leaf")

    await muse_repository.save_variation(
        async_session, root,
        project_id="proj-l", base_state_id="s1", conversation_id="c",
        region_metadata={},
    )
    await muse_repository.save_variation(
        async_session, mid,
        project_id="proj-l", base_state_id="s1", conversation_id="c",
        region_metadata={},
        parent_variation_id=root.variation_id,
    )
    await muse_repository.save_variation(
        async_session, leaf,
        project_id="proj-l", base_state_id="s1", conversation_id="c",
        region_metadata={},
        parent_variation_id=mid.variation_id,
    )
    await async_session.commit()

    lineage = await muse_repository.get_lineage(async_session, leaf.variation_id)
    assert len(lineage) == 3
    assert lineage[0].variation_id == root.variation_id
    assert lineage[1].variation_id == mid.variation_id
    assert lineage[2].variation_id == leaf.variation_id


# ---------------------------------------------------------------------------
# Phase 5 — Replay plan tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replay_plan_linear(async_session: AsyncSession) -> None:

    """build_replay_plan reconstructs A → B lineage correctly."""
    var_a = _make_variation()
    var_b = _make_child_variation(var_a.variation_id, "child-b")

    await muse_repository.save_variation(
        async_session, var_a,
        project_id="proj-rp", base_state_id="s1", conversation_id="c",
        region_metadata={},
    )
    await muse_repository.save_variation(
        async_session, var_b,
        project_id="proj-rp", base_state_id="s1", conversation_id="c",
        region_metadata={},
        parent_variation_id=var_a.variation_id,
    )
    await async_session.commit()

    plan = await muse_replay.build_replay_plan(
        async_session, "proj-rp", var_b.variation_id,
    )
    assert plan is not None
    assert plan.ordered_variation_ids == [var_a.variation_id, var_b.variation_id]
    assert len(plan.ordered_phrase_ids) == len(var_a.phrases) + len(var_b.phrases)
    assert len(plan.region_updates) >= 1


@pytest.mark.anyio
async def test_replay_plan_single_variation(async_session: AsyncSession) -> None:

    """Replay plan for a root variation (no parent) works correctly."""
    var = _make_variation()
    await muse_repository.save_variation(
        async_session, var,
        project_id="proj-single", base_state_id="s1", conversation_id="c",
        region_metadata={},
    )
    await async_session.commit()

    plan = await muse_replay.build_replay_plan(
        async_session, "proj-single", var.variation_id,
    )
    assert plan is not None
    assert plan.ordered_variation_ids == [var.variation_id]
    assert len(plan.ordered_phrase_ids) == len(var.phrases)


@pytest.mark.anyio
async def test_replay_plan_nonexistent_returns_none(async_session: AsyncSession) -> None:

    """Replay plan for nonexistent variation returns None."""
    plan = await muse_replay.build_replay_plan(
        async_session, "proj-x", "nonexistent",
    )
    assert plan is None


@pytest.mark.anyio
async def test_replay_preserves_phrase_ordering(async_session: AsyncSession) -> None:

    """Restart safety: persist, reload, build plan — phrase order is stable."""
    var_a = _make_variation()
    var_b = _make_child_variation(var_a.variation_id, "after-restart")

    await muse_repository.save_variation(
        async_session, var_a,
        project_id="proj-restart", base_state_id="s1", conversation_id="c",
        region_metadata={},
    )
    await muse_repository.save_variation(
        async_session, var_b,
        project_id="proj-restart", base_state_id="s1", conversation_id="c",
        region_metadata={},
        parent_variation_id=var_a.variation_id,
    )
    await async_session.commit()

    plan = await muse_replay.build_replay_plan(
        async_session, "proj-restart", var_b.variation_id,
    )
    assert plan is not None

    expected_phrases = [
        p.phrase_id for p in var_a.phrases
    ] + [
        p.phrase_id for p in var_b.phrases
    ]
    assert plan.ordered_phrase_ids == expected_phrases


# ---------------------------------------------------------------------------
# Boundary check — muse_repository must not import StateStore
# ---------------------------------------------------------------------------


def test_muse_repository_boundary() -> None:
    """muse_repository must not import StateStore or executor modules."""
    import importlib
    spec = importlib.util.find_spec("app.services.muse_repository")
    assert spec is not None and spec.origin is not None

    with open(spec.origin) as f:
        source = f.read()

    tree = ast.parse(source)
    forbidden = {"StateStore", "get_or_create_store", "EntityRegistry"}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            assert "state_store" not in module, (
                f"muse_repository imports state_store: {module}"
            )
            assert "executor" not in module, (
                f"muse_repository imports executor: {module}"
            )
            if hasattr(node, "names"):
                for alias in node.names:
                    assert alias.name not in forbidden, (
                        f"muse_repository imports forbidden name: {alias.name}"
                    )


# ---------------------------------------------------------------------------
# Boundary check — muse_replay must be pure data (Phase 5)
# ---------------------------------------------------------------------------


def test_muse_replay_boundary() -> None:
    """muse_replay must not import StateStore, executor, or LLM handlers."""
    import importlib
    spec = importlib.util.find_spec("app.services.muse_replay")
    assert spec is not None and spec.origin is not None

    with open(spec.origin) as f:
        source = f.read()

    tree = ast.parse(source)
    forbidden_modules = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
    forbidden_names = {"StateStore", "get_or_create_store", "EntityRegistry"}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            for fb in forbidden_modules:
                assert fb not in module, (
                    f"muse_replay imports forbidden module: {module}"
                )
            if hasattr(node, "names"):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"muse_replay imports forbidden name: {alias.name}"
                    )
