"""Tests for ``muse emotion-diff`` — CLI interface, service logic, and output formats.

Covers:
- Emotion vector lookup from canonical table.
- Inference from commit metadata (tempo-based).
- Per-dimension delta computation and drift distance.
- Narrative generation across drift magnitudes.
- Explicit-tag sourcing, inferred sourcing, and mixed sourcing.
- CLI text and JSON output formats.
- Edge cases: same commit, no metadata, unknown labels, missing commits.
- Commit ref resolution: HEAD, HEAD~N, abbreviated hash.
- --track and --section flag handling (stub boundary).

Naming convention: ``test_<behaviour>_<scenario>``
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from maestro.db.database import Base
import maestro.muse_cli.models  # noqa: F401  — registers MuseCli* with Base.metadata
from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.emotion_diff import (
    _emotion_diff_async,
    render_json,
    render_text,
)
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot, MuseCliTag
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_emotion_diff import (
    EMOTION_DIMENSIONS,
    EMOTION_VECTORS,
    EmotionDiffResult,
    EmotionDimDelta,
    EmotionVector,
    build_narrative,
    compute_dimension_deltas,
    compute_emotion_diff,
    get_emotion_tag,
    infer_vector_from_metadata,
    resolve_commit_id,
    vector_from_label,
)

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_REPO_ID = "test-repo-emotion-001"
_BRANCH = "main"
_COUNTER = 0


def _unique_hash(prefix: str) -> str:
    global _COUNTER
    _COUNTER += 1
    raw = f"{prefix}-{_COUNTER}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_snapshot(manifest: dict[str, str] | None = None) -> MuseCliSnapshot:
    m = manifest or {"placeholder.mid": _unique_hash("blob")}
    parts = sorted(f"{k}:{v}" for k, v in m.items())
    snap_id = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return MuseCliSnapshot(snapshot_id=snap_id, manifest=m)


def _make_commit(
    snapshot: MuseCliSnapshot,
    branch: str = _BRANCH,
    parent: MuseCliCommit | None = None,
    seq: int = 0,
    metadata: dict[str, object] | None = None,
) -> MuseCliCommit:
    cid = _unique_hash(f"commit-{branch}-{seq}")
    # Use epoch offset to avoid day-out-of-range when seq is large
    base = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    committed_at = base + datetime.timedelta(hours=seq)
    return MuseCliCommit(
        commit_id=cid,
        repo_id=_REPO_ID,
        branch=branch,
        snapshot_id=snapshot.snapshot_id,
        parent_commit_id=parent.commit_id if parent else None,
        message=f"commit seq={seq}",
        author="test",
        committed_at=committed_at,
        commit_metadata=metadata,
    )


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with the full Maestro schema."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    """Create a minimal .muse/ layout."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    return rid


def _write_head_ref(root: pathlib.Path, commit_id: str, branch: str = "main") -> None:
    muse = root / ".muse"
    (muse / "refs" / "heads" / branch).write_text(commit_id)


# ---------------------------------------------------------------------------
# Unit — EmotionVector
# ---------------------------------------------------------------------------


def test_emotion_vector_drift_from_identical_is_zero() -> None:
    """Drift from a vector to itself is exactly 0.0."""
    vec = EmotionVector(energy=0.5, valence=0.5, tension=0.5, darkness=0.5)
    assert vec.drift_from(vec) == 0.0


def test_emotion_vector_drift_from_opposite_is_max() -> None:
    """Drift between (0,0,0,0) and (1,1,1,1) is 2.0."""
    lo = EmotionVector(energy=0.0, valence=0.0, tension=0.0, darkness=0.0)
    hi = EmotionVector(energy=1.0, valence=1.0, tension=1.0, darkness=1.0)
    assert abs(hi.drift_from(lo) - 2.0) < 0.001


def test_emotion_vector_as_tuple_order() -> None:
    """as_tuple() returns (energy, valence, tension, darkness) order."""
    vec = EmotionVector(energy=0.1, valence=0.2, tension=0.3, darkness=0.4)
    assert vec.as_tuple() == (0.1, 0.2, 0.3, 0.4)


# ---------------------------------------------------------------------------
# Unit — vector_from_label
# ---------------------------------------------------------------------------


def test_vector_from_label_known_label_returns_vector() -> None:
    """Known emotion labels return non-None EmotionVector."""
    for label in EMOTION_VECTORS:
        result = vector_from_label(label)
        assert result is not None, f"Expected vector for {label!r}"
        assert isinstance(result, EmotionVector)


def test_vector_from_label_unknown_returns_none() -> None:
    """Unknown emotion labels return None."""
    assert vector_from_label("nonexistent_emotion") is None


def test_vector_from_label_case_insensitive() -> None:
    """Label lookup is case-insensitive."""
    assert vector_from_label("JOYFUL") == vector_from_label("joyful")


def test_vector_from_label_joyful_high_valence() -> None:
    """Joyful vector has valence > 0.8."""
    vec = vector_from_label("joyful")
    assert vec is not None
    assert vec.valence > 0.8


def test_vector_from_label_melancholic_low_energy() -> None:
    """Melancholic vector has energy < 0.5."""
    vec = vector_from_label("melancholic")
    assert vec is not None
    assert vec.energy < 0.5


def test_vector_from_label_tense_high_tension() -> None:
    """Tense vector has tension > 0.7."""
    vec = vector_from_label("tense")
    assert vec is not None
    assert vec.tension > 0.7


# ---------------------------------------------------------------------------
# Unit — infer_vector_from_metadata
# ---------------------------------------------------------------------------


def test_infer_vector_from_metadata_none_returns_neutral() -> None:
    """None metadata returns a neutral midpoint vector."""
    vec = infer_vector_from_metadata(None)
    assert vec.energy == 0.50
    assert vec.valence == 0.50
    assert vec.tension == 0.50
    assert vec.darkness == 0.50


def test_infer_vector_from_metadata_no_tempo_returns_neutral() -> None:
    """Metadata without tempo_bpm returns a neutral vector."""
    vec = infer_vector_from_metadata({"key": "Am"})
    assert vec.energy == 0.50


def test_infer_vector_from_metadata_fast_tempo_high_energy() -> None:
    """Fast tempo (180 BPM) yields high energy."""
    vec = infer_vector_from_metadata({"tempo_bpm": 180.0})
    assert vec.energy > 0.8


def test_infer_vector_from_metadata_slow_tempo_low_energy() -> None:
    """Slow tempo (60 BPM) yields low energy."""
    vec = infer_vector_from_metadata({"tempo_bpm": 60.0})
    assert vec.energy == 0.0


def test_infer_vector_from_metadata_tempo_not_numeric_returns_neutral() -> None:
    """Non-numeric tempo_bpm falls back to neutral."""
    vec = infer_vector_from_metadata({"tempo_bpm": "allegro"})
    assert vec.energy == 0.50


def test_infer_vector_dimensions_in_range() -> None:
    """All inferred dimensions are in [0.0, 1.0] for any reasonable tempo."""
    for bpm in [40, 60, 80, 100, 120, 140, 160, 180, 200]:
        vec = infer_vector_from_metadata({"tempo_bpm": float(bpm)})
        for dim_val in vec.as_tuple():
            assert 0.0 <= dim_val <= 1.0, f"Dimension out of range at {bpm} BPM"


# ---------------------------------------------------------------------------
# Unit — compute_dimension_deltas
# ---------------------------------------------------------------------------


def test_compute_dimension_deltas_correct_order() -> None:
    """Deltas are returned in EMOTION_DIMENSIONS order."""
    a = EmotionVector(energy=0.3, valence=0.3, tension=0.4, darkness=0.6)
    b = EmotionVector(energy=0.8, valence=0.9, tension=0.2, darkness=0.1)
    deltas = compute_dimension_deltas(a, b)
    assert len(deltas) == 4
    assert tuple(d.dimension for d in deltas) == EMOTION_DIMENSIONS


def test_compute_dimension_deltas_positive_delta() -> None:
    """Delta is positive when commit B > commit A."""
    a = EmotionVector(energy=0.3, valence=0.3, tension=0.4, darkness=0.6)
    b = EmotionVector(energy=0.8, valence=0.9, tension=0.2, darkness=0.1)
    deltas = compute_dimension_deltas(a, b)
    energy_delta = next(d for d in deltas if d.dimension == "energy")
    assert energy_delta.delta > 0


def test_compute_dimension_deltas_negative_delta() -> None:
    """Delta is negative when commit B < commit A."""
    a = EmotionVector(energy=0.3, valence=0.3, tension=0.4, darkness=0.6)
    b = EmotionVector(energy=0.8, valence=0.9, tension=0.2, darkness=0.1)
    deltas = compute_dimension_deltas(a, b)
    darkness_delta = next(d for d in deltas if d.dimension == "darkness")
    assert darkness_delta.delta < 0


def test_compute_dimension_deltas_zero_when_same() -> None:
    """Delta is 0 when both commits have the same vector."""
    vec = EmotionVector(energy=0.5, valence=0.5, tension=0.5, darkness=0.5)
    deltas = compute_dimension_deltas(vec, vec)
    for d in deltas:
        assert d.delta == 0.0


# ---------------------------------------------------------------------------
# Unit — build_narrative
# ---------------------------------------------------------------------------


def test_build_narrative_minimal_drift() -> None:
    """Drift < 0.05 → 'unchanged' in narrative."""
    dims = compute_dimension_deltas(
        EmotionVector(0.5, 0.5, 0.5, 0.5),
        EmotionVector(0.5, 0.5, 0.5, 0.5),
    )
    narrative = build_narrative("joyful", "joyful", dims, 0.01, "explicit_tags")
    assert "unchanged" in narrative.lower()


def test_build_narrative_major_drift() -> None:
    """Drift > 0.8 → 'major' or 'dramatic' in narrative."""
    dims = compute_dimension_deltas(
        EmotionVector(0.0, 0.0, 0.0, 0.0),
        EmotionVector(1.0, 1.0, 1.0, 1.0),
    )
    narrative = build_narrative(None, None, dims, 1.5, "inferred")
    assert "major" in narrative.lower() or "dramatic" in narrative.lower()


def test_build_narrative_includes_label_transition() -> None:
    """Narrative includes label → label transition when both labels known."""
    dims = compute_dimension_deltas(
        EmotionVector(0.3, 0.3, 0.4, 0.6),
        EmotionVector(0.8, 0.9, 0.2, 0.1),
    )
    narrative = build_narrative("melancholic", "joyful", dims, 0.97, "explicit_tags")
    assert "melancholic" in narrative
    assert "joyful" in narrative


def test_build_narrative_inferred_source_note() -> None:
    """Inferred sourcing adds '[inferred' notice to narrative."""
    dims = compute_dimension_deltas(
        EmotionVector(0.5, 0.5, 0.5, 0.5),
        EmotionVector(0.7, 0.7, 0.7, 0.7),
    )
    narrative = build_narrative(None, None, dims, 0.4, "inferred")
    assert "inferred" in narrative.lower()


# ---------------------------------------------------------------------------
# Integration — get_emotion_tag
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_emotion_tag_returns_label_when_present(
    db_session: AsyncSession,
) -> None:
    """get_emotion_tag returns the label portion of an emotion:* tag."""
    snap = _make_snapshot()
    commit = _make_commit(snap, seq=1)
    db_session.add(snap)
    db_session.add(commit)
    db_session.add(
        MuseCliTag(
            repo_id=_REPO_ID,
            commit_id=commit.commit_id,
            tag="emotion:melancholic",
        )
    )
    await db_session.flush()

    label = await get_emotion_tag(db_session, _REPO_ID, commit.commit_id)
    assert label == "melancholic"


@pytest.mark.anyio
async def test_get_emotion_tag_returns_none_when_absent(
    db_session: AsyncSession,
) -> None:
    """get_emotion_tag returns None when no emotion:* tag exists."""
    snap = _make_snapshot()
    commit = _make_commit(snap, seq=2)
    db_session.add(snap)
    db_session.add(commit)
    db_session.add(
        MuseCliTag(repo_id=_REPO_ID, commit_id=commit.commit_id, tag="stage:rough-mix")
    )
    await db_session.flush()

    label = await get_emotion_tag(db_session, _REPO_ID, commit.commit_id)
    assert label is None


# ---------------------------------------------------------------------------
# Integration — resolve_commit_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_commit_id_head(db_session: AsyncSession) -> None:
    """HEAD resolves to the most recent commit on the branch."""
    snap = _make_snapshot()
    commit = _make_commit(snap, seq=10)
    db_session.add(snap)
    db_session.add(commit)
    await db_session.flush()

    resolved = await resolve_commit_id(db_session, _REPO_ID, "HEAD", _BRANCH)
    assert resolved == commit.commit_id


@pytest.mark.anyio
async def test_resolve_commit_id_head_tilde_1(db_session: AsyncSession) -> None:
    """HEAD~1 resolves to the parent commit."""
    snap1 = _make_snapshot()
    snap2 = _make_snapshot()
    c1 = _make_commit(snap1, seq=20)
    c2 = _make_commit(snap2, seq=21, parent=c1)
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    await db_session.flush()

    resolved = await resolve_commit_id(db_session, _REPO_ID, "HEAD~1", _BRANCH)
    assert resolved == c1.commit_id


@pytest.mark.anyio
async def test_resolve_commit_id_head_tilde_beyond_root(
    db_session: AsyncSession,
) -> None:
    """HEAD~N where N exceeds depth returns None."""
    snap = _make_snapshot()
    commit = _make_commit(snap, seq=30)
    db_session.add(snap)
    db_session.add(commit)
    await db_session.flush()

    resolved = await resolve_commit_id(db_session, _REPO_ID, "HEAD~5", _BRANCH)
    assert resolved is None


@pytest.mark.anyio
async def test_resolve_commit_id_full_hash(db_session: AsyncSession) -> None:
    """A full 64-char commit hash resolves to itself."""
    snap = _make_snapshot()
    commit = _make_commit(snap, seq=40)
    db_session.add(snap)
    db_session.add(commit)
    await db_session.flush()

    resolved = await resolve_commit_id(db_session, _REPO_ID, commit.commit_id, _BRANCH)
    assert resolved == commit.commit_id


@pytest.mark.anyio
async def test_resolve_commit_id_abbreviated_hash(db_session: AsyncSession) -> None:
    """An 8-char abbreviated hash resolves via prefix match."""
    snap = _make_snapshot()
    commit = _make_commit(snap, seq=50)
    db_session.add(snap)
    db_session.add(commit)
    await db_session.flush()

    short = commit.commit_id[:8]
    resolved = await resolve_commit_id(db_session, _REPO_ID, short, _BRANCH)
    assert resolved == commit.commit_id


@pytest.mark.anyio
async def test_resolve_commit_id_nonexistent_returns_none(
    db_session: AsyncSession,
) -> None:
    """A non-existent ref returns None."""
    resolved = await resolve_commit_id(db_session, _REPO_ID, "deadbeef", _BRANCH)
    assert resolved is None


# ---------------------------------------------------------------------------
# Integration — compute_emotion_diff
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_emotion_diff_explicit_tags_correct_source(
    db_session: AsyncSession,
) -> None:
    """Two commits with explicit emotion tags → source='explicit_tags'."""
    snap1, snap2 = _make_snapshot(), _make_snapshot()
    c1 = _make_commit(snap1, seq=60)
    c2 = _make_commit(snap2, seq=61, parent=c1)
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    db_session.add(MuseCliTag(repo_id=_REPO_ID, commit_id=c1.commit_id, tag="emotion:melancholic"))
    db_session.add(MuseCliTag(repo_id=_REPO_ID, commit_id=c2.commit_id, tag="emotion:joyful"))
    await db_session.flush()

    result = await compute_emotion_diff(
        db_session,
        repo_id=_REPO_ID,
        commit_a=c1.commit_id,
        commit_b=c2.commit_id,
        branch=_BRANCH,
    )
    assert result.source == "explicit_tags"
    assert result.label_a == "melancholic"
    assert result.label_b == "joyful"


@pytest.mark.anyio
async def test_emotion_diff_outputs_readable_description(
    db_session: AsyncSession,
) -> None:
    """Regression: emotion-diff produces a non-empty human-readable narrative."""
    snap1, snap2 = _make_snapshot(), _make_snapshot()
    c1 = _make_commit(snap1, seq=70)
    c2 = _make_commit(snap2, seq=71, parent=c1)
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    db_session.add(MuseCliTag(repo_id=_REPO_ID, commit_id=c1.commit_id, tag="emotion:anxious"))
    db_session.add(MuseCliTag(repo_id=_REPO_ID, commit_id=c2.commit_id, tag="emotion:cinematic"))
    await db_session.flush()

    result = await compute_emotion_diff(
        db_session,
        repo_id=_REPO_ID,
        commit_a=c1.commit_id,
        commit_b=c2.commit_id,
        branch=_BRANCH,
    )
    assert result.narrative, "Narrative must be non-empty"
    assert len(result.narrative) > 20, "Narrative should be a meaningful sentence"
    # The shift from anxious to cinematic should be noted
    assert "anxious" in result.narrative
    assert "cinematic" in result.narrative


@pytest.mark.anyio
async def test_emotion_diff_inferred_source_no_tags(
    db_session: AsyncSession,
) -> None:
    """Commits without emotion tags → source='inferred'."""
    snap1, snap2 = _make_snapshot(), _make_snapshot()
    c1 = _make_commit(snap1, seq=80, metadata={"tempo_bpm": 90.0})
    c2 = _make_commit(snap2, seq=81, parent=c1, metadata={"tempo_bpm": 150.0})
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    await db_session.flush()

    result = await compute_emotion_diff(
        db_session,
        repo_id=_REPO_ID,
        commit_a=c1.commit_id,
        commit_b=c2.commit_id,
        branch=_BRANCH,
    )
    assert result.source == "inferred"
    assert result.label_a is None
    assert result.label_b is None
    assert result.vector_a is not None
    assert result.vector_b is not None
    # Faster tempo → higher energy
    assert result.vector_b.energy > result.vector_a.energy


@pytest.mark.anyio
async def test_emotion_diff_mixed_source_one_tag(
    db_session: AsyncSession,
) -> None:
    """One commit with tag, one without → source='mixed'."""
    snap1, snap2 = _make_snapshot(), _make_snapshot()
    c1 = _make_commit(snap1, seq=90)
    c2 = _make_commit(snap2, seq=91, parent=c1)
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    db_session.add(MuseCliTag(repo_id=_REPO_ID, commit_id=c1.commit_id, tag="emotion:dark"))
    await db_session.flush()

    result = await compute_emotion_diff(
        db_session,
        repo_id=_REPO_ID,
        commit_a=c1.commit_id,
        commit_b=c2.commit_id,
        branch=_BRANCH,
    )
    assert result.source == "mixed"


@pytest.mark.anyio
async def test_emotion_diff_drift_is_nonnegative(
    db_session: AsyncSession,
) -> None:
    """Drift is always ≥ 0."""
    snap1, snap2 = _make_snapshot(), _make_snapshot()
    c1 = _make_commit(snap1, seq=100)
    c2 = _make_commit(snap2, seq=101, parent=c1)
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    await db_session.flush()

    result = await compute_emotion_diff(
        db_session,
        repo_id=_REPO_ID,
        commit_a=c1.commit_id,
        commit_b=c2.commit_id,
        branch=_BRANCH,
    )
    assert result.drift >= 0.0


@pytest.mark.anyio
async def test_emotion_diff_has_four_dimension_deltas(
    db_session: AsyncSession,
) -> None:
    """Result always contains exactly 4 dimension deltas."""
    snap1, snap2 = _make_snapshot(), _make_snapshot()
    c1 = _make_commit(snap1, seq=110)
    c2 = _make_commit(snap2, seq=111, parent=c1)
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    await db_session.flush()

    result = await compute_emotion_diff(
        db_session,
        repo_id=_REPO_ID,
        commit_a=c1.commit_id,
        commit_b=c2.commit_id,
        branch=_BRANCH,
    )
    assert len(result.dimensions) == 4
    assert tuple(d.dimension for d in result.dimensions) == EMOTION_DIMENSIONS


@pytest.mark.anyio
async def test_emotion_diff_raises_for_unresolvable_commit(
    db_session: AsyncSession,
) -> None:
    """ValueError is raised when commit_a cannot be resolved."""
    with pytest.raises(ValueError, match="Cannot resolve commit ref"):
        await compute_emotion_diff(
            db_session,
            repo_id=_REPO_ID,
            commit_a="nonexistent00",
            commit_b="alsobad00000",
            branch=_BRANCH,
        )


@pytest.mark.anyio
async def test_emotion_diff_track_filter_noted_in_result(
    db_session: AsyncSession,
) -> None:
    """--track value is preserved in the result."""
    snap1, snap2 = _make_snapshot(), _make_snapshot()
    c1 = _make_commit(snap1, seq=120)
    c2 = _make_commit(snap2, seq=121, parent=c1)
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    await db_session.flush()

    result = await compute_emotion_diff(
        db_session,
        repo_id=_REPO_ID,
        commit_a=c1.commit_id,
        commit_b=c2.commit_id,
        branch=_BRANCH,
        track="keys",
    )
    assert result.track == "keys"


@pytest.mark.anyio
async def test_emotion_diff_section_filter_noted_in_result(
    db_session: AsyncSession,
) -> None:
    """--section value is preserved in the result."""
    snap1, snap2 = _make_snapshot(), _make_snapshot()
    c1 = _make_commit(snap1, seq=130)
    c2 = _make_commit(snap2, seq=131, parent=c1)
    for obj in (snap1, snap2, c1, c2):
        db_session.add(obj)
    await db_session.flush()

    result = await compute_emotion_diff(
        db_session,
        repo_id=_REPO_ID,
        commit_a=c1.commit_id,
        commit_b=c2.commit_id,
        branch=_BRANCH,
        section="chorus",
    )
    assert result.section == "chorus"


# ---------------------------------------------------------------------------
# Unit — renderers
# ---------------------------------------------------------------------------


def _make_diff_result(
    label_a: str | None = "melancholic",
    label_b: str | None = "joyful",
    source: str = "explicit_tags",
) -> EmotionDiffResult:
    """Build a synthetic EmotionDiffResult for renderer tests."""
    vec_a = vector_from_label(label_a) if label_a else EmotionVector(0.5, 0.5, 0.5, 0.5)
    vec_b = vector_from_label(label_b) if label_b else EmotionVector(0.6, 0.6, 0.6, 0.6)
    assert vec_a is not None
    assert vec_b is not None
    dims = compute_dimension_deltas(vec_a, vec_b)
    drift = vec_b.drift_from(vec_a)
    narrative = build_narrative(label_a, label_b, dims, drift, source)
    return EmotionDiffResult(
        commit_a="a1b2c3d4",
        commit_b="f9e8d7c6",
        source=source,
        label_a=label_a,
        label_b=label_b,
        vector_a=vec_a,
        vector_b=vec_b,
        dimensions=dims,
        drift=drift,
        narrative=narrative,
        track=None,
        section=None,
    )


def test_render_text_includes_commit_refs(capsys: pytest.CaptureFixture[str]) -> None:
    """render_text output includes both commit short refs."""
    render_text(_make_diff_result())
    out = capsys.readouterr().out
    assert "a1b2c3d4" in out
    assert "f9e8d7c6" in out


def test_render_text_includes_labels(capsys: pytest.CaptureFixture[str]) -> None:
    """render_text output includes emotion labels."""
    render_text(_make_diff_result())
    out = capsys.readouterr().out
    assert "melancholic" in out
    assert "joyful" in out


def test_render_text_includes_drift(capsys: pytest.CaptureFixture[str]) -> None:
    """render_text output includes the drift value."""
    render_text(_make_diff_result())
    out = capsys.readouterr().out
    assert "Drift" in out


def test_render_text_includes_all_dimensions(capsys: pytest.CaptureFixture[str]) -> None:
    """render_text output includes all 4 dimension names."""
    render_text(_make_diff_result())
    out = capsys.readouterr().out
    for dim in EMOTION_DIMENSIONS:
        assert dim in out


def test_render_json_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    """render_json produces valid JSON."""
    render_json(_make_diff_result())
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["commit_a"] == "a1b2c3d4"
    assert payload["commit_b"] == "f9e8d7c6"


def test_render_json_has_all_required_keys(capsys: pytest.CaptureFixture[str]) -> None:
    """render_json output includes all required top-level keys."""
    render_json(_make_diff_result())
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    required = {
        "commit_a", "commit_b", "source", "label_a", "label_b",
        "vector_a", "vector_b", "dimensions", "drift", "narrative",
        "track", "section",
    }
    assert required <= set(payload.keys())


def test_render_json_vector_has_four_keys(capsys: pytest.CaptureFixture[str]) -> None:
    """vector_a and vector_b in JSON each have all 4 dimension keys."""
    render_json(_make_diff_result())
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    for vec_key in ("vector_a", "vector_b"):
        assert set(payload[vec_key].keys()) == {"energy", "valence", "tension", "darkness"}


def test_render_json_dimensions_list_length(capsys: pytest.CaptureFixture[str]) -> None:
    """dimensions list in JSON has exactly 4 entries."""
    render_json(_make_diff_result())
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert len(payload["dimensions"]) == 4


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------


def test_cli_emotion_diff_no_repo(tmp_path: pathlib.Path) -> None:
    """Running emotion-diff outside a Muse repo exits with REPO_NOT_FOUND."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        result = runner.invoke(cli, ["emotion-diff"])
    finally:
        del os.environ["MUSE_REPO_ROOT"]
    assert result.exit_code == ExitCode.REPO_NOT_FOUND


def test_cli_emotion_diff_help() -> None:
    """emotion-diff --help exits 0 and includes usage information."""
    result = runner.invoke(cli, ["emotion-diff", "--help"])
    assert result.exit_code == 0
    assert "COMMIT_A" in result.output or "commit" in result.output.lower()
