"""Tests for Muse persistent variation storage — roundtrip + commit replay.

Verifies:
- Variation → DB → domain model roundtrip fidelity.
- Commit-from-DB produces identical results to commit-from-memory.
- muse_repository module respects boundary rules.
"""

import ast
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.db import muse_models  # noqa: F401 — register tables
from app.models.variation import (
    MidiNoteSnapshot,
    NoteChange,
    Phrase,
    Variation,
)
from app.services import muse_repository


@pytest.fixture
async def async_session():
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
        controller_changes=[{"cc": 64, "beat": 0.0, "value": 127}],
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
        controller_changes=[],
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
async def test_variation_roundtrip(async_session: AsyncSession):
    """Persist a variation, reload it, assert equality."""
    original = _make_variation()
    region_metadata = {
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
        assert load_p.controller_changes == orig_p.controller_changes
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
async def test_variation_status_lifecycle(async_session: AsyncSession):
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
async def test_variation_discard(async_session: AsyncSession):
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
async def test_load_nonexistent_returns_none(async_session: AsyncSession):
    """Load with unknown ID returns None."""
    result = await muse_repository.load_variation(async_session, "nonexistent-id")
    assert result is None


@pytest.mark.anyio
async def test_region_metadata_roundtrip(async_session: AsyncSession):
    """Region metadata stored on phrases is retrievable."""
    var = _make_variation()
    region_metadata = {
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
async def test_phrase_ids_in_order(async_session: AsyncSession):
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
async def test_commit_replay_from_db(async_session: AsyncSession):
    """Simulate memory loss: persist variation, reload, verify commit-ready data."""
    original = _make_variation()
    region_metadata = {
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
# Boundary check — muse_repository must not import StateStore
# ---------------------------------------------------------------------------


def test_muse_repository_boundary():
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
