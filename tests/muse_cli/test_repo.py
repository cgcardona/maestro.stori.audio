"""Tests for ``maestro.muse_cli._repo`` and public ``maestro.muse_cli.repo``.

Covers every acceptance criterion from issue #46:

- Returns current dir if ``.muse/`` is present
- Traverses up and finds a parent ``.muse/``
- Returns ``None`` (never raises) when no ``.muse/`` ancestor exists
- ``MUSE_REPO_ROOT`` env-var takes precedence over traversal
- ``require_repo_root()`` exits 2 with the standardised git-style error
  message when no repo is found
- Public ``repo.py`` module re-exports ``find_repo_root`` and
  ``require_repo_root`` identically to the private ``_repo`` module
- ``MuseNotARepoError`` alias exists in ``errors.py``

All tests use ``tmp_path`` and ``monkeypatch`` for isolation.
"""
from __future__ import annotations

import os
import pathlib

import pytest
from typer.testing import CliRunner

from maestro.muse_cli._repo import find_repo_root, require_repo, require_repo_root
from maestro.muse_cli.app import cli
from maestro.muse_cli.errors import ExitCode, MuseNotARepoError, RepoNotFoundError

runner = CliRunner()


# ---------------------------------------------------------------------------
# find_repo_root()
# ---------------------------------------------------------------------------


def test_find_repo_root_current_dir(tmp_path: pathlib.Path) -> None:
    """Returns current directory when ``.muse/`` is present there."""
    (tmp_path / ".muse").mkdir()
    root = find_repo_root(tmp_path)
    assert root == tmp_path


def test_find_repo_root_parent_dir(tmp_path: pathlib.Path) -> None:
    """Traverses up and finds a ``.muse/`` in a parent directory."""
    (tmp_path / ".muse").mkdir()
    nested = tmp_path / "project" / "subdir"
    nested.mkdir(parents=True)

    root = find_repo_root(nested)
    assert root == tmp_path


def test_find_repo_root_returns_none_outside_repo(tmp_path: pathlib.Path) -> None:
    """Returns ``None`` (not an exception) when no ``.muse/`` ancestor exists."""
    # tmp_path has no .muse/ and is an isolated temp dir.
    root = find_repo_root(tmp_path)
    assert root is None


def test_find_repo_root_uses_cwd_when_no_start(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no ``start`` argument, uses ``Path.cwd()`` as the start."""
    (tmp_path / ".muse").mkdir()
    monkeypatch.chdir(tmp_path)
    root = find_repo_root()
    assert root == tmp_path


def test_find_repo_root_env_var_override(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MUSE_REPO_ROOT`` env var takes precedence over directory traversal."""
    override_dir = tmp_path / "override"
    override_dir.mkdir()
    (override_dir / ".muse").mkdir()

    monkeypatch.setenv("MUSE_REPO_ROOT", str(override_dir))

    # Even if there's a different .muse/ higher up, the override wins.
    root = find_repo_root(tmp_path)
    assert root == override_dir


def test_find_repo_root_env_var_override_invalid_returns_none(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MUSE_REPO_ROOT`` pointing to a dir without ``.muse/`` returns ``None``."""
    no_muse_dir = tmp_path / "no_muse"
    no_muse_dir.mkdir()
    monkeypatch.setenv("MUSE_REPO_ROOT", str(no_muse_dir))

    root = find_repo_root()
    assert root is None


def test_find_repo_root_stops_at_filesystem_root(tmp_path: pathlib.Path) -> None:
    """Traversal stops at filesystem root and returns ``None``, never loops."""
    # Use a temp dir that has no .muse/ in any ancestor up to its root.
    deeply_nested = tmp_path / "a" / "b" / "c"
    deeply_nested.mkdir(parents=True)
    root = find_repo_root(deeply_nested)
    assert root is None


# ---------------------------------------------------------------------------
# require_repo()
# ---------------------------------------------------------------------------


def test_require_repo_exits_2_when_no_muse(tmp_path: pathlib.Path) -> None:
    """``require_repo()`` from the CLI exits 2 when outside a Muse repo."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    finally:
        os.chdir(prev)


def test_require_repo_error_message_matches_standard(tmp_path: pathlib.Path) -> None:
    """The error message matches the git-style format specified in issue #46."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["status"])
        assert "fatal: not a muse repository" in result.output
        assert "muse init" in result.output
    finally:
        os.chdir(prev)


def test_require_repo_returns_root_when_found(tmp_path: pathlib.Path) -> None:
    """``require_repo()`` returns the resolved root path when ``.muse/`` exists."""
    (tmp_path / ".muse").mkdir()
    root = require_repo(tmp_path)
    assert root == tmp_path


# ---------------------------------------------------------------------------
# Issue #46 additions — aliases and public module
# ---------------------------------------------------------------------------


def test_require_repo_root_alias_is_identical(tmp_path: pathlib.Path) -> None:
    """``require_repo_root`` is the same callable as ``require_repo``."""
    assert require_repo_root is require_repo


def test_muse_not_a_repo_error_alias(tmp_path: pathlib.Path) -> None:
    """``MuseNotARepoError`` is the canonical alias for ``RepoNotFoundError``."""
    assert MuseNotARepoError is RepoNotFoundError


def test_public_repo_module_exports_find_repo_root(tmp_path: pathlib.Path) -> None:
    """Public ``maestro.muse_cli.repo`` re-exports ``find_repo_root``."""
    from maestro.muse_cli.repo import find_repo_root as public_fn

    (tmp_path / ".muse").mkdir()
    assert public_fn(tmp_path) == tmp_path


def test_public_repo_module_exports_require_repo_root(tmp_path: pathlib.Path) -> None:
    """Public ``maestro.muse_cli.repo`` re-exports ``require_repo_root``."""
    from maestro.muse_cli.repo import require_repo_root as public_fn
    from maestro.muse_cli._repo import require_repo_root as private_fn

    assert public_fn is private_fn
