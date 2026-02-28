"""Tests for the Muse CLI skeleton — subcommand stubs and exit-code contract."""
from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()

ALL_SUBCOMMANDS = [
    "init",
    "status",
    "commit",
    "log",
    "checkout",
    "merge",
    "remote",
    "push",
    "pull",
]

# Commands that are not yet fully implemented — they print "not yet implemented"
# when invoked inside a repo with a bare .muse/ directory.
# ``init``     is excluded: fully implemented (issue #31).
# ``commit``   is excluded: fully implemented (issue #32).
# ``log``      is excluded: fully implemented (issue #33).
# ``merge``    is excluded: fully implemented (issue #35).
# ``remote``   is excluded: fully implemented (issues #38, #81).
# ``push``     is excluded: fully implemented (issue #36).
# ``pull``     is excluded: fully implemented (issue #37).
# ``checkout`` is excluded: fully implemented (issue #34).
STUB_COMMANDS: list[str] = []

# Repo-dependent commands that exit 2 outside a .muse/ repo.
# ``commit``   requires -m so its no-repo exit-2 test lives in test_commit.py.
# ``log``      no-repo exit-2 test lives in test_log.py.
# ``merge``    requires a BRANCH arg — repo check tested in test_merge.py.
# ``checkout`` requires a BRANCH arg — repo check tested below in test_checkout_no_repo_exits_2.
REPO_DEPENDENT_COMMANDS = [
    "status",
    "remote",
    "push",
    "pull",
]


def test_cli_help_exits_zero() -> None:
    """``muse --help`` exits 0 and lists all subcommand names."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ALL_SUBCOMMANDS:
        assert cmd in result.output


@pytest.mark.parametrize("cmd", STUB_COMMANDS)
def test_cli_subcommand_stub_exits_zero(cmd: str, tmp_path: pathlib.Path) -> None:
    """Each not-yet-implemented stub exits 0 when run inside a Muse repository."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, [cmd])
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"
        assert "not yet implemented" in result.output
    finally:
        os.chdir(prev)


@pytest.mark.parametrize("cmd", REPO_DEPENDENT_COMMANDS)
def test_cli_no_repo_exits_2(cmd: str) -> None:
    """Repo-dependent commands exit 2 when no ``.muse/`` directory exists."""
    with tempfile.TemporaryDirectory() as d:
        prev = os.getcwd()
        try:
            os.chdir(d)
            result = runner.invoke(cli, [cmd])
            assert result.exit_code == int(ExitCode.REPO_NOT_FOUND), (
                f"{cmd} should exit {ExitCode.REPO_NOT_FOUND}, got {result.exit_code}: {result.output}"
            )
            assert "not a muse repository" in result.output.lower()
        finally:
            os.chdir(prev)


def test_exit_code_enum_values() -> None:
    """Exit code enum values match the specification (0/1/2/3)."""
    assert int(ExitCode.SUCCESS) == 0
    assert int(ExitCode.USER_ERROR) == 1
    assert int(ExitCode.REPO_NOT_FOUND) == 2
    assert int(ExitCode.INTERNAL_ERROR) == 3


def test_checkout_no_repo_exits_2() -> None:
    """``muse checkout <branch>`` outside a repo exits 2 with a clear message.

    Separate from the parametrized ``test_cli_no_repo_exits_2`` because
    checkout requires a positional BRANCH argument — without it, typer fails
    before the repo check runs.
    """
    with tempfile.TemporaryDirectory() as d:
        prev = os.getcwd()
        try:
            os.chdir(d)
            result = runner.invoke(cli, ["checkout", "main"])
            assert result.exit_code == int(ExitCode.REPO_NOT_FOUND), (
                f"checkout should exit {ExitCode.REPO_NOT_FOUND}, got {result.exit_code}: {result.output}"
            )
            assert "not a muse repository" in result.output.lower()
        finally:
            os.chdir(prev)
