"""Tests for controller drift detection (CC, pitch bends, aftertouch).

Verifies:
- Clean controller state detection.
- CC event drift (add / remove / modify).
- Pitch bend drift.
- Aftertouch drift.
- HEAD snapshot reconstruction fidelity for controllers.
- Controller matching boundary isolation.
"""
from __future__ import annotations

import ast
import uuid
from collections.abc import AsyncGenerator
import pytest
from app.contracts.json_types import AftertouchDict, CCEventDict, ControllerEventDict, NoteDict, PitchBendDict
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
    DriftSeverity,
    compute_drift_report,
)
from app.services.muse_replay import reconstruct_head_snapshot
from app.services.variation.note_matching import (
    EventMatch,
    match_cc_events,
    match_pitch_bends,
    match_aftertouch,
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


def _cc(cc_num: int, beat: float, value: int) -> CCEventDict:

    return {"cc": cc_num, "beat": beat, "value": value}


def _cc_ctrl(cc_num: int, beat: float, value: int) -> ControllerEventDict:
    """CC event with 'kind' discriminator for controller_changes persistence."""
    return ControllerEventDict(kind="cc", cc=cc_num, beat=beat, value=value)


def _pb(beat: float, value: int) -> PitchBendDict:
    return {"beat": beat, "value": value}


def _pb_ctrl(beat: float, value: int) -> ControllerEventDict:
    """Pitch bend with 'kind' discriminator for controller_changes persistence."""
    return ControllerEventDict(kind="pitch_bend", beat=beat, value=value)


def _at(beat: float, value: int, pitch: int | None = None) -> AftertouchDict:
    d: AftertouchDict = {"beat": beat, "value": value}
    if pitch is not None:
        d["pitch"] = pitch
    return d


def _at_ctrl(beat: float, value: int, pitch: int | None = None) -> ControllerEventDict:
    """Aftertouch with 'kind' discriminator for controller_changes persistence."""
    d = ControllerEventDict(kind="aftertouch", beat=beat, value=value)
    if pitch is not None:
        d["pitch"] = pitch
    return d


def _note(pitch: int, start: float) -> NoteDict:
    return {"pitch": pitch, "start_beat": start, "duration_beats": 1.0, "velocity": 100, "channel": 0}


def _make_variation_with_controllers(
    notes: list[NoteDict],
    controller_changes: list[ControllerEventDict],
    region_id: str = "region-1",
    track_id: str = "track-1",
) -> Variation:
    vid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    return Variation(
        variation_id=vid,
        intent="controller test",
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
                label="Test Phrase",
                note_changes=[
                    NoteChange(
                        note_id=str(uuid.uuid4()),
                        change_type="added",
                        after=MidiNoteSnapshot.from_note_dict(n),
                    )
                    for n in notes
                ],
                controller_changes=controller_changes,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 6.1 — Clean controller state
# ---------------------------------------------------------------------------


class TestCleanControllerState:

    def test_identical_cc_is_clean(self) -> None:

        cc = [_cc(64, 0.0, 127)]
        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_cc={"r1": cc}, working_cc={"r1": cc},
        )
        assert report.is_clean is True
        assert report.severity == DriftSeverity.CLEAN
        assert report.region_summaries["r1"].cc_added == 0

    def test_identical_all_controllers_is_clean(self) -> None:

        cc = [_cc(64, 0.0, 127)]
        pb = [_pb(1.0, 4096)]
        at = [_at(2.0, 80)]
        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_cc={"r1": cc}, working_cc={"r1": cc},
            head_pb={"r1": pb}, working_pb={"r1": pb},
            head_at={"r1": at}, working_at={"r1": at},
        )
        assert report.is_clean is True
        assert report.total_changes == 0

    @pytest.mark.anyio
    async def test_reconstructed_head_clean(self, async_session: AsyncSession) -> None:

        """Persist with CC, reconstruct HEAD, drift against identical data → CLEAN."""
        notes = [_note(60, 0.0)]
        ccs_ctrl = [_cc_ctrl(64, 0.0, 127), _cc_ctrl(1, 2.0, 64)]
        var = _make_variation_with_controllers(notes, ccs_ctrl)

        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-cc", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(async_session, var.variation_id, commit_state_id="s2")
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-cc")
        assert snap is not None

        report = compute_drift_report(
            project_id="proj-cc",
            head_variation_id=snap.variation_id,
            head_snapshot_notes=snap.notes,
            working_snapshot_notes=snap.notes,
            track_regions=snap.track_regions,
            head_cc=snap.cc, working_cc=snap.cc,
            head_pb=snap.pitch_bends, working_pb=snap.pitch_bends,
            head_at=snap.aftertouch, working_at=snap.aftertouch,
        )
        assert report.is_clean is True


# ---------------------------------------------------------------------------
# 6.2 — Sustain pedal drift (CC64)
# ---------------------------------------------------------------------------


class TestSustainPedalDrift:

    def test_cc_added_in_working(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_cc={"r1": []},
            working_cc={"r1": [_cc(64, 0.0, 127)]},
        )
        assert report.is_clean is False
        s = report.region_summaries["r1"]
        assert s.cc_added == 1
        assert s.cc_removed == 0

    def test_cc_removed_from_working(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_cc={"r1": [_cc(64, 0.0, 127)]},
            working_cc={"r1": []},
        )
        assert report.is_clean is False
        s = report.region_summaries["r1"]
        assert s.cc_removed == 1

    def test_cc_value_modified(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_cc={"r1": [_cc(64, 0.0, 127)]},
            working_cc={"r1": [_cc(64, 0.0, 0)]},
        )
        assert report.is_clean is False
        s = report.region_summaries["r1"]
        assert s.cc_modified == 1


# ---------------------------------------------------------------------------
# 6.3 — Pitch bend modification
# ---------------------------------------------------------------------------


class TestPitchBendDrift:

    def test_pb_same_beat_different_value(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_pb={"r1": [_pb(1.0, 4096)]},
            working_pb={"r1": [_pb(1.0, 8192)]},
        )
        assert report.is_clean is False
        s = report.region_summaries["r1"]
        assert s.pb_modified == 1

    def test_pb_added(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_pb={"r1": []},
            working_pb={"r1": [_pb(1.0, 4096)]},
        )
        s = report.region_summaries["r1"]
        assert s.pb_added == 1

    def test_pb_removed(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_pb={"r1": [_pb(1.0, 4096)]},
            working_pb={"r1": []},
        )
        s = report.region_summaries["r1"]
        assert s.pb_removed == 1


# ---------------------------------------------------------------------------
# 6.4 — Aftertouch add / delete
# ---------------------------------------------------------------------------


class TestAftertouchDrift:

    def test_at_added(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_at={"r1": []},
            working_at={"r1": [_at(2.0, 80)]},
        )
        s = report.region_summaries["r1"]
        assert s.at_added == 1

    def test_at_removed(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_at={"r1": [_at(2.0, 80)]},
            working_at={"r1": []},
        )
        s = report.region_summaries["r1"]
        assert s.at_removed == 1

    def test_at_modified_value(self) -> None:

        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_at={"r1": [_at(2.0, 80)]},
            working_at={"r1": [_at(2.0, 40)]},
        )
        s = report.region_summaries["r1"]
        assert s.at_modified == 1

    def test_poly_aftertouch_pitch_discriminated(self) -> None:

        """Poly aftertouch on different pitches → add + remove, not modify."""
        report = compute_drift_report(
            project_id="p", head_variation_id="v",
            head_snapshot_notes={"r1": [_note(60, 0.0)]},
            working_snapshot_notes={"r1": [_note(60, 0.0)]},
            track_regions={"r1": "t1"},
            head_at={"r1": [_at(2.0, 80, pitch=60)]},
            working_at={"r1": [_at(2.0, 80, pitch=72)]},
        )
        s = report.region_summaries["r1"]
        assert s.at_added == 1
        assert s.at_removed == 1
        assert s.at_modified == 0


# ---------------------------------------------------------------------------
# 6.5 — Replay fidelity (controllers roundtrip through DB)
# ---------------------------------------------------------------------------


class TestReplayFidelity:

    @pytest.mark.anyio
    async def test_cc_roundtrip(self, async_session: AsyncSession) -> None:

        notes = [_note(60, 0.0)]
        ccs_ctrl = [_cc_ctrl(64, 0.0, 127), _cc_ctrl(1, 4.0, 64)]
        var = _make_variation_with_controllers(notes, ccs_ctrl)

        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-rt", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(async_session, var.variation_id, commit_state_id="s2")
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-rt")
        assert snap is not None
        assert len(snap.cc.get("region-1", [])) == 2
        cc_vals = sorted(e["cc"] for e in snap.cc["region-1"])
        assert cc_vals == [1, 64]

    @pytest.mark.anyio
    async def test_pb_roundtrip(self, async_session: AsyncSession) -> None:

        notes = [_note(60, 0.0)]
        pbs_ctrl = [_pb_ctrl(1.0, 4096), _pb_ctrl(3.0, 8192)]
        var = _make_variation_with_controllers(notes, pbs_ctrl)

        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-pb-rt", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(async_session, var.variation_id, commit_state_id="s2")
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-pb-rt")
        assert snap is not None
        assert len(snap.pitch_bends.get("region-1", [])) == 2

    @pytest.mark.anyio
    async def test_at_roundtrip(self, async_session: AsyncSession) -> None:

        notes = [_note(60, 0.0)]
        ats_ctrl = [_at_ctrl(2.0, 80), _at_ctrl(4.0, 100, pitch=60)]
        var = _make_variation_with_controllers(notes, ats_ctrl)

        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-at-rt", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(async_session, var.variation_id, commit_state_id="s2")
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-at-rt")
        assert snap is not None
        assert len(snap.aftertouch.get("region-1", [])) == 2

    @pytest.mark.anyio
    async def test_mixed_controllers_roundtrip(self, async_session: AsyncSession) -> None:

        """All three controller types persist and reconstruct correctly."""
        notes = [_note(60, 0.0)]
        controllers = [_cc_ctrl(64, 0.0, 127), _pb_ctrl(1.0, 4096), _at_ctrl(2.0, 80)]
        var = _make_variation_with_controllers(notes, controllers)

        await muse_repository.save_variation(
            async_session, var,
            project_id="proj-mix", base_state_id="s1", conversation_id="c",
            region_metadata={},
        )
        await muse_repository.set_head(async_session, var.variation_id, commit_state_id="s2")
        await async_session.commit()

        snap = await reconstruct_head_snapshot(async_session, "proj-mix")
        assert snap is not None
        assert len(snap.cc.get("region-1", [])) == 1
        assert len(snap.pitch_bends.get("region-1", [])) == 1
        assert len(snap.aftertouch.get("region-1", [])) == 1


# ---------------------------------------------------------------------------
# Controller matching unit tests
# ---------------------------------------------------------------------------


class TestEventMatching:

    def test_cc_match_same_cc_and_beat(self) -> None:

        matches = match_cc_events(
            [{"cc": 64, "beat": 0.0, "value": 127}],
            [{"cc": 64, "beat": 0.0, "value": 127}],
        )
        assert len(matches) == 1
        assert matches[0].is_unchanged

    def test_cc_no_match_different_cc_number(self) -> None:

        matches = match_cc_events(
            [{"cc": 64, "beat": 0.0, "value": 127}],
            [{"cc": 1, "beat": 0.0, "value": 127}],
        )
        added = [m for m in matches if m.is_added]
        removed = [m for m in matches if m.is_removed]
        assert len(added) == 1
        assert len(removed) == 1

    def test_pb_match_same_beat(self) -> None:

        matches = match_pitch_bends(
            [{"beat": 1.0, "value": 4096}],
            [{"beat": 1.0, "value": 4096}],
        )
        assert len(matches) == 1
        assert matches[0].is_unchanged

    def test_pb_modified_different_value(self) -> None:

        matches = match_pitch_bends(
            [{"beat": 1.0, "value": 4096}],
            [{"beat": 1.0, "value": 8192}],
        )
        assert len(matches) == 1
        assert matches[0].is_modified

    def test_at_match_with_pitch(self) -> None:

        matches = match_aftertouch(
            [{"beat": 2.0, "value": 80, "pitch": 60}],
            [{"beat": 2.0, "value": 80, "pitch": 60}],
        )
        assert len(matches) == 1
        assert matches[0].is_unchanged

    def test_at_no_match_different_pitch(self) -> None:

        matches = match_aftertouch(
            [{"beat": 2.0, "value": 80, "pitch": 60}],
            [{"beat": 2.0, "value": 80, "pitch": 72}],
        )
        added = [m for m in matches if m.is_added]
        removed = [m for m in matches if m.is_removed]
        assert len(added) == 1
        assert len(removed) == 1


# ---------------------------------------------------------------------------
# Boundary seal
# ---------------------------------------------------------------------------


class TestControllerMatchingBoundary:

    def test_note_matching_no_forbidden_imports(self) -> None:

        from pathlib import Path
        filepath = Path(__file__).resolve().parent.parent / "app" / "services" / "variation" / "note_matching.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"state_store", "executor", "maestro_handlers", "maestro_editing", "maestro_composing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"note_matching imports forbidden module: {node.module}"
                    )
