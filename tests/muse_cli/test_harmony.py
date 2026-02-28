"""Tests for ``muse harmony`` — CLI interface, flag parsing, and stub output format.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so that argument parsing, flag handling, and exit codes are exercised
end-to-end.

Async core tests call ``_harmony_analyze_async`` directly with an in-memory
SQLite session (the stub does not query the DB, so the session satisfies
the signature contract only).
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
import maestro.muse_cli.models  # noqa: F401  — registers MuseCli* with Base.metadata
from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.harmony import (
    KNOWN_MODES,
    KNOWN_MODES_SET,
    HarmonyCompareResult,
    HarmonyResult,
    _harmony_analyze_async,
    _render_compare_human,
    _render_compare_json,
    _render_result_human,
    _render_result_json,
    _stub_harmony,
    _tension_label,
)
from maestro.muse_cli.errors import ExitCode

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
    """In-memory SQLite session (stub harmony does not actually query it)."""
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
# Unit — constants and helpers
# ---------------------------------------------------------------------------


def test_known_modes_set_matches_tuple() -> None:
    """KNOWN_MODES_SET and KNOWN_MODES tuple are in sync."""
    assert set(KNOWN_MODES) == KNOWN_MODES_SET


def test_known_modes_contains_standard_modes() -> None:
    """All seven standard modes plus major/minor are present."""
    for mode in ("major", "minor", "dorian", "mixolydian", "lydian"):
        assert mode in KNOWN_MODES_SET


def test_tension_label_rising() -> None:
    """Monotonically rising profile is labeled as 'Rising'."""
    label = _tension_label([0.1, 0.3, 0.6, 0.9])
    assert "Rising" in label


def test_tension_label_falling() -> None:
    """Monotonically falling profile is labeled as 'Falling'."""
    label = _tension_label([0.9, 0.6, 0.3, 0.1])
    assert "Falling" in label


def test_tension_label_arch() -> None:
    """Low-high-low arc produces a 'tension-release' label."""
    label = _tension_label([0.2, 0.4, 0.8, 0.3])
    assert "Resolution" in label or "release" in label.lower()


def test_tension_label_single_value_low() -> None:
    label = _tension_label([0.1])
    assert label == "Low"


def test_tension_label_single_value_high() -> None:
    label = _tension_label([0.8])
    assert label == "High"


def test_tension_label_empty() -> None:
    assert _tension_label([]) == "unknown"


# ---------------------------------------------------------------------------
# Unit — stub data
# ---------------------------------------------------------------------------


def test_stub_harmony_returns_harmony_result() -> None:
    """_stub_harmony returns a HarmonyResult with the expected fields."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    assert result["commit_id"] == "a1b2c3d4"
    assert result["branch"] == "main"
    assert result["key"] is not None
    assert result["mode"] in KNOWN_MODES_SET
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["chord_progression"], list)
    assert len(result["chord_progression"]) > 0
    assert result["harmonic_rhythm_avg"] > 0
    assert isinstance(result["tension_profile"], list)
    assert result["track"] == "all"
    assert result["source"] == "stub"


def test_stub_harmony_track_scope() -> None:
    """_stub_harmony respects the track argument."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main", track="keys")
    assert result["track"] == "keys"


def test_stub_harmony_chord_progression_are_strings() -> None:
    """Every chord in the stub progression is a non-empty string."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    for chord in result["chord_progression"]:
        assert isinstance(chord, str)
        assert len(chord) > 0


# ---------------------------------------------------------------------------
# Unit — renderers
# ---------------------------------------------------------------------------


def test_render_result_human_full(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_result_human with no flags shows key, mode, chords, and tension."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    _render_result_human(result, False, False, False, False)
    out = capsys.readouterr().out
    assert "Harmonic Analysis" in out
    assert "Key:" in out
    assert "Mode:" in out
    assert "Chord progression:" in out
    assert "Tension profile:" in out


def test_render_result_human_key_only(capsys: pytest.CaptureFixture[str]) -> None:
    """--key shows only the key line."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    _render_result_human(result, False, True, False, False)
    out = capsys.readouterr().out
    assert "Key:" in out
    assert "Mode:" not in out
    assert "Chord progression:" not in out
    assert "Tension profile:" not in out


def test_render_result_human_mode_only(capsys: pytest.CaptureFixture[str]) -> None:
    """--mode shows only the mode line."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    _render_result_human(result, False, False, True, False)
    out = capsys.readouterr().out
    assert "Mode:" in out
    assert "Key:" not in out


def test_render_result_human_progression_only(capsys: pytest.CaptureFixture[str]) -> None:
    """--progression shows only the chord progression line."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    _render_result_human(result, True, False, False, False)
    out = capsys.readouterr().out
    assert "Chord progression:" in out
    assert "Key:" not in out
    assert "Tension profile:" not in out


def test_render_result_human_tension_only(capsys: pytest.CaptureFixture[str]) -> None:
    """--tension shows only the tension profile line."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    _render_result_human(result, False, False, False, True)
    out = capsys.readouterr().out
    assert "Tension profile:" in out
    assert "Key:" not in out
    assert "Chord progression:" not in out


def test_render_result_json_full(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_result_json with no flags emits all HarmonyResult fields."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    _render_result_json(result, False, False, False, False)
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    for field in ("commit_id", "branch", "key", "mode", "confidence",
                  "chord_progression", "harmonic_rhythm_avg", "tension_profile"):
        assert field in payload, f"Missing field: {field}"


def test_render_result_json_key_only(capsys: pytest.CaptureFixture[str]) -> None:
    """--key JSON includes only key and confidence."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    _render_result_json(result, False, True, False, False)
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert "key" in payload
    assert "confidence" in payload
    assert "mode" not in payload
    assert "chord_progression" not in payload


def test_render_result_json_progression_only(capsys: pytest.CaptureFixture[str]) -> None:
    """--progression JSON includes only chord_progression."""
    result = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    _render_result_json(result, True, False, False, False)
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert "chord_progression" in payload
    assert "key" not in payload


def test_render_compare_human(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_compare_human shows both commits and change flags."""
    head = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    ref = _stub_harmony(commit_id="deadbeef", branch="main")
    cmp: HarmonyCompareResult = HarmonyCompareResult(
        head=head,
        compare=ref,
        key_changed=False,
        mode_changed=False,
        chord_progression_delta=[],
    )
    _render_compare_human(cmp)
    out = capsys.readouterr().out
    assert "a1b2c3d4" in out
    assert "deadbeef" in out
    assert "Key changed" in out


def test_render_compare_json(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_compare_json emits valid JSON with head/compare/delta keys."""
    head = _stub_harmony(commit_id="a1b2c3d4", branch="main")
    ref = _stub_harmony(commit_id="deadbeef", branch="main")
    cmp: HarmonyCompareResult = HarmonyCompareResult(
        head=head,
        compare=ref,
        key_changed=False,
        mode_changed=False,
        chord_progression_delta=[],
    )
    _render_compare_json(cmp)
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert "head" in payload
    assert "compare" in payload
    assert "key_changed" in payload
    assert "chord_progression_delta" in payload


# ---------------------------------------------------------------------------
# Async core — _harmony_analyze_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_harmony_async_default_output(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_harmony_analyze_async with no flags shows full harmonic summary."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Harmonic Analysis" in out
    assert "Key:" in out
    assert "Mode:" in out
    assert "Chord progression:" in out
    assert "Tension profile:" in out
    assert result["source"] == "stub"


@pytest.mark.anyio
async def test_harmony_async_json_mode(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_harmony_analyze_async --json emits valid JSON with all HarmonyResult fields."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    for field in ("commit_id", "branch", "key", "mode", "confidence",
                  "chord_progression", "harmonic_rhythm_avg", "tension_profile"):
        assert field in payload, f"Missing field: {field}"


@pytest.mark.anyio
async def test_harmony_async_no_commits_exits_success(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no commits and no explicit commit arg, exits 0 with informative message."""
    _init_muse_repo(tmp_path)
    # No _commit_ref call — branch ref is empty.

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _harmony_analyze_async(
            root=tmp_path,
            session=db_session,
            commit=None,
            track=None,
            section=None,
            compare=None,
            commit_range=None,
            show_progression=False,
            show_key=False,
            show_mode=False,
            show_tension=False,
            as_json=False,
        )
    assert exc_info.value.exit_code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "No commits yet" in out


@pytest.mark.anyio
async def test_harmony_async_explicit_commit_ref(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit commit ref appears in the output."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit="deadbeef",
        track=None,
        section=None,
        compare=None,
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "deadbeef" in out
    assert result["commit_id"] == "deadbeef"


@pytest.mark.anyio
async def test_harmony_async_track_scoped(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--track is reflected in the result track field."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track="keys",
        section=None,
        compare=None,
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=False,
    )

    assert result["track"] == "keys"


@pytest.mark.anyio
async def test_harmony_async_progression_flag(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--progression shows chord progression and suppresses other fields."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        commit_range=None,
        show_progression=True,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Chord progression:" in out
    assert "Key:" not in out
    assert "Tension profile:" not in out


@pytest.mark.anyio
async def test_harmony_async_key_flag(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--key shows key center and suppresses other fields."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        commit_range=None,
        show_progression=False,
        show_key=True,
        show_mode=False,
        show_tension=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Key:" in out
    assert "Mode:" not in out


@pytest.mark.anyio
async def test_harmony_async_mode_flag(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--mode shows mode and suppresses other fields."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=True,
        show_tension=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Mode:" in out
    assert "Key:" not in out


@pytest.mark.anyio
async def test_harmony_async_tension_flag(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--tension shows tension profile and suppresses other fields."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=True,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Tension profile:" in out
    assert "Key:" not in out


@pytest.mark.anyio
async def test_harmony_async_compare_mode(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--compare renders a comparison between HEAD and the reference commit."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare="deadbeef",
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "deadbeef" in out
    assert "Harmonic Comparison" in out


@pytest.mark.anyio
async def test_harmony_async_compare_json(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--compare --json emits a HarmonyCompareResult as JSON."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare="deadbeef",
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert "head" in payload
    assert "compare" in payload
    assert "key_changed" in payload
    assert "mode_changed" in payload
    assert "chord_progression_delta" in payload


@pytest.mark.anyio
async def test_harmony_async_range_flag_warns(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--range emits a stub boundary warning but still renders HEAD result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        commit_range="HEAD~10..HEAD",
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "--range" in out
    assert "Harmonic Analysis" in out


@pytest.mark.anyio
async def test_harmony_async_section_flag_warns(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--section emits a stub boundary warning but still renders HEAD result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _harmony_analyze_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section="verse",
        compare=None,
        commit_range=None,
        show_progression=False,
        show_key=False,
        show_mode=False,
        show_tension=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "--section" in out
    assert "Harmonic Analysis" in out


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_harmony_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse harmony`` exits 2 when invoked outside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["harmony"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_harmony_help_lists_flags() -> None:
    """``muse harmony --help`` shows all documented flags."""
    result = runner.invoke(cli, ["harmony", "--help"])
    assert result.exit_code == 0
    for flag in ("--track", "--section", "--compare", "--range", "--progression",
                 "--key", "--mode", "--tension", "--json"):
        assert flag in result.output, f"Flag '{flag}' not found in help output"


def test_cli_harmony_appears_in_muse_help() -> None:
    """``muse --help`` lists the harmony subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "harmony" in result.output
