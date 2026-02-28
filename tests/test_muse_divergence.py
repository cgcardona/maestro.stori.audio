"""Tests for muse divergence — musical divergence between two CLI branches.

Covers:
- Common ancestor auto-detection (merge-base computation).
- Per-dimension divergence scores and level labels.
- JSON output format.
- Multiple divergent commits across branches.
- Boundary seal (no forbidden imports in service or command).

Naming convention: ``test_<behaviour>_<scenario>``
"""
from __future__ import annotations

import ast
import datetime
import hashlib
import json
import pathlib
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.db.database import Base
from maestro.db import muse_models  # noqa: F401  — registers ORM models with Base
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.services.muse_divergence import (
    ALL_DIMENSIONS,
    DivergenceLevel,
    MuseDivergenceResult,
    classify_path,
    compute_dimension_divergence,
    compute_divergence,
    score_to_level,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with the full Maestro schema."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_REPO_ID = "test-repo-001"
_COUNTER = 0


def _unique_hash(prefix: str) -> str:
    """Deterministic 64-char hash for test IDs."""
    global _COUNTER
    _COUNTER += 1
    raw = f"{prefix}-{_COUNTER}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_snapshot(manifest: dict[str, str]) -> MuseCliSnapshot:
    """Create a :class:`MuseCliSnapshot` from a manifest dict."""
    parts = sorted(f"{k}:{v}" for k, v in manifest.items())
    snap_id = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return MuseCliSnapshot(snapshot_id=snap_id, manifest=manifest)


def _make_commit(
    snapshot: MuseCliSnapshot,
    branch: str,
    parent: MuseCliCommit | None = None,
    parent2: MuseCliCommit | None = None,
    seq: int = 0,
) -> MuseCliCommit:
    """Create a :class:`MuseCliCommit` linked to *snapshot* on *branch*."""
    cid = _unique_hash(f"commit-{branch}-{seq}")
    return MuseCliCommit(
        commit_id=cid,
        repo_id=_REPO_ID,
        branch=branch,
        snapshot_id=snapshot.snapshot_id,
        parent_commit_id=parent.commit_id if parent else None,
        parent2_commit_id=parent2.commit_id if parent2 else None,
        message=f"commit on {branch} seq={seq}",
        author="test",
        committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        + datetime.timedelta(seconds=seq),
    )


async def _save(
    session: AsyncSession,
    snapshot: MuseCliSnapshot,
    commit: MuseCliCommit,
) -> None:
    """Persist *snapshot* and *commit* to the session."""
    existing_snap = await session.get(MuseCliSnapshot, snapshot.snapshot_id)
    if existing_snap is None:
        session.add(snapshot)
    session.add(commit)
    await session.flush()


# ---------------------------------------------------------------------------
# 1 — classify_path (pure, no DB)
# ---------------------------------------------------------------------------


class TestClassifyPath:

    def test_melodic_keywords_matched(self) -> None:
        assert "melodic" in classify_path("lead_guitar.mid")
        assert "melodic" in classify_path("Melody_Line.midi")
        assert "melodic" in classify_path("SOLO.MID")

    def test_harmonic_keywords_matched(self) -> None:
        assert "harmonic" in classify_path("chord_progression.txt")
        assert "harmonic" in classify_path("harmony_track.mid")
        assert "harmonic" in classify_path("key_of_d.json")

    def test_rhythmic_keywords_matched(self) -> None:
        assert "rhythmic" in classify_path("drum_pattern.mid")
        assert "rhythmic" in classify_path("beat.mid")
        assert "rhythmic" in classify_path("groove_01.wav")

    def test_structural_keywords_matched(self) -> None:
        assert "structural" in classify_path("chorus.mid")
        assert "structural" in classify_path("verse_01.mid")
        assert "structural" in classify_path("intro.mid")
        assert "structural" in classify_path("bridge_section.mid")

    def test_dynamic_keywords_matched(self) -> None:
        assert "dynamic" in classify_path("mixdown.wav")
        assert "dynamic" in classify_path("master_vol.mid")
        assert "dynamic" in classify_path("level_control.json")

    def test_unclassified_path_returns_empty_set(self) -> None:
        assert classify_path("project.muse") == set()
        assert classify_path("readme.txt") == set()

    def test_multi_dimension_path(self) -> None:
        result = classify_path("melody_key.mid")
        assert "melodic" in result
        assert "harmonic" in result


# ---------------------------------------------------------------------------
# 2 — score_to_level (pure)
# ---------------------------------------------------------------------------


class TestScoreToLevel:

    def test_zero_is_none(self) -> None:
        assert score_to_level(0.0) is DivergenceLevel.NONE

    def test_boundary_0_15_is_low(self) -> None:
        assert score_to_level(0.15) is DivergenceLevel.LOW

    def test_boundary_0_40_is_med(self) -> None:
        assert score_to_level(0.40) is DivergenceLevel.MED

    def test_boundary_0_70_is_high(self) -> None:
        assert score_to_level(0.70) is DivergenceLevel.HIGH

    def test_full_score_is_high(self) -> None:
        assert score_to_level(1.0) is DivergenceLevel.HIGH


# ---------------------------------------------------------------------------
# 3 — compute_dimension_divergence (pure)
# ---------------------------------------------------------------------------


class TestComputeDimensionDivergence:

    def test_no_files_on_either_branch_gives_none_level(self) -> None:
        result = compute_dimension_divergence("melodic", set(), set())
        assert result.score == 0.0
        assert result.level is DivergenceLevel.NONE
        assert result.branch_a_summary == "0 melodic file(s) changed"

    def test_same_melodic_file_on_both_branches_gives_zero_score(self) -> None:
        a = {"melody.mid"}
        b = {"melody.mid"}
        result = compute_dimension_divergence("melodic", a, b)
        assert result.score == 0.0
        assert result.level is DivergenceLevel.NONE

    def test_melodic_only_on_branch_a_gives_high_score(self) -> None:
        a = {"lead_guitar.mid", "solo_01.mid"}
        b: set[str] = set()
        result = compute_dimension_divergence("melodic", a, b)
        assert result.score == 1.0
        assert result.level is DivergenceLevel.HIGH

    def test_partial_overlap_gives_low_score(self) -> None:
        a = {"melody.mid", "lead.mid", "solo.mid"}
        b = {"melody.mid", "vocal.mid"}
        result = compute_dimension_divergence("melodic", a, b)
        # union = {melody, lead, solo, vocal} = 4; sym_diff = {lead, solo, vocal} = 3
        assert 0.5 < result.score < 1.0

    def test_non_matching_paths_ignored(self) -> None:
        a = {"beat.mid", "drum.mid"}
        b = {"beat.mid"}
        result = compute_dimension_divergence("melodic", a, b)
        # Neither 'beat' nor 'drum' matches melodic keywords
        assert result.score == 0.0
        assert result.branch_a_summary == "0 melodic file(s) changed"

    def test_description_mentions_dimension_name(self) -> None:
        result = compute_dimension_divergence("harmonic", {"chord.mid"}, set())
        assert "harmonic" in result.description.lower()


# ---------------------------------------------------------------------------
# 4 — compute_divergence (async, DB)
# ---------------------------------------------------------------------------


class TestComputeDivergenceDetectsCommonAncestor:
    """test_muse_divergence_detects_common_ancestor_automatically"""

    @pytest.mark.anyio
    async def test_detects_common_ancestor_automatically(
        self, async_session: AsyncSession
    ) -> None:
        """
        root (main) ─── branch-a (adds melody.mid)
                    └── branch-b (adds chord.mid)

        Common ancestor = root commit.
        """
        base_snap = _make_snapshot({})
        base_commit = _make_commit(base_snap, "main", seq=0)
        await _save(async_session, base_snap, base_commit)

        a_snap = _make_snapshot({"melody.mid": "hash-melody"})
        a_commit = _make_commit(a_snap, "branch-a", parent=base_commit, seq=1)
        await _save(async_session, a_snap, a_commit)

        b_snap = _make_snapshot({"chord.mid": "hash-chord"})
        b_commit = _make_commit(b_snap, "branch-b", parent=base_commit, seq=2)
        await _save(async_session, b_snap, b_commit)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="branch-a",
            branch_b="branch-b",
        )

        assert isinstance(result, MuseDivergenceResult)
        assert result.common_ancestor == base_commit.commit_id
        assert result.branch_a == "branch-a"
        assert result.branch_b == "branch-b"

    @pytest.mark.anyio
    async def test_since_override_skips_merge_base_computation(
        self, async_session: AsyncSession
    ) -> None:
        """``--since`` overrides automatic LCA detection."""
        base_snap = _make_snapshot({})
        base_commit = _make_commit(base_snap, "main", seq=10)
        await _save(async_session, base_snap, base_commit)

        a_snap = _make_snapshot({"lead.mid": "h1"})
        a_commit = _make_commit(a_snap, "dev-a", parent=base_commit, seq=11)
        await _save(async_session, a_snap, a_commit)

        b_snap = _make_snapshot({"harm.mid": "h2"})
        b_commit = _make_commit(b_snap, "dev-b", parent=base_commit, seq=12)
        await _save(async_session, b_snap, b_commit)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="dev-a",
            branch_b="dev-b",
            since=base_commit.commit_id,
        )

        assert result.common_ancestor == base_commit.commit_id

    @pytest.mark.anyio
    async def test_missing_branch_raises_value_error(
        self, async_session: AsyncSession
    ) -> None:
        """A branch with no commits raises ``ValueError``."""
        snap = _make_snapshot({})
        commit = _make_commit(snap, "existing-branch", seq=20)
        await _save(async_session, snap, commit)
        await async_session.commit()

        with pytest.raises(ValueError, match="no commits"):
            await compute_divergence(
                async_session,
                repo_id=_REPO_ID,
                branch_a="existing-branch",
                branch_b="nonexistent-branch",
            )


class TestComputeDivergencePerDimensionScores:
    """test_muse_divergence_per_dimension_scores"""

    @pytest.mark.anyio
    async def test_melodic_divergence_high_when_only_branch_a_has_melody(
        self, async_session: AsyncSession
    ) -> None:
        """Branch A adds melody.mid; Branch B adds chord.mid.

        Melodic: only A → HIGH.  Harmonic: only B → HIGH.
        Rhythmic: neither → NONE.
        """
        base_snap = _make_snapshot({})
        base_commit = _make_commit(base_snap, "main", seq=30)
        await _save(async_session, base_snap, base_commit)

        a_snap = _make_snapshot({"melody.mid": "m1"})
        a_commit = _make_commit(a_snap, "feat-melody", parent=base_commit, seq=31)
        await _save(async_session, a_snap, a_commit)

        b_snap = _make_snapshot({"chord_sheet.txt": "c1"})
        b_commit = _make_commit(b_snap, "feat-harmony", parent=base_commit, seq=32)
        await _save(async_session, b_snap, b_commit)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="feat-melody",
            branch_b="feat-harmony",
        )

        dim_by_name = {d.dimension: d for d in result.dimensions}

        assert dim_by_name["melodic"].level is DivergenceLevel.HIGH
        assert dim_by_name["melodic"].score == 1.0

        assert dim_by_name["harmonic"].level is DivergenceLevel.HIGH
        assert dim_by_name["harmonic"].score == 1.0

        assert dim_by_name["rhythmic"].level is DivergenceLevel.NONE
        assert dim_by_name["rhythmic"].score == 0.0

    @pytest.mark.anyio
    async def test_rhythmic_divergence_none_when_same_beat_file_on_both(
        self, async_session: AsyncSession
    ) -> None:
        """Both branches add the same beat.mid — rhythmic divergence = 0."""
        base_snap = _make_snapshot({})
        base_commit = _make_commit(base_snap, "main", seq=40)
        await _save(async_session, base_snap, base_commit)

        a_snap = _make_snapshot({"beat.mid": "b1", "melody.mid": "m1"})
        a_commit = _make_commit(a_snap, "rhy-a", parent=base_commit, seq=41)
        await _save(async_session, a_snap, a_commit)

        b_snap = _make_snapshot({"beat.mid": "b1", "chord.mid": "c1"})
        b_commit = _make_commit(b_snap, "rhy-b", parent=base_commit, seq=42)
        await _save(async_session, b_snap, b_commit)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="rhy-a",
            branch_b="rhy-b",
        )

        dim_by_name = {d.dimension: d for d in result.dimensions}
        assert dim_by_name["rhythmic"].score == 0.0
        assert dim_by_name["rhythmic"].level is DivergenceLevel.NONE

    @pytest.mark.anyio
    async def test_dimensions_filter_limits_output(
        self, async_session: AsyncSession
    ) -> None:
        """``dimensions=['melodic', 'harmonic']`` returns only those two."""
        base_snap = _make_snapshot({})
        base_commit = _make_commit(base_snap, "main", seq=50)
        await _save(async_session, base_snap, base_commit)

        a_snap = _make_snapshot({"melody.mid": "m1"})
        a_commit = _make_commit(a_snap, "filter-a", parent=base_commit, seq=51)
        await _save(async_session, a_snap, a_commit)

        b_snap = _make_snapshot({"chord.mid": "c1"})
        b_commit = _make_commit(b_snap, "filter-b", parent=base_commit, seq=52)
        await _save(async_session, b_snap, b_commit)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="filter-a",
            branch_b="filter-b",
            dimensions=["melodic", "harmonic"],
        )

        assert len(result.dimensions) == 2
        returned_names = {d.dimension for d in result.dimensions}
        assert returned_names == {"melodic", "harmonic"}

    @pytest.mark.anyio
    async def test_overall_score_is_mean_of_dimension_scores(
        self, async_session: AsyncSession
    ) -> None:
        """``overall_score`` equals the mean of individual dimension scores."""
        base_snap = _make_snapshot({})
        base_commit = _make_commit(base_snap, "main", seq=60)
        await _save(async_session, base_snap, base_commit)

        a_snap = _make_snapshot({"melody.mid": "m1"})
        a_commit = _make_commit(a_snap, "overall-a", parent=base_commit, seq=61)
        await _save(async_session, a_snap, a_commit)

        b_snap = _make_snapshot({"melody.mid": "m1"})
        b_commit = _make_commit(b_snap, "overall-b", parent=base_commit, seq=62)
        await _save(async_session, b_snap, b_commit)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="overall-a",
            branch_b="overall-b",
        )

        computed_mean = round(
            sum(d.score for d in result.dimensions) / len(result.dimensions), 4
        )
        assert result.overall_score == computed_mean


class TestComputeDivergenceJsonOutput:
    """test_muse_divergence_json_output"""

    @pytest.mark.anyio
    async def test_result_serializes_to_valid_json(
        self, async_session: AsyncSession
    ) -> None:
        """MuseDivergenceResult fields round-trip cleanly through json.dumps."""
        base_snap = _make_snapshot({})
        base_commit = _make_commit(base_snap, "main", seq=70)
        await _save(async_session, base_snap, base_commit)

        a_snap = _make_snapshot({"lead.mid": "h1"})
        a_commit = _make_commit(a_snap, "json-a", parent=base_commit, seq=71)
        await _save(async_session, a_snap, a_commit)

        b_snap = _make_snapshot({"chord.mid": "h2"})
        b_commit = _make_commit(b_snap, "json-b", parent=base_commit, seq=72)
        await _save(async_session, b_snap, b_commit)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="json-a",
            branch_b="json-b",
        )

        data = {
            "branch_a": result.branch_a,
            "branch_b": result.branch_b,
            "common_ancestor": result.common_ancestor,
            "overall_score": result.overall_score,
            "dimensions": [
                {
                    "dimension": d.dimension,
                    "level": d.level.value,
                    "score": d.score,
                    "description": d.description,
                    "branch_a_summary": d.branch_a_summary,
                    "branch_b_summary": d.branch_b_summary,
                }
                for d in result.dimensions
            ],
        }
        serialised = json.dumps(data)
        parsed = json.loads(serialised)

        assert parsed["branch_a"] == "json-a"
        assert parsed["branch_b"] == "json-b"
        assert parsed["common_ancestor"] == base_commit.commit_id
        assert isinstance(parsed["overall_score"], float)
        assert len(parsed["dimensions"]) == len(ALL_DIMENSIONS)
        for dim in parsed["dimensions"]:
            assert "dimension" in dim
            assert "level" in dim
            assert "score" in dim
            assert "description" in dim


class TestComputeDivergenceMultipleDivergentCommits:
    """test_muse_divergence_multiple_divergent_commits"""

    @pytest.mark.anyio
    async def test_multiple_commits_accumulate_changes_correctly(
        self, async_session: AsyncSession
    ) -> None:
        """Branch A adds files across multiple commits; tip manifest reflects all.

        root → [branch-multi-a: commit1 adds melody.mid, commit2 adds solo.mid]
             → [branch-multi-b: commit1 adds chord.mid]

        Melodic divergence on branch A should count both melody.mid and solo.mid.
        """
        base_snap = _make_snapshot({})
        base_commit = _make_commit(base_snap, "main", seq=80)
        await _save(async_session, base_snap, base_commit)

        # Branch A: two sequential commits
        a1_snap = _make_snapshot({"melody.mid": "m1"})
        a1_commit = _make_commit(a1_snap, "branch-multi-a", parent=base_commit, seq=81)
        await _save(async_session, a1_snap, a1_commit)

        a2_snap = _make_snapshot({"melody.mid": "m1", "solo.mid": "s1"})
        a2_commit = _make_commit(a2_snap, "branch-multi-a", parent=a1_commit, seq=82)
        await _save(async_session, a2_snap, a2_commit)

        # Branch B: single commit
        b1_snap = _make_snapshot({"chord.mid": "c1"})
        b1_commit = _make_commit(b1_snap, "branch-multi-b", parent=base_commit, seq=83)
        await _save(async_session, b1_snap, b1_commit)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="branch-multi-a",
            branch_b="branch-multi-b",
        )

        dim_by_name = {d.dimension: d for d in result.dimensions}

        # Both melody.mid and solo.mid are on branch A → 2 melodic files
        assert "2 melodic" in dim_by_name["melodic"].branch_a_summary
        assert dim_by_name["melodic"].level is DivergenceLevel.HIGH

    @pytest.mark.anyio
    async def test_disjoint_histories_common_ancestor_is_none(
        self, async_session: AsyncSession
    ) -> None:
        """Branches with no common history produce ``common_ancestor=None``."""
        snap_x = _make_snapshot({"melody.mid": "mx"})
        commit_x = _make_commit(snap_x, "isolated-x", seq=90)
        await _save(async_session, snap_x, commit_x)

        snap_y = _make_snapshot({"chord.mid": "cy"})
        commit_y = _make_commit(snap_y, "isolated-y", seq=91)
        await _save(async_session, snap_y, commit_y)

        await async_session.commit()

        result = await compute_divergence(
            async_session,
            repo_id=_REPO_ID,
            branch_a="isolated-x",
            branch_b="isolated-y",
        )

        assert result.common_ancestor is None


# ---------------------------------------------------------------------------
# 5 — Boundary seal
# ---------------------------------------------------------------------------


class TestDivergenceBoundarySeal:
    """Verify that service and command modules do not import forbidden modules."""

    _SERVICES_DIR = (
        pathlib.Path(__file__).resolve().parent.parent
        / "maestro"
        / "services"
    )
    _COMMANDS_DIR = (
        pathlib.Path(__file__).resolve().parent.parent
        / "maestro"
        / "muse_cli"
        / "commands"
    )

    def test_service_does_not_import_forbidden_modules(self) -> None:
        filepath = self._SERVICES_DIR / "muse_divergence.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {
            "state_store", "executor", "maestro_handlers",
            "maestro_editing", "mcp", "muse_merge_base",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_divergence imports forbidden module: {node.module}"
                    )

    def test_command_does_not_import_state_store(self) -> None:
        filepath = self._COMMANDS_DIR / "divergence.py"
        tree = ast.parse(filepath.read_text())
        forbidden = {"state_store", "executor", "maestro_handlers", "mcp"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"divergence command imports forbidden module: {node.module}"
                    )

    def test_service_starts_with_future_annotations(self) -> None:
        filepath = self._SERVICES_DIR / "muse_divergence.py"
        source = filepath.read_text()
        assert "from __future__ import annotations" in source
