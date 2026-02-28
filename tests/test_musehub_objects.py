"""Tests for Muse Hub object endpoints — issue #209 (piano roll / MIDI parsing).

Covers:
- test_parse_midi_bytes_basic              — parser returns MidiParseResult shape
- test_parse_midi_bytes_note_data          — notes have correct fields
- test_parse_midi_bytes_empty_track        — empty MIDI file returns zero beats
- test_parse_midi_bytes_invalid_data       — bad bytes raise ValueError
- test_parse_midi_object_endpoint_404      — unknown object returns 404
- test_parse_midi_object_non_midi_404      — non-MIDI object returns 404
- test_piano_roll_pitch_to_name            — pitch_to_name helper correctness
"""
from __future__ import annotations

import io
import os
import struct
import tempfile

import mido
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubObject, MusehubRepo
from maestro.services.musehub_midi_parser import (
    MidiNote,
    MidiParseResult,
    MidiTrack,
    parse_midi_bytes,
    pitch_to_name,
)


# ---------------------------------------------------------------------------
# MIDI file builder helpers
# ---------------------------------------------------------------------------


def _make_simple_midi() -> bytes:
    """Return a minimal but valid SMF Type-0 MIDI file with one note-on/off."""
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(mido.Message("note_on", channel=0, note=60, velocity=80, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _make_multi_track_midi() -> bytes:
    """Return an SMF Type-1 file with two tracks."""
    mid = mido.MidiFile(type=1, ticks_per_beat=480)

    track0 = mido.MidiTrack()
    mid.tracks.append(track0)
    track0.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    track0.append(mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0))
    track0.append(mido.MetaMessage("track_name", name="Piano", time=0))
    track0.append(mido.MetaMessage("end_of_track", time=0))

    track1 = mido.MidiTrack()
    mid.tracks.append(track1)
    track1.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track1.append(mido.Message("note_on", channel=1, note=36, velocity=100, time=0))
    track1.append(mido.Message("note_off", channel=1, note=36, velocity=0, time=960))
    track1.append(mido.MetaMessage("end_of_track", time=0))

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Unit tests — musehub_midi_parser
# ---------------------------------------------------------------------------


def test_parse_midi_bytes_basic_shape() -> None:
    """parse_midi_bytes returns all required MidiParseResult keys."""
    result = parse_midi_bytes(_make_simple_midi())
    assert "tracks" in result
    assert "tempo_bpm" in result
    assert "time_signature" in result
    assert "total_beats" in result


def test_parse_midi_bytes_note_data() -> None:
    """Parsed note has correct pitch, velocity, and positive duration."""
    result = parse_midi_bytes(_make_simple_midi())
    tracks = result["tracks"]
    assert len(tracks) >= 1
    notes = tracks[0]["notes"]
    assert len(notes) == 1
    note = notes[0]
    assert note["pitch"] == 60
    assert note["velocity"] == 80
    assert note["duration_beats"] > 0
    assert note["start_beat"] == 0.0
    assert note["track_id"] == 0
    assert note["channel"] == 0


def test_parse_midi_bytes_tempo() -> None:
    """Default 500000 µs/beat = 120 BPM is parsed correctly."""
    result = parse_midi_bytes(_make_simple_midi())
    assert abs(result["tempo_bpm"] - 120.0) < 0.1


def test_parse_midi_bytes_time_signature() -> None:
    """Time signature from meta message is returned as 'N/D' string."""
    midi_bytes = _make_multi_track_midi()
    result = parse_midi_bytes(midi_bytes)
    assert result["time_signature"] == "3/4"


def test_parse_midi_bytes_multi_track() -> None:
    """Multi-track MIDI produces one MidiTrack entry per SMF track."""
    result = parse_midi_bytes(_make_multi_track_midi())
    assert len(result["tracks"]) == 2
    # Bass track should have its note
    bass_track = result["tracks"][1]
    assert bass_track["name"] == "Bass"
    assert len(bass_track["notes"]) == 1
    assert bass_track["notes"][0]["pitch"] == 36


def test_parse_midi_bytes_total_beats_positive() -> None:
    """total_beats is greater than zero when notes are present."""
    result = parse_midi_bytes(_make_simple_midi())
    assert result["total_beats"] > 0


def test_parse_midi_bytes_empty_track() -> None:
    """An SMF file with no notes returns zero total_beats."""
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("end_of_track", time=0))
    buf = io.BytesIO()
    mid.save(file=buf)
    result = parse_midi_bytes(buf.getvalue())
    assert result["total_beats"] == 0.0


def test_parse_midi_bytes_invalid_data_raises() -> None:
    """Garbage bytes raise ValueError with a descriptive message."""
    with pytest.raises(ValueError, match="Could not parse MIDI"):
        parse_midi_bytes(b"\x00\x01\x02\x03garbage")


def test_parse_midi_bytes_notes_sorted_by_start_beat() -> None:
    """Notes within each track are sorted by start_beat ascending."""
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    # Two notes at different beat positions
    track.append(mido.Message("note_on", channel=0, note=64, velocity=70, time=0))
    track.append(mido.Message("note_off", channel=0, note=64, velocity=0, time=240))
    track.append(mido.Message("note_on", channel=0, note=60, velocity=80, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    buf = io.BytesIO()
    mid.save(file=buf)
    result = parse_midi_bytes(buf.getvalue())
    notes = result["tracks"][0]["notes"]
    beats = [n["start_beat"] for n in notes]
    assert beats == sorted(beats)


def test_pitch_to_name_middle_c() -> None:
    """MIDI pitch 60 is middle C (C4)."""
    assert pitch_to_name(60) == "C4"


def test_pitch_to_name_a4() -> None:
    """MIDI pitch 69 is A4 (concert A)."""
    assert pitch_to_name(69) == "A4"


def test_pitch_to_name_a0() -> None:
    """MIDI pitch 21 is A0 (lowest piano key)."""
    assert pitch_to_name(21) == "A0"


# ---------------------------------------------------------------------------
# HTTP endpoint tests — parse-midi route
# ---------------------------------------------------------------------------


_OBJ_COUNTER = 0


async def _seed_repo_and_obj(
    db_session: AsyncSession,
    disk_path: str = "/nonexistent/track.mid",
    path: str = "tracks/bass.mid",
) -> tuple[str, str]:
    """Seed a repo and object; return (repo_id, object_id)."""
    global _OBJ_COUNTER
    _OBJ_COUNTER += 1
    object_id = f"sha256:test{_OBJ_COUNTER:04d}"

    repo = MusehubRepo(
        name=f"midi-test-{_OBJ_COUNTER}",
        owner="testuser",
        slug=f"midi-test-{_OBJ_COUNTER}",
        visibility="public",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)

    obj = MusehubObject(
        object_id=object_id,
        repo_id=str(repo.repo_id),
        path=path,
        size_bytes=0,
        disk_path=disk_path,
    )
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return str(repo.repo_id), str(obj.object_id)


@pytest.mark.anyio
async def test_parse_midi_object_endpoint_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /parse-midi for an unknown repo_id returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/unknown-repo/objects/unknown-obj/parse-midi",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_parse_midi_object_endpoint_unknown_object_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /parse-midi for a missing object_id returns 404."""
    repo_id, _ = await _seed_repo_and_obj(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/objects/missing-object-id/parse-midi",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_parse_midi_object_non_midi_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /parse-midi for a non-MIDI object (e.g. .mp3) returns 404."""
    repo_id, obj_id = await _seed_repo_and_obj(
        db_session, path="tracks/audio.mp3"
    )
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/objects/{obj_id}/parse-midi",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert "MIDI" in response.json()["detail"]


@pytest.mark.anyio
async def test_parse_midi_object_missing_disk_file_410(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /parse-midi when disk file is gone returns 410."""
    repo_id, obj_id = await _seed_repo_and_obj(
        db_session, disk_path="/nonexistent/missing.mid", path="missing.mid"
    )
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/objects/{obj_id}/parse-midi",
        headers=auth_headers,
    )
    assert response.status_code == 410


@pytest.mark.anyio
async def test_parse_midi_object_returns_valid_result(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /parse-midi for a valid MIDI file returns MidiParseResult JSON."""
    midi_data = _make_simple_midi()
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as fh:
        fh.write(midi_data)
        tmp_path = fh.name

    try:
        repo_id, obj_id = await _seed_repo_and_obj(
            db_session, disk_path=tmp_path, path="track.mid"
        )
        response = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/objects/{obj_id}/parse-midi",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert "tracks" in body
        assert "tempo_bpm" in body
        assert "time_signature" in body
        assert "total_beats" in body
        assert body["total_beats"] > 0
        tracks = body["tracks"]
        assert isinstance(tracks, list)
        assert len(tracks) >= 1
        notes = tracks[0]["notes"]
        assert isinstance(notes, list)
        assert len(notes) >= 1
        note = notes[0]
        assert "pitch" in note
        assert "start_beat" in note
        assert "duration_beats" in note
        assert "velocity" in note
        assert "track_id" in note
        assert "channel" in note
    finally:
        os.unlink(tmp_path)
