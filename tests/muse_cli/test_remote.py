"""Tests for ``muse remote add`` and ``muse remote -v``.

Covers acceptance criteria from issue #38:
- ``muse remote add origin <url>`` writes to ``.muse/config.toml``.
- ``muse remote -v`` prints all remotes with their URLs.
- URL validation: non-http(s) URLs are rejected with exit 1.
- Duplicate ``add`` overwrites the existing URL.
- ``muse remote`` outside a repo exits 2.
"""
from __future__ import annotations

import os
import pathlib

import pytest
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.config import get_remote, list_remotes, set_remote


runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal .muse/ repo structure under tmp_path."""
    import json
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo-id"}), encoding="utf-8"
    )
    (muse_dir / "HEAD").write_text("refs/heads/main", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# test_remote_add_writes_config
# ---------------------------------------------------------------------------


def test_remote_add_writes_config(tmp_path: pathlib.Path) -> None:
    """muse remote add writes [remotes.<name>] url to .muse/config.toml."""
    root = _init_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["remote", "add", "origin", "https://hub.example.com/musehub/repos/my-repo"],
        env={"MUSE_REPO_ROOT": str(root)},
    )
    assert result.exit_code == 0, result.output

    url = get_remote("origin", root)
    assert url == "https://hub.example.com/musehub/repos/my-repo"


def test_remote_add_multiple_remotes(tmp_path: pathlib.Path) -> None:
    """Adding two remotes stores both independently in config.toml."""
    root = _init_repo(tmp_path)

    runner.invoke(
        cli,
        ["remote", "add", "origin", "https://hub.example.com/musehub/repos/repo-a"],
        env={"MUSE_REPO_ROOT": str(root)},
    )
    runner.invoke(
        cli,
        ["remote", "add", "staging", "https://staging.example.com/musehub/repos/repo-a"],
        env={"MUSE_REPO_ROOT": str(root)},
    )

    remotes = list_remotes(root)
    names = [r["name"] for r in remotes]
    assert "origin" in names
    assert "staging" in names


def test_remote_add_overwrites_existing(tmp_path: pathlib.Path) -> None:
    """muse remote add with an existing name updates the URL."""
    root = _init_repo(tmp_path)
    set_remote("origin", "https://old.example.com", root)

    runner.invoke(
        cli,
        ["remote", "add", "origin", "https://new.example.com/musehub/repos/x"],
        env={"MUSE_REPO_ROOT": str(root)},
    )

    url = get_remote("origin", root)
    assert url == "https://new.example.com/musehub/repos/x"


def test_remote_add_invalid_url_exits_1(tmp_path: pathlib.Path) -> None:
    """muse remote add rejects non-http(s) URLs with exit code 1."""
    root = _init_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["remote", "add", "origin", "ftp://bad-url.example.com"],
        env={"MUSE_REPO_ROOT": str(root)},
    )
    assert result.exit_code == 1
    assert "http" in result.output.lower()


# ---------------------------------------------------------------------------
# test_remote_v_shows_remotes
# ---------------------------------------------------------------------------


def test_remote_v_shows_remotes(tmp_path: pathlib.Path) -> None:
    """muse remote -v prints name and URL for each configured remote."""
    root = _init_repo(tmp_path)
    set_remote("origin", "https://hub.example.com/musehub/repos/my-repo", root)

    result = runner.invoke(
        cli,
        ["remote", "-v"],
        env={"MUSE_REPO_ROOT": str(root)},
    )
    assert result.exit_code == 0, result.output
    assert "origin" in result.output
    assert "https://hub.example.com/musehub/repos/my-repo" in result.output


def test_remote_v_no_remotes_shows_hint(tmp_path: pathlib.Path) -> None:
    """muse remote -v with no remotes configured prints a helpful hint."""
    root = _init_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["remote", "-v"],
        env={"MUSE_REPO_ROOT": str(root)},
    )
    assert result.exit_code == 0
    assert "no remotes" in result.output.lower()


def test_remote_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """muse remote outside a repo exits with code 2."""
    result = runner.invoke(
        cli,
        ["remote", "-v"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# test_remote_add_output_confirms_success
# ---------------------------------------------------------------------------


def test_remote_add_output_confirms_success(tmp_path: pathlib.Path) -> None:
    """muse remote add echoes a confirmation including the remote name."""
    root = _init_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["remote", "add", "origin", "https://hub.example.com/musehub/repos/r"],
        env={"MUSE_REPO_ROOT": str(root)},
    )
    assert result.exit_code == 0
    assert "origin" in result.output
