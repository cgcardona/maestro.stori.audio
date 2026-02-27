"""Unit tests for maestro.muse_cli.snapshot.

All tests are pure — no database, no network, no Typer runner.
They verify the deterministic hash derivation contract documented in
snapshot.py's module docstring.
"""
from __future__ import annotations

import hashlib
import pathlib

import pytest

from maestro.muse_cli.snapshot import (
    build_snapshot_manifest,
    compute_commit_id,
    compute_snapshot_id,
    hash_file,
    walk_workdir,
)


# ---------------------------------------------------------------------------
# hash_file
# ---------------------------------------------------------------------------


def test_hash_file_known_digest(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    expected = hashlib.sha256(b"hello").hexdigest()
    assert hash_file(f) == expected


def test_hash_file_empty_file(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "empty.mid"
    f.write_bytes(b"")
    assert hash_file(f) == hashlib.sha256(b"").hexdigest()


def test_hash_file_different_content_different_digest(tmp_path: pathlib.Path) -> None:
    a = tmp_path / "a.mid"
    b = tmp_path / "b.mid"
    a.write_bytes(b"MIDI-A")
    b.write_bytes(b"MIDI-B")
    assert hash_file(a) != hash_file(b)


# ---------------------------------------------------------------------------
# walk_workdir / build_snapshot_manifest
# ---------------------------------------------------------------------------


def test_walk_workdir_returns_relative_posix_paths(tmp_path: pathlib.Path) -> None:
    (tmp_path / "bass.mid").write_bytes(b"bass")
    (tmp_path / "drums.mp3").write_bytes(b"drums")
    result = walk_workdir(tmp_path)
    assert "bass.mid" in result
    assert "drums.mp3" in result


def test_walk_workdir_excludes_hidden_files(tmp_path: pathlib.Path) -> None:
    (tmp_path / "track.mid").write_bytes(b"data")
    (tmp_path / ".DS_Store").write_bytes(b"mac junk")
    result = walk_workdir(tmp_path)
    assert ".DS_Store" not in result
    assert "track.mid" in result


def test_walk_workdir_recurses_into_subdirectories(tmp_path: pathlib.Path) -> None:
    sub = tmp_path / "loops"
    sub.mkdir()
    (sub / "beat.mid").write_bytes(b"beat")
    result = walk_workdir(tmp_path)
    assert "loops/beat.mid" in result


def test_walk_workdir_empty_directory(tmp_path: pathlib.Path) -> None:
    result = walk_workdir(tmp_path)
    assert result == {}


def test_build_snapshot_manifest_same_as_walk_workdir(tmp_path: pathlib.Path) -> None:
    (tmp_path / "x.mid").write_bytes(b"x")
    assert build_snapshot_manifest(tmp_path) == walk_workdir(tmp_path)


# ---------------------------------------------------------------------------
# compute_snapshot_id
# ---------------------------------------------------------------------------


def test_snapshot_id_is_deterministic(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.mid").write_bytes(b"A")
    m1 = walk_workdir(tmp_path)
    m2 = walk_workdir(tmp_path)
    assert compute_snapshot_id(m1) == compute_snapshot_id(m2)


def test_snapshot_id_changes_when_content_changes(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "a.mid"
    f.write_bytes(b"original")
    snap1 = compute_snapshot_id(walk_workdir(tmp_path))
    f.write_bytes(b"modified")
    snap2 = compute_snapshot_id(walk_workdir(tmp_path))
    assert snap1 != snap2


def test_snapshot_id_is_order_independent() -> None:
    """snapshot_id must not depend on dict insertion order."""
    m1 = {"b.mid": "bbb", "a.mid": "aaa"}
    m2 = {"a.mid": "aaa", "b.mid": "bbb"}
    assert compute_snapshot_id(m1) == compute_snapshot_id(m2)


def test_snapshot_id_is_sha256_hex(tmp_path: pathlib.Path) -> None:
    (tmp_path / "f.mid").write_bytes(b"data")
    sid = compute_snapshot_id(walk_workdir(tmp_path))
    assert len(sid) == 64
    assert all(c in "0123456789abcdef" for c in sid)


# ---------------------------------------------------------------------------
# compute_commit_id
# ---------------------------------------------------------------------------


def test_commit_id_is_deterministic() -> None:
    cid1 = compute_commit_id([], "snap1", "first commit", "2026-01-01T00:00:00+00:00")
    cid2 = compute_commit_id([], "snap1", "first commit", "2026-01-01T00:00:00+00:00")
    assert cid1 == cid2


def test_commit_id_changes_with_different_message() -> None:
    cid1 = compute_commit_id([], "snap1", "take 1", "2026-01-01T00:00:00+00:00")
    cid2 = compute_commit_id([], "snap1", "take 2", "2026-01-01T00:00:00+00:00")
    assert cid1 != cid2


def test_commit_id_changes_with_different_snapshot() -> None:
    cid1 = compute_commit_id([], "snap-A", "msg", "2026-01-01T00:00:00+00:00")
    cid2 = compute_commit_id([], "snap-B", "msg", "2026-01-01T00:00:00+00:00")
    assert cid1 != cid2


def test_commit_id_parent_order_does_not_matter() -> None:
    """commit_id must be stable regardless of parent_ids list order."""
    cid1 = compute_commit_id(["p1", "p2"], "snap", "msg", "2026-01-01T00:00:00+00:00")
    cid2 = compute_commit_id(["p2", "p1"], "snap", "msg", "2026-01-01T00:00:00+00:00")
    assert cid1 == cid2


def test_commit_id_is_sha256_hex() -> None:
    cid = compute_commit_id([], "snap", "msg", "2026-01-01T00:00:00+00:00")
    assert len(cid) == 64
    assert all(c in "0123456789abcdef" for c in cid)


@pytest.mark.parametrize(
    "parent_ids,snapshot_id,message,ts",
    [
        ([], "abc", "boom bap demo take 1", "2026-02-01T12:00:00+00:00"),
        (["p1"], "def", "ambient take 3", "2026-02-02T08:00:00+00:00"),
        (["p1", "p2"], "ghi", "merge feature/drums", "2026-02-27T00:00:00+00:00"),
    ],
)
def test_commit_id_parametrized_deterministic(
    parent_ids: list[str], snapshot_id: str, message: str, ts: str
) -> None:
    assert compute_commit_id(parent_ids, snapshot_id, message, ts) == compute_commit_id(
        parent_ids, snapshot_id, message, ts
    )
