"""Tests for ``muse similarity`` — CLI interface, flag parsing, and stub output.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so argument parsing, flag handling, and exit codes are exercised end-to-end.

Async core tests call ``_similarity_async`` directly with an in-memory SQLite
session.  The stub does not query the DB, so the session is injected only to
satisfy the signature contract.
"""
from __future__ import annotations

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
from maestro.muse_cli.commands.similarity import (
    DIMENSION_NAMES,
    DimensionScore,
    SimilarityResult,
    _ALL_DIMENSIONS,
    _bar,
    _max_divergence_dimension,
    _overall_label,
    _similarity_async,
    _stub_dimension_scores,
    _weighted_overall,
    build_similarity_result,
    render_similarity_json,
    render_similarity_text,
)
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    """Create a minimal .muse/ layout required by _similarity_async."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    (muse / "refs" / "heads" / branch).write_text("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2")
    return rid


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session — stub similarity does not query the DB."""
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


# ---------------------------------------------------------------------------
# Unit — helpers
# ---------------------------------------------------------------------------


def test_bar_full_score() -> None:
    """A score of 1.0 produces a fully filled bar."""
    bar = _bar(1.0, width=10)
    assert bar == "\u2588" * 10


def test_bar_zero_score() -> None:
    """A score of 0.0 produces an empty bar."""
    bar = _bar(0.0, width=10)
    assert bar == "\u2591" * 10


def test_bar_half_score() -> None:
    """A score of 0.5 produces a half-filled bar."""
    bar = _bar(0.5, width=10)
    assert bar.count("\u2588") == 5
    assert bar.count("\u2591") == 5
    assert len(bar) == 10


def test_overall_label_nearly_identical() -> None:
    assert _overall_label(0.95) == "Nearly identical — minimal change"


def test_overall_label_significantly_different() -> None:
    assert _overall_label(0.45) == "Significantly different — major rework"


def test_overall_label_completely_different() -> None:
    assert _overall_label(0.0) == "Completely different — new direction"


def test_max_divergence_returns_lowest() -> None:
    scores = [
        DimensionScore(dimension="harmonic", score=0.45, note=""),
        DimensionScore(dimension="rhythmic", score=0.89, note=""),
        DimensionScore(dimension="dynamic", score=0.55, note=""),
    ]
    assert _max_divergence_dimension(scores) == "harmonic"


def test_max_divergence_empty_returns_empty() -> None:
    assert _max_divergence_dimension([]) == ""


def test_weighted_overall_all_dimensions() -> None:
    """Weighted average across all five dimensions is within [0, 1]."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    overall = _weighted_overall(scores)
    assert 0.0 <= overall <= 1.0


def test_weighted_overall_empty() -> None:
    assert _weighted_overall([]) == 0.0


def test_weighted_overall_single_dimension() -> None:
    scores = [DimensionScore(dimension="harmonic", score=0.6, note="")]
    result = _weighted_overall(scores)
    assert result == 0.6


def test_stub_dimension_scores_all() -> None:
    """Stub returns all five dimensions in DIMENSION_NAMES order."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    assert len(scores) == 5
    assert [s["dimension"] for s in scores] == list(DIMENSION_NAMES)


def test_stub_dimension_scores_subset() -> None:
    """Subset filter returns only the requested dimensions."""
    subset = frozenset({"harmonic", "rhythmic"})
    scores = _stub_dimension_scores(subset)
    dims = {s["dimension"] for s in scores}
    assert dims == subset


def test_stub_dimension_scores_in_range() -> None:
    """Every stub score is in [0.0, 1.0]."""
    for s in _stub_dimension_scores(_ALL_DIMENSIONS):
        assert 0.0 <= s["score"] <= 1.0


# ---------------------------------------------------------------------------
# Unit — build_similarity_result
# ---------------------------------------------------------------------------


def test_build_similarity_result_structure() -> None:
    """build_similarity_result returns a fully-populated SimilarityResult."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    result = build_similarity_result("HEAD~10", "HEAD", scores)
    assert result["commit_a"] == "HEAD~10"
    assert result["commit_b"] == "HEAD"
    assert isinstance(result["dimensions"], list)
    assert 0.0 <= result["overall"] <= 1.0
    assert isinstance(result["label"], str)
    assert result["max_divergence"] in _ALL_DIMENSIONS


def test_build_similarity_result_max_divergence_is_lowest() -> None:
    """max_divergence should match the dimension with the lowest score."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    result = build_similarity_result("a", "b", scores)
    min_score = min(s["score"] for s in scores)
    lowest_dim = next(s["dimension"] for s in scores if s["score"] == min_score)
    assert result["max_divergence"] == lowest_dim


# ---------------------------------------------------------------------------
# Unit — renderers
# ---------------------------------------------------------------------------


def test_render_similarity_text_contains_commit_refs() -> None:
    """Text output includes both commit refs in the header."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    result = build_similarity_result("HEAD~10", "HEAD", scores)
    text = render_similarity_text(result)
    assert "HEAD~10" in text
    assert "HEAD" in text


def test_render_similarity_text_contains_all_dimensions() -> None:
    """Text output mentions all five dimension names."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    result = build_similarity_result("a", "b", scores)
    text = render_similarity_text(result)
    for dim in DIMENSION_NAMES:
        assert dim in text.lower()


def test_render_similarity_text_contains_overall() -> None:
    """Text output includes the overall score and label."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    result = build_similarity_result("a", "b", scores)
    text = render_similarity_text(result)
    assert "Overall" in text
    assert result["label"] in text


def test_render_similarity_json_is_valid() -> None:
    """JSON output is parseable and contains the expected top-level keys."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    result = build_similarity_result("HEAD~5", "HEAD", scores)
    raw = render_similarity_json(result)
    payload = json.loads(raw)
    assert payload["commit_a"] == "HEAD~5"
    assert payload["commit_b"] == "HEAD"
    assert isinstance(payload["dimensions"], list)
    assert isinstance(payload["overall"], float)
    assert isinstance(payload["label"], str)
    assert "max_divergence" in payload


def test_render_similarity_json_dimensions_structure() -> None:
    """Each dimension entry in JSON has dimension, score, and note fields."""
    scores = _stub_dimension_scores(_ALL_DIMENSIONS)
    result = build_similarity_result("a", "b", scores)
    payload = json.loads(render_similarity_json(result))
    for entry in payload["dimensions"]:
        assert "dimension" in entry
        assert "score" in entry
        assert "note" in entry


# ---------------------------------------------------------------------------
# Regression: test_muse_similarity_returns_per_dimension_scores
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_similarity_returns_per_dimension_scores(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_similarity_async with no filters produces all five dimension scores."""
    _init_muse_repo(tmp_path)
    exit_code = await _similarity_async(
        root=tmp_path,
        session=db_session,
        commit_a="HEAD~10",
        commit_b="HEAD",
        dimensions=_ALL_DIMENSIONS,
        section=None,
        track=None,
        threshold=None,
        as_json=False,
    )
    assert exit_code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "HEAD~10" in out
    for dim in DIMENSION_NAMES:
        assert dim in out.lower()
    assert "Overall" in out


# ---------------------------------------------------------------------------
# Unit: test_muse_similarity_dimensions_flag_filters_output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_similarity_dimensions_flag_filters_output(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--dimensions harmonic,rhythmic shows only those two dimensions."""
    _init_muse_repo(tmp_path)
    subset = frozenset({"harmonic", "rhythmic"})
    exit_code = await _similarity_async(
        root=tmp_path,
        session=db_session,
        commit_a="a1b2c3d4",
        commit_b="e5f6a7b8",
        dimensions=subset,
        section=None,
        track=None,
        threshold=None,
        as_json=False,
    )
    assert exit_code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "harmonic" in out.lower()
    assert "rhythmic" in out.lower()
    assert "melodic" not in out.lower()
    assert "structural" not in out.lower()
    assert "dynamic" not in out.lower()


# ---------------------------------------------------------------------------
# Unit: test_muse_similarity_threshold_exits_nonzero_when_below
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_similarity_threshold_exits_nonzero_when_below(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--threshold exits 1 when overall similarity is below the threshold."""
    _init_muse_repo(tmp_path)
    # Stub overall is ~0.65; threshold of 0.99 forces a non-zero exit.
    exit_code = await _similarity_async(
        root=tmp_path,
        session=db_session,
        commit_a="a",
        commit_b="b",
        dimensions=_ALL_DIMENSIONS,
        section=None,
        track=None,
        threshold=0.99,
        as_json=False,
    )
    assert exit_code == 1


@pytest.mark.anyio
async def test_muse_similarity_threshold_exits_zero_when_above(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--threshold exits 0 when overall similarity meets or exceeds threshold."""
    _init_muse_repo(tmp_path)
    # Stub overall is ~0.65; threshold of 0.5 should pass.
    exit_code = await _similarity_async(
        root=tmp_path,
        session=db_session,
        commit_a="a",
        commit_b="b",
        dimensions=_ALL_DIMENSIONS,
        section=None,
        track=None,
        threshold=0.5,
        as_json=False,
    )
    assert exit_code == int(ExitCode.SUCCESS)


# ---------------------------------------------------------------------------
# Unit: test_muse_similarity_json_output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_similarity_json_output(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--json produces valid JSON with expected top-level fields."""
    _init_muse_repo(tmp_path)
    exit_code = await _similarity_async(
        root=tmp_path,
        session=db_session,
        commit_a="HEAD~5",
        commit_b="HEAD",
        dimensions=_ALL_DIMENSIONS,
        section=None,
        track=None,
        threshold=None,
        as_json=True,
    )
    assert exit_code == int(ExitCode.SUCCESS)
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["commit_a"] == "HEAD~5"
    assert payload["commit_b"] == "HEAD"
    assert len(payload["dimensions"]) == 5
    assert 0.0 <= payload["overall"] <= 1.0


@pytest.mark.anyio
async def test_muse_similarity_section_flag_warns(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--section emits a stub warning but still produces output."""
    _init_muse_repo(tmp_path)
    exit_code = await _similarity_async(
        root=tmp_path,
        session=db_session,
        commit_a="a",
        commit_b="b",
        dimensions=_ALL_DIMENSIONS,
        section="verse",
        track=None,
        threshold=None,
        as_json=False,
    )
    assert exit_code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "section" in out.lower()


@pytest.mark.anyio
async def test_muse_similarity_track_flag_warns(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--track emits a stub warning but still produces output."""
    _init_muse_repo(tmp_path)
    exit_code = await _similarity_async(
        root=tmp_path,
        session=db_session,
        commit_a="a",
        commit_b="b",
        dimensions=_ALL_DIMENSIONS,
        section=None,
        track="bass",
        threshold=None,
        as_json=False,
    )
    assert exit_code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "track" in out.lower()


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_similarity_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse similarity`` exits 2 when not inside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["similarity", "HEAD~1", "HEAD"], catch_exceptions=False)
    finally:
        os.chdir(prev)
    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_similarity_help_lists_flags() -> None:
    """``muse similarity --help`` shows all documented flags."""
    result = runner.invoke(cli, ["similarity", "--help"])
    assert result.exit_code == 0
    for flag in ("--dimensions", "--section", "--track", "--json", "--threshold"):
        assert flag in result.output, f"Flag '{flag}' not found in help output"


def test_cli_similarity_appears_in_muse_help() -> None:
    """``muse --help`` lists the similarity subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "similarity" in result.output


def test_cli_similarity_invalid_dimension_exits_1(tmp_path: pathlib.Path) -> None:
    """``muse similarity --dimensions badvalue`` exits USER_ERROR before repo detection.

    Options must precede positional arguments to satisfy Click/Typer parsing
    in nested callback groups (known behavior with invoke_without_command=True).
    Flag validation runs before require_repo(), so no .muse/ dir is needed.
    """
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["similarity", "--dimensions", "badvalue", "HEAD~1", "HEAD"],
            catch_exceptions=False,
        )
    finally:
        os.chdir(prev)
    assert result.exit_code == int(ExitCode.USER_ERROR)


def test_cli_similarity_threshold_out_of_range_exits_1(tmp_path: pathlib.Path) -> None:
    """``muse similarity --threshold 2.0`` exits USER_ERROR before repo detection.

    Options must precede positional arguments to satisfy Click/Typer parsing
    in nested callback groups (known behavior with invoke_without_command=True).
    Flag validation runs before require_repo(), so no .muse/ dir is needed.
    """
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["similarity", "--threshold", "2.0", "HEAD~1", "HEAD"],
            catch_exceptions=False,
        )
    finally:
        os.chdir(prev)
    assert result.exit_code == int(ExitCode.USER_ERROR)
