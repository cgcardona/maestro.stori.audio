"""Tests for the canonical Muse object store — ``maestro.muse_cli.object_store``.

This file is the authoritative test suite for the shared blob store.  Every
Muse command that reads or writes objects (``muse commit``, ``muse read-tree``,
``muse reset --hard``) must route through this module.  Tests here verify:

Unit tests (pure filesystem, no DB):
- test_object_path_uses_sharded_layout        — path is <sha2>/<sha62>
- test_object_path_shard_dir_is_first_two     — shard dir name is first 2 chars
- test_write_object_creates_shard_dir         — shard dir created on first write
- test_write_object_stores_content            — bytes are persisted correctly
- test_write_object_idempotent_returns_false  — second write returns False, file unchanged
- test_write_object_from_path_stores_content  — path-based write stores bytes correctly
- test_write_object_from_path_idempotent      — path-based write is idempotent
- test_read_object_returns_bytes              — returns stored content
- test_read_object_returns_none_when_missing  — returns None for absent object
- test_has_object_true_after_write            — True after write_object
- test_has_object_false_before_write          — False when absent
- test_restore_object_copies_to_dest          — file appears at dest
- test_restore_object_creates_parent_dirs     — dest parent dirs are created
- test_restore_object_returns_false_missing   — returns False when object absent

Regression tests (cross-command round-trips):
- test_same_layout_commit_then_read_tree      — objects written by commit are found by read-tree
- test_same_layout_commit_then_reset_hard     — objects written by commit are found by reset --hard
"""
from __future__ import annotations

import datetime
import json
import pathlib
import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.db.database import Base
from maestro.muse_cli import models as cli_models  # noqa: F401 — register tables
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot
from maestro.muse_cli.object_store import (
    has_object,
    object_path,
    read_object,
    restore_object,
    write_object,
    write_object_from_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all Muse CLI tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def repo_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def repo_root(tmp_path: pathlib.Path, repo_id: str) -> pathlib.Path:
    """Minimal Muse repository structure with repo.json and HEAD."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "HEAD").write_text("refs/heads/main")
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "refs" / "heads" / "main").write_text("")
    (muse_dir / "repo.json").write_text(json.dumps({"repo_id": repo_id}))
    return tmp_path


def _sha(seed: str, length: int = 64) -> str:
    """Build a deterministic fake SHA of exactly *length* hex chars."""
    return (seed * (length // len(seed) + 1))[:length]


# ---------------------------------------------------------------------------
# Unit tests — object_path layout
# ---------------------------------------------------------------------------


class TestObjectPath:

    def test_object_path_uses_sharded_layout(self, tmp_path: pathlib.Path) -> None:
        """object_path returns .muse/objects/<sha2>/<sha62> — the sharded layout."""
        root = tmp_path
        object_id = "ab" + "cd" * 31  # 64 hex chars
        result = object_path(root, object_id)
        expected = root / ".muse" / "objects" / "ab" / ("cd" * 31)
        assert result == expected

    def test_object_path_shard_dir_is_first_two_chars(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The shard directory name is exactly the first two hex characters."""
        object_id = "ff" + "00" * 31
        result = object_path(tmp_path, object_id)
        assert result.parent.name == "ff"

    def test_object_path_filename_is_remaining_62_chars(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The filename under the shard dir is the remaining 62 characters."""
        object_id = "1a" + "bc" * 31
        result = object_path(tmp_path, object_id)
        assert result.name == "bc" * 31
        assert len(result.name) == 62


# ---------------------------------------------------------------------------
# Unit tests — write_object (bytes)
# ---------------------------------------------------------------------------


class TestWriteObject:

    def test_write_object_creates_shard_dir(self, tmp_path: pathlib.Path) -> None:
        """write_object creates the shard subdirectory on first write."""
        (tmp_path / ".muse").mkdir()
        object_id = "ab" + "11" * 31
        write_object(tmp_path, object_id, b"MIDI data")
        shard_dir = tmp_path / ".muse" / "objects" / "ab"
        assert shard_dir.is_dir()

    def test_write_object_stores_content(self, tmp_path: pathlib.Path) -> None:
        """write_object persists the exact bytes at the sharded path."""
        (tmp_path / ".muse").mkdir()
        object_id = "cc" + "dd" * 31
        content = b"track: bass, tempo: 120bpm"
        write_object(tmp_path, object_id, content)
        dest = object_path(tmp_path, object_id)
        assert dest.read_bytes() == content

    def test_write_object_returns_true_on_new_write(
        self, tmp_path: pathlib.Path
    ) -> None:
        """write_object returns True when the object is newly stored."""
        (tmp_path / ".muse").mkdir()
        object_id = "ee" + "ff" * 31
        result = write_object(tmp_path, object_id, b"new blob")
        assert result is True

    def test_write_object_idempotent_returns_false(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Second write for the same object_id returns False without changing the file."""
        (tmp_path / ".muse").mkdir()
        object_id = "11" + "22" * 31
        write_object(tmp_path, object_id, b"original content")
        dest = object_path(tmp_path, object_id)
        mtime_first = dest.stat().st_mtime

        result = write_object(tmp_path, object_id, b"different content")

        assert result is False
        assert dest.stat().st_mtime == mtime_first  # file not touched
        assert dest.read_bytes() == b"original content"  # original content preserved


# ---------------------------------------------------------------------------
# Unit tests — write_object_from_path (path-based write)
# ---------------------------------------------------------------------------


class TestWriteObjectFromPath:

    def test_write_object_from_path_stores_content(
        self, tmp_path: pathlib.Path
    ) -> None:
        """write_object_from_path copies the source file into the sharded store."""
        (tmp_path / ".muse").mkdir()
        object_id = "aa" + "bb" * 31
        src = tmp_path / "drums.mid"
        src.write_bytes(b"MIDI drums data")

        write_object_from_path(tmp_path, object_id, src)

        dest = object_path(tmp_path, object_id)
        assert dest.read_bytes() == b"MIDI drums data"

    def test_write_object_from_path_returns_true_on_new_write(
        self, tmp_path: pathlib.Path
    ) -> None:
        """write_object_from_path returns True when the object is newly stored."""
        (tmp_path / ".muse").mkdir()
        object_id = "33" + "44" * 31
        src = tmp_path / "keys.mid"
        src.write_bytes(b"piano riff")

        result = write_object_from_path(tmp_path, object_id, src)
        assert result is True

    def test_write_object_from_path_idempotent(self, tmp_path: pathlib.Path) -> None:
        """Second call with the same object_id returns False, file unchanged."""
        (tmp_path / ".muse").mkdir()
        object_id = "55" + "66" * 31
        src = tmp_path / "lead.mid"
        src.write_bytes(b"lead melody")

        write_object_from_path(tmp_path, object_id, src)
        dest = object_path(tmp_path, object_id)
        mtime_first = dest.stat().st_mtime

        result = write_object_from_path(tmp_path, object_id, src)
        assert result is False
        assert dest.stat().st_mtime == mtime_first


# ---------------------------------------------------------------------------
# Unit tests — read_object
# ---------------------------------------------------------------------------


class TestReadObject:

    def test_read_object_returns_bytes(self, tmp_path: pathlib.Path) -> None:
        """read_object returns the exact bytes that were written."""
        (tmp_path / ".muse").mkdir()
        object_id = "77" + "88" * 31
        content = b"chorus riff, key of C"
        write_object(tmp_path, object_id, content)

        result = read_object(tmp_path, object_id)
        assert result == content

    def test_read_object_returns_none_when_missing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """read_object returns None for an object not in the store."""
        (tmp_path / ".muse").mkdir()
        object_id = "99" + "aa" * 31
        result = read_object(tmp_path, object_id)
        assert result is None


# ---------------------------------------------------------------------------
# Unit tests — has_object
# ---------------------------------------------------------------------------


class TestHasObject:

    def test_has_object_false_before_write(self, tmp_path: pathlib.Path) -> None:
        """has_object returns False before any write."""
        (tmp_path / ".muse").mkdir()
        object_id = "bb" + "cc" * 31
        assert has_object(tmp_path, object_id) is False

    def test_has_object_true_after_write(self, tmp_path: pathlib.Path) -> None:
        """has_object returns True after write_object."""
        (tmp_path / ".muse").mkdir()
        object_id = "dd" + "ee" * 31
        write_object(tmp_path, object_id, b"pad chord")
        assert has_object(tmp_path, object_id) is True


# ---------------------------------------------------------------------------
# Unit tests — restore_object
# ---------------------------------------------------------------------------


class TestRestoreObject:

    def test_restore_object_copies_to_dest(self, tmp_path: pathlib.Path) -> None:
        """restore_object writes the stored blob to the given destination path."""
        (tmp_path / ".muse").mkdir()
        object_id = "12" + "34" * 31
        content = b"bridge melody, Bm"
        write_object(tmp_path, object_id, content)

        dest = tmp_path / "muse-work" / "bridge.mid"
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = restore_object(tmp_path, object_id, dest)

        assert result is True
        assert dest.read_bytes() == content

    def test_restore_object_creates_parent_dirs(self, tmp_path: pathlib.Path) -> None:
        """restore_object creates missing parent directories for the dest path."""
        (tmp_path / ".muse").mkdir()
        object_id = "56" + "78" * 31
        write_object(tmp_path, object_id, b"nested track")

        dest = tmp_path / "muse-work" / "tracks" / "strings" / "viola.mid"
        # Parent dirs do NOT exist yet — restore_object must create them.
        assert not dest.parent.exists()

        result = restore_object(tmp_path, object_id, dest)
        assert result is True
        assert dest.read_bytes() == b"nested track"

    def test_restore_object_returns_false_when_missing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """restore_object returns False cleanly when the object is absent."""
        (tmp_path / ".muse").mkdir()
        object_id = "90" + "ab" * 31
        dest = tmp_path / "muse-work" / "ghost.mid"
        dest.parent.mkdir(parents=True, exist_ok=True)

        result = restore_object(tmp_path, object_id, dest)
        assert result is False
        assert not dest.exists()


# ---------------------------------------------------------------------------
# Cross-command round-trip tests
#
# These are the regression tests the issue specifically calls for.  They wire
# together the real _commit_async / _read_tree_async / perform_reset cores
# against the shared object store to prove that objects written by one command
# are found by every other command.
# ---------------------------------------------------------------------------


async def _add_commit_row(
    session: AsyncSession,
    *,
    repo_id: str,
    manifest: dict[str, str],
    branch: str = "main",
    message: str = "test commit",
    parent_commit_id: str | None = None,
    committed_at: datetime.datetime | None = None,
) -> MuseCliCommit:
    """Insert a MuseCliCommit + MuseCliSnapshot row and return the commit."""
    snapshot_id = _sha(str(uuid.uuid4()).replace("-", ""))
    commit_id = _sha(str(uuid.uuid4()).replace("-", ""))

    for object_id in manifest.values():
        existing = await session.get(MuseCliObject, object_id)
        if existing is None:
            session.add(MuseCliObject(object_id=object_id, size_bytes=10))

    session.add(MuseCliSnapshot(snapshot_id=snapshot_id, manifest=manifest))
    await session.flush()

    ts = committed_at or datetime.datetime.now(datetime.timezone.utc)
    commit = MuseCliCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=parent_commit_id,
        snapshot_id=snapshot_id,
        message=message,
        author="",
        committed_at=ts,
    )
    session.add(commit)
    await session.flush()
    return commit


class TestCrossCommandRoundTrips:
    """Regression: objects from ``muse commit`` must be findable by all other commands."""

    @pytest.mark.anyio
    async def test_same_layout_commit_then_read_tree(
        self,
        async_session: AsyncSession,
        repo_root: pathlib.Path,
        repo_id: str,
    ) -> None:
        """Objects written via write_object_from_path (commit) are readable by read_tree.

        This is the primary regression for issue #177: flat-layout objects
        written by muse commit could not be found by muse read-tree which
        used the same module.  Both now use the sharded layout.
        """
        from maestro.muse_cli.commands.read_tree import _read_tree_async

        # Seed muse-work/ with a file.
        workdir = repo_root / "muse-work"
        workdir.mkdir()
        track_file = workdir / "track.mid"
        track_content = b"verse hook, 4/4, 120bpm"
        track_file.write_bytes(track_content)

        # Compute hash and store via the commit path.
        from maestro.muse_cli.snapshot import hash_file

        object_id = hash_file(track_file)
        write_object_from_path(repo_root, object_id, track_file)

        # Insert the snapshot + commit row.
        manifest = {"track.mid": object_id}
        commit = await _add_commit_row(
            async_session,
            repo_id=repo_id,
            manifest=manifest,
        )

        # Simulate a clean working tree before read-tree.
        track_file.unlink()
        assert not track_file.exists()

        # read-tree should restore the file from the object store.
        result = await _read_tree_async(
            snapshot_id=commit.snapshot_id,
            root=repo_root,
            session=async_session,
        )

        assert "track.mid" in result.files_written
        assert track_file.exists()
        assert track_file.read_bytes() == track_content

    @pytest.mark.anyio
    async def test_same_layout_commit_then_reset_hard(
        self,
        async_session: AsyncSession,
        repo_root: pathlib.Path,
        repo_id: str,
    ) -> None:
        """Objects written via write_object_from_path (commit) are readable by reset --hard.

        This is the primary regression for issue #177: muse reset --hard used a
        sharded layout but muse commit used a flat layout, so reset could never
        find the objects commit had stored.  Both now use the same sharded layout.
        """
        from maestro.services.muse_reset import ResetMode, perform_reset

        # v1 content — the snapshot we'll reset back to.
        object_id_v1 = "11" * 32
        content_v1 = b"intro riff, Em"
        write_object(repo_root, object_id_v1, content_v1)

        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        c1 = await _add_commit_row(
            async_session,
            repo_id=repo_id,
            manifest={"lead.mid": object_id_v1},
            committed_at=t0,
            message="v1",
        )

        # v2 content — the current HEAD we'll reset away from.
        object_id_v2 = "22" * 32
        content_v2 = b"chorus, C major"
        write_object(repo_root, object_id_v2, content_v2)

        c2 = await _add_commit_row(
            async_session,
            repo_id=repo_id,
            manifest={"lead.mid": object_id_v2},
            parent_commit_id=c1.commit_id,
            message="v2",
        )

        # Set branch HEAD to c2 and populate muse-work/ with v2 content.
        ref_path = repo_root / ".muse" / "refs" / "heads" / "main"
        ref_path.write_text(c2.commit_id)

        workdir = repo_root / "muse-work"
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "lead.mid").write_bytes(content_v2)

        # Hard reset to c1 — must find v1 object written above.
        result = await perform_reset(
            root=repo_root,
            session=async_session,
            ref=c1.commit_id,
            mode=ResetMode.HARD,
        )

        assert result.files_restored == 1
        assert result.target_commit_id == c1.commit_id
        assert (workdir / "lead.mid").read_bytes() == content_v1

    @pytest.mark.anyio
    async def test_commit_write_then_read_tree_write_produce_same_path(
        self,
        repo_root: pathlib.Path,
    ) -> None:
        """write_object and write_object_from_path both produce the same sharded path.

        Ensures neither write variant creates a layout inconsistency.
        """
        (repo_root / ".muse").mkdir(exist_ok=True)
        object_id = "ab" + "cd" * 31
        content = b"same object, two write paths"

        # Write via bytes API (as _commit_async used to).
        write_object(repo_root, object_id, content)
        p_bytes = object_path(repo_root, object_id)

        # Clear store.
        p_bytes.unlink()
        p_bytes.parent.rmdir()

        # Write via path API (as _commit_async now does).
        src = repo_root / "tmp_source.mid"
        src.write_bytes(content)
        write_object_from_path(repo_root, object_id, src)
        p_path = object_path(repo_root, object_id)

        assert p_bytes == p_path  # identical paths
        assert p_path.read_bytes() == content
