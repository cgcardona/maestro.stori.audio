"""Tests for ``muse read-tree`` command.

Exercises ``_read_tree_async`` directly (async core) and indirectly
through the object store integration with ``_commit_async``.

All async tests use ``@pytest.mark.anyio``.
The ``muse_cli_db_session`` fixture is defined in tests/muse_cli/conftest.py.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.read_tree import (
    ReadTreeResult,
    _read_tree_async,
    _resolve_snapshot,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.object_store import has_object, object_path, write_object
from maestro.muse_cli.snapshot import compute_snapshot_id, hash_file


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal ``.muse/`` layout for testing."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _populate_workdir(
    root: pathlib.Path,
    files: dict[str, bytes] | None = None,
) -> None:
    """Create muse-work/ with the given files (default: two MIDI stubs)."""
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-BEAT", "lead.mid": b"MIDI-LEAD"}
    for name, content in files.items():
        target = workdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


# ---------------------------------------------------------------------------
# object_store unit tests
# ---------------------------------------------------------------------------


class TestObjectStore:
    """Unit tests for the local content-addressed object store."""

    def test_write_and_read_object(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        content = b"hello muse"
        oid = "a" * 64
        write_object(tmp_path, oid, content)
        dest = object_path(tmp_path, oid)
        assert dest.exists()
        assert dest.read_bytes() == content

    def test_write_object_idempotent(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        content = b"original"
        oid = "b" * 64
        assert write_object(tmp_path, oid, content) is True
        # Second write with different bytes — should be skipped (content-addressed).
        assert write_object(tmp_path, oid, b"different") is False
        dest = object_path(tmp_path, oid)
        assert dest.read_bytes() == content  # Original bytes preserved.

    def test_has_object_false_before_write(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        assert not has_object(tmp_path, "c" * 64)

    def test_has_object_true_after_write(self, tmp_path: pathlib.Path) -> None:
        _init_muse_repo(tmp_path)
        oid = "d" * 64
        write_object(tmp_path, oid, b"data")
        assert has_object(tmp_path, oid)


# ---------------------------------------------------------------------------
# commit stores objects in local store (regression)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_writes_objects_to_local_store(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """muse commit must persist file bytes to .muse/objects/ so read-tree works.

    Regression: before this feature, commit only wrote object metadata to
    the DB without persisting bytes on disk.
    """
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"BEAT-BYTES", "lead.mid": b"LEAD-BYTES"})

    await _commit_async(
        message="store objects test",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    workdir = tmp_path / "muse-work"
    for filename in ["beat.mid", "lead.mid"]:
        file_path = workdir / filename
        oid = hash_file(file_path)
        assert has_object(tmp_path, oid), f"Object for {filename} not in local store"


# ---------------------------------------------------------------------------
# _resolve_snapshot tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_snapshot_full_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Full 64-char snapshot ID resolves correctly."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="resolve full id",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    workdir = tmp_path / "muse-work"
    from maestro.muse_cli.snapshot import build_snapshot_manifest
    manifest = build_snapshot_manifest(workdir)
    snap_id = compute_snapshot_id(manifest)

    snapshot = await _resolve_snapshot(muse_cli_db_session, snap_id)
    assert snapshot is not None
    assert snapshot.snapshot_id == snap_id


@pytest.mark.anyio
async def test_resolve_snapshot_prefix(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Abbreviated 8-char prefix resolves to the correct snapshot."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="resolve prefix",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    workdir = tmp_path / "muse-work"
    from maestro.muse_cli.snapshot import build_snapshot_manifest
    manifest = build_snapshot_manifest(workdir)
    snap_id = compute_snapshot_id(manifest)

    snapshot = await _resolve_snapshot(muse_cli_db_session, snap_id[:8])
    assert snapshot is not None
    assert snapshot.snapshot_id == snap_id


@pytest.mark.anyio
async def test_resolve_snapshot_unknown_returns_none(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Unknown snapshot ID returns None."""
    snapshot = await _resolve_snapshot(muse_cli_db_session, "0" * 64)
    assert snapshot is None


# ---------------------------------------------------------------------------
# _read_tree_async — basic population
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_tree_populates_workdir(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """read-tree writes correct file content to muse-work/."""
    _init_muse_repo(tmp_path)
    file_content = b"PIANO-RIFF"
    _populate_workdir(tmp_path, {"piano.mid": file_content})

    await _commit_async(
        message="piano riff",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    from maestro.muse_cli.snapshot import build_snapshot_manifest
    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    snap_id = compute_snapshot_id(manifest)

    # Simulate a cleared working directory.
    (tmp_path / "muse-work" / "piano.mid").unlink()

    result = await _read_tree_async(
        snapshot_id=snap_id,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert isinstance(result, ReadTreeResult)
    assert result.snapshot_id == snap_id
    assert "piano.mid" in result.files_written
    assert not result.dry_run
    assert not result.reset

    restored = tmp_path / "muse-work" / "piano.mid"
    assert restored.exists()
    assert restored.read_bytes() == file_content


@pytest.mark.anyio
async def test_read_tree_populates_nested_paths(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """read-tree creates parent directories for nested file paths."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"tracks/drums/kick.mid": b"KICK-DATA"})

    await _commit_async(
        message="nested",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    from maestro.muse_cli.snapshot import build_snapshot_manifest
    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    snap_id = compute_snapshot_id(manifest)

    (tmp_path / "muse-work" / "tracks" / "drums" / "kick.mid").unlink()

    result = await _read_tree_async(
        snapshot_id=snap_id,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert "tracks/drums/kick.mid" in result.files_written
    restored = tmp_path / "muse-work" / "tracks" / "drums" / "kick.mid"
    assert restored.exists()
    assert restored.read_bytes() == b"KICK-DATA"


# ---------------------------------------------------------------------------
# _read_tree_async — dry-run
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_tree_dry_run_does_not_write(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--dry-run must not write files to muse-work/."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"bass.mid": b"BASS-GROOVE"})

    await _commit_async(
        message="dry-run test",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    from maestro.muse_cli.snapshot import build_snapshot_manifest
    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    snap_id = compute_snapshot_id(manifest)

    original_file = tmp_path / "muse-work" / "bass.mid"
    original_file.unlink()  # Remove so we can detect if it's restored.

    result = await _read_tree_async(
        snapshot_id=snap_id,
        root=tmp_path,
        session=muse_cli_db_session,
        dry_run=True,
    )

    assert result.dry_run
    assert "bass.mid" in result.files_written
    # File must NOT have been written.
    assert not original_file.exists()


# ---------------------------------------------------------------------------
# _read_tree_async — reset flag
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_tree_reset_clears_workdir_first(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--reset removes files not in the snapshot before populating."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"main.mid": b"MAIN"})

    await _commit_async(
        message="reset snapshot",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    from maestro.muse_cli.snapshot import build_snapshot_manifest
    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    snap_id = compute_snapshot_id(manifest)

    # Add an extra file that is NOT in the snapshot.
    extra = tmp_path / "muse-work" / "scratch.mid"
    extra.write_bytes(b"SCRATCH")

    result = await _read_tree_async(
        snapshot_id=snap_id,
        root=tmp_path,
        session=muse_cli_db_session,
        reset=True,
    )

    assert result.reset
    assert "main.mid" in result.files_written
    # Extra file must be gone after --reset.
    assert not extra.exists()
    # Snapshot file must be present.
    assert (tmp_path / "muse-work" / "main.mid").exists()


@pytest.mark.anyio
async def test_read_tree_without_reset_leaves_extra_files(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Without --reset, files not in the snapshot are left untouched."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"main.mid": b"MAIN"})

    await _commit_async(
        message="no-reset snapshot",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    from maestro.muse_cli.snapshot import build_snapshot_manifest
    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    snap_id = compute_snapshot_id(manifest)

    extra = tmp_path / "muse-work" / "extra.mid"
    extra.write_bytes(b"EXTRA")

    await _read_tree_async(
        snapshot_id=snap_id,
        root=tmp_path,
        session=muse_cli_db_session,
        reset=False,
    )

    # Extra file must still be present.
    assert extra.exists()
    assert extra.read_bytes() == b"EXTRA"


# ---------------------------------------------------------------------------
# _read_tree_async — error cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_tree_unknown_snapshot_id_exits(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Unknown snapshot ID produces USER_ERROR exit."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _read_tree_async(
            snapshot_id="0" * 64,
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_read_tree_short_id_exits(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Snapshot ID shorter than 4 chars produces USER_ERROR exit."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _read_tree_async(
            snapshot_id="ab",
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_read_tree_missing_objects_exits(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Snapshot with objects missing from local store exits with USER_ERROR.

    This simulates a snapshot whose objects were never committed locally
    (e.g., the snapshot was pulled from a remote but never locally committed).
    """
    from maestro.muse_cli.models import MuseCliSnapshot
    from maestro.muse_cli.snapshot import compute_snapshot_id

    _init_muse_repo(tmp_path)

    # Manually insert a snapshot that references objects NOT in the store.
    fake_manifest = {"ghost.mid": "a" * 64}
    fake_snap_id = compute_snapshot_id(fake_manifest)
    snap = MuseCliSnapshot(snapshot_id=fake_snap_id, manifest=fake_manifest)
    muse_cli_db_session.add(snap)
    await muse_cli_db_session.flush()

    with pytest.raises(typer.Exit) as exc_info:
        await _read_tree_async(
            snapshot_id=fake_snap_id,
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Abbreviated snapshot ID resolves correctly end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_tree_abbreviated_snapshot_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """read-tree accepts abbreviated (≥ 4 char) snapshot IDs."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"keys.mid": b"KEYS-LOOP"})

    await _commit_async(
        message="abbreviated id test",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    from maestro.muse_cli.snapshot import build_snapshot_manifest
    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    snap_id = compute_snapshot_id(manifest)

    (tmp_path / "muse-work" / "keys.mid").unlink()

    result = await _read_tree_async(
        snapshot_id=snap_id[:8],  # 8-char abbreviated ID
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert result.snapshot_id == snap_id
    assert "keys.mid" in result.files_written
    assert (tmp_path / "muse-work" / "keys.mid").read_bytes() == b"KEYS-LOOP"
