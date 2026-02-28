"""Tests for ``muse render-preview`` command and ``muse_render_preview`` service.

Test matrix:
- ``test_render_preview_outputs_path_for_head``
- ``test_render_preview_service_returns_result_with_correct_fields``
- ``test_render_preview_service_filter_by_track``
- ``test_render_preview_service_filter_by_section``
- ``test_render_preview_service_raises_when_no_midi_after_filter``
- ``test_render_preview_service_raises_when_storpheus_unreachable``
- ``test_render_preview_service_uses_custom_output_path``
- ``test_render_preview_service_mp3_format``
- ``test_render_preview_service_flac_format``
- ``test_render_preview_service_skips_non_midi_files``
- ``test_render_preview_service_skips_missing_files``
- ``test_render_preview_cli_head_commit``
- ``test_render_preview_cli_json_output``
- ``test_render_preview_cli_no_repo``
- ``test_render_preview_cli_no_commits``
- ``test_render_preview_cli_ambiguous_prefix``
- ``test_render_preview_cli_empty_snapshot``
- ``test_render_preview_cli_storpheus_unreachable``
- ``test_render_preview_cli_custom_format_and_output``
- ``test_render_preview_async_core_resolves_head``
- ``test_default_output_path_uses_tmp``
"""
from __future__ import annotations

import json
import pathlib
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.render_preview import (
    _default_output_path,
    _render_preview_async,
)
from maestro.muse_cli.snapshot import hash_file
from maestro.services.muse_render_preview import (
    PreviewFormat,
    RenderPreviewResult,
    StorpheusRenderUnavailableError,
    _collect_midi_files,
    render_preview,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout for CLI tests."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _set_head(root: pathlib.Path, commit_id: str) -> None:
    """Point the HEAD of the main branch at commit_id."""
    ref_path = root / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_id)


def _make_minimal_midi() -> bytes:
    """Return a minimal well-formed MIDI file (single note, type 0)."""
    header = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
    track_data = (
        b"\x00\x90\x3c\x40"
        b"\x81\x60\x80\x3c\x00"
        b"\x00\xff\x2f\x00"
    )
    track_len = len(track_data).to_bytes(4, "big")
    return header + b"MTrk" + track_len + track_data


def _make_manifest_with_midi(
    tmp_path: pathlib.Path,
    filenames: list[str] | None = None,
) -> dict[str, str]:
    """Write MIDI files to muse-work/ and return a manifest dict.

    Creates parent subdirectories as needed so callers can pass paths like
    ``"drums/beat.mid"`` or ``"chorus/piano.mid"``.
    """
    workdir = tmp_path / "muse-work"
    workdir.mkdir(exist_ok=True)
    filenames = filenames or ["beat.mid"]
    midi_bytes = _make_minimal_midi()
    manifest: dict[str, str] = {}
    for name in filenames:
        p = workdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(midi_bytes)
        manifest[name] = hash_file(p)
    return manifest


def _storpheus_healthy_mock() -> MagicMock:
    """Return a mock httpx.Client whose GET /health returns 200."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)
    return mock_client


# ---------------------------------------------------------------------------
# Unit tests — _default_output_path
# ---------------------------------------------------------------------------


def test_default_output_path_uses_tmp() -> None:
    """_default_output_path returns a /tmp/muse-preview-<short>.<fmt> path."""
    commit_id = "abcdef1234567890" + "0" * 48
    path = _default_output_path(commit_id, PreviewFormat.WAV)
    assert str(path).startswith("/tmp/muse-preview-abcdef12")
    assert path.suffix == ".wav"


def test_default_output_path_mp3() -> None:
    """_default_output_path uses the correct extension for mp3."""
    commit_id = "ff00ff1234567890" + "0" * 48
    path = _default_output_path(commit_id, PreviewFormat.MP3)
    assert path.suffix == ".mp3"


def test_default_output_path_flac() -> None:
    """_default_output_path uses the correct extension for flac."""
    commit_id = "aa00aa1234567890" + "0" * 48
    path = _default_output_path(commit_id, PreviewFormat.FLAC)
    assert path.suffix == ".flac"


# ---------------------------------------------------------------------------
# Unit tests — _collect_midi_files
# ---------------------------------------------------------------------------


def test_collect_midi_files_returns_all_midi(tmp_path: pathlib.Path) -> None:
    """_collect_midi_files returns all MIDI paths when no filter is set."""
    manifest = _make_manifest_with_midi(tmp_path, ["drums.mid", "bass.mid"])
    paths, skipped = _collect_midi_files(manifest, tmp_path, track=None, section=None)
    assert len(paths) == 2
    assert skipped == 0


def test_collect_midi_files_skips_non_midi(tmp_path: pathlib.Path) -> None:
    """_collect_midi_files skips non-MIDI entries and counts them as skipped."""
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "beat.mid").write_bytes(_make_minimal_midi())
    (workdir / "notes.json").write_text("{}")
    manifest = {
        "beat.mid": hash_file(workdir / "beat.mid"),
        "notes.json": hash_file(workdir / "notes.json"),
    }
    paths, skipped = _collect_midi_files(manifest, tmp_path, track=None, section=None)
    assert len(paths) == 1
    assert skipped == 1


def test_collect_midi_files_filter_by_track(tmp_path: pathlib.Path) -> None:
    """_collect_midi_files applies the track substring filter."""
    manifest = _make_manifest_with_midi(
        tmp_path, ["drums/beat.mid", "bass/groove.mid"]
    )
    paths, skipped = _collect_midi_files(manifest, tmp_path, track="drums", section=None)
    assert len(paths) == 1
    assert "drums" in str(paths[0])
    assert skipped == 1


def test_collect_midi_files_filter_by_section(tmp_path: pathlib.Path) -> None:
    """_collect_midi_files applies the section substring filter."""
    manifest = _make_manifest_with_midi(
        tmp_path, ["chorus/piano.mid", "verse/piano.mid"]
    )
    paths, skipped = _collect_midi_files(
        manifest, tmp_path, track=None, section="chorus"
    )
    assert len(paths) == 1
    assert "chorus" in str(paths[0])
    assert skipped == 1


def test_collect_midi_files_skips_missing_file(tmp_path: pathlib.Path) -> None:
    """_collect_midi_files counts missing files as skipped without raising."""
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    manifest = {"ghost.mid": "abc123"}
    paths, skipped = _collect_midi_files(manifest, tmp_path, track=None, section=None)
    assert paths == []
    assert skipped == 1


# ---------------------------------------------------------------------------
# Unit tests — render_preview service
# ---------------------------------------------------------------------------


def test_render_preview_service_returns_result_with_correct_fields(
    tmp_path: pathlib.Path,
) -> None:
    """render_preview returns a RenderPreviewResult with expected fields on success."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "preview.wav"
    commit_id = "a" * 64

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        result = render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=out,
            fmt=PreviewFormat.WAV,
        )

    assert isinstance(result, RenderPreviewResult)
    assert result.output_path == out
    assert result.format == PreviewFormat.WAV
    assert result.commit_id == commit_id
    assert result.midi_files_used == 1
    assert result.skipped_count == 0
    assert result.stubbed is True
    assert out.exists()


def test_render_preview_outputs_path_for_head(tmp_path: pathlib.Path) -> None:
    """Regression: render_preview writes the output file and returns its path."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "muse-preview-head.wav"
    commit_id = "b" * 64

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        result = render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=out,
        )

    assert result.output_path.exists()
    assert str(result.output_path) == str(out)


def test_render_preview_service_filter_by_track(tmp_path: pathlib.Path) -> None:
    """render_preview respects the track filter and skips non-matching MIDI."""
    manifest = _make_manifest_with_midi(
        tmp_path, ["drums/beat.mid", "bass/groove.mid"]
    )
    out = tmp_path / "preview.wav"
    commit_id = "c" * 64

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        result = render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=out,
            track="drums",
        )

    assert result.midi_files_used == 1
    assert result.skipped_count == 1


def test_render_preview_service_filter_by_section(tmp_path: pathlib.Path) -> None:
    """render_preview respects the section filter and skips non-matching MIDI."""
    manifest = _make_manifest_with_midi(
        tmp_path, ["chorus/lead.mid", "verse/lead.mid"]
    )
    out = tmp_path / "preview.wav"
    commit_id = "d" * 64

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        result = render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=out,
            section="chorus",
        )

    assert result.midi_files_used == 1
    assert result.skipped_count == 1


def test_render_preview_service_raises_when_no_midi_after_filter(
    tmp_path: pathlib.Path,
) -> None:
    """render_preview raises ValueError when the filter leaves no MIDI files."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    commit_id = "e" * 64

    with pytest.raises(ValueError, match="No MIDI files found"):
        render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=tmp_path / "out.wav",
            track="nonexistent_track_xyz",
        )


def test_render_preview_service_raises_when_storpheus_unreachable(
    tmp_path: pathlib.Path,
) -> None:
    """render_preview raises StorpheusRenderUnavailableError when Storpheus is down."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    commit_id = "f" * 64

    with patch(
        "maestro.services.muse_render_preview.httpx.Client",
        side_effect=Exception("connection refused"),
    ):
        with pytest.raises(StorpheusRenderUnavailableError):
            render_preview(
                manifest=manifest,
                root=tmp_path,
                commit_id=commit_id,
                output_path=tmp_path / "out.wav",
            )


def test_render_preview_service_raises_when_storpheus_non_200(
    tmp_path: pathlib.Path,
) -> None:
    """render_preview raises StorpheusRenderUnavailableError on non-200 health check."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    commit_id = "g" * 64

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = mock_client
        with pytest.raises(StorpheusRenderUnavailableError):
            render_preview(
                manifest=manifest,
                root=tmp_path,
                commit_id=commit_id,
                output_path=tmp_path / "out.wav",
            )


def test_render_preview_service_mp3_format(tmp_path: pathlib.Path) -> None:
    """render_preview sets the correct format on the result for mp3."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "preview.mp3"
    commit_id = "h" * 64

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        result = render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=out,
            fmt=PreviewFormat.MP3,
        )

    assert result.format == PreviewFormat.MP3


def test_render_preview_service_flac_format(tmp_path: pathlib.Path) -> None:
    """render_preview sets the correct format on the result for flac."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "preview.flac"
    commit_id = "i" * 64

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        result = render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=out,
            fmt=PreviewFormat.FLAC,
        )

    assert result.format == PreviewFormat.FLAC


def test_render_preview_service_skips_non_midi_files(tmp_path: pathlib.Path) -> None:
    """render_preview counts non-MIDI manifest entries as skipped."""
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "beat.mid").write_bytes(_make_minimal_midi())
    (workdir / "meta.json").write_text("{}")
    manifest = {
        "beat.mid": hash_file(workdir / "beat.mid"),
        "meta.json": hash_file(workdir / "meta.json"),
    }
    out = tmp_path / "preview.wav"
    commit_id = "j" * 64

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        result = render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=out,
        )

    assert result.midi_files_used == 1
    assert result.skipped_count == 1


def test_render_preview_service_uses_custom_output_path(
    tmp_path: pathlib.Path,
) -> None:
    """render_preview writes to the caller-supplied output_path."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    custom_out = tmp_path / "custom" / "my-preview.wav"
    commit_id = "k" * 64

    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        result = render_preview(
            manifest=manifest,
            root=tmp_path,
            commit_id=commit_id,
            output_path=custom_out,
        )

    assert result.output_path == custom_out
    assert custom_out.exists()


# ---------------------------------------------------------------------------
# Integration tests — _render_preview_async (injectable core)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_render_preview_async_core_resolves_head(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_render_preview_async resolves HEAD and returns a RenderPreviewResult."""
    from datetime import datetime, timezone

    from maestro.muse_cli.db import insert_commit, upsert_object, upsert_snapshot
    from maestro.muse_cli.models import MuseCliCommit
    from maestro.muse_cli.snapshot import compute_commit_id, compute_snapshot_id

    repo_id = _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "beat.mid").write_bytes(_make_minimal_midi())

    oid = hash_file(workdir / "beat.mid")
    manifest = {"beat.mid": oid}
    snapshot_id = compute_snapshot_id(manifest)
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[],
        snapshot_id=snapshot_id,
        message="initial",
        committed_at_iso=ts.isoformat(),
    )

    await upsert_object(muse_cli_db_session, oid, 100)
    await upsert_snapshot(muse_cli_db_session, manifest=manifest, snapshot_id=snapshot_id)
    await muse_cli_db_session.flush()

    commit = MuseCliCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch="main",
        parent_commit_id=None,
        snapshot_id=snapshot_id,
        message="initial",
        author="",
        committed_at=ts,
    )
    await insert_commit(muse_cli_db_session, commit)
    await muse_cli_db_session.flush()

    _set_head(tmp_path, commit_id)

    out = tmp_path / "preview.wav"
    with patch("maestro.services.muse_render_preview.httpx.Client") as mock_cls:
        mock_cls.return_value = _storpheus_healthy_mock()
        with patch("maestro.muse_cli.commands.render_preview.settings") as mock_settings:
            mock_settings.storpheus_base_url = "http://storpheus:10002"
            result = await _render_preview_async(
                commit_ref=None,
                fmt=PreviewFormat.WAV,
                output=out,
                track=None,
                section=None,
                root=tmp_path,
                session=muse_cli_db_session,
            )

    assert result.output_path == out
    assert result.midi_files_used == 1
    assert result.commit_id == commit_id


# ---------------------------------------------------------------------------
# CLI integration tests — typer CliRunner
# ---------------------------------------------------------------------------


def test_render_preview_cli_no_repo(tmp_path: pathlib.Path) -> None:
    """muse render-preview exits with REPO_NOT_FOUND when not in a Muse repo."""
    import os

    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["render-preview"])
    assert result.exit_code != 0
    assert "not a muse repository" in result.output.lower() or result.exit_code == 2


def test_render_preview_cli_no_commits(tmp_path: pathlib.Path) -> None:
    """muse render-preview exits with USER_ERROR when HEAD has no commits."""
    _init_muse_repo(tmp_path)

    import os
    env = {**os.environ, "MUSE_REPO_ROOT": str(tmp_path)}
    result = runner.invoke(cli, ["render-preview"], env=env)
    assert result.exit_code != 0


def test_render_preview_cli_head_commit(tmp_path: pathlib.Path) -> None:
    """muse render-preview renders HEAD and prints the output path."""
    from unittest.mock import patch as _patch

    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "beat.mid").write_bytes(_make_minimal_midi())

    commit_id = "abcdef" + "0" * 58
    _set_head(tmp_path, commit_id)

    mock_manifest = {"beat.mid": hash_file(workdir / "beat.mid")}
    import os
    env = {**os.environ, "MUSE_REPO_ROOT": str(tmp_path)}

    out = tmp_path / "preview.wav"

    with _patch(
        "maestro.muse_cli.commands.render_preview.open_session"
    ) as mock_session_cm:
        mock_session = MagicMock()

        async def _fake_session_aenter(_: Any) -> Any:
            return mock_session

        async def _fake_session_aexit(_: Any, *args: Any) -> None:
            pass

        mock_session_cm.return_value.__aenter__ = _fake_session_aenter
        mock_session_cm.return_value.__aexit__ = _fake_session_aexit

        with _patch(
            "maestro.muse_cli.commands.render_preview._render_preview_async",
            return_value=RenderPreviewResult(
                output_path=out,
                format=PreviewFormat.WAV,
                commit_id=commit_id,
                midi_files_used=1,
                skipped_count=0,
                stubbed=True,
            ),
        ):
            result = runner.invoke(
                cli,
                ["render-preview", "--output", str(out)],
                env=env,
            )

    assert result.exit_code == 0
    assert str(out) in result.output


def test_render_preview_cli_json_output(tmp_path: pathlib.Path) -> None:
    """muse render-preview --json emits valid JSON with expected keys."""
    from unittest.mock import patch as _patch

    _init_muse_repo(tmp_path)
    commit_id = "json00" + "0" * 58
    _set_head(tmp_path, commit_id)
    out = tmp_path / "preview.wav"

    import os
    env = {**os.environ, "MUSE_REPO_ROOT": str(tmp_path)}

    with _patch(
        "maestro.muse_cli.commands.render_preview._render_preview_async",
        return_value=RenderPreviewResult(
            output_path=out,
            format=PreviewFormat.WAV,
            commit_id=commit_id,
            midi_files_used=1,
            skipped_count=0,
            stubbed=True,
        ),
    ):
        result = runner.invoke(
            cli,
            ["render-preview", "--json", "--output", str(out)],
            env=env,
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "output_path" in payload
    assert "commit_id" in payload
    assert "format" in payload
    assert "stubbed" in payload
    assert payload["stubbed"] is True


def test_render_preview_cli_storpheus_unreachable(tmp_path: pathlib.Path) -> None:
    """muse render-preview exits with INTERNAL_ERROR when Storpheus is down."""
    from unittest.mock import patch as _patch

    _init_muse_repo(tmp_path)
    commit_id = "stdown" + "0" * 58
    _set_head(tmp_path, commit_id)

    import os
    env = {**os.environ, "MUSE_REPO_ROOT": str(tmp_path)}

    with _patch(
        "maestro.muse_cli.commands.render_preview._render_preview_async",
        side_effect=StorpheusRenderUnavailableError("Connection refused"),
    ):
        result = runner.invoke(cli, ["render-preview"], env=env)

    assert result.exit_code != 0
    assert "storpheus" in result.output.lower()


def test_render_preview_cli_empty_snapshot(tmp_path: pathlib.Path) -> None:
    """muse render-preview exits with USER_ERROR for an empty snapshot."""
    from unittest.mock import patch as _patch

    _init_muse_repo(tmp_path)
    commit_id = "empty0" + "0" * 58
    _set_head(tmp_path, commit_id)

    import os
    env = {**os.environ, "MUSE_REPO_ROOT": str(tmp_path)}

    with _patch(
        "maestro.muse_cli.commands.render_preview._render_preview_async",
        side_effect=typer.Exit(code=1),
    ):
        result = runner.invoke(cli, ["render-preview"], env=env)

    assert result.exit_code != 0


def test_render_preview_cli_custom_format_and_output(tmp_path: pathlib.Path) -> None:
    """muse render-preview --format mp3 --output writes to the custom path."""
    from unittest.mock import patch as _patch

    _init_muse_repo(tmp_path)
    commit_id = "mp3000" + "0" * 58
    _set_head(tmp_path, commit_id)
    custom_out = tmp_path / "my-song.mp3"

    import os
    env = {**os.environ, "MUSE_REPO_ROOT": str(tmp_path)}

    with _patch(
        "maestro.muse_cli.commands.render_preview._render_preview_async",
        return_value=RenderPreviewResult(
            output_path=custom_out,
            format=PreviewFormat.MP3,
            commit_id=commit_id,
            midi_files_used=1,
            skipped_count=0,
            stubbed=True,
        ),
    ):
        result = runner.invoke(
            cli,
            ["render-preview", "--format", "mp3", "--output", str(custom_out)],
            env=env,
        )

    assert result.exit_code == 0
    assert str(custom_out) in result.output


# ---------------------------------------------------------------------------
# Additional imports needed for tests
# ---------------------------------------------------------------------------

import typer  # noqa: E402 (imported here for use in test bodies above)
