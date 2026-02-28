"""Tests for ``muse context``.

All async tests call ``_context_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running process
required.  Commits are seeded via ``_commit_async`` so the full pipeline
(commit → context) is exercised as an integrated pair.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.context import OutputFormat, _context_async
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_context import (
    MuseContextResult,
    MuseHeadCommitInfo,
    MuseHistoryEntry,
    MuseMusicalState,
    build_muse_context,
    _extract_track_names,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Initialise a minimal .muse/ repo structure under *root*."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    for name, content in files.items():
        (workdir / name).write_bytes(content)


async def _make_commits(
    root: pathlib.Path,
    session: AsyncSession,
    messages: list[str],
    file_seed: int = 0,
) -> list[str]:
    """Create N commits on the repo with different file content per commit."""
    commit_ids: list[str] = []
    for i, msg in enumerate(messages):
        _write_workdir(
            root,
            {f"track_{file_seed + i}.mid": f"MIDI-{file_seed + i}".encode()},
        )
        cid = await _commit_async(message=msg, root=root, session=session)
        commit_ids.append(cid)
    return commit_ids


# ---------------------------------------------------------------------------
# Unit tests for _extract_track_names
# ---------------------------------------------------------------------------


def test_extract_track_names_midi_files() -> None:
    """MIDI file stems become track names, sorted and de-duplicated."""
    manifest = {"drums.mid": "aaa", "bass.mid": "bbb", "piano.midi": "ccc"}
    result = _extract_track_names(manifest)
    assert result == ["bass", "drums", "piano"]


def test_extract_track_names_ignores_non_music_files() -> None:
    """Non-music files (JSON, txt, png) are not treated as tracks."""
    manifest = {
        "drums.mid": "aaa",
        "README.txt": "bbb",
        "cover.png": "ccc",
        "meta.json": "ddd",
    }
    result = _extract_track_names(manifest)
    assert result == ["drums"]


def test_extract_track_names_ignores_hash_stems() -> None:
    """64-char hex stems that look like SHA-256 hashes are filtered out."""
    sha = "a" * 64
    manifest = {f"{sha}.mid": "abc", "bass.mid": "def"}
    result = _extract_track_names(manifest)
    assert result == ["bass"]


def test_extract_track_names_case_insensitive() -> None:
    """File extensions are matched case-insensitively."""
    manifest = {"Drums.MID": "aaa", "Bass.Mp3": "bbb"}
    result = _extract_track_names(manifest)
    assert result == ["bass", "drums"]


def test_extract_track_names_empty_manifest() -> None:
    """An empty manifest returns an empty list."""
    assert _extract_track_names({}) == []


# ---------------------------------------------------------------------------
# test_muse_context_returns_full_musical_state_at_head
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_returns_full_musical_state_at_head(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """All top-level keys are present in the result at HEAD."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"drums.mid": b"MIDI-drums", "bass.mid": b"MIDI-bass"})
    await _commit_async(message="initial commit", root=tmp_path, session=muse_cli_db_session)

    result = await build_muse_context(
        muse_cli_db_session, root=tmp_path, depth=5
    )

    assert isinstance(result, MuseContextResult)
    assert isinstance(result.head_commit, MuseHeadCommitInfo)
    assert isinstance(result.musical_state, MuseMusicalState)
    assert isinstance(result.history, list)
    assert isinstance(result.missing_elements, list)
    assert isinstance(result.suggestions, dict)
    assert result.current_branch == "main"
    assert result.head_commit.message == "initial commit"


# ---------------------------------------------------------------------------
# test_muse_context_active_tracks_from_manifest
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_active_tracks_from_manifest(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """active_tracks is derived from MIDI file names in the snapshot manifest."""
    _init_muse_repo(tmp_path)
    _write_workdir(
        tmp_path,
        {"drums.mid": b"d", "bass.mid": b"b", "piano.mid": b"p"},
    )
    await _commit_async(message="three tracks", root=tmp_path, session=muse_cli_db_session)

    result = await build_muse_context(muse_cli_db_session, root=tmp_path)

    assert sorted(result.musical_state.active_tracks) == ["bass", "drums", "piano"]


# ---------------------------------------------------------------------------
# test_muse_context_depth_limits_history_length
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_depth_limits_history_length(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """--depth 3 returns exactly 3 history entries for a chain of 5 commits."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        ["c1", "c2", "c3", "c4", "c5"],
    )

    result = await build_muse_context(muse_cli_db_session, root=tmp_path, depth=3)

    assert len(result.history) == 3


# ---------------------------------------------------------------------------
# test_muse_context_depth_zero_returns_empty_history
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_depth_zero_returns_empty_history(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """depth=0 omits history entirely."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["a", "b", "c"])

    result = await build_muse_context(muse_cli_db_session, root=tmp_path, depth=0)

    assert result.history == []


# ---------------------------------------------------------------------------
# test_muse_context_sections_flag_expands_section_detail
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_sections_flag_expands_section_detail(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """--sections populates musical_state.sections with track names."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"drums.mid": b"d", "bass.mid": b"b"})
    await _commit_async(
        message="with sections", root=tmp_path, session=muse_cli_db_session
    )

    result = await build_muse_context(
        muse_cli_db_session, root=tmp_path, include_sections=True
    )

    assert result.musical_state.sections is not None
    assert "main" in result.musical_state.sections
    section = result.musical_state.sections["main"]
    assert sorted(section.tracks) == ["bass", "drums"]


# ---------------------------------------------------------------------------
# test_muse_context_sections_flag_false_omits_sections
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_sections_flag_false_omits_sections(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Without --sections, musical_state.sections is None."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"drums.mid": b"d"})
    await _commit_async(message="no sections", root=tmp_path, session=muse_cli_db_session)

    result = await build_muse_context(muse_cli_db_session, root=tmp_path)

    assert result.musical_state.sections is None


# ---------------------------------------------------------------------------
# test_muse_context_tracks_flag_adds_per_track_breakdown
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_tracks_flag_adds_per_track_breakdown(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """--tracks populates musical_state.tracks with one entry per active track."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"drums.mid": b"d", "bass.mid": b"b"})
    await _commit_async(message="tracks test", root=tmp_path, session=muse_cli_db_session)

    result = await build_muse_context(
        muse_cli_db_session, root=tmp_path, include_tracks=True
    )

    assert result.musical_state.tracks is not None
    track_names = sorted(t.track_name for t in result.musical_state.tracks)
    assert track_names == ["bass", "drums"]


# ---------------------------------------------------------------------------
# test_muse_context_specific_commit_resolves_correctly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_specific_commit_resolves_correctly(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Passing an explicit commit_id returns context for that commit, not HEAD."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path, muse_cli_db_session, ["first commit", "second commit"]
    )
    first_commit_id = cids[0]

    result = await build_muse_context(
        muse_cli_db_session, root=tmp_path, commit_id=first_commit_id
    )

    assert result.head_commit.commit_id == first_commit_id
    assert result.head_commit.message == "first commit"


# ---------------------------------------------------------------------------
# test_muse_context_no_commits_raises_runtime_error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_no_commits_raises_runtime_error(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """build_muse_context raises RuntimeError when the repo has no commits."""
    _init_muse_repo(tmp_path)

    with pytest.raises(RuntimeError, match="no commits yet"):
        await build_muse_context(muse_cli_db_session, root=tmp_path)


# ---------------------------------------------------------------------------
# test_muse_context_unknown_commit_raises_value_error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_unknown_commit_raises_value_error(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """build_muse_context raises ValueError for an unknown commit_id."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["one commit"])

    with pytest.raises(ValueError, match="not found in DB"):
        await build_muse_context(
            muse_cli_db_session,
            root=tmp_path,
            commit_id="nonexistent" * 5,
        )


# ---------------------------------------------------------------------------
# test_muse_context_to_dict_is_serialisable
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_to_dict_is_serialisable(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """to_dict() produces a dict that round-trips cleanly through json.dumps."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"drums.mid": b"d"})
    await _commit_async(message="json test", root=tmp_path, session=muse_cli_db_session)

    result = await build_muse_context(muse_cli_db_session, root=tmp_path)

    d = result.to_dict()
    serialised = json.dumps(d, default=str)
    parsed = json.loads(serialised)

    assert parsed["current_branch"] == "main"
    assert "musical_state" in parsed
    assert "head_commit" in parsed


# ---------------------------------------------------------------------------
# test_muse_context_format_json_outputs_valid_json (via _context_async)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_format_json_outputs_valid_json(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_context_async with json format emits valid JSON to stdout."""
    _init_muse_repo(tmp_path)
    _write_workdir(tmp_path, {"drums.mid": b"d"})
    await _commit_async(message="json format", root=tmp_path, session=muse_cli_db_session)

    capsys.readouterr()
    await _context_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        depth=5,
        sections=False,
        tracks=False,
        include_history=False,
        fmt=OutputFormat.json,
    )
    out = capsys.readouterr().out

    parsed = json.loads(out)
    assert "repo_id" in parsed
    assert "musical_state" in parsed
    assert "head_commit" in parsed
    assert "history" in parsed


# ---------------------------------------------------------------------------
# test_muse_context_history_entries_are_newest_first
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_context_history_entries_are_newest_first(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """History entries appear newest-first (most recent ancestor at index 0)."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path, muse_cli_db_session, ["oldest", "middle", "head"]
    )

    result = await build_muse_context(
        muse_cli_db_session, root=tmp_path, depth=5
    )

    assert len(result.history) == 2
    assert result.history[0].commit_id == cids[1]  # middle
    assert result.history[1].commit_id == cids[0]  # oldest


# ---------------------------------------------------------------------------
# test_muse_context_outside_repo_exits_2 (CLI integration)
# ---------------------------------------------------------------------------


def test_muse_context_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """muse context outside a .muse/ directory exits with code 2."""
    import os

    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["context"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND
