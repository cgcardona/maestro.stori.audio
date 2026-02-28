"""Tests for ``muse export`` command and export_engine module.

Test matrix:
- ``test_export_midi_writes_valid_midi_file``
- ``test_export_json_outputs_full_note_structure``
- ``test_export_musicxml_produces_valid_xml``
- ``test_export_track_scoped_midi``
- ``test_export_section_scoped_midi``
- ``test_export_split_tracks_creates_one_file_per_track``
- ``test_export_wav_raises_clear_error_when_storpheus_unavailable``
- ``test_export_no_commits_exits_user_error``
- ``test_export_commit_prefix_resolution``
- ``test_filter_manifest_track``
- ``test_filter_manifest_section``
- ``test_filter_manifest_both``
- ``test_filter_manifest_no_filter``
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
from maestro.muse_cli.commands.export import _export_async, _default_output_path
from maestro.muse_cli.export_engine import (
    ExportFormat,
    MuseExportOptions,
    StorpheusUnavailableError,
    export_json,
    export_midi,
    export_musicxml,
    export_abc,
    export_wav,
    export_snapshot,
    filter_manifest,
    resolve_commit_id,
    _midi_note_to_abc,
    _midi_note_to_step_octave,
)
from maestro.muse_cli.snapshot import hash_file

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
    """Return a minimal, well-formed MIDI file (single note, type 0).

    Format: Type 0, 1 track, 480 ticks/beat.
    Track: Note On C4 (velocity 64) at tick 0, Note Off at tick 480.
    """
    # MIDI header: MThd + length(6) + format(0) + tracks(1) + tpb(480)
    header = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
    # Track: Note On C4, wait 480 ticks, Note Off C4, end-of-track
    track_data = (
        b"\x00\x90\x3c\x40"     # delta=0, Note On ch=0, pitch=60, vel=64
        b"\x81\x60\x80\x3c\x00" # delta=480 (var-len), Note Off ch=0, pitch=60, vel=0
        b"\x00\xff\x2f\x00"     # delta=0, End of Track
    )
    track_len = len(track_data).to_bytes(4, "big")
    return header + b"MTrk" + track_len + track_data


def _make_manifest_with_midi(
    tmp_path: pathlib.Path,
    filenames: list[str] | None = None,
) -> dict[str, str]:
    """Write MIDI files to muse-work/ and return a manifest dict."""
    workdir = tmp_path / "muse-work"
    workdir.mkdir(exist_ok=True)
    filenames = filenames or ["beat.mid"]
    midi_bytes = _make_minimal_midi()
    manifest: dict[str, str] = {}
    for name in filenames:
        p = workdir / name
        p.write_bytes(midi_bytes)
        manifest[name] = hash_file(p)
    return manifest


# ---------------------------------------------------------------------------
# Unit tests — filter_manifest
# ---------------------------------------------------------------------------


def test_filter_manifest_no_filter() -> None:
    """filter_manifest returns all entries when no filters are given."""
    m = {"tracks/piano/take1.mid": "aaa", "tracks/bass/take1.mid": "bbb"}
    result = filter_manifest(m, track=None, section=None)
    assert result == m


def test_filter_manifest_track() -> None:
    """filter_manifest keeps only entries matching the track substring."""
    m = {
        "tracks/piano/take1.mid": "aaa",
        "tracks/bass/take1.mid": "bbb",
        "tracks/piano/take2.mid": "ccc",
    }
    result = filter_manifest(m, track="piano", section=None)
    assert set(result.keys()) == {"tracks/piano/take1.mid", "tracks/piano/take2.mid"}


def test_filter_manifest_section() -> None:
    """filter_manifest keeps only entries matching the section substring."""
    m = {
        "chorus/piano.mid": "aaa",
        "verse/piano.mid": "bbb",
        "chorus/bass.mid": "ccc",
    }
    result = filter_manifest(m, track=None, section="chorus")
    assert set(result.keys()) == {"chorus/piano.mid", "chorus/bass.mid"}


def test_filter_manifest_both() -> None:
    """filter_manifest applies both track and section filters (AND semantics)."""
    m = {
        "chorus/piano.mid": "aaa",
        "verse/piano.mid": "bbb",
        "chorus/bass.mid": "ccc",
    }
    result = filter_manifest(m, track="piano", section="chorus")
    assert set(result.keys()) == {"chorus/piano.mid"}


# ---------------------------------------------------------------------------
# Unit tests — export_midi
# ---------------------------------------------------------------------------


def test_export_midi_writes_valid_midi_file(tmp_path: pathlib.Path) -> None:
    """export_midi copies a MIDI file to the output path."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "exports" / "out.mid"
    opts = MuseExportOptions(
        format=ExportFormat.MIDI,
        commit_id="abc123",
        output_path=out,
    )

    result = export_midi(manifest, tmp_path, opts)

    assert result.paths_written == [out]
    assert out.exists()
    assert out.read_bytes() == _make_minimal_midi()


def test_export_track_scoped_midi(tmp_path: pathlib.Path) -> None:
    """export_midi respects the track filter: only piano files are exported."""
    (tmp_path / "muse-work").mkdir()
    midi_bytes = _make_minimal_midi()
    (tmp_path / "muse-work" / "piano.mid").write_bytes(midi_bytes)
    (tmp_path / "muse-work" / "bass.mid").write_bytes(midi_bytes)
    manifest = {
        "piano.mid": hash_file(tmp_path / "muse-work" / "piano.mid"),
        "bass.mid": hash_file(tmp_path / "muse-work" / "bass.mid"),
    }

    filtered = filter_manifest(manifest, track="piano", section=None)
    out = tmp_path / "exports" / "out.mid"
    opts = MuseExportOptions(
        format=ExportFormat.MIDI,
        commit_id="abc123",
        output_path=out,
    )
    result = export_midi(filtered, tmp_path, opts)

    assert len(result.paths_written) == 1
    assert result.paths_written[0].name == "out.mid"


def test_export_section_scoped_midi(tmp_path: pathlib.Path) -> None:
    """export_midi respects the section filter: only chorus files are exported."""
    workdir = tmp_path / "muse-work"
    (workdir / "chorus").mkdir(parents=True)
    (workdir / "verse").mkdir(parents=True)
    midi_bytes = _make_minimal_midi()
    (workdir / "chorus" / "piano.mid").write_bytes(midi_bytes)
    (workdir / "verse" / "piano.mid").write_bytes(midi_bytes)
    manifest = {
        "chorus/piano.mid": hash_file(workdir / "chorus" / "piano.mid"),
        "verse/piano.mid": hash_file(workdir / "verse" / "piano.mid"),
    }

    filtered = filter_manifest(manifest, track=None, section="chorus")
    out_dir = tmp_path / "exports"
    opts = MuseExportOptions(
        format=ExportFormat.MIDI,
        commit_id="abc123",
        output_path=out_dir,
        split_tracks=True,
    )
    result = export_midi(filtered, tmp_path, opts)

    assert len(result.paths_written) == 1
    assert "chorus" not in result.paths_written[0].name  # stem is "piano"
    assert result.paths_written[0].name == "piano.mid"


def test_export_split_tracks_creates_one_file_per_track(tmp_path: pathlib.Path) -> None:
    """--split-tracks writes one .mid file per MIDI entry in the manifest."""
    manifest = _make_manifest_with_midi(tmp_path, ["drums.mid", "keys.mid", "bass.mid"])
    out_dir = tmp_path / "exports"
    opts = MuseExportOptions(
        format=ExportFormat.MIDI,
        commit_id="abc123",
        output_path=out_dir,
        split_tracks=True,
    )

    result = export_midi(manifest, tmp_path, opts)

    assert len(result.paths_written) == 3
    stems = {p.stem for p in result.paths_written}
    assert stems == {"drums", "keys", "bass"}


# ---------------------------------------------------------------------------
# Unit tests — export_json
# ---------------------------------------------------------------------------


def test_export_json_outputs_full_note_structure(tmp_path: pathlib.Path) -> None:
    """export_json writes a JSON file with commit_id, exported_at, and files array."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "exports" / "out.json"
    commit_id = "deadbeef" * 8  # 64-char hex
    opts = MuseExportOptions(
        format=ExportFormat.JSON,
        commit_id=commit_id,
        output_path=out,
    )

    result = export_json(manifest, tmp_path, opts)

    assert result.paths_written == [out]
    data = json.loads(out.read_text())
    assert data["commit_id"] == commit_id
    assert "exported_at" in data
    assert isinstance(data["files"], list)
    assert len(data["files"]) == 1
    assert data["files"][0]["path"] == "beat.mid"
    assert "object_id" in data["files"][0]
    assert data["files"][0]["exists_in_workdir"] is True


# ---------------------------------------------------------------------------
# Unit tests — export_musicxml
# ---------------------------------------------------------------------------


def test_export_musicxml_produces_valid_xml(tmp_path: pathlib.Path) -> None:
    """export_musicxml writes a well-formed MusicXML file."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "exports" / "out.xml"
    opts = MuseExportOptions(
        format=ExportFormat.MUSICXML,
        commit_id="abc123",
        output_path=out,
    )

    result = export_musicxml(manifest, tmp_path, opts)

    assert result.paths_written == [out]
    content = out.read_text(encoding="utf-8")
    assert '<?xml version="1.0"' in content
    assert "<score-partwise" in content
    assert "<part-list" in content


# ---------------------------------------------------------------------------
# Unit tests — WAV (Storpheus unavailable)
# ---------------------------------------------------------------------------


def test_export_wav_raises_clear_error_when_storpheus_unavailable(
    tmp_path: pathlib.Path,
) -> None:
    """export_wav raises StorpheusUnavailableError when Storpheus is not reachable."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "exports" / "out.wav"
    opts = MuseExportOptions(
        format=ExportFormat.WAV,
        commit_id="abc123",
        output_path=out,
    )

    with patch("maestro.muse_cli.export_engine.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.side_effect = ConnectionRefusedError(
            "Connection refused"
        )
        with pytest.raises(StorpheusUnavailableError) as exc_info:
            export_wav(manifest, tmp_path, opts, storpheus_url="http://localhost:10002")

    assert "not reachable" in str(exc_info.value).lower() or "storpheus" in str(exc_info.value).lower()


def test_export_wav_non_200_raises_unavailable(tmp_path: pathlib.Path) -> None:
    """export_wav raises StorpheusUnavailableError on non-200 health response."""
    manifest = _make_manifest_with_midi(tmp_path, ["beat.mid"])
    out = tmp_path / "exports" / "out.wav"
    opts = MuseExportOptions(
        format=ExportFormat.WAV,
        commit_id="abc123",
        output_path=out,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 503

    with patch("maestro.muse_cli.export_engine.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
        with pytest.raises(StorpheusUnavailableError):
            export_wav(manifest, tmp_path, opts, storpheus_url="http://localhost:10002")


# ---------------------------------------------------------------------------
# Unit tests — CLI integration
# ---------------------------------------------------------------------------


def test_export_no_commits_exits_user_error(tmp_path: pathlib.Path) -> None:
    """``muse export --format json`` exits 1 when there are no commits (HEAD empty)."""
    _init_muse_repo(tmp_path)  # HEAD ref file is empty

    with patch.dict("os.environ", {"MUSE_REPO_ROOT": str(tmp_path)}):
        result = runner.invoke(cli, ["export", "--format", "json"])

    # Exit code 1 (USER_ERROR) expected because HEAD is empty.
    assert result.exit_code != 0


def test_export_cli_json_format(tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession) -> None:
    """``muse export --format json`` writes a JSON file to the default path."""
    import asyncio
    from maestro.muse_cli.commands.commit import _commit_async

    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "beat.mid").write_bytes(_make_minimal_midi())

    commit_id = asyncio.get_event_loop().run_until_complete(
        _commit_async(
            message="test commit",
            root=tmp_path,
            session=muse_cli_db_session,
        )
    )
    _set_head(tmp_path, commit_id)

    # Use _export_async directly to avoid the DB session bootstrapping in the CLI.
    async def _run() -> None:
        result = await _export_async(
            commit_ref=None,
            fmt=ExportFormat.JSON,
            output=tmp_path / "out.json",
            track=None,
            section=None,
            split_tracks=False,
            root=tmp_path,
            session=muse_cli_db_session,
        )
        assert result.paths_written
        data = json.loads((tmp_path / "out.json").read_text())
        assert data["commit_id"] == commit_id
        assert len(data["files"]) == 1

    asyncio.get_event_loop().run_until_complete(_run())


@pytest.mark.anyio
async def test_export_commit_prefix_resolution(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """_export_async resolves a short commit prefix to the full commit ID."""
    from maestro.muse_cli.commands.commit import _commit_async

    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "melody.mid").write_bytes(_make_minimal_midi())

    commit_id = await _commit_async(
        message="prefix resolution test",
        root=tmp_path,
        session=muse_cli_db_session,
    )
    _set_head(tmp_path, commit_id)

    result = await _export_async(
        commit_ref=commit_id[:8],  # use short prefix
        fmt=ExportFormat.JSON,
        output=tmp_path / "prefix_out.json",
        track=None,
        section=None,
        split_tracks=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert result.commit_id == commit_id
    assert result.paths_written


# ---------------------------------------------------------------------------
# Unit tests — helper functions
# ---------------------------------------------------------------------------


def test_midi_note_to_step_octave_middle_c() -> None:
    """MIDI note 60 (middle C) maps to step='C', octave=4."""
    step, octave = _midi_note_to_step_octave(60)
    assert step == "C"
    assert octave == 4


def test_midi_note_to_step_octave_sharp() -> None:
    """MIDI note 61 (C#4) maps to step='C#', octave=4."""
    step, octave = _midi_note_to_step_octave(61)
    assert step == "C#"
    assert octave == 4


def test_midi_note_to_abc_middle_c() -> None:
    """MIDI note 60 (C4) maps to ABC 'C'."""
    assert _midi_note_to_abc(60) == "C"


def test_midi_note_to_abc_c5() -> None:
    """MIDI note 72 (C5) maps to ABC 'c' (lowercase)."""
    assert _midi_note_to_abc(72) == "c"


def test_midi_note_to_abc_c3() -> None:
    """MIDI note 48 (C3) maps to ABC 'C,' (comma suffix)."""
    assert _midi_note_to_abc(48) == "C,"


def test_resolve_commit_id_returns_head(tmp_path: pathlib.Path) -> None:
    """resolve_commit_id returns the HEAD commit ID when prefix is None."""
    _init_muse_repo(tmp_path)
    commit_id = "a" * 64
    _set_head(tmp_path, commit_id)

    result = resolve_commit_id(tmp_path, None)
    assert result == commit_id


def test_resolve_commit_id_returns_prefix(tmp_path: pathlib.Path) -> None:
    """resolve_commit_id returns the prefix unchanged when provided."""
    _init_muse_repo(tmp_path)
    prefix = "abcd1234"

    result = resolve_commit_id(tmp_path, prefix)
    assert result == prefix


def test_resolve_commit_id_raises_when_no_commits(tmp_path: pathlib.Path) -> None:
    """resolve_commit_id raises ValueError when HEAD has no commits."""
    _init_muse_repo(tmp_path)  # HEAD ref is empty

    with pytest.raises(ValueError, match="No commits yet"):
        resolve_commit_id(tmp_path, None)


def test_default_output_path() -> None:
    """_default_output_path uses first 8 chars of commit_id and format extension."""
    commit_id = "abcdef12" + "0" * 56
    path = _default_output_path(commit_id, ExportFormat.MIDI)
    assert path.name == "abcdef12.midi"
    assert path.parent.name == "exports"
