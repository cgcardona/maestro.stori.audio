"""Tests for ``muse tempo``.

All async tests call the async core functions directly with an in-memory
SQLite session and a ``tmp_path`` repo root — no real Postgres or running
process required.  Commits are seeded via ``_commit_async`` so tempo and
commit commands are tested as an integrated pair.
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
from maestro.muse_cli.commands.tempo import (
    _tempo_history_async,
    _tempo_read_async,
    _tempo_set_async,
)
from maestro.muse_cli.db import set_commit_tempo_bpm
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_tempo import (
    MuseTempoHistoryEntry,
    MuseTempoResult,
    build_tempo_history,
    detect_all_tempos_from_midi,
    extract_bpm_from_midi,
)


# ---------------------------------------------------------------------------
# Repo + workdir helpers (mirrors test_log.py pattern)
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Initialise a minimal .muse/ repo structure for tests."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    for name, content in files.items():
        (workdir / name).write_bytes(content)


async def _make_commit(
    root: pathlib.Path,
    session: AsyncSession,
    message: str,
    file_seed: int = 0,
) -> str:
    _write_workdir(root, {f"track_{file_seed}.mid": f"MIDI-{file_seed}".encode()})
    return await _commit_async(message=message, root=root, session=session)


def _build_midi_with_tempo(uspb: int) -> bytes:
    """Return a minimal MIDI file byte string with one Set Tempo event.

    The tempo event is embedded in a Type-0 MIDI file header followed by
    a single track containing only the FF 51 03 event.
    """
    # Set Tempo event: delta_time(0) + FF 51 03 + 3-byte big-endian uspb
    tempo_bytes = bytes([(uspb >> 16) & 0xFF, (uspb >> 8) & 0xFF, uspb & 0xFF])
    track_event = b"\x00\xFF\x51\x03" + tempo_bytes + b"\x00\xFF\x2F\x00"  # + end-of-track

    # MThd header: type=0, ntrks=1, division=480
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
    # MTrk chunk
    track = b"MTrk" + struct.pack(">I", len(track_event)) + track_event
    return header + track


# ---------------------------------------------------------------------------
# Unit tests: extract_bpm_from_midi
# ---------------------------------------------------------------------------


def test_extract_bpm_from_midi_returns_correct_bpm() -> None:
    """120 BPM = 500000 µs/beat → extract_bpm_from_midi returns 120.0."""
    midi_data = _build_midi_with_tempo(500_000)
    bpm = extract_bpm_from_midi(midi_data)
    assert bpm is not None
    assert abs(bpm - 120.0) < 0.1


def test_extract_bpm_from_midi_140_bpm() -> None:
    """140 BPM = 428571 µs/beat."""
    uspb = int(60_000_000 / 140)
    midi_data = _build_midi_with_tempo(uspb)
    bpm = extract_bpm_from_midi(midi_data)
    assert bpm is not None
    assert abs(bpm - 140.0) < 0.5


def test_extract_bpm_from_midi_no_header_returns_none() -> None:
    """Non-MIDI bytes return None."""
    assert extract_bpm_from_midi(b"not midi data") is None


def test_extract_bpm_from_midi_no_tempo_event_returns_none() -> None:
    """A valid MIDI file with no Set Tempo event returns None."""
    # Just the MThd header with an empty track — no tempo event
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
    eot = b"\x00\xFF\x2F\x00"
    track = b"MTrk" + struct.pack(">I", len(eot)) + eot
    assert extract_bpm_from_midi(header + track) is None


def test_extract_bpm_from_midi_empty_bytes_returns_none() -> None:
    """Empty bytes → None (no crash)."""
    assert extract_bpm_from_midi(b"") is None


def test_detect_all_tempos_returns_multiple_events() -> None:
    """A file with two tempo events returns both BPMs."""
    def _tempo_event(uspb: int) -> bytes:
        b3 = bytes([(uspb >> 16) & 0xFF, (uspb >> 8) & 0xFF, uspb & 0xFF])
        return b"\x00\xFF\x51\x03" + b3

    events = _tempo_event(500_000) + _tempo_event(428_571) + b"\x00\xFF\x2F\x00"
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
    track = b"MTrk" + struct.pack(">I", len(events)) + events
    tempos = detect_all_tempos_from_midi(header + track)
    assert len(tempos) == 2
    assert abs(tempos[0] - 120.0) < 0.1
    assert abs(tempos[1] - 140.0) < 0.5


# ---------------------------------------------------------------------------
# Integration tests: _tempo_read_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tempo_read_no_annotation_no_midi_shows_dash(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reading tempo on a commit with no annotation shows '--'."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, "take 1")

    capsys.readouterr()
    result = await _tempo_read_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        as_json=False,
    )
    out = capsys.readouterr().out
    assert result.tempo_bpm is None
    assert result.detected_bpm is None
    assert "--" in out


@pytest.mark.anyio
async def test_tempo_read_shows_annotated_bpm(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After --set 128, reading tempo shows 128.0 BPM (annotated)."""
    _init_muse_repo(tmp_path)
    commit_id = await _make_commit(tmp_path, muse_cli_db_session, "boom bap take 1")
    await set_commit_tempo_bpm(muse_cli_db_session, commit_id, 128.0)

    capsys.readouterr()
    result = await _tempo_read_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        as_json=False,
    )
    out = capsys.readouterr().out
    assert result.tempo_bpm == 128.0
    assert "128.0" in out
    assert "annotated" in out


@pytest.mark.anyio
async def test_tempo_read_midi_detection(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A MIDI file with a Set Tempo event in muse-work/ is auto-detected."""
    _init_muse_repo(tmp_path)
    midi_data = _build_midi_with_tempo(500_000)  # 120 BPM
    _write_workdir(tmp_path, {"groove.mid": midi_data})
    await _commit_async(message="groove", root=tmp_path, session=muse_cli_db_session)

    capsys.readouterr()
    result = await _tempo_read_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        as_json=False,
    )
    out = capsys.readouterr().out
    assert result.detected_bpm is not None
    assert abs(result.detected_bpm - 120.0) < 0.5
    assert "120" in out
    assert "detected" in out


@pytest.mark.anyio
async def test_tempo_read_json_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` flag produces valid JSON with all expected keys."""
    _init_muse_repo(tmp_path)
    commit_id = await _make_commit(tmp_path, muse_cli_db_session, "samba")
    await set_commit_tempo_bpm(muse_cli_db_session, commit_id, 100.0)

    capsys.readouterr()
    await _tempo_read_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        as_json=True,
    )
    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert data["tempo_bpm"] == 100.0
    assert "commit_id" in data
    assert "effective_bpm" in data


@pytest.mark.anyio
async def test_tempo_read_abbreviated_commit_ref(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An abbreviated commit SHA resolves to the correct commit."""
    _init_muse_repo(tmp_path)
    commit_id = await _make_commit(tmp_path, muse_cli_db_session, "reggae")
    await set_commit_tempo_bpm(muse_cli_db_session, commit_id, 76.0)

    capsys.readouterr()
    result = await _tempo_read_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=commit_id[:8],
        as_json=False,
    )
    assert result.commit_id == commit_id
    assert result.tempo_bpm == 76.0


@pytest.mark.anyio
async def test_tempo_read_invalid_ref_exits_user_error(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """An unknown commit ref exits with USER_ERROR."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, "bossa")

    with pytest.raises(typer.Exit) as exc_info:
        await _tempo_read_async(
            root=tmp_path,
            session=muse_cli_db_session,
            commit_ref="deadbeef",
            as_json=False,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Integration tests: _tempo_set_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tempo_set_stores_bpm_in_metadata(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--set 128`` writes tempo_bpm into commit.commit_metadata."""
    _init_muse_repo(tmp_path)
    commit_id = await _make_commit(tmp_path, muse_cli_db_session, "hip hop beat")

    capsys.readouterr()
    await _tempo_set_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        bpm=128.0,
    )
    out = capsys.readouterr().out
    assert "128.0" in out

    # Verify it was actually stored
    result = await _tempo_read_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=commit_id,
        as_json=False,
    )
    assert result.tempo_bpm == 128.0


@pytest.mark.anyio
async def test_tempo_set_preserves_existing_metadata(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Setting tempo does not clobber other metadata keys."""
    from maestro.muse_cli.models import MuseCliCommit

    _init_muse_repo(tmp_path)
    commit_id = await _make_commit(tmp_path, muse_cli_db_session, "funk")

    # Pre-load some other metadata key
    commit = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert commit is not None
    commit.commit_metadata = {"some_other_key": "value"}
    muse_cli_db_session.add(commit)
    await muse_cli_db_session.flush()

    await _tempo_set_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        bpm=95.0,
    )

    updated = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert updated is not None
    assert updated.commit_metadata is not None
    assert updated.commit_metadata["tempo_bpm"] == 95.0
    assert updated.commit_metadata["some_other_key"] == "value"


# ---------------------------------------------------------------------------
# Integration tests: _tempo_history_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tempo_history_shows_all_commits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--history`` shows one row per commit in the chain."""
    _init_muse_repo(tmp_path)
    for i in range(3):
        await _make_commit(tmp_path, muse_cli_db_session, f"take {i + 1}", file_seed=i)

    capsys.readouterr()
    history = await _tempo_history_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        as_json=False,
    )
    assert len(history) == 3
    out = capsys.readouterr().out
    assert "take 1" in out
    assert "take 2" in out
    assert "take 3" in out


@pytest.mark.anyio
async def test_tempo_history_delta_computed_correctly(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Delta BPM between annotated commits is computed correctly."""
    _init_muse_repo(tmp_path)
    c1 = await _make_commit(tmp_path, muse_cli_db_session, "slow", file_seed=0)
    c2 = await _make_commit(tmp_path, muse_cli_db_session, "fast", file_seed=1)

    await set_commit_tempo_bpm(muse_cli_db_session, c1, 80.0)
    await set_commit_tempo_bpm(muse_cli_db_session, c2, 140.0)

    history = await _tempo_history_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        as_json=False,
    )
    # Newest-first: fast (c2) then slow (c1)
    assert history[0].commit_id == c2
    assert history[0].effective_bpm == 140.0
    assert history[0].delta_bpm == pytest.approx(60.0)

    assert history[1].commit_id == c1
    assert history[1].effective_bpm == 80.0
    assert history[1].delta_bpm is None  # oldest — no ancestor


@pytest.mark.anyio
async def test_tempo_history_json_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--history --json`` produces valid JSON list."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, "jazz")

    capsys.readouterr()
    await _tempo_history_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_ref=None,
        as_json=True,
    )
    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert isinstance(data, list)
    assert len(data) == 1
    assert "commit_id" in data[0]
    assert "effective_bpm" in data[0]


# ---------------------------------------------------------------------------
# Unit tests: build_tempo_history
# ---------------------------------------------------------------------------


def test_build_tempo_history_oldest_has_no_delta() -> None:
    """The oldest commit in the chain has delta_bpm = None."""
    from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
    import datetime

    def _fake_commit(cid: str, parent: str | None, bpm: float | None) -> MuseCliCommit:
        c = MuseCliCommit(
            commit_id=cid,
            repo_id="r",
            branch="main",
            parent_commit_id=parent,
            snapshot_id="s",
            message=f"take {cid[:4]}",
            author="test",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        c.commit_metadata = {"tempo_bpm": bpm} if bpm is not None else None
        return c

    # Newest first: c3 → c2 → c1
    commits = [
        _fake_commit("ccc", "bbb", 140.0),
        _fake_commit("bbb", "aaa", 120.0),
        _fake_commit("aaa", None, 80.0),
    ]
    history = build_tempo_history(commits)
    # Still newest-first after build_tempo_history
    assert history[0].commit_id == "ccc"
    assert history[0].delta_bpm == pytest.approx(20.0)
    assert history[1].commit_id == "bbb"
    assert history[1].delta_bpm == pytest.approx(40.0)
    assert history[2].commit_id == "aaa"
    assert history[2].delta_bpm is None
