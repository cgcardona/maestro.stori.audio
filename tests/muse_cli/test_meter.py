"""Tests for ``muse meter`` — time-signature read/set/detect/history/polyrhythm.

All async tests use the ``muse_cli_db_session`` fixture (in-memory SQLite).
Pure-logic tests (MIDI parsing, validation) are synchronous.
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
from maestro.muse_cli.commands.meter import (
    MuseMeterHistoryEntry,
    MuseMeterReadResult,
    MusePolyrhythmResult,
    _meter_history_async,
    _meter_polyrhythm_async,
    _meter_read_async,
    _meter_set_async,
    detect_midi_time_signature,
    scan_workdir_for_time_signatures,
    validate_time_signature,
)
from maestro.muse_cli.errors import ExitCode


# ──────────────────────────────────────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────────────────────────────────────


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _populate_workdir(root: pathlib.Path, files: dict[str, bytes] | None = None) -> None:
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-DATA"}
    for name, content in files.items():
        (workdir / name).write_bytes(content)


def _make_midi_with_time_sig(numerator: int, denominator_exp: int) -> bytes:
    """Build a minimal valid MIDI file containing a time-signature meta event."""
    # Time-signature meta event: FF 58 04 nn dd cc bb
    time_sig_event = bytes([
        0x00,                           # delta time (0)
        0xFF, 0x58, 0x04,              # meta type, length
        numerator, denominator_exp,    # numerator, denominator exponent
        0x18, 0x08,                    # clocks/tick, 32nds/quarter
    ])
    # End-of-track event
    eot = bytes([0x00, 0xFF, 0x2F, 0x00])

    track_data = time_sig_event + eot
    track_length = len(track_data)

    # MThd header: type=1, ntracks=1, division=480
    header = b"MThd" + struct.pack(">I", 6) + struct.pack(">HHH", 1, 1, 480)
    # MTrk chunk
    track = b"MTrk" + struct.pack(">I", track_length) + track_data
    return header + track


# ──────────────────────────────────────────────────────────────────────────────
# validate_time_signature — pure logic
# ──────────────────────────────────────────────────────────────────────────────


def test_validate_time_signature_accepts_4_4() -> None:
    assert validate_time_signature("4/4") == "4/4"


def test_validate_time_signature_accepts_7_8() -> None:
    assert validate_time_signature("7/8") == "7/8"


def test_validate_time_signature_accepts_5_4() -> None:
    assert validate_time_signature("5/4") == "5/4"


def test_validate_time_signature_accepts_3_4() -> None:
    assert validate_time_signature("3/4") == "3/4"


def test_validate_time_signature_accepts_12_8() -> None:
    assert validate_time_signature("12/8") == "12/8"


def test_validate_time_signature_strips_whitespace() -> None:
    assert validate_time_signature("  4/4  ") == "4/4"


def test_validate_time_signature_rejects_non_power_of_two_denominator() -> None:
    with pytest.raises(ValueError, match="power of 2"):
        validate_time_signature("4/3")


def test_validate_time_signature_rejects_zero_numerator() -> None:
    with pytest.raises(ValueError, match="[Nn]umerator"):
        validate_time_signature("0/4")


def test_validate_time_signature_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        validate_time_signature("four-four")


def test_validate_time_signature_rejects_missing_slash() -> None:
    with pytest.raises(ValueError):
        validate_time_signature("44")


# ──────────────────────────────────────────────────────────────────────────────
# detect_midi_time_signature — MIDI parsing
# ──────────────────────────────────────────────────────────────────────────────


def test_detect_midi_time_signature_4_4() -> None:
    midi = _make_midi_with_time_sig(numerator=4, denominator_exp=2)  # 2^2=4
    assert detect_midi_time_signature(midi) == "4/4"


def test_detect_midi_time_signature_3_4() -> None:
    midi = _make_midi_with_time_sig(numerator=3, denominator_exp=2)
    assert detect_midi_time_signature(midi) == "3/4"


def test_detect_midi_time_signature_7_8() -> None:
    midi = _make_midi_with_time_sig(numerator=7, denominator_exp=3)  # 2^3=8
    assert detect_midi_time_signature(midi) == "7/8"


def test_detect_midi_time_signature_returns_none_for_empty_bytes() -> None:
    assert detect_midi_time_signature(b"") is None


def test_detect_midi_time_signature_returns_none_for_no_event() -> None:
    # Random bytes with no FF 58 sequence
    assert detect_midi_time_signature(b"\x00\x90\x3C\x7F\x00\x80\x3C\x00") is None


def test_detect_midi_time_signature_12_8() -> None:
    midi = _make_midi_with_time_sig(numerator=12, denominator_exp=3)  # 2^3=8
    assert detect_midi_time_signature(midi) == "12/8"


# ──────────────────────────────────────────────────────────────────────────────
# scan_workdir_for_time_signatures
# ──────────────────────────────────────────────────────────────────────────────


def test_scan_workdir_finds_time_signature_in_midi(tmp_path: pathlib.Path) -> None:
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "beat.mid").write_bytes(_make_midi_with_time_sig(4, 2))  # 4/4

    sigs = scan_workdir_for_time_signatures(workdir)
    assert sigs == {"beat.mid": "4/4"}


def test_scan_workdir_returns_question_mark_for_unknown(tmp_path: pathlib.Path) -> None:
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "no-sig.mid").write_bytes(b"\x00\x90\x3C\x7F")

    sigs = scan_workdir_for_time_signatures(workdir)
    assert sigs == {"no-sig.mid": "?"}


def test_scan_workdir_returns_empty_for_missing_workdir(tmp_path: pathlib.Path) -> None:
    sigs = scan_workdir_for_time_signatures(tmp_path / "muse-work")
    assert sigs == {}


def test_scan_workdir_ignores_non_midi_files(tmp_path: pathlib.Path) -> None:
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "render.mp3").write_bytes(b"MP3-DATA")
    (workdir / "beat.mid").write_bytes(_make_midi_with_time_sig(3, 2))  # 3/4

    sigs = scan_workdir_for_time_signatures(workdir)
    assert "render.mp3" not in sigs
    assert "beat.mid" in sigs


def test_scan_workdir_multiple_midi_files(tmp_path: pathlib.Path) -> None:
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "drums.mid").write_bytes(_make_midi_with_time_sig(4, 2))   # 4/4
    (workdir / "bass.mid").write_bytes(_make_midi_with_time_sig(4, 2))    # 4/4

    sigs = scan_workdir_for_time_signatures(workdir)
    assert len(sigs) == 2
    assert all(s == "4/4" for s in sigs.values())


# ──────────────────────────────────────────────────────────────────────────────
# _meter_read_async / _meter_set_async — DB integration
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_meter_read_returns_none_when_not_set(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Reading meter on an uncommitted repo raises USER_ERROR (no HEAD)."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="bare commit", root=tmp_path, session=muse_cli_db_session
    )

    result = await _meter_read_async(
        session=muse_cli_db_session, root=tmp_path, commit_ref=None
    )
    assert isinstance(result, MuseMeterReadResult)
    assert result.commit_id == commit_id
    assert result.time_signature is None


@pytest.mark.anyio
async def test_meter_set_and_read_roundtrip(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Setting a meter annotation and reading it back returns the same value."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="jazz take", root=tmp_path, session=muse_cli_db_session
    )

    await _meter_set_async(
        session=muse_cli_db_session,
        root=tmp_path,
        commit_ref=None,
        time_signature="7/8",
    )
    await muse_cli_db_session.flush()

    result = await _meter_read_async(
        session=muse_cli_db_session, root=tmp_path, commit_ref=None
    )
    assert result.commit_id == commit_id
    assert result.time_signature == "7/8"


@pytest.mark.anyio
async def test_meter_set_by_abbreviated_commit_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--set works when an abbreviated commit ID is passed."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="boom bap", root=tmp_path, session=muse_cli_db_session
    )

    set_commit_id = await _meter_set_async(
        session=muse_cli_db_session,
        root=tmp_path,
        commit_ref=commit_id[:8],
        time_signature="4/4",
    )
    assert set_commit_id == commit_id


@pytest.mark.anyio
async def test_meter_read_no_commits_raises_exit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Reading meter when there are no commits raises typer.Exit(USER_ERROR)."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _meter_read_async(
            session=muse_cli_db_session, root=tmp_path, commit_ref=None
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_meter_set_unknown_commit_raises_exit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Setting meter on an unknown commit ref raises typer.Exit(USER_ERROR)."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)
    await _commit_async(message="init", root=tmp_path, session=muse_cli_db_session)

    with pytest.raises(typer.Exit) as exc_info:
        await _meter_set_async(
            session=muse_cli_db_session,
            root=tmp_path,
            commit_ref="deadbeef",
            time_signature="4/4",
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_meter_set_overwrites_previous_annotation(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Setting meter twice on the same commit overwrites the first annotation."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)
    await _commit_async(message="v1", root=tmp_path, session=muse_cli_db_session)

    await _meter_set_async(
        session=muse_cli_db_session, root=tmp_path, commit_ref=None, time_signature="4/4"
    )
    await muse_cli_db_session.flush()
    await _meter_set_async(
        session=muse_cli_db_session, root=tmp_path, commit_ref=None, time_signature="3/4"
    )
    await muse_cli_db_session.flush()

    result = await _meter_read_async(
        session=muse_cli_db_session, root=tmp_path, commit_ref=None
    )
    assert result.time_signature == "3/4"


# ──────────────────────────────────────────────────────────────────────────────
# _meter_history_async
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_meter_history_returns_empty_for_no_commits(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    _init_muse_repo(tmp_path)
    entries = await _meter_history_async(session=muse_cli_db_session, root=tmp_path)
    assert entries == []


@pytest.mark.anyio
async def test_meter_history_shows_annotated_and_unannotated_commits(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """History walks the full chain, returning None for unannotated commits."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"V1"})

    cid1 = await _commit_async(message="v1", root=tmp_path, session=muse_cli_db_session)
    await _meter_set_async(
        session=muse_cli_db_session,
        root=tmp_path,
        commit_ref=None,
        time_signature="4/4",
    )
    await muse_cli_db_session.flush()

    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"V2")
    cid2 = await _commit_async(message="v2", root=tmp_path, session=muse_cli_db_session)

    entries = await _meter_history_async(session=muse_cli_db_session, root=tmp_path)

    assert len(entries) == 2
    # Newest-first: v2 has no annotation, v1 has 4/4
    assert entries[0].commit_id == cid2
    assert entries[0].time_signature is None
    assert entries[1].commit_id == cid1
    assert entries[1].time_signature == "4/4"


@pytest.mark.anyio
async def test_meter_history_entries_are_muse_meter_history_entry(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)
    await _commit_async(message="only commit", root=tmp_path, session=muse_cli_db_session)

    entries = await _meter_history_async(session=muse_cli_db_session, root=tmp_path)
    assert len(entries) == 1
    assert isinstance(entries[0], MuseMeterHistoryEntry)
    assert entries[0].message == "only commit"


# ──────────────────────────────────────────────────────────────────────────────
# _meter_polyrhythm_async
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_meter_polyrhythm_no_polyrhythm_when_same_signature(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "drums.mid").write_bytes(_make_midi_with_time_sig(4, 2))
    (workdir / "bass.mid").write_bytes(_make_midi_with_time_sig(4, 2))
    await _commit_async(message="4/4 all", root=tmp_path, session=muse_cli_db_session)

    result = await _meter_polyrhythm_async(
        session=muse_cli_db_session, root=tmp_path, commit_ref=None
    )
    assert isinstance(result, MusePolyrhythmResult)
    assert result.is_polyrhythmic is False


@pytest.mark.anyio
async def test_meter_polyrhythm_detected_when_mixed_signatures(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "drums.mid").write_bytes(_make_midi_with_time_sig(4, 2))   # 4/4
    (workdir / "melody.mid").write_bytes(_make_midi_with_time_sig(7, 3))   # 7/8
    await _commit_async(message="polyrhythm", root=tmp_path, session=muse_cli_db_session)

    result = await _meter_polyrhythm_async(
        session=muse_cli_db_session, root=tmp_path, commit_ref=None
    )
    assert result.is_polyrhythmic is True
    assert "drums.mid" in result.signatures_by_file
    assert "melody.mid" in result.signatures_by_file
    assert result.signatures_by_file["drums.mid"] == "4/4"
    assert result.signatures_by_file["melody.mid"] == "7/8"


@pytest.mark.anyio
async def test_meter_polyrhythm_not_polyrhythmic_when_unknown_only(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Files with no time-sig meta events are '?' — not considered for polyrhythm."""
    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work"
    workdir.mkdir()
    (workdir / "a.mid").write_bytes(b"\x00\x90\x3C\x7F")
    (workdir / "b.mid").write_bytes(b"\x00\x90\x3C\x7F")
    await _commit_async(message="unknown sigs", root=tmp_path, session=muse_cli_db_session)

    result = await _meter_polyrhythm_async(
        session=muse_cli_db_session, root=tmp_path, commit_ref=None
    )
    assert result.is_polyrhythmic is False


# ──────────────────────────────────────────────────────────────────────────────
# CLI integration (Typer runner)
# ──────────────────────────────────────────────────────────────────────────────


def test_meter_no_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """Running muse meter outside a repo exits with REPO_NOT_FOUND (2)."""
    import os

    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()
    orig = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["meter"], catch_exceptions=False)
    finally:
        os.chdir(orig)
    assert result.exit_code == ExitCode.REPO_NOT_FOUND


def test_validate_time_signature_denominator_zero_raises() -> None:
    with pytest.raises(ValueError):
        validate_time_signature("4/0")
