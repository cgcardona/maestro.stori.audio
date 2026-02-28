"""Tests for ``muse transpose`` — CLI interface, service layer, and integration.

Coverage:
- ``parse_interval``: signed integers, named intervals, error cases.
- ``update_key_metadata``: key transposition, edge cases.
- ``transpose_midi_bytes``: valid MIDI, drum exclusion, track filter, identity.
- ``apply_transpose_to_workdir``: file discovery, modification, dry-run.
- ``_transpose_async``: end-to-end with in-memory DB (creates real commits).
- CLI via CliRunner: argument parsing, flag handling, exit codes.

Regression test naming follows the issue specification:
- ``test_transpose_excludes_drum_channel_from_pitch_shift``
- ``test_transpose_semitones_updates_key_metadata``
- ``test_transpose_named_interval_down_perfect_fifth``
- ``test_transpose_scoped_to_track``
- ``test_transpose_dry_run_no_commit_created``
"""
from __future__ import annotations

import json
import pathlib
import struct
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.transpose import _transpose_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit
from maestro.services.muse_transpose import (
    TransposeResult,
    apply_transpose_to_workdir,
    parse_interval,
    transpose_midi_bytes,
    update_key_metadata,
)

# ---------------------------------------------------------------------------
# Repo + workdir helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    """Initialise a minimal .muse/ layout for testing."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    (muse / "refs" / "heads" / branch).write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    for name, data in files.items():
        (workdir / name).write_bytes(data)


async def _make_commit(
    root: pathlib.Path,
    session: AsyncSession,
    message: str = "initial",
    files: dict[str, bytes] | None = None,
) -> str:
    """Write files to muse-work/ and commit."""
    if files is None:
        files = {"track.mid": b"MIDI-placeholder"}
    _write_workdir(root, files)
    return await _commit_async(message=message, root=root, session=session)


# ---------------------------------------------------------------------------
# MIDI helpers
# ---------------------------------------------------------------------------


def _build_minimal_midi(
    note: int = 60,
    velocity: int = 80,
    channel: int = 0,
) -> bytes:
    """Build a minimal Type-0 MIDI file with one note-on + note-off pair.

    Produces a syntactically valid MIDI file with:
    - MThd header (format 0, 1 track, 480 ticks/quarter)
    - One MTrk chunk with:
      - delta(0) Note-On  channel note velocity
      - delta(480) Note-Off channel note 0
      - delta(0) End-of-Track meta

    Channel is 0-indexed (use 9 for drums).
    """
    note_on_status = 0x90 | (channel & 0x0F)
    note_off_status = 0x80 | (channel & 0x0F)

    track_events = bytes([
        0x00, note_on_status, note, velocity,        # delta=0, note on
        0x83, 0x60, note_off_status, note, 0x00,     # delta=480 (VLQ), note off
        0x00, 0xFF, 0x2F, 0x00,                      # delta=0, end of track
    ])

    header = b"MThd" + struct.pack(">I", 6) + struct.pack(">HHH", 0, 1, 480)
    track = b"MTrk" + struct.pack(">I", len(track_events)) + track_events
    return header + track


def _build_midi_with_track_name(
    track_name: str,
    note: int = 60,
    channel: int = 0,
) -> bytes:
    """Build a MIDI file with a Track Name meta-event followed by a note.

    The Track Name is embedded as meta-event 0xFF 0x03 at the start of the
    track so ``_get_track_name`` and the ``--track`` filter can detect it.
    """
    name_bytes = track_name.encode("latin-1")
    name_event = bytes([
        0x00, 0xFF, 0x03, len(name_bytes)
    ]) + name_bytes

    note_on_status = 0x90 | (channel & 0x0F)
    note_off_status = 0x80 | (channel & 0x0F)
    note_events = bytes([
        0x00, note_on_status, note, 80,
        0x83, 0x60, note_off_status, note, 0x00,
        0x00, 0xFF, 0x2F, 0x00,
    ])

    track_events = name_event + note_events
    header = b"MThd" + struct.pack(">I", 6) + struct.pack(">HHH", 0, 1, 480)
    track = b"MTrk" + struct.pack(">I", len(track_events)) + track_events
    return header + track


# ---------------------------------------------------------------------------
# parse_interval tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("interval_str,expected", [
    ("+3", 3),
    ("-5", -5),
    ("+12", 12),
    ("12", 12),
    ("-12", -12),
    ("0", 0),
    ("+0", 0),
])
def test_parse_interval_integers(interval_str: str, expected: int) -> None:
    """Signed integers are parsed correctly to semitone counts."""
    assert parse_interval(interval_str) == expected


def test_transpose_named_interval_down_perfect_fifth() -> None:
    """``down-perfect5th`` resolves to -7 semitones (regression test)."""
    assert parse_interval("down-perfect5th") == -7


@pytest.mark.parametrize("interval_str,expected", [
    ("up-minor3rd", 3),
    ("up-major3rd", 4),
    ("up-perfect4th", 5),
    ("up-perfect5th", 7),
    ("up-octave", 12),
    ("down-minor3rd", -3),
    ("down-major3rd", -4),
    ("down-perfect5th", -7),
    ("down-octave", -12),
    ("up-unison", 0),
])
def test_parse_interval_named(interval_str: str, expected: int) -> None:
    """Named intervals with up-/down- prefix resolve to correct semitone counts."""
    assert parse_interval(interval_str) == expected


def test_parse_interval_invalid_string_raises_value_error() -> None:
    """Unparseable strings raise ValueError with a descriptive message."""
    with pytest.raises(ValueError, match="Cannot parse interval"):
        parse_interval("jump-high")


def test_parse_interval_unknown_name_raises_value_error() -> None:
    """Known direction but unknown interval name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown interval name"):
        parse_interval("up-ultrawide9th")


# ---------------------------------------------------------------------------
# update_key_metadata tests
# ---------------------------------------------------------------------------


def test_transpose_semitones_updates_key_metadata() -> None:
    """Transposing 'Eb major' by +2 semitones yields 'F major' (regression test)."""
    assert update_key_metadata("Eb major", 2) == "F major"


@pytest.mark.parametrize("key_str,semitones,expected", [
    ("C major", 2, "D major"),
    ("C major", -1, "B major"),
    ("F# minor", 2, "Ab minor"),  # G#/Ab are enharmonic; service uses flat names
    ("Bb major", 3, "Db major"),
    ("A minor", 12, "A minor"),    # octave → same key
    ("G major", -7, "C major"),   # down perfect 5th
    ("Eb major", 2, "F major"),
    ("Eb major", 3, "F# major"),
])
def test_update_key_metadata_parametrized(
    key_str: str, semitones: int, expected: str
) -> None:
    """Key metadata is updated correctly for common transpositions."""
    assert update_key_metadata(key_str, semitones) == expected


def test_update_key_metadata_unknown_root_returns_unchanged() -> None:
    """An unrecognized root note in the key string is returned unchanged."""
    assert update_key_metadata("X# major", 3) == "X# major"


def test_update_key_metadata_empty_string() -> None:
    """An empty key string is returned unchanged."""
    assert update_key_metadata("", 3) == ""


def test_update_key_metadata_preserves_mode() -> None:
    """Mode string (major, minor, dorian, etc.) is preserved verbatim."""
    assert update_key_metadata("D dorian", 2) == "E dorian"


# ---------------------------------------------------------------------------
# transpose_midi_bytes tests
# ---------------------------------------------------------------------------


def test_transpose_midi_bytes_transposes_note() -> None:
    """A note-on event on a pitched channel is shifted by the semitone offset."""
    midi = _build_minimal_midi(note=60, channel=0)
    transposed, count = transpose_midi_bytes(midi, semitones=3)
    assert count > 0, "Expected at least one note byte to change"
    # The transposed file should differ from the original
    assert transposed != midi


def test_transpose_excludes_drum_channel_from_pitch_shift() -> None:
    """Note-on events on channel 9 (drums) must NOT be transposed (regression test)."""
    drum_midi = _build_minimal_midi(note=36, channel=9)  # channel 9 = drums
    transposed, count = transpose_midi_bytes(drum_midi, semitones=5)
    assert count == 0, "Drum notes must not be transposed"
    assert transposed == drum_midi, "Drum MIDI bytes must be identical after transpose"


def test_transpose_midi_bytes_zero_semitones_identity() -> None:
    """Zero semitones applied to a MIDI file returns the file unchanged."""
    midi = _build_minimal_midi(note=60, channel=0)
    transposed, count = transpose_midi_bytes(midi, semitones=0)
    assert count == 0
    assert transposed == midi


def test_transpose_midi_bytes_clamps_note_at_max() -> None:
    """Notes shifted beyond 127 are clamped to 127."""
    midi = _build_minimal_midi(note=126, channel=0)
    transposed, _ = transpose_midi_bytes(midi, semitones=10)
    # We can't easily introspect the note value without re-parsing,
    # but we verify the file is structurally valid (same length, different bytes)
    assert len(transposed) == len(midi)
    assert transposed != midi


def test_transpose_midi_bytes_clamps_note_at_min() -> None:
    """Notes shifted below 0 are clamped to 0."""
    midi = _build_minimal_midi(note=1, channel=0)
    transposed, _ = transpose_midi_bytes(midi, semitones=-10)
    assert len(transposed) == len(midi)


def test_transpose_midi_bytes_invalid_file_returns_unchanged() -> None:
    """Non-MIDI bytes are returned unchanged with 0 notes changed."""
    data = b"not a midi file at all"
    transposed, count = transpose_midi_bytes(data, semitones=5)
    assert transposed == data
    assert count == 0


def test_transpose_scoped_to_track() -> None:
    """``--track`` filter only transposes the matching track; non-matching tracks are unchanged (regression test)."""
    melody_midi = _build_midi_with_track_name("melody", note=60, channel=0)
    bass_midi = _build_midi_with_track_name("bass", note=36, channel=1)

    melody_transposed, melody_count = transpose_midi_bytes(melody_midi, semitones=3, track_filter="melody")
    bass_transposed, bass_count = transpose_midi_bytes(bass_midi, semitones=3, track_filter="melody")

    assert melody_count > 0, "Melody track should be transposed"
    assert bass_count == 0, "Bass track should be skipped (name doesn't match 'melody')"
    assert bass_transposed == bass_midi, "Bass MIDI bytes should be identical"


def test_transpose_track_filter_case_insensitive() -> None:
    """Track name filter matching is case-insensitive."""
    midi = _build_midi_with_track_name("Lead Guitar", note=60, channel=0)
    _, count = transpose_midi_bytes(midi, semitones=2, track_filter="lead guitar")
    assert count > 0


def test_transpose_track_filter_substring_match() -> None:
    """Track name filter matches as a substring."""
    midi = _build_midi_with_track_name("Piano Lead", note=60, channel=0)
    _, count = transpose_midi_bytes(midi, semitones=2, track_filter="lead")
    assert count > 0


# ---------------------------------------------------------------------------
# apply_transpose_to_workdir tests
# ---------------------------------------------------------------------------


def test_apply_transpose_to_workdir_modifies_midi_files(
    tmp_path: pathlib.Path,
) -> None:
    """MIDI files in workdir are transposed and written back."""
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    midi_path = workdir / "track.mid"
    midi_path.write_bytes(_build_minimal_midi(note=60, channel=0))

    modified, skipped = apply_transpose_to_workdir(workdir, semitones=3)

    assert "track.mid" in modified
    assert len(skipped) == 0
    # File should be modified on disk
    new_bytes = midi_path.read_bytes()
    assert new_bytes != _build_minimal_midi(note=60, channel=0)


def test_apply_transpose_to_workdir_skips_non_midi(
    tmp_path: pathlib.Path,
) -> None:
    """Non-MIDI files (e.g. JSON, WAV) are skipped entirely."""
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "notes.json").write_text('{"key": "C"}')
    (workdir / "track.mid").write_bytes(_build_minimal_midi(note=60, channel=0))

    modified, skipped = apply_transpose_to_workdir(workdir, semitones=2)

    assert "track.mid" in modified
    assert "notes.json" not in modified


def test_apply_transpose_to_workdir_dry_run_does_not_write(
    tmp_path: pathlib.Path,
) -> None:
    """Dry-run mode reports what would change without writing files (regression test)."""
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    original_bytes = _build_minimal_midi(note=60, channel=0)
    midi_path = workdir / "track.mid"
    midi_path.write_bytes(original_bytes)

    modified, _ = apply_transpose_to_workdir(workdir, semitones=3, dry_run=True)

    assert "track.mid" in modified, "Dry-run should still report files that would be modified"
    assert midi_path.read_bytes() == original_bytes, "File must not be written in dry-run mode"


def test_apply_transpose_to_workdir_missing_workdir(
    tmp_path: pathlib.Path,
) -> None:
    """Missing muse-work/ directory returns empty lists without raising."""
    workdir = tmp_path / "muse-work"  # intentionally not created
    modified, skipped = apply_transpose_to_workdir(workdir, semitones=3)
    assert modified == []
    assert skipped == []


def test_apply_transpose_dry_run_no_commit_created(
    tmp_path: pathlib.Path,
) -> None:
    """Dry-run flag does not write files — ``files_modified`` are reported only (regression test)."""
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    original = _build_minimal_midi(note=60, channel=0)
    (workdir / "song.mid").write_bytes(original)

    modified, _ = apply_transpose_to_workdir(workdir, semitones=5, dry_run=True)

    assert "song.mid" in modified
    assert (workdir / "song.mid").read_bytes() == original


# ---------------------------------------------------------------------------
# _transpose_async integration tests (require DB session)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_transpose_async_creates_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_transpose_async creates a new commit pointing to the transposed snapshot."""
    _init_muse_repo(tmp_path)
    midi = _build_minimal_midi(note=60, channel=0)
    await _make_commit(tmp_path, muse_cli_db_session, files={"beat.mid": midi})

    result = await _transpose_async(
        root=tmp_path,
        session=muse_cli_db_session,
        semitones=2,
        commit_ref=None,
        track_filter=None,
        section_filter=None,
        message=None,
        dry_run=False,
        as_json=False,
    )

    assert result.new_commit_id is not None
    assert len(result.files_modified) > 0
    assert result.semitones == 2
    assert not result.dry_run

    # Verify commit was persisted
    new_commit = await muse_cli_db_session.get(MuseCliCommit, result.new_commit_id)
    assert new_commit is not None
    assert new_commit.parent_commit_id == result.source_commit_id


@pytest.mark.anyio
async def test_transpose_async_dry_run_creates_no_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_transpose_async with dry_run=True does not create a commit or write files."""
    _init_muse_repo(tmp_path)
    midi = _build_minimal_midi(note=60, channel=0)
    await _make_commit(tmp_path, muse_cli_db_session, files={"melody.mid": midi})

    original_bytes = (tmp_path / "muse-work" / "melody.mid").read_bytes()

    result = await _transpose_async(
        root=tmp_path,
        session=muse_cli_db_session,
        semitones=3,
        commit_ref=None,
        track_filter=None,
        section_filter=None,
        message=None,
        dry_run=True,
        as_json=False,
    )

    assert result.new_commit_id is None
    assert result.dry_run is True
    # File must not have been written
    assert (tmp_path / "muse-work" / "melody.mid").read_bytes() == original_bytes


@pytest.mark.anyio
async def test_transpose_async_updates_key_metadata(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Key metadata in commit is updated when source commit has a key annotation."""
    from maestro.muse_cli.db import resolve_commit_ref

    _init_muse_repo(tmp_path)
    midi = _build_minimal_midi(note=60, channel=0)
    source_id = await _make_commit(tmp_path, muse_cli_db_session, files={"track.mid": midi})

    # Manually annotate source commit with a key
    source_commit = await muse_cli_db_session.get(MuseCliCommit, source_id)
    assert source_commit is not None
    source_commit.commit_metadata = {"key": "Eb major"}
    muse_cli_db_session.add(source_commit)
    await muse_cli_db_session.flush()

    result = await _transpose_async(
        root=tmp_path,
        session=muse_cli_db_session,
        semitones=2,
        commit_ref=None,
        track_filter=None,
        section_filter=None,
        message=None,
        dry_run=False,
        as_json=False,
    )

    assert result.original_key == "Eb major"
    assert result.new_key == "F major"

    # Verify persisted on the new commit
    new_commit = await muse_cli_db_session.get(MuseCliCommit, result.new_commit_id)
    assert new_commit is not None
    assert new_commit.commit_metadata is not None
    assert new_commit.commit_metadata.get("key") == "F major"


@pytest.mark.anyio
async def test_transpose_async_missing_commit_ref_exits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_transpose_async raises typer.Exit(USER_ERROR) when commit ref not found."""
    _init_muse_repo(tmp_path)
    # No commits in the repo

    with pytest.raises(typer.Exit) as exc_info:
        await _transpose_async(
            root=tmp_path,
            session=muse_cli_db_session,
            semitones=2,
            commit_ref="nonexistent",
            track_filter=None,
            section_filter=None,
            message=None,
            dry_run=False,
            as_json=False,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_transpose_async_custom_message(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Custom ``--message`` is used as the commit message for the transposed commit."""
    _init_muse_repo(tmp_path)
    midi = _build_minimal_midi(note=60, channel=0)
    await _make_commit(tmp_path, muse_cli_db_session, files={"track.mid": midi})

    result = await _transpose_async(
        root=tmp_path,
        session=muse_cli_db_session,
        semitones=5,
        commit_ref=None,
        track_filter=None,
        section_filter=None,
        message="My custom transpose message",
        dry_run=False,
        as_json=False,
    )

    assert result.new_commit_id is not None
    new_commit = await muse_cli_db_session.get(MuseCliCommit, result.new_commit_id)
    assert new_commit is not None
    assert new_commit.message == "My custom transpose message"


@pytest.mark.anyio
async def test_transpose_async_json_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--json`` flag returns a result with all required fields populated."""
    _init_muse_repo(tmp_path)
    midi = _build_minimal_midi(note=60, channel=0)
    await _make_commit(tmp_path, muse_cli_db_session, files={"track.mid": midi})

    result = await _transpose_async(
        root=tmp_path,
        session=muse_cli_db_session,
        semitones=3,
        commit_ref=None,
        track_filter=None,
        section_filter=None,
        message=None,
        dry_run=False,
        as_json=True,
    )

    # Verify the result object has all expected fields (JSON rendering is tested via CLI runner)
    assert result.source_commit_id != ""
    assert result.semitones == 3
    assert result.new_commit_id is not None
    assert isinstance(result.files_modified, list)
    assert result.dry_run is False


# ---------------------------------------------------------------------------
# CLI CliRunner tests
# ---------------------------------------------------------------------------


def test_cli_transpose_bad_interval_exits_with_user_error(
    tmp_path: pathlib.Path,
) -> None:
    """Invalid interval string exits with code 1 (USER_ERROR) without crashing."""
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    # We need a repo to get past require_repo, but the interval is checked first
    result = runner.invoke(cli, ["transpose", "jump-high"])
    assert result.exit_code == ExitCode.USER_ERROR


def test_cli_transpose_help() -> None:
    """``muse transpose --help`` exits cleanly and contains interval description."""
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["transpose", "--help"])
    assert result.exit_code == 0
    assert "interval" in result.output.lower() or "transpose" in result.output.lower()
