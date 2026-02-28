"""Tests for ``muse import`` — MIDI and MusicXML import pipeline.

All async tests use ``@pytest.mark.anyio``.
The ``muse_cli_db_session`` fixture (in tests/muse_cli/conftest.py) provides
an isolated in-memory SQLite session; no real Postgres instance is required.

Test MIDI fixtures are synthesised in-memory using ``mido`` so no binary
files need to be committed to the repository.
"""
from __future__ import annotations

import json
import pathlib
import struct
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.commands.import_cmd import _import_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.midi_parser import (
    MuseImportData,
    NoteEvent,
    analyze_import,
    apply_track_map,
    parse_file,
    parse_midi_file,
    parse_musicxml_file,
    parse_track_map_arg,
)
from maestro.muse_cli.models import MuseCliCommit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout compatible with _commit_async."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _make_minimal_midi(path: pathlib.Path) -> None:
    """Write a minimal but valid Type-0 MIDI file using raw bytes.

    Contains a single track with: tempo (120 BPM), note-on C4 ch0, note-off C4 ch0.
    Using raw bytes avoids requiring mido at test-fixture-creation time.
    """
    # MIDI header: MThd, length=6, format=0, ntracks=1, division=480
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)

    # Track events (delta_time, event):
    # 0   FF 51 03 07 A1 20  — set_tempo: 500000 µs = 120 BPM
    # 0   90 3C 64           — note_on ch0 pitch=60 vel=100
    # 240 80 3C 00           — note_off ch0 pitch=60
    # 0   FF 2F 00           — end_of_track
    track_data = (
        b"\x00\xFF\x51\x03\x07\xA1\x20"   # tempo
        b"\x00\x90\x3C\x64"               # note_on C4
        b"\x81\x70\x80\x3C\x00"           # delta=240 (varint), note_off
        b"\x00\xFF\x2F\x00"               # end_of_track
    )
    track = b"MTrk" + struct.pack(">I", len(track_data)) + track_data
    path.write_bytes(header + track)


def _make_minimal_musicxml(path: pathlib.Path) -> None:
    """Write a minimal valid MusicXML file with one part and two notes."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC
    "-//Recordare//DTD MusicXML 3.1 Partwise//EN"
    "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1">
      <part-name>Piano</part-name>
    </score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
      </attributes>
      <direction placement="above">
        <direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>120</per-minute></metronome></direction-type>
        <sound tempo="120"/>
      </direction>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration>
        <type>quarter</type>
      </note>
      <note>
        <pitch><step>E</step><octave>4</octave></pitch>
        <duration>1</duration>
        <type>quarter</type>
      </note>
    </measure>
  </part>
</score-partwise>
"""
    path.write_text(xml)


# ---------------------------------------------------------------------------
# midi_parser unit tests
# ---------------------------------------------------------------------------


def test_parse_midi_file_returns_note_data(tmp_path: pathlib.Path) -> None:
    """parse_midi_file extracts at least one NoteEvent from a valid MIDI file."""
    mid = tmp_path / "song.mid"
    _make_minimal_midi(mid)
    data = parse_midi_file(mid)
    assert data.format == "midi"
    assert len(data.notes) >= 1
    assert data.ticks_per_beat == 480
    assert data.tempo_bpm == pytest.approx(120.0, abs=1.0)


def test_parse_musicxml_creates_commit(tmp_path: pathlib.Path) -> None:
    """parse_musicxml_file returns a MuseImportData with notes for a valid MusicXML."""
    xml = tmp_path / "song.musicxml"
    _make_minimal_musicxml(xml)
    data = parse_musicxml_file(xml)
    assert data.format == "musicxml"
    assert len(data.notes) >= 1
    assert data.tempo_bpm == pytest.approx(120.0, abs=1.0)
    assert "Piano" in data.tracks


def test_parse_file_dispatches_by_extension(tmp_path: pathlib.Path) -> None:
    """`parse_file` dispatches to the correct parser via extension."""
    mid = tmp_path / "x.mid"
    _make_minimal_midi(mid)
    data = parse_file(mid)
    assert data.format == "midi"

    xml = tmp_path / "x.musicxml"
    _make_minimal_musicxml(xml)
    data2 = parse_file(xml)
    assert data2.format == "musicxml"


def test_import_unsupported_extension_raises_error(tmp_path: pathlib.Path) -> None:
    """parse_file raises ValueError for unsupported extensions."""
    bad = tmp_path / "song.mp3"
    bad.write_bytes(b"not midi")
    with pytest.raises(ValueError, match="Unsupported file extension"):
        parse_file(bad)


def test_import_malformed_midi_raises_clear_error(tmp_path: pathlib.Path) -> None:
    """Malformed MIDI content raises RuntimeError with a clear message (regression test)."""
    bad = tmp_path / "bad.mid"
    bad.write_bytes(b"not a midi file at all")
    with pytest.raises(RuntimeError, match="Cannot parse MIDI file"):
        parse_midi_file(bad)


def test_import_track_map_assigns_named_tracks(tmp_path: pathlib.Path) -> None:
    """apply_track_map renames channel_name fields per the provided mapping."""
    mid = tmp_path / "song.mid"
    _make_minimal_midi(mid)
    data = parse_midi_file(mid)

    remapped = apply_track_map(data.notes, {"ch0": "bass", "ch1": "piano"})
    ch0_notes = [n for n in remapped if n.channel == 0]
    assert all(n.channel_name == "bass" for n in ch0_notes)


def test_apply_track_map_bare_channel_key(tmp_path: pathlib.Path) -> None:
    """apply_track_map accepts bare channel numbers as keys (e.g. '0' not 'ch0')."""
    notes = [NoteEvent(pitch=60, velocity=80, start_tick=0, duration_ticks=100, channel=0, channel_name="ch0")]
    remapped = apply_track_map(notes, {"0": "bass"})
    assert remapped[0].channel_name == "bass"


def test_apply_track_map_does_not_mutate_original() -> None:
    """apply_track_map returns new NoteEvent objects; originals are unchanged."""
    note = NoteEvent(pitch=60, velocity=80, start_tick=0, duration_ticks=100, channel=0, channel_name="ch0")
    apply_track_map([note], {"ch0": "bass"})
    assert note.channel_name == "ch0"


def test_parse_track_map_arg_valid() -> None:
    """parse_track_map_arg parses comma-separated KEY=VALUE pairs."""
    result = parse_track_map_arg("ch0=bass,ch1=piano,ch9=drums")
    assert result == {"ch0": "bass", "ch1": "piano", "ch9": "drums"}


def test_parse_track_map_arg_invalid_raises() -> None:
    """parse_track_map_arg raises ValueError for malformed entries."""
    with pytest.raises(ValueError, match="KEY=VALUE"):
        parse_track_map_arg("ch0=bass,nodivider")


def test_analyze_import_returns_string(tmp_path: pathlib.Path) -> None:
    """analyze_import produces a non-empty multi-line analysis string."""
    mid = tmp_path / "song.mid"
    _make_minimal_midi(mid)
    data = parse_midi_file(mid)
    analysis = analyze_import(data)
    assert "Harmonic" in analysis
    assert "Rhythmic" in analysis
    assert "Dynamic" in analysis


def test_analyze_import_empty_notes() -> None:
    """analyze_import handles empty note lists gracefully."""
    data = MuseImportData(
        source_path=pathlib.Path("/tmp/empty.mid"),
        format="midi",
        ticks_per_beat=480,
        tempo_bpm=120.0,
        notes=[],
        tracks=[],
        raw_meta={},
    )
    result = analyze_import(data)
    assert "no notes found" in result


def test_musicxml_part_name_becomes_track(tmp_path: pathlib.Path) -> None:
    """MusicXML <part-name> elements are used as channel_name values."""
    xml = tmp_path / "song.xml"
    _make_minimal_musicxml(xml)
    data = parse_musicxml_file(xml)
    assert "Piano" in data.tracks
    assert all(n.channel_name == "Piano" for n in data.notes if n.channel == 0)


def test_parse_musicxml_malformed_raises(tmp_path: pathlib.Path) -> None:
    """Malformed XML raises RuntimeError with a clear message."""
    bad = tmp_path / "bad.xml"
    bad.write_text("not xml at all <unclosed")
    with pytest.raises(RuntimeError, match="Cannot parse MusicXML file"):
        parse_musicxml_file(bad)


# ---------------------------------------------------------------------------
# _import_async integration tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_import_midi_creates_commit_with_note_data(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """_import_async creates a MuseCliCommit with correct message and copies the file."""
    _init_muse_repo(tmp_path)
    mid = tmp_path / "session.mid"
    _make_minimal_midi(mid)

    commit_id = await _import_async(
        file_path=mid,
        root=tmp_path,
        session=muse_cli_db_session,
        message="Import original session MIDI",
    )

    assert commit_id is not None
    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.message == "Import original session MIDI"

    # File was copied into muse-work/imports/
    dest = tmp_path / "muse-work" / "imports" / "session.mid"
    assert dest.exists()

    # Metadata JSON was written
    meta_path = tmp_path / "muse-work" / "imports" / "session.mid.meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["format"] == "midi"
    assert meta["note_count"] >= 1


@pytest.mark.anyio
async def test_import_default_message_is_import_filename(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When no --message is given the commit message defaults to 'Import <filename>'."""
    _init_muse_repo(tmp_path)
    mid = tmp_path / "groove.mid"
    _make_minimal_midi(mid)

    commit_id = await _import_async(
        file_path=mid,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert commit_id is not None
    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one()
    assert row.message == "Import groove.mid"


@pytest.mark.anyio
async def test_import_track_map_recorded_in_metadata(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--track-map is persisted in the .meta.json file."""
    _init_muse_repo(tmp_path)
    mid = tmp_path / "band.mid"
    _make_minimal_midi(mid)

    await _import_async(
        file_path=mid,
        root=tmp_path,
        session=muse_cli_db_session,
        track_map={"ch0": "bass", "ch1": "piano", "ch9": "drums"},
    )

    meta = json.loads(
        (tmp_path / "muse-work" / "imports" / "band.mid.meta.json").read_text()
    )
    assert meta["track_map"] == {"ch0": "bass", "ch1": "piano", "ch9": "drums"}


@pytest.mark.anyio
async def test_import_dry_run_no_commit_created(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--dry-run returns None and does not create a commit or copy files."""
    _init_muse_repo(tmp_path)
    mid = tmp_path / "check.mid"
    _make_minimal_midi(mid)

    result = await _import_async(
        file_path=mid,
        root=tmp_path,
        session=muse_cli_db_session,
        dry_run=True,
    )

    assert result is None

    # No file copied
    dest = tmp_path / "muse-work" / "imports" / "check.mid"
    assert not dest.exists()

    # No commit row in DB
    rows = await muse_cli_db_session.execute(select(MuseCliCommit))
    assert rows.scalars().all() == []


@pytest.mark.anyio
async def test_import_musicxml_creates_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """_import_async handles .musicxml files and creates a valid commit."""
    _init_muse_repo(tmp_path)
    xml = tmp_path / "score.musicxml"
    _make_minimal_musicxml(xml)

    commit_id = await _import_async(
        file_path=xml,
        root=tmp_path,
        session=muse_cli_db_session,
        message="Import MusicXML score",
    )

    assert commit_id is not None
    meta = json.loads(
        (tmp_path / "muse-work" / "imports" / "score.musicxml.meta.json").read_text()
    )
    assert meta["format"] == "musicxml"


@pytest.mark.anyio
async def test_import_analyze_runs_context_analysis(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--analyze prints harmonic, rhythmic, and dynamic analysis after import."""
    _init_muse_repo(tmp_path)
    mid = tmp_path / "song.mid"
    _make_minimal_midi(mid)

    await _import_async(
        file_path=mid,
        root=tmp_path,
        session=muse_cli_db_session,
        analyze=True,
    )

    captured = capsys.readouterr()
    assert "Harmonic" in captured.out
    assert "Rhythmic" in captured.out
    assert "Dynamic" in captured.out


@pytest.mark.anyio
async def test_import_missing_file_exits_user_error(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Importing a nonexistent file exits with USER_ERROR."""
    import typer

    _init_muse_repo(tmp_path)
    missing = tmp_path / "ghost.mid"

    with pytest.raises(typer.Exit) as exc_info:
        await _import_async(
            file_path=missing,
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_import_unsupported_extension_exits_user_error(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Importing an unsupported file extension exits with USER_ERROR."""
    import typer

    _init_muse_repo(tmp_path)
    bad = tmp_path / "song.mp3"
    bad.write_bytes(b"not midi")

    with pytest.raises(typer.Exit) as exc_info:
        await _import_async(
            file_path=bad,
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_import_section_recorded_in_metadata(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--section is persisted in the .meta.json file."""
    _init_muse_repo(tmp_path)
    mid = tmp_path / "intro.mid"
    _make_minimal_midi(mid)

    await _import_async(
        file_path=mid,
        root=tmp_path,
        session=muse_cli_db_session,
        section="verse",
    )

    meta = json.loads(
        (tmp_path / "muse-work" / "imports" / "intro.mid.meta.json").read_text()
    )
    assert meta["section"] == "verse"
