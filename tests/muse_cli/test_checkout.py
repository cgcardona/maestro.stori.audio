"""Tests for ``muse checkout``.

All async tests call ``_checkout_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running process
required.  Commits are seeded via ``_commit_async`` so the two commands are
tested as an integrated pair.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.checkout import _checkout_async
from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.log import _log_async
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_log.py / test_commit.py)
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


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    for name, content in files.items():
        (workdir / name).write_bytes(content)


async def _make_commit(
    root: pathlib.Path,
    session: AsyncSession,
    message: str,
    filename: str = "track.mid",
    content: bytes = b"MIDI-0",
) -> str:
    _write_workdir(root, {filename: content})
    return await _commit_async(message=message, root=root, session=session)


async def _do_checkout(
    root: pathlib.Path,
    session: AsyncSession,
    branch_name: str,
    *,
    create: bool = False,
    force: bool = False,
) -> None:
    await _checkout_async(
        branch_name=branch_name,
        create=create,
        force=force,
        root=root,
        session=session,
    )


# ---------------------------------------------------------------------------
# test_checkout_switches_head
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_switches_head(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``.muse/HEAD`` is updated when switching to an existing branch."""
    _init_muse_repo(tmp_path)

    # Create a second branch manually (as if already committed there)
    (tmp_path / ".muse" / "refs" / "heads" / "experiment").write_text("")

    await _do_checkout(tmp_path, muse_cli_db_session, "experiment")

    head = (tmp_path / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/experiment"


# ---------------------------------------------------------------------------
# test_checkout_b_creates_branch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_b_creates_branch(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """-b creates ``.muse/refs/heads/<branch>`` and switches HEAD."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, "initial commit")

    await _do_checkout(tmp_path, muse_cli_db_session, "feature", create=True)

    ref_file = tmp_path / ".muse" / "refs" / "heads" / "feature"
    assert ref_file.exists(), "ref file for new branch should exist"
    head = (tmp_path / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/feature"


# ---------------------------------------------------------------------------
# test_checkout_b_forks_at_current_head_commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_b_forks_at_current_head_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """New branch ref points to the same commit as main's HEAD."""
    _init_muse_repo(tmp_path)
    cid = await _make_commit(tmp_path, muse_cli_db_session, "initial")

    # main ref should now have the commit id
    main_commit = (tmp_path / ".muse" / "refs" / "heads" / "main").read_text().strip()
    assert main_commit == cid

    await _do_checkout(tmp_path, muse_cli_db_session, "neo-soul", create=True)

    new_branch_commit = (
        tmp_path / ".muse" / "refs" / "heads" / "neo-soul"
    ).read_text().strip()
    assert new_branch_commit == cid, "new branch should fork from current HEAD commit"


# ---------------------------------------------------------------------------
# test_checkout_nonexistent_without_b_exits_1
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_nonexistent_without_b_exits_1(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Checking out a non-existent branch without -b exits 1 with a hint."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _do_checkout(tmp_path, muse_cli_db_session, "ghost-branch")

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
    out = capsys.readouterr().out
    assert "does not exist" in out
    assert "-b" in out


# ---------------------------------------------------------------------------
# test_checkout_dirty_workdir_blocked
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_dirty_workdir_blocked(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Checkout is blocked when muse-work/ has uncommitted changes."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, "initial commit")

    # Create target branch
    (tmp_path / ".muse" / "refs" / "heads" / "other").write_text("")

    # Dirty the working tree
    _write_workdir(tmp_path, {"new_track.mid": b"MIDI-DIRTY"})

    with pytest.raises(typer.Exit) as exc_info:
        await _do_checkout(tmp_path, muse_cli_db_session, "other")

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
    out = capsys.readouterr().out
    assert "Uncommitted changes" in out


# ---------------------------------------------------------------------------
# test_checkout_force_ignores_dirty
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_force_ignores_dirty(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--force succeeds even when muse-work/ has uncommitted changes."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, "initial commit")

    # Create target branch
    (tmp_path / ".muse" / "refs" / "heads" / "other").write_text("")

    # Dirty the working tree
    _write_workdir(tmp_path, {"new_track.mid": b"MIDI-DIRTY"})

    # Force checkout should succeed (no exception)
    capsys.readouterr()
    await _do_checkout(tmp_path, muse_cli_db_session, "other", force=True)

    head = (tmp_path / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/other"
    out = capsys.readouterr().out
    assert "Switched to branch" in out


# ---------------------------------------------------------------------------
# test_checkout_branches_diverge
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_branches_diverge(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After branching, ``muse log`` shows different histories per branch."""
    _init_muse_repo(tmp_path)

    # Commit on main
    await _make_commit(tmp_path, muse_cli_db_session, "main: initial beat", filename="beat.mid", content=b"MIDI-MAIN-1")

    # Create and switch to experiment branch
    await _do_checkout(tmp_path, muse_cli_db_session, "experiment", create=True)

    assert (tmp_path / ".muse" / "HEAD").read_text().strip() == "refs/heads/experiment"

    # Commit on experiment branch (unique file)
    _write_workdir(tmp_path, {"experiment.mid": b"MIDI-EXP-1"})
    cid_exp = await _commit_async(
        message="experiment: neo-soul take 1",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Check experiment log
    capsys.readouterr()
    await _log_async(root=tmp_path, session=muse_cli_db_session, limit=100, graph=False)
    exp_log = capsys.readouterr().out

    # Switch back to main
    await _do_checkout(tmp_path, muse_cli_db_session, "main")

    assert (tmp_path / ".muse" / "HEAD").read_text().strip() == "refs/heads/main"

    # Commit something else on main
    _write_workdir(tmp_path, {"mainv2.mid": b"MIDI-MAIN-2"})
    cid_main2 = await _commit_async(
        message="main: second arrangement",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Check main log
    capsys.readouterr()
    await _log_async(root=tmp_path, session=muse_cli_db_session, limit=100, graph=False)
    main_log = capsys.readouterr().out

    # Main log should have "second arrangement" but NOT the experiment commit
    assert "second arrangement" in main_log
    assert "neo-soul" not in main_log

    # Experiment log should have "neo-soul" but NOT "second arrangement"
    assert "neo-soul" in exp_log
    assert "second arrangement" not in exp_log

    # The experiment commit id should appear in experiment log, main's second in main log
    assert cid_exp[:8] in exp_log
    assert cid_main2[:8] in main_log


# ---------------------------------------------------------------------------
# test_checkout_already_on_branch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_already_on_branch(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Checking out the current branch exits 0 with an 'Already on' message."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _do_checkout(tmp_path, muse_cli_db_session, "main")

    assert exc_info.value.exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "Already on" in out


# ---------------------------------------------------------------------------
# test_checkout_b_fails_if_branch_exists
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_b_fails_if_branch_exists(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """-b exits 1 when the target branch already exists."""
    _init_muse_repo(tmp_path)
    # Create branch manually
    (tmp_path / ".muse" / "refs" / "heads" / "existing").write_text("abc123")

    with pytest.raises(typer.Exit) as exc_info:
        await _do_checkout(tmp_path, muse_cli_db_session, "existing", create=True)

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
    out = capsys.readouterr().out
    assert "already exists" in out


# ---------------------------------------------------------------------------
# test_checkout_outside_repo_exits_2  (via Typer CLI runner)
# ---------------------------------------------------------------------------


def test_checkout_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse checkout`` exits 2 when no ``.muse/`` directory exists."""
    import os

    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["checkout", "main"])
        assert result.exit_code == int(ExitCode.REPO_NOT_FOUND), (
            f"Expected exit 2, got {result.exit_code}: {result.output}"
        )
        assert "Not a Muse repository" in result.output
    finally:
        os.chdir(prev)


# ---------------------------------------------------------------------------
# test_checkout_invalid_branch_name_exits_1
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_invalid_branch_name_exits_1(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A branch name with illegal characters exits 1."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _do_checkout(tmp_path, muse_cli_db_session, "bad name!")

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
    out = capsys.readouterr().out
    assert "Invalid branch name" in out


# ---------------------------------------------------------------------------
# test_checkout_clean_tree_not_blocked
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_checkout_clean_tree_not_blocked(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Checkout succeeds when muse-work/ matches the last commit snapshot."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, "committed state")

    # Create target branch
    (tmp_path / ".muse" / "refs" / "heads" / "clean-target").write_text("")

    # Do NOT modify muse-work/ — tree is clean
    capsys.readouterr()
    await _do_checkout(tmp_path, muse_cli_db_session, "clean-target")

    head = (tmp_path / ".muse" / "HEAD").read_text().strip()
    assert head == "refs/heads/clean-target"
