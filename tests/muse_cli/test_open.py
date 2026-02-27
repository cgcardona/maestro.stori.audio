"""Tests for ``muse open`` command.

Tests:
- ``test_open_calls_system_open`` — happy path: subprocess.run(['open', path]) called.
- ``test_open_file_not_found_exits_1`` — exit code 1 when file does not exist.
- ``test_open_requires_macos`` — exit code 1 on non-macOS platforms.
"""
from __future__ import annotations

import json
import pathlib
import uuid
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from maestro.muse_cli.app import cli

runner = CliRunner()


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


def test_open_calls_system_open(tmp_path: pathlib.Path) -> None:
    """``muse open <path>`` calls subprocess.run(['open', <path>]) and exits 0."""
    _init_muse_repo(tmp_path)
    artifact = tmp_path / "muse-work" / "jazz_4b_run1.mid"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"MIDI")

    with (
        patch("maestro.muse_cli.commands.open_cmd.platform.system", return_value="Darwin"),
        patch("maestro.muse_cli.commands.open_cmd.subprocess.run") as mock_run,
        patch.dict("os.environ", {"MUSE_REPO_ROOT": str(tmp_path)}),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["open", str(artifact)])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "open"
    assert str(artifact.resolve()) in call_args[1]


def test_open_file_not_found_exits_1(tmp_path: pathlib.Path) -> None:
    """``muse open <missing>`` exits 1 with a clear error message."""
    _init_muse_repo(tmp_path)

    with (
        patch("maestro.muse_cli.commands.open_cmd.platform.system", return_value="Darwin"),
        patch.dict("os.environ", {"MUSE_REPO_ROOT": str(tmp_path)}),
    ):
        result = runner.invoke(cli, ["open", "no_such_file.mid"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "❌" in result.output


def test_open_requires_macos(tmp_path: pathlib.Path) -> None:
    """``muse open`` exits 1 with a clear message on non-macOS platforms."""
    _init_muse_repo(tmp_path)
    artifact = tmp_path / "muse-work" / "beat.mid"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"MIDI")

    with (
        patch("maestro.muse_cli.commands.open_cmd.platform.system", return_value="Linux"),
        patch.dict("os.environ", {"MUSE_REPO_ROOT": str(tmp_path)}),
    ):
        result = runner.invoke(cli, ["open", str(artifact)])

    assert result.exit_code == 1
    assert "macOS" in result.output
