"""Tests for ``muse play`` command.

Tests:
- ``test_play_calls_afplay`` — happy path: subprocess.run(['afplay', path]) called.
- ``test_play_mid_falls_back_to_open`` — MIDI files fall back to ``open``.
- ``test_play_file_not_found_exits_1`` — exit code 1 when file does not exist.
- ``test_play_requires_macos`` — exit code 1 on non-macOS platforms.
"""
from __future__ import annotations

import json
import pathlib
import uuid
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.play import _play_path

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


def test_play_calls_afplay(tmp_path: pathlib.Path) -> None:
    """``muse play <mp3>`` calls subprocess.run(['afplay', <path>]) and exits 0."""
    _init_muse_repo(tmp_path)
    artifact = tmp_path / "muse-work" / "jazz_4b_run1.mp3"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"MP3DATA")

    with (
        patch("maestro.muse_cli.commands.play.platform.system", return_value="Darwin"),
        patch("maestro.muse_cli.commands.play.subprocess.run") as mock_run,
        patch.dict("os.environ", {"MUSE_REPO_ROOT": str(tmp_path)}),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["play", str(artifact)])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "afplay"
    assert str(artifact.resolve()) in call_args[1]


def test_play_mid_falls_back_to_open(tmp_path: pathlib.Path) -> None:
    """``muse play <mid>`` falls back to ``open`` and shows a warning."""
    _init_muse_repo(tmp_path)
    artifact = tmp_path / "muse-work" / "track.mid"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"MIDI")

    with (
        patch("maestro.muse_cli.commands.play.platform.system", return_value="Darwin"),
        patch("maestro.muse_cli.commands.play.subprocess.run") as mock_run,
        patch.dict("os.environ", {"MUSE_REPO_ROOT": str(tmp_path)}),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["play", str(artifact)])

    assert result.exit_code == 0, result.output
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "open"
    assert "MIDI" in result.output or "afplay" in result.output or "system default" in result.output


def test_play_file_not_found_exits_1(tmp_path: pathlib.Path) -> None:
    """``muse play <missing>`` exits 1 with a clear error message."""
    _init_muse_repo(tmp_path)

    with (
        patch("maestro.muse_cli.commands.play.platform.system", return_value="Darwin"),
        patch.dict("os.environ", {"MUSE_REPO_ROOT": str(tmp_path)}),
    ):
        result = runner.invoke(cli, ["play", "no_such_file.mp3"])

    assert result.exit_code == 1


def test_play_requires_macos(tmp_path: pathlib.Path) -> None:
    """``muse play`` exits 1 with a clear message on non-macOS platforms."""
    _init_muse_repo(tmp_path)
    artifact = tmp_path / "muse-work" / "beat.mp3"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"MP3DATA")

    with (
        patch("maestro.muse_cli.commands.play.platform.system", return_value="Linux"),
        patch.dict("os.environ", {"MUSE_REPO_ROOT": str(tmp_path)}),
    ):
        result = runner.invoke(cli, ["play", str(artifact)])

    assert result.exit_code == 1
    assert "macOS" in result.output


def test_play_path_calls_afplay_directly(tmp_path: pathlib.Path) -> None:
    """``_play_path`` helper calls afplay for mp3 files."""
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"MP3")

    with patch("maestro.muse_cli.commands.play.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _play_path(mp3)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][0] == "afplay"


def test_play_path_midi_falls_back_to_open(tmp_path: pathlib.Path) -> None:
    """``_play_path`` helper falls back to open for .mid files."""
    mid = tmp_path / "track.mid"
    mid.write_bytes(b"MIDI")

    with patch("maestro.muse_cli.commands.play.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _play_path(mid)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][0] == "open"
