"""Tests for the Muse Drift Detection Engine.

Verifies:
- Clean working tree detection (identical notes → is_clean=True).
- Dirty working tree detection (added/removed/modified notes).
- Added/deleted region detection.
- HEAD snapshot reconstruction from persisted data.
- Fingerprint stability for caching.
- muse_drift boundary rules.
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
from app.services.muse_drift import (
    DriftReport,
    DriftSeverity,
    RegionDriftSummary,
    compute_drift_report,
    _fingerprint,
)
from app.services.muse_replay import reconstruct_head_snapshot, HeadSnapshot


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def async_session():
    """In-memory SQLite async session for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────


def _note(pitch: int, start: float, dur: float = 1.0, vel: int = 100) -> dict:
    return {
        "pitch": pitch,
        "start_beat": start,
        "duration_beats": dur,
        "velocity": vel,
        "channel": 0,
    }


def _make_variation_with_notes(
    notes: list[dict],
    region_id: str = "region-1",
    track_id: str = "track-1",
    intent: str = "test",
) -> Variation:
    """Build a variation where all notes are 'added' type."""
    vid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    note_changes = [
        NoteChange(
            note_id=str(uuid.uuid4()),
            change_type="added",
            after=MidiNoteSnapshot.from_note_dict(n),
        )
        for n in notes
    ]
    return Variation(
        variation_id=vid,
        intent=intent,
        ai_explanation="test explanation",
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
                label="Test Phrase",
                note_changes=note_changes,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 6.1 — Clean Working Tree
# ---------------------------------------------------------------------------


class TestCleanWorkingTree:

    def test_identical_notes_is_clean(self):
        """HEAD and working have the same notes → CLEAN."""
        notes = [_note(60, 0.0), _note(64, 1.0), _note(67, 2.0)]
        report = compute_drift_report(
            project_id="proj-1",
            head_variation_id="v-1",
            head_snapshot_notes={"r1": notes},
            working_snapshot_notes={"r1": notes},
            track_regions={"r1": "t1"},
        )
        assert report.is_clean is True
        assert report.severity == DriftSeverity.CLEAN
        assert len(report.changed_regions) == 0
        assert len(report.added_regions) == 0
        assert len(report.deleted_regions) == 0
        assert report.total_changes == 0

    def test_empty_regions_is_clean(self):
        """Both snapshots have a region with no notes → CLEAN."""
        report = compute_drift_report(
            project_id="proj-1",
            head_variation_id="v-1",
            head_snapshot_notes={"r1": []},
            working_snapshot_notes={"r1": []},
            track_regions={"r1": "t1"},
        )
        assert report.is_clean is True

    def test_no_regions_is_clean(self):
        """Both snapshots have zero regions → CLEAN."""
        report = compute_drift_report(
            project_id="proj-1",
            head_variation_id="v-1",
            head_snapshot_notes={},
            working_snapshot_notes={},
            track_regions={},
        )
        assert report.is_clean is True


# ---------------------------------------------------------------------------
# 6.2 — Dirty Working Tree (Notes Added)
# ---------------------------------------------------------------------------


class TestDirtyNotesAdded:

    def test_extra_note_in_working(self):
        """Working has an extra note → DIRTY with 1 add."""
        head = [_note(60, 0.0)]
        working = [_note(60, 0.0), _note(72, 4.0)]
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": head},
            working_snapshot_notes={"r1": working},
            track_regions={"r1": "t1"},
        )
        assert report.is_clean is False
        assert report.severity == DriftSeverity.DIRTY
        assert "r1" in report.changed_regions
        summary = report.region_summaries["r1"]
        assert summary.added == 1
        assert summary.removed == 0
        assert summary.modified == 0

    def test_multiple_added_notes(self):
        """Working has several extra notes → correct add count."""
        head = [_note(60, 0.0)]
        working = [_note(60, 0.0), _note(64, 1.0), _note(67, 2.0), _note(72, 3.0)]
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": head},
            working_snapshot_notes={"r1": working},
            track_regions={"r1": "t1"},
        )
        summary = report.region_summaries["r1"]
        assert summary.added == 3


# ---------------------------------------------------------------------------
# 6.3 — Dirty Working Tree (Note Modified)
# ---------------------------------------------------------------------------


class TestDirtyNotesModified:

    def test_velocity_change_detected(self):
        """Same pitch/time, different velocity → modified."""
        head = [_note(60, 0.0, vel=100)]
        working = [_note(60, 0.0, vel=50)]
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": head},
            working_snapshot_notes={"r1": working},
            track_regions={"r1": "t1"},
        )
        assert report.is_clean is False
        summary = report.region_summaries["r1"]
        assert summary.modified == 1
        assert summary.added == 0
        assert summary.removed == 0

    def test_duration_change_detected(self):
        """Same pitch/time, different duration → modified."""
        head = [_note(60, 0.0, dur=1.0)]
        working = [_note(60, 0.0, dur=4.0)]
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": head},
            working_snapshot_notes={"r1": working},
            track_regions={"r1": "t1"},
        )
        summary = report.region_summaries["r1"]
        assert summary.modified == 1

    def test_note_removed_from_working(self):
        """Head has note, working doesn't → removed."""
        head = [_note(60, 0.0), _note(64, 1.0)]
        working = [_note(60, 0.0)]
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": head},
            working_snapshot_notes={"r1": working},
            track_regions={"r1": "t1"},
        )
        summary = report.region_summaries["r1"]
        assert summary.removed == 1


# ---------------------------------------------------------------------------
# 6.4 — Added / Deleted Region Detection
# ---------------------------------------------------------------------------


class TestRegionDrift:

    def test_added_region(self):
        """Region exists in working but not head → added."""
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)], "r2": [_note(72, 0.0)]},
            track_regions={"r1": "t1", "r2": "t2"},
        )
        assert "r2" in report.added_regions
        assert report.is_clean is False
        summary = report.region_summaries["r2"]
        assert summary.added == 1

    def test_deleted_region(self):
        """Region exists in head but not working → deleted."""
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)], "r2": [_note(72, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1", "r2": "t2"},
        )
        assert "r2" in report.deleted_regions
        assert report.is_clean is False
        summary = report.region_summaries["r2"]
        assert summary.removed == 1

    def test_both_added_and_deleted(self):
        """One region added, another deleted → both detected."""
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r2": [_note(72, 0.0)]},
            track_regions={"r1": "t1", "r2": "t2"},
        )
        assert "r1" in report.deleted_regions
        assert "r2" in report.added_regions
        assert report.is_clean is False


# ---------------------------------------------------------------------------
# Fingerprint tests
# ---------------------------------------------------------------------------


class TestFingerprint:

    def test_identical_notes_same_fingerprint(self):
        notes = [_note(60, 0.0), _note(64, 1.0)]
        assert _fingerprint(notes) == _fingerprint(notes)

    def test_order_independent(self):
        """Fingerprint should be stable regardless of note order."""
        a = [_note(60, 0.0), _note(64, 1.0)]
        b = [_note(64, 1.0), _note(60, 0.0)]
        assert _fingerprint(a) == _fingerprint(b)

    def test_different_notes_different_fingerprint(self):
        a = [_note(60, 0.0)]
        b = [_note(72, 0.0)]
        assert _fingerprint(a) != _fingerprint(b)


# ---------------------------------------------------------------------------
# Sample changes capping
# ---------------------------------------------------------------------------


class TestSampleChanges:

    def test_sample_changes_capped(self):
        """Sample changes should not exceed MAX_SAMPLE_CHANGES."""
        head: list[dict] = []
        working = [_note(i, float(i)) for i in range(20)]
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": head},
            working_snapshot_notes={"r1": working},
            track_regions={"r1": "t1"},
        )
        summary = report.region_summaries["r1"]
        assert len(summary.sample_changes) <= 5
        assert summary.added == 20

    def test_sample_changes_include_type(self):
        """Each sample change should have a 'type' key."""
        head = [_note(60, 0.0)]
        working = [_note(60, 0.0, vel=50), _note(72, 4.0)]
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={"r1": head},
            working_snapshot_notes={"r1": working},
            track_regions={"r1": "t1"},
        )
        summary = report.region_summaries["r1"]
        for sc in summary.sample_changes:
            assert "type" in sc
            assert sc["type"] in ("added", "removed", "modified")


# ---------------------------------------------------------------------------
# DriftReport properties
# ---------------------------------------------------------------------------


class TestDriftReportProperties:

    def test_total_changes_sums_all_regions(self):
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={
                "r1": [_note(60, 0.0)],
                "r2": [_note(64, 0.0)],
            },
            working_snapshot_notes={
                "r1": [_note(60, 0.0), _note(72, 4.0)],
                "r2": [],
            },
            track_regions={"r1": "t1", "r2": "t2"},
        )
        assert report.total_changes == 2  # 1 add + 1 remove

    def test_no_legacy_flags(self):
        """notes_only and partial_reconstruction flags have been removed."""
        report = compute_drift_report(
            project_id="p1",
            head_variation_id="v1",
            head_snapshot_notes={},
            working_snapshot_notes={},
            track_regions={},
        )
        assert not hasattr(report, "notes_only")
        assert not hasattr(report, "partial_reconstruction")


# ---------------------------------------------------------------------------
# HEAD Snapshot Reconstruction (requires DB)
# ---------------------------------------------------------------------------


class TestReconstructHeadSnapshot:

    @pytest.mark.anyio
    async def test_no_head_returns_none(self, async_session: AsyncSession):
        result = await reconstruct_head_snapshot(async_session, "nonexistent-project")
        assert result is None

    @pytest.mark.anyio
    async def test_single_variation_head(self, async_session: AsyncSession):
        """Persist a variation, set HEAD, reconstruct snapshot."""
        notes = [_note(60, 0.0), _note(64, 1.0), _note(67, 2.0)]
        var = _make_variation_with_notes(notes)

        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-h", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(
            async_session, var.variation_id, commit_state_id="s2",
        )
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-h")
        assert snap is not None
        assert snap.variation_id == var.variation_id
        assert not hasattr(snap, "partial")
        assert "region-1" in snap.notes
        assert len(snap.notes["region-1"]) == 3
        assert snap.track_regions["region-1"] == "track-1"

    @pytest.mark.anyio
    async def test_lineage_accumulates_notes(self, async_session: AsyncSession):
        """Two variations in lineage → snapshot has notes from both."""
        notes_a = [_note(60, 0.0)]
        notes_b = [_note(72, 4.0)]
        var_a = _make_variation_with_notes(notes_a, region_id="region-1")
        var_b = _make_variation_with_notes(notes_b, region_id="region-2", track_id="track-2")

        await muse_repository.save_variation(
            async_session, var_a,
            project_id="proj-lin", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.save_variation(
            async_session, var_b,
            project_id="proj-lin", base_state_id="s1", conversation_id="c",
            region_metadata={},
            parent_variation_id=var_a.variation_id,
        )
        await muse_repository.set_head(
            async_session, var_b.variation_id, commit_state_id="s2",
        )
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-lin")
        assert snap is not None
        assert "region-1" in snap.notes
        assert "region-2" in snap.notes
        assert len(snap.notes["region-1"]) == 1
        assert len(snap.notes["region-2"]) == 1


# ---------------------------------------------------------------------------
# End-to-end: reconstruct HEAD + compute drift
# ---------------------------------------------------------------------------


class TestEndToEndDrift:

    @pytest.mark.anyio
    async def test_clean_after_commit(self, async_session: AsyncSession):
        """Persist, set HEAD, reconstruct, compare with identical working → CLEAN."""
        notes = [_note(60, 0.0), _note(64, 1.0)]
        var = _make_variation_with_notes(notes)

        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-e2e", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(
            async_session, var.variation_id, commit_state_id="s2",
        )
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-e2e")
        assert snap is not None

        report = compute_drift_report(
            project_id="proj-e2e",
            head_variation_id=snap.variation_id,
            head_snapshot_notes=snap.notes,
            working_snapshot_notes=snap.notes,
            track_regions=snap.track_regions,
        )
        assert report.is_clean is True
        assert report.severity == DriftSeverity.CLEAN

    @pytest.mark.anyio
    async def test_dirty_after_user_edit(self, async_session: AsyncSession):
        """HEAD has notes, working has different notes → DIRTY."""
        notes = [_note(60, 0.0)]
        var = _make_variation_with_notes(notes)

        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-dirty", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(
            async_session, var.variation_id, commit_state_id="s2",
        )
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-dirty")
        assert snap is not None

        working_notes = {"region-1": [_note(60, 0.0), _note(72, 4.0)]}
        report = compute_drift_report(
            project_id="proj-dirty",
            head_variation_id=snap.variation_id,
            head_snapshot_notes=snap.notes,
            working_snapshot_notes=working_notes,
            track_regions=snap.track_regions,
        )
        assert report.is_clean is False
        assert report.severity == DriftSeverity.DIRTY
        assert "region-1" in report.changed_regions


# ---------------------------------------------------------------------------
# Boundary seal tests
# ---------------------------------------------------------------------------


class TestMuseDriftBoundary:

    def test_no_state_store_or_executor_import(self):
        """muse_drift must not import StateStore, executor, or LLM handlers."""
        import importlib
        spec = importlib.util.find_spec("app.services.muse_drift")
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
                        f"muse_drift imports forbidden module: {module}"
                    )
                if hasattr(node, "names"):
                    for alias in node.names:
                        assert alias.name not in forbidden_names, (
                            f"muse_drift imports forbidden name: {alias.name}"
                        )

    def test_no_get_or_create_store_call(self):
        """muse_drift must not call get_or_create_store (AST-level check)."""
        import importlib
        spec = importlib.util.find_spec("app.services.muse_drift")
        assert spec is not None and spec.origin is not None

        with open(spec.origin) as f:
            source = f.read()

        tree = ast.parse(source)
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
