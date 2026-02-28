"""Tests for ``muse diff`` — music-dimension diff flags.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so argument parsing, flag handling, and exit codes are exercised end-to-end.

Async core tests call dimension functions directly with a minimal .muse/ layout
(defined in ``_init_muse_repo``). The session parameter is reserved for the full
implementation and is not exercised by the stub — it is injected only to keep
the function signatures stable.

Test naming follows the ``test_<behavior>_<scenario>`` convention.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
import pytest_asyncio
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from maestro.db.database import Base
import maestro.muse_cli.models  # noqa: F401 — registers MuseCli* ORM models
from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.diff import (
    DynamicDiffResult,
    HarmonicDiffResult,
    MelodicDiffResult,
    MusicDiffReport,
    RhythmicDiffResult,
    StructuralDiffResult,
    _diff_all_async,
    _dynamic_diff_async,
    _harmonic_diff_async,
    _melodic_diff_async,
    _resolve_refs,
    _rhythmic_diff_async,
    _stub_dynamic,
    _stub_harmonic,
    _stub_melodic,
    _stub_rhythmic,
    _stub_structural,
    _structural_diff_async,
    _tension_label,
    _render_dynamic,
    _render_harmonic,
    _render_melodic,
    _render_rhythmic,
    _render_structural,
    _render_report,
)
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    """Create a minimal .muse/ layout with one empty commit ref."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    (muse / "refs" / "heads" / branch).write_text("")
    return rid


def _commit_ref(root: pathlib.Path, branch: str = "main") -> None:
    """Write a fake commit SHA into the branch ref file."""
    muse = root / ".muse"
    (muse / "refs" / "heads" / branch).write_text("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2")


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session (stub diff does not query the DB)."""
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
# Unit — _tension_label
# ---------------------------------------------------------------------------


def test_tension_label_low() -> None:
    """Values below 0.33 are labelled Low."""
    assert _tension_label(0.0) == "Low"
    assert _tension_label(0.32) == "Low"


def test_tension_label_medium() -> None:
    """Values in [0.33, 0.66) are labelled Medium."""
    assert _tension_label(0.33) == "Medium"
    assert _tension_label(0.50) == "Medium"


def test_tension_label_medium_high() -> None:
    """Values in [0.66, 0.80) are labelled Medium-High."""
    assert _tension_label(0.66) == "Medium-High"
    assert _tension_label(0.79) == "Medium-High"


def test_tension_label_high() -> None:
    """Values >= 0.80 are labelled High."""
    assert _tension_label(0.80) == "High"
    assert _tension_label(1.0) == "High"


# ---------------------------------------------------------------------------
# Unit — stub constructors
# ---------------------------------------------------------------------------


def test_stub_harmonic_fields_present() -> None:
    """HarmonicDiffResult from stub has all required TypedDict fields."""
    result = _stub_harmonic("abc1234", "def5678")
    assert result["commit_a"] == "abc1234"
    assert result["commit_b"] == "def5678"
    assert result["key_a"] and result["key_b"]
    assert result["mode_a"] and result["mode_b"]
    assert result["chord_prog_a"] and result["chord_prog_b"]
    assert 0.0 <= result["tension_a"] <= 1.0
    assert 0.0 <= result["tension_b"] <= 1.0
    assert result["summary"]
    assert isinstance(result["changed"], bool)


def test_stub_rhythmic_fields_present() -> None:
    """RhythmicDiffResult from stub has all required TypedDict fields."""
    result = _stub_rhythmic("abc1234", "def5678")
    assert result["tempo_a"] > 0
    assert result["tempo_b"] > 0
    assert result["meter_a"] and result["meter_b"]
    assert 0.5 <= result["swing_a"] <= 0.67
    assert 0.5 <= result["swing_b"] <= 0.67
    assert result["summary"]


def test_stub_melodic_fields_present() -> None:
    """MelodicDiffResult from stub has all required TypedDict fields."""
    result = _stub_melodic("abc1234", "def5678")
    assert isinstance(result["motifs_introduced"], list)
    assert isinstance(result["motifs_removed"], list)
    assert result["contour_a"] and result["contour_b"]
    assert result["range_low_a"] < result["range_high_a"]
    assert result["range_low_b"] < result["range_high_b"]


def test_stub_structural_fields_present() -> None:
    """StructuralDiffResult from stub has all required TypedDict fields."""
    result = _stub_structural("abc1234", "def5678")
    assert isinstance(result["sections_added"], list)
    assert isinstance(result["sections_removed"], list)
    assert isinstance(result["instruments_added"], list)
    assert isinstance(result["instruments_removed"], list)
    assert result["form_a"] and result["form_b"]


def test_stub_dynamic_fields_present() -> None:
    """DynamicDiffResult from stub has all required TypedDict fields."""
    result = _stub_dynamic("abc1234", "def5678")
    assert 0 <= result["avg_velocity_a"] <= 127
    assert 0 <= result["avg_velocity_b"] <= 127
    assert result["arc_a"] and result["arc_b"]
    assert isinstance(result["tracks_louder"], list)
    assert isinstance(result["tracks_softer"], list)
    assert isinstance(result["tracks_silent"], list)


# ---------------------------------------------------------------------------
# Unit — _resolve_refs
# ---------------------------------------------------------------------------


def test_resolve_refs_defaults_to_head(tmp_path: pathlib.Path) -> None:
    """When both refs are None, resolves to head commit (or HEAD) and HEAD~1."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    ref_a, ref_b = _resolve_refs(tmp_path, None, None)
    assert ref_b == "a1b2c3d4"
    assert ref_a == "a1b2c3d4~1"


def test_resolve_refs_explicit_refs_passthrough(tmp_path: pathlib.Path) -> None:
    """Explicit ref strings are returned unchanged."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    ref_a, ref_b = _resolve_refs(tmp_path, "abc123", "def456")
    assert ref_a == "abc123"
    assert ref_b == "def456"


def test_resolve_refs_no_commits_falls_back(tmp_path: pathlib.Path) -> None:
    """With no commits, falls back to symbolic HEAD token."""
    _init_muse_repo(tmp_path)
    ref_a, ref_b = _resolve_refs(tmp_path, None, None)
    assert ref_b == "HEAD"
    assert ref_a == "HEAD~1"


# ---------------------------------------------------------------------------
# Unit — renderers
# ---------------------------------------------------------------------------


def test_render_harmonic_contains_key_fields() -> None:
    """_render_harmonic output contains commit refs and key change info."""
    result = _stub_harmonic("abc1234", "def5678")
    text = _render_harmonic(result)
    assert "abc1234" in text
    assert "def5678" in text
    assert result["key_a"] in text
    assert result["key_b"] in text
    assert result["summary"] in text


def test_render_rhythmic_shows_tempo_delta() -> None:
    """_render_rhythmic output shows tempo delta with sign."""
    result = _stub_rhythmic("abc1234", "def5678")
    text = _render_rhythmic(result)
    assert "BPM" in text
    assert result["summary"] in text


def test_render_melodic_lists_motifs() -> None:
    """_render_melodic output includes introduced motif names."""
    result = _stub_melodic("abc1234", "def5678")
    text = _render_melodic(result)
    for motif in result["motifs_introduced"]:
        assert motif in text


def test_render_structural_shows_sections() -> None:
    """_render_structural output lists added and removed sections."""
    result = _stub_structural("abc1234", "def5678")
    text = _render_structural(result)
    for section in result["sections_added"]:
        assert section in text


def test_render_dynamic_shows_velocity_delta() -> None:
    """_render_dynamic output includes avg velocity and arc change."""
    result = _stub_dynamic("abc1234", "def5678")
    text = _render_dynamic(result)
    assert str(result["avg_velocity_a"]) in text
    assert str(result["avg_velocity_b"]) in text
    assert result["arc_a"] in text
    assert result["arc_b"] in text


def test_render_report_contains_all_dimensions() -> None:
    """_render_report includes headers for all five dimensions."""
    import asyncio
    import pathlib

    async def _make_report() -> MusicDiffReport:
        return await _diff_all_async(
            root=pathlib.Path("/tmp"),
            commit_a="abc1234",
            commit_b="def5678",
        )

    report = asyncio.run(_make_report())
    text = _render_report(report)
    for dim in ("Harmonic", "Rhythmic", "Melodic", "Structural", "Dynamic"):
        assert dim in text


# ---------------------------------------------------------------------------
# Async core — individual dimension functions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_harmonic_diff_async_returns_correct_type(
    tmp_path: pathlib.Path,
) -> None:
    """_harmonic_diff_async returns a HarmonicDiffResult with correct commit refs."""
    _init_muse_repo(tmp_path)
    result = await _harmonic_diff_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    assert result["commit_a"] == "abc1234"
    assert result["commit_b"] == "def5678"
    assert isinstance(result["changed"], bool)


@pytest.mark.anyio
async def test_rhythmic_diff_async_returns_correct_type(
    tmp_path: pathlib.Path,
) -> None:
    """_rhythmic_diff_async returns a RhythmicDiffResult with correct commit refs."""
    _init_muse_repo(tmp_path)
    result = await _rhythmic_diff_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    assert result["commit_a"] == "abc1234"
    assert result["commit_b"] == "def5678"


@pytest.mark.anyio
async def test_melodic_diff_async_returns_correct_type(
    tmp_path: pathlib.Path,
) -> None:
    """_melodic_diff_async returns a MelodicDiffResult with correct commit refs."""
    _init_muse_repo(tmp_path)
    result = await _melodic_diff_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    assert result["commit_a"] == "abc1234"
    assert result["commit_b"] == "def5678"


@pytest.mark.anyio
async def test_structural_diff_async_returns_correct_type(
    tmp_path: pathlib.Path,
) -> None:
    """_structural_diff_async returns a StructuralDiffResult with correct commit refs."""
    _init_muse_repo(tmp_path)
    result = await _structural_diff_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    assert result["commit_a"] == "abc1234"
    assert result["commit_b"] == "def5678"


@pytest.mark.anyio
async def test_dynamic_diff_async_returns_correct_type(
    tmp_path: pathlib.Path,
) -> None:
    """_dynamic_diff_async returns a DynamicDiffResult with correct commit refs."""
    _init_muse_repo(tmp_path)
    result = await _dynamic_diff_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    assert result["commit_a"] == "abc1234"
    assert result["commit_b"] == "def5678"


# ---------------------------------------------------------------------------
# Regression — test_muse_diff_harmonic_flag_produces_harmonic_report
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_diff_harmonic_flag_produces_harmonic_report(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: --harmonic flag produces a harmonic-dimension report, not a generic file diff."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _harmonic_diff_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    text = _render_harmonic(result)
    assert "Harmonic diff" in text
    assert "Key:" in text
    assert "Chord prog:" in text
    assert "Tension:" in text
    assert "Summary:" in text


# ---------------------------------------------------------------------------
# Regression — test_muse_diff_rhythmic_flag_produces_rhythmic_report
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_diff_rhythmic_flag_produces_rhythmic_report(
    tmp_path: pathlib.Path,
) -> None:
    """Regression: --rhythmic flag produces a rhythmic-dimension report."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _rhythmic_diff_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    text = _render_rhythmic(result)
    assert "Rhythmic diff" in text
    assert "Tempo:" in text
    assert "Swing:" in text


# ---------------------------------------------------------------------------
# Regression — test_muse_diff_all_flag_combines_all_dimensions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_diff_all_flag_combines_all_dimensions(
    tmp_path: pathlib.Path,
) -> None:
    """Regression: --all produces a MusicDiffReport with all five dimensions populated."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    report = await _diff_all_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    assert report["harmonic"] is not None
    assert report["rhythmic"] is not None
    assert report["melodic"] is not None
    assert report["structural"] is not None
    assert report["dynamic"] is not None
    assert report["summary"]
    assert isinstance(report["changed_dimensions"], list)
    assert isinstance(report["unchanged_dimensions"], list)


# ---------------------------------------------------------------------------
# Regression — test_muse_diff_unchanged_dimension_reported_not_omitted
# ---------------------------------------------------------------------------


def test_muse_diff_unchanged_dimension_reported_not_omitted() -> None:
    """Regression: dimensions with no change report 'Unchanged', not an omission."""
    from maestro.muse_cli.commands.diff import (
        HarmonicDiffResult,
        _render_harmonic,
    )

    unchanged_result = HarmonicDiffResult(
        commit_a="abc1234",
        commit_b="def5678",
        key_a="C major",
        key_b="C major",
        mode_a="Major",
        mode_b="Major",
        chord_prog_a="I-IV-V-I",
        chord_prog_b="I-IV-V-I",
        tension_a=0.2,
        tension_b=0.2,
        tension_label_a="Low",
        tension_label_b="Low",
        summary="No harmonic change detected.",
        changed=False,
    )
    text = _render_harmonic(unchanged_result)
    assert "Unchanged" in text


# ---------------------------------------------------------------------------
# Async — _diff_all_async JSON roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_diff_all_json_roundtrip(tmp_path: pathlib.Path) -> None:
    """MusicDiffReport from _diff_all_async is JSON-serializable."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    report = await _diff_all_async(
        root=tmp_path, commit_a="abc1234", commit_b="def5678"
    )
    raw = json.dumps(dict(report))
    parsed = json.loads(raw)
    assert parsed["commit_a"] == "abc1234"
    assert parsed["commit_b"] == "def5678"
    assert "harmonic" in parsed
    assert "rhythmic" in parsed
    assert "melodic" in parsed
    assert "structural" in parsed
    assert "dynamic" in parsed


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_diff_no_flags_exits_success(tmp_path: pathlib.Path) -> None:
    """``muse diff`` with no dimension flags exits 0 and prints usage hint."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.SUCCESS)
    assert "--harmonic" in result.output or "dimension flag" in result.output


def test_cli_diff_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse diff --harmonic`` exits 2 when invoked outside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--harmonic"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_diff_help_lists_all_flags() -> None:
    """``muse diff --help`` documents all six dimension flags."""
    result = runner.invoke(cli, ["diff", "--help"])
    assert result.exit_code == 0
    for flag in ("--harmonic", "--rhythmic", "--melodic", "--structural", "--dynamic", "--all", "--json"):
        assert flag in result.output, f"Flag '{flag}' missing from help"


def test_cli_diff_appears_in_muse_help() -> None:
    """``muse --help`` lists the diff subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "diff" in result.output


def test_cli_diff_harmonic_flag_shows_harmonic_report(tmp_path: pathlib.Path) -> None:
    """``muse diff --harmonic`` shows a harmonic diff block."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--harmonic"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Harmonic diff" in result.output
    assert "Key:" in result.output


def test_cli_diff_rhythmic_flag_shows_rhythmic_report(tmp_path: pathlib.Path) -> None:
    """``muse diff --rhythmic`` shows a rhythmic diff block."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--rhythmic"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Rhythmic diff" in result.output
    assert "Tempo:" in result.output


def test_cli_diff_melodic_flag_shows_melodic_report(tmp_path: pathlib.Path) -> None:
    """``muse diff --melodic`` shows a melodic diff block."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--melodic"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Melodic diff" in result.output


def test_cli_diff_structural_flag_shows_structural_report(tmp_path: pathlib.Path) -> None:
    """``muse diff --structural`` shows a structural diff block."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--structural"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Structural diff" in result.output


def test_cli_diff_dynamic_flag_shows_dynamic_report(tmp_path: pathlib.Path) -> None:
    """``muse diff --dynamic`` shows a dynamic diff block."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--dynamic"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Dynamic diff" in result.output


def test_cli_diff_all_flag_shows_all_dimensions(tmp_path: pathlib.Path) -> None:
    """``muse diff --all`` shows all five dimension blocks in one report."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--all"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    for dim in ("Harmonic", "Rhythmic", "Melodic", "Structural", "Dynamic"):
        assert dim in result.output, f"Dimension '{dim}' missing from --all output"


def test_cli_diff_json_flag_produces_valid_json(tmp_path: pathlib.Path) -> None:
    """``muse diff --harmonic --json`` emits parseable JSON."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--harmonic", "--json"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "commit_a" in payload
    assert "commit_b" in payload
    assert "key_a" in payload
    assert "key_b" in payload


def test_cli_diff_all_json_flag_produces_valid_json(tmp_path: pathlib.Path) -> None:
    """``muse diff --all --json`` emits parseable JSON with all dimensions."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["diff", "--all", "--json"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "harmonic" in payload
    assert "rhythmic" in payload
    assert "melodic" in payload
    assert "structural" in payload
    assert "dynamic" in payload
    assert "changed_dimensions" in payload
    assert "unchanged_dimensions" in payload


def test_cli_diff_multiple_flags_shows_multiple_blocks(tmp_path: pathlib.Path) -> None:
    """``muse diff --harmonic --rhythmic`` shows both dimension blocks."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["diff", "--harmonic", "--rhythmic"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Harmonic diff" in result.output
    assert "Rhythmic diff" in result.output


@pytest.mark.anyio
async def test_explicit_commits_appear_in_output(tmp_path: pathlib.Path) -> None:
    """Explicit commit refs are threaded through to the harmonic diff output."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _harmonic_diff_async(
        root=tmp_path, commit_a="aabbccdd", commit_b="eeffgghh"
    )
    text = _render_harmonic(result)
    assert "aabbccdd" in text
    assert "eeffgghh" in text
