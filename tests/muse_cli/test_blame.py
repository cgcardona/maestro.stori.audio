"""Tests for ``muse blame``.

All async tests call ``_blame_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running process
required.  Commits are seeded via ``_commit_async`` so blame and commit
are tested as an integrated pair.

Covered scenarios:

- ``test_blame_returns_last_commit_per_path``  (regression)
- ``test_blame_path_filter_restricts_output``
- ``test_blame_track_filter_glob``
- ``test_blame_section_filter``
- ``test_blame_json_output``
- ``test_blame_no_commits_exits_zero``
- ``test_blame_outside_repo_exits_2``
- ``test_blame_single_commit_all_added``
- ``test_blame_unmodified_file_attributes_oldest_commit``
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.blame import _blame_async, _render_blame
from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_file(root: pathlib.Path, rel_path: str, content: bytes) -> None:
    """Write a file inside muse-work/ at the given relative path."""
    target = root / "muse-work" / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


# ---------------------------------------------------------------------------
# test_blame_returns_last_commit_per_path (regression)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_returns_last_commit_per_path(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Blame returns the most-recent commit that changed each path.

    Regression for issue #72: ``muse blame <path>`` must walk the commit
    graph and return the correct last-change commit, not simply the HEAD
    commit for all paths.
    """
    _init_muse_repo(tmp_path)

    # Commit 1: add two files
    _write_file(tmp_path, "bass/bassline.mid", b"bass-v1")
    _write_file(tmp_path, "keys/melody.mid", b"keys-v1")
    cid1 = await _commit_async(message="initial take", root=tmp_path, session=muse_cli_db_session)

    # Commit 2: modify only bassline
    _write_file(tmp_path, "bass/bassline.mid", b"bass-v2")
    cid2 = await _commit_async(message="update bass groove", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter=None,
        track_filter=None,
        section_filter=None,
        line_range=None,
    )

    entries = {e["path"].split("muse-work/")[-1]: e for e in result["entries"]}

    # bass/bassline.mid was changed in commit 2
    assert "bass/bassline.mid" in entries
    assert entries["bass/bassline.mid"]["commit_short"] == cid2[:8]
    assert entries["bass/bassline.mid"]["change_type"] == "modified"

    # keys/melody.mid was only in commit 1 and not changed in commit 2
    assert "keys/melody.mid" in entries
    assert entries["keys/melody.mid"]["commit_short"] == cid1[:8]
    assert entries["keys/melody.mid"]["change_type"] == "added"


# ---------------------------------------------------------------------------
# test_blame_path_filter_restricts_output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_path_filter_restricts_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Positional path filter returns only matching entries."""
    _init_muse_repo(tmp_path)

    _write_file(tmp_path, "bass/bassline.mid", b"bass")
    _write_file(tmp_path, "keys/melody.mid", b"keys")
    await _commit_async(message="init", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter="bassline.mid",
        track_filter=None,
        section_filter=None,
        line_range=None,
    )

    assert len(result["entries"]) == 1
    assert "bassline.mid" in result["entries"][0]["path"]


# ---------------------------------------------------------------------------
# test_blame_track_filter_glob
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_track_filter_glob(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--track`` filters by basename glob pattern."""
    _init_muse_repo(tmp_path)

    _write_file(tmp_path, "bass/bassline.mid", b"bass")
    _write_file(tmp_path, "drums/kick.wav", b"kick")
    _write_file(tmp_path, "keys/piano.mid", b"piano")
    await _commit_async(message="init", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter=None,
        track_filter="*.mid",
        section_filter=None,
        line_range=None,
    )

    # Only .mid files should appear
    for entry in result["entries"]:
        assert entry["path"].endswith(".mid"), f"Non-.mid file leaked: {entry['path']}"
    # Both MIDI files should be present
    paths = [e["path"] for e in result["entries"]]
    assert any("bassline.mid" in p for p in paths)
    assert any("piano.mid" in p for p in paths)
    assert not any("kick.wav" in p for p in paths)


# ---------------------------------------------------------------------------
# test_blame_section_filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_section_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--section`` filters to files inside the named section directory."""
    _init_muse_repo(tmp_path)

    _write_file(tmp_path, "chorus/lead.mid", b"chorus-lead")
    _write_file(tmp_path, "verse/rhythm.mid", b"verse-rhythm")
    _write_file(tmp_path, "chorus/bass.mid", b"chorus-bass")
    await _commit_async(message="init", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter=None,
        track_filter=None,
        section_filter="chorus",
        line_range=None,
    )

    paths = [e["path"] for e in result["entries"]]
    assert all("chorus" in p for p in paths)
    assert not any("verse" in p for p in paths)
    assert len(result["entries"]) == 2


# ---------------------------------------------------------------------------
# test_blame_json_output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_json_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` flag emits parseable JSON with the correct keys."""
    _init_muse_repo(tmp_path)

    _write_file(tmp_path, "drums/beat.mid", b"drums")
    await _commit_async(message="beat commit", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter=None,
        track_filter=None,
        section_filter=None,
        line_range=None,
    )

    output = json.dumps(dict(result), indent=2)
    parsed: dict[str, object] = json.loads(output)

    assert "entries" in parsed
    assert isinstance(parsed["entries"], list)
    assert len(parsed["entries"]) == 1

    entry = parsed["entries"][0]
    assert isinstance(entry, dict)
    for key in ("path", "commit_id", "commit_short", "author", "committed_at", "message", "change_type"):
        assert key in entry, f"Missing key in BlameEntry: {key}"


# ---------------------------------------------------------------------------
# test_blame_no_commits_exits_zero
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_no_commits_exits_zero(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse blame`` on a repo with no commits exits 0 with a friendly message."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _blame_async(
            root=tmp_path,
            session=muse_cli_db_session,
            path_filter=None,
            track_filter=None,
            section_filter=None,
            line_range=None,
        )

    assert exc_info.value.exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "No commits yet" in out


# ---------------------------------------------------------------------------
# test_blame_outside_repo_exits_2
# ---------------------------------------------------------------------------


def test_blame_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse blame`` outside a .muse/ directory exits with code 2."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["blame"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND


# ---------------------------------------------------------------------------
# test_blame_single_commit_all_added
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_single_commit_all_added(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """All files in a single-commit repo are attributed to that commit as 'added'."""
    _init_muse_repo(tmp_path)

    _write_file(tmp_path, "bass.mid", b"bass")
    _write_file(tmp_path, "drums.mid", b"drums")
    cid = await _commit_async(message="first commit", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter=None,
        track_filter=None,
        section_filter=None,
        line_range=None,
    )

    assert len(result["entries"]) == 2
    for entry in result["entries"]:
        assert entry["commit_short"] == cid[:8]
        assert entry["change_type"] == "added"


# ---------------------------------------------------------------------------
# test_blame_unmodified_file_attributes_oldest_commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_unmodified_file_attributes_oldest_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """A file that never changes across three commits is attributed to the first commit."""
    _init_muse_repo(tmp_path)

    # Commit 1: add stable.mid and volatile.mid
    _write_file(tmp_path, "stable.mid", b"stable-content-never-changes")
    _write_file(tmp_path, "volatile.mid", b"volatile-v1")
    cid1 = await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)

    # Commit 2: modify only volatile
    _write_file(tmp_path, "volatile.mid", b"volatile-v2")
    await _commit_async(message="update volatile", root=tmp_path, session=muse_cli_db_session)

    # Commit 3: modify only volatile again
    _write_file(tmp_path, "volatile.mid", b"volatile-v3")
    cid3 = await _commit_async(message="another volatile update", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter=None,
        track_filter=None,
        section_filter=None,
        line_range=None,
    )

    entries = {e["path"].split("muse-work/")[-1]: e for e in result["entries"]}

    # stable.mid was never changed — must point to cid1
    assert "stable.mid" in entries
    assert entries["stable.mid"]["commit_short"] == cid1[:8]

    # volatile.mid was last changed in cid3
    assert "volatile.mid" in entries
    assert entries["volatile.mid"]["commit_short"] == cid3[:8]


# ---------------------------------------------------------------------------
# test_blame_render_human_readable
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_render_human_readable(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Human-readable output contains the short commit ID and file path."""
    _init_muse_repo(tmp_path)

    _write_file(tmp_path, "bass/groove.mid", b"groove")
    cid = await _commit_async(message="add groove", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter=None,
        track_filter=None,
        section_filter=None,
        line_range=None,
    )

    rendered = _render_blame(result)
    assert cid[:8] in rendered
    assert "groove.mid" in rendered
    assert "add groove" in rendered


# ---------------------------------------------------------------------------
# test_blame_line_range_recorded_in_output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blame_line_range_recorded_in_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--line-range`` is recorded in the result and shown in human-readable output."""
    _init_muse_repo(tmp_path)

    _write_file(tmp_path, "score.mxl", b"<musicxml/>")
    await _commit_async(message="add score", root=tmp_path, session=muse_cli_db_session)

    result = await _blame_async(
        root=tmp_path,
        session=muse_cli_db_session,
        path_filter=None,
        track_filter=None,
        section_filter=None,
        line_range="10,20",
    )

    assert result["line_range"] == "10,20"
    rendered = _render_blame(result)
    assert "line-range" in rendered
    assert "10,20" in rendered
