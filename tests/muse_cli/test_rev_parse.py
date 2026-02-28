"""Tests for ``muse rev-parse``.

All async tests call ``_rev_parse_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running process
required.  Commits are seeded via ``_commit_async`` for realistic parent
chain data.

Covers all revision expression types:
- HEAD
- <branch>
- <commit_id> (full and prefix)
- HEAD~N
- <branch>~N

And all flags:
- --short
- --verify
- --abbrev-ref
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.rev_parse import (
    RevParseResult,
    _rev_parse_async,
    resolve_revision,
)
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Initialise a minimal .muse/ directory and return the repo_id."""
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
    """Seed N commits on main, returning their commit_ids oldest-first."""
    commit_ids: list[str] = []
    for i, msg in enumerate(messages):
        _write_workdir(root, {f"track_{file_seed + i}.mid": f"MIDI-{file_seed + i}".encode()})
        cid = await _commit_async(message=msg, root=root, session=session)
        commit_ids.append(cid)
    return commit_ids


# ---------------------------------------------------------------------------
# test_rev_parse_HEAD_returns_head_commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_HEAD_returns_head_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse rev-parse HEAD`` prints the most recent commit ID."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["take 1", "take 2"])

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="HEAD",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == cids[-1]  # newest commit


# ---------------------------------------------------------------------------
# test_rev_parse_branch_name_returns_tip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_branch_name_returns_tip(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse rev-parse main`` resolves the tip of the named branch."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["alpha", "beta"])

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="main",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == cids[-1]


# ---------------------------------------------------------------------------
# test_rev_parse_full_commit_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_full_commit_id(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse rev-parse <full_id>`` echoes the same commit ID back."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["v1", "v2", "v3"])
    target = cids[1]  # middle commit

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision=target,
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == target


# ---------------------------------------------------------------------------
# test_rev_parse_prefix_commit_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_prefix_commit_id(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An 8-char prefix is resolved to the full commit ID."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["groove"])
    prefix = cids[0][:8]

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision=prefix,
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == cids[0]


# ---------------------------------------------------------------------------
# test_rev_parse_HEAD_tilde_1
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_HEAD_tilde_1(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``HEAD~1`` resolves to the parent of the current HEAD."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["a", "b", "c"])

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="HEAD~1",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == cids[1]  # one step back from cids[2]


# ---------------------------------------------------------------------------
# test_rev_parse_HEAD_tilde_2
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_HEAD_tilde_2(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``HEAD~2`` walks two parents back from HEAD."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["x", "y", "z"])

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="HEAD~2",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == cids[0]  # two steps back from cids[2]


# ---------------------------------------------------------------------------
# test_rev_parse_branch_tilde
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_branch_tilde(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main~1`` walks one parent back from the main branch tip."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["first", "second"])

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="main~1",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == cids[0]


# ---------------------------------------------------------------------------
# test_rev_parse_short_flag
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_short_flag(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--short`` outputs only the first 8 characters of the commit ID."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["take"])

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="HEAD",
        short=True,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == cids[0][:8]
    assert len(out) == 8


# ---------------------------------------------------------------------------
# test_rev_parse_abbrev_ref_HEAD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_abbrev_ref_HEAD(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--abbrev-ref HEAD`` prints the current branch name, not the commit ID."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["beat"])

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="HEAD",
        short=False,
        verify=False,
        abbrev_ref=True,
    )
    out = capsys.readouterr().out.strip()
    assert out == "main"


# ---------------------------------------------------------------------------
# test_rev_parse_verify_fails_on_unknown_ref
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_verify_fails_on_unknown_ref(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--verify`` exits USER_ERROR when the revision does not resolve."""
    _init_muse_repo(tmp_path)
    # No commits — nothing to resolve

    with pytest.raises(typer.Exit) as exc_info:
        await _rev_parse_async(
            root=tmp_path,
            session=muse_cli_db_session,
            revision="nonexistent",
            short=False,
            verify=True,
            abbrev_ref=False,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# test_rev_parse_no_verify_prints_nothing_on_miss
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_no_verify_prints_nothing_on_miss(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without ``--verify``, an unresolvable ref prints nothing and exits 0."""
    _init_muse_repo(tmp_path)

    # Should not raise
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="deadbeef",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == ""


# ---------------------------------------------------------------------------
# test_rev_parse_tilde_beyond_root_returns_nothing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_tilde_beyond_root_returns_nothing(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``HEAD~10`` on a 2-commit chain prints nothing (no --verify)."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["one", "two"])

    capsys.readouterr()  # discard commit output from _make_commits
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="HEAD~10",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out = capsys.readouterr().out.strip()
    assert out == ""


# ---------------------------------------------------------------------------
# test_rev_parse_head_tilde_zero_equals_HEAD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rev_parse_head_tilde_zero_equals_HEAD(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``HEAD~0`` resolves to the same commit as ``HEAD``."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["r1", "r2"])

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="HEAD~0",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out_tilde = capsys.readouterr().out.strip()

    capsys.readouterr()
    await _rev_parse_async(
        root=tmp_path,
        session=muse_cli_db_session,
        revision="HEAD",
        short=False,
        verify=False,
        abbrev_ref=False,
    )
    out_head = capsys.readouterr().out.strip()

    assert out_tilde == out_head == cids[-1]


# ---------------------------------------------------------------------------
# test_resolve_revision_returns_RevParseResult_type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_revision_returns_RevParseResult_type(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``resolve_revision`` returns a ``RevParseResult`` with correct fields."""
    repo_id = _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["init"])

    result = await resolve_revision(
        session=muse_cli_db_session,
        repo_id=repo_id,
        current_branch="main",
        muse_dir=tmp_path / ".muse",
        revision_expr="HEAD",
    )

    assert result is not None
    assert isinstance(result, RevParseResult)
    assert result.commit_id == cids[0]
    assert result.branch == "main"
    assert result.revision_expr == "HEAD"


# ---------------------------------------------------------------------------
# test_rev_parse_outside_repo_exits_2
# ---------------------------------------------------------------------------


def test_rev_parse_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse rev-parse`` outside a .muse/ directory exits with code 2."""
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["rev-parse", "HEAD"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND
