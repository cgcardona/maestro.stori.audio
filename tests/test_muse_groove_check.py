"""Tests for ``muse groove-check`` — CLI interface, flag parsing, and stub output format.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so that argument parsing, flag handling, and exit codes are exercised end-to-end.

Async core tests call ``_groove_check_async`` directly with an in-memory SQLite
session (the stub does not query the DB; the session satisfies the signature
contract only).
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
from maestro.muse_cli.commands.groove_check import (
    _groove_check_async,
    _render_json,
    _render_table,
)
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_groove_check import (
    DEFAULT_THRESHOLD,
    CommitGrooveMetrics,
    GrooveCheckResult,
    GrooveStatus,
    build_stub_entries,
    classify_status,
    compute_groove_check,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
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
    """Write a fake commit ID into the branch ref so HEAD is non-empty."""
    muse = root / ".muse"
    (muse / "refs" / "heads" / branch).write_text("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2")


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session (stub groove-check does not actually query it)."""
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
# Unit — classify_status
# ---------------------------------------------------------------------------


def test_classify_status_ok_below_threshold() -> None:
    """delta ≤ threshold → OK."""
    assert classify_status(0.05, 0.1) == GrooveStatus.OK


def test_classify_status_ok_at_threshold() -> None:
    """delta exactly at threshold → OK (inclusive boundary)."""
    assert classify_status(0.1, 0.1) == GrooveStatus.OK


def test_classify_status_warn_between_threshold_and_double() -> None:
    """threshold < delta ≤ 2× threshold → WARN."""
    assert classify_status(0.15, 0.1) == GrooveStatus.WARN


def test_classify_status_fail_above_double_threshold() -> None:
    """delta > 2× threshold → FAIL."""
    assert classify_status(0.25, 0.1) == GrooveStatus.FAIL


def test_classify_status_zero_delta_ok() -> None:
    """First commit always has delta 0.0 → OK regardless of threshold."""
    assert classify_status(0.0, 0.1) == GrooveStatus.OK


# ---------------------------------------------------------------------------
# Unit — build_stub_entries
# ---------------------------------------------------------------------------


def test_build_stub_entries_returns_correct_limit() -> None:
    """build_stub_entries respects the limit argument."""
    entries = build_stub_entries(threshold=0.1, track=None, section=None, limit=3)
    assert len(entries) == 3


def test_build_stub_entries_first_entry_has_zero_delta() -> None:
    """The oldest commit in the window always has drift_delta == 0.0."""
    entries = build_stub_entries(threshold=0.1, track=None, section=None, limit=5)
    assert entries[0].drift_delta == 0.0


def test_build_stub_entries_track_stored_in_metadata() -> None:
    """Track filter is stored in each entry's track field."""
    entries = build_stub_entries(threshold=0.1, track="drums", section=None, limit=3)
    for e in entries:
        assert e.track == "drums"


def test_build_stub_entries_section_stored_in_metadata() -> None:
    """Section filter is stored in each entry's section field."""
    entries = build_stub_entries(threshold=0.1, track=None, section="verse", limit=3)
    for e in entries:
        assert e.section == "verse"


def test_build_stub_entries_status_matches_classification() -> None:
    """Each entry's status is consistent with classify_status(drift_delta, threshold)."""
    threshold = 0.05
    entries = build_stub_entries(threshold=threshold, track=None, section=None, limit=7)
    for e in entries:
        expected = classify_status(e.drift_delta, threshold)
        assert e.status == expected, f"{e.commit}: status mismatch"


def test_build_stub_entries_groove_scores_positive() -> None:
    """All groove_score values are non-negative."""
    entries = build_stub_entries(threshold=0.1, track=None, section=None, limit=7)
    for e in entries:
        assert e.groove_score >= 0.0, f"{e.commit}: negative groove_score"


# ---------------------------------------------------------------------------
# Unit — compute_groove_check
# ---------------------------------------------------------------------------


def test_compute_groove_check_returns_result() -> None:
    """compute_groove_check returns a GrooveCheckResult with entries."""
    result = compute_groove_check(commit_range="HEAD~5..HEAD")
    assert isinstance(result, GrooveCheckResult)
    assert len(result.entries) > 0


def test_compute_groove_check_stores_range() -> None:
    """commit_range is echoed in the result."""
    result = compute_groove_check(commit_range="abc123..def456")
    assert result.commit_range == "abc123..def456"


def test_compute_groove_check_stores_threshold() -> None:
    """threshold is echoed in the result."""
    result = compute_groove_check(commit_range="HEAD~5..HEAD", threshold=0.05)
    assert result.threshold == 0.05


def test_compute_groove_check_flagged_count_consistent() -> None:
    """flagged_commits == number of entries whose status != OK."""
    result = compute_groove_check(commit_range="HEAD~10..HEAD", threshold=0.01)
    manual_count = sum(1 for e in result.entries if e.status != GrooveStatus.OK)
    assert result.flagged_commits == manual_count


def test_compute_groove_check_worst_commit_has_max_delta() -> None:
    """worst_commit refers to the entry with the largest drift_delta."""
    result = compute_groove_check(commit_range="HEAD~10..HEAD")
    if result.worst_commit:
        max_entry = max(result.entries, key=lambda e: e.drift_delta)
        assert result.worst_commit == max_entry.commit


def test_compute_groove_check_tight_threshold_flags_more() -> None:
    """A tighter threshold flags more commits than a loose one."""
    loose = compute_groove_check(commit_range="HEAD~10..HEAD", threshold=0.5)
    tight = compute_groove_check(commit_range="HEAD~10..HEAD", threshold=0.01)
    assert tight.flagged_commits >= loose.flagged_commits


# ---------------------------------------------------------------------------
# Unit — renderers
# ---------------------------------------------------------------------------


def test_render_table_outputs_header(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_table includes the range, threshold, and column headers."""
    result = compute_groove_check(commit_range="HEAD~5..HEAD", threshold=0.1)
    _render_table(result)
    out = capsys.readouterr().out
    assert "Groove-check" in out
    assert "HEAD~5..HEAD" in out
    assert "0.1 beats" in out
    assert "Commit" in out
    assert "Groove Score" in out
    assert "Drift" in out
    assert "Status" in out


def test_render_table_shows_all_commits(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_table emits one row per entry."""
    result = compute_groove_check(commit_range="HEAD~5..HEAD")
    _render_table(result)
    out = capsys.readouterr().out
    for entry in result.entries:
        assert entry.commit in out


def test_render_table_shows_flagged_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_table includes 'Flagged:' summary line."""
    result = compute_groove_check(commit_range="HEAD~5..HEAD")
    _render_table(result)
    out = capsys.readouterr().out
    assert "Flagged:" in out


def test_render_json_is_valid(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_json emits parseable JSON with the expected top-level keys."""
    result = compute_groove_check(commit_range="HEAD~5..HEAD", threshold=0.1)
    _render_json(result)
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["commit_range"] == "HEAD~5..HEAD"
    assert payload["threshold"] == 0.1
    assert "total_commits" in payload
    assert "flagged_commits" in payload
    assert "worst_commit" in payload
    assert isinstance(payload["entries"], list)


def test_render_json_entries_have_required_fields(capsys: pytest.CaptureFixture[str]) -> None:
    """Each JSON entry contains all required per-commit fields."""
    result = compute_groove_check(commit_range="HEAD~5..HEAD")
    _render_json(result)
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    required = {"commit", "groove_score", "drift_delta", "status", "track", "section", "midi_files"}
    for entry in payload["entries"]:
        assert required.issubset(entry.keys()), f"Missing fields in entry: {entry}"


def test_render_json_status_values_valid(capsys: pytest.CaptureFixture[str]) -> None:
    """All JSON status values are valid GrooveStatus members."""
    valid = {s.value for s in GrooveStatus}
    result = compute_groove_check(commit_range="HEAD~5..HEAD", threshold=0.01)
    _render_json(result)
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    for entry in payload["entries"]:
        assert entry["status"] in valid, f"Unknown status: {entry['status']}"


# ---------------------------------------------------------------------------
# Async core — _groove_check_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_groove_check_async_default_output(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_groove_check_async with no filters shows a table with commit rows."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _groove_check_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        track=None,
        section=None,
        threshold=DEFAULT_THRESHOLD,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Groove-check" in out
    assert "Flagged:" in out


@pytest.mark.anyio
async def test_groove_check_async_json_mode(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_groove_check_async --json emits valid JSON with entries list."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _groove_check_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        track=None,
        section=None,
        threshold=DEFAULT_THRESHOLD,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert "entries" in payload
    assert len(payload["entries"]) > 0


@pytest.mark.anyio
async def test_groove_check_async_explicit_range(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit commit range appears in the table header."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _groove_check_async(
        root=tmp_path,
        session=db_session,
        commit_range="HEAD~3..HEAD",
        track=None,
        section=None,
        threshold=DEFAULT_THRESHOLD,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "HEAD~3..HEAD" in out


@pytest.mark.anyio
async def test_groove_check_async_track_filter_stored(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--track is propagated to the result entries."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _groove_check_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        track="drums",
        section=None,
        threshold=DEFAULT_THRESHOLD,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    for entry in payload["entries"]:
        assert entry["track"] == "drums"


@pytest.mark.anyio
async def test_groove_check_async_section_filter_stored(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--section is propagated to the result entries."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _groove_check_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        track=None,
        section="verse",
        threshold=DEFAULT_THRESHOLD,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    for entry in payload["entries"]:
        assert entry["section"] == "verse"


@pytest.mark.anyio
async def test_groove_check_async_custom_threshold(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Custom threshold is reflected in the JSON output."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _groove_check_async(
        root=tmp_path,
        session=db_session,
        commit_range=None,
        track=None,
        section=None,
        threshold=0.05,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["threshold"] == 0.05


@pytest.mark.anyio
async def test_groove_check_async_invalid_threshold_exits(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """threshold ≤ 0 exits with USER_ERROR."""
    import typer

    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _groove_check_async(
            root=tmp_path,
            session=db_session,
            commit_range=None,
            track=None,
            section=None,
            threshold=0.0,
            as_json=False,
        )
    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


@pytest.mark.anyio
async def test_groove_check_async_no_commits_exits_success(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no commits and no explicit range, exits 0 with informative message."""
    import typer

    _init_muse_repo(tmp_path)
    # No _commit_ref call — branch ref is empty.

    with pytest.raises(typer.Exit) as exc_info:
        await _groove_check_async(
            root=tmp_path,
            session=db_session,
            commit_range=None,
            track=None,
            section=None,
            threshold=DEFAULT_THRESHOLD,
            as_json=False,
        )
    assert exc_info.value.exit_code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "No commits yet" in out


# ---------------------------------------------------------------------------
# Regression — test_groove_check_outputs_table_with_drift_status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_groove_check_outputs_table_with_drift_status(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: groove-check always outputs a table with commit/drift/status columns.

    This is the primary acceptance-criteria test from issue #95:
    the table must include commit refs, groove_score, drift_delta, and a status
    column with OK/WARN/FAIL values.
    """
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _groove_check_async(
        root=tmp_path,
        session=db_session,
        commit_range="HEAD~6..HEAD",
        track=None,
        section=None,
        threshold=DEFAULT_THRESHOLD,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Groove-check" in out
    assert "Commit" in out
    assert "Groove Score" in out
    assert "Drift" in out
    assert "Status" in out
    # At least one valid status label must appear
    assert any(status in out for status in ("OK", "WARN", "FAIL"))


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_groove_check_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse groove-check`` exits 2 when invoked outside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["groove-check"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_groove_check_help_lists_flags(tmp_path: pathlib.Path) -> None:
    """``muse groove-check --help`` shows all documented flags."""
    result = runner.invoke(cli, ["groove-check", "--help"])
    assert result.exit_code == 0
    for flag in ("--track", "--section", "--threshold", "--json"):
        assert flag in result.output, f"Flag '{flag}' not found in help output"


def test_cli_groove_check_appears_in_muse_help() -> None:
    """``muse --help`` lists the groove-check subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "groove-check" in result.output
