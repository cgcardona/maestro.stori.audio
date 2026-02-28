"""Tests for ``muse write-tree``.

``_write_tree_async`` is exercised directly with an in-memory SQLite session
(via the ``muse_cli_db_session`` fixture) so no real Postgres instance is
needed.  The Typer CLI runner covers the command surface (repo detection,
exit codes, flag handling).

All async tests use ``@pytest.mark.anyio``.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.commands.write_tree import _write_tree_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot
from maestro.muse_cli.snapshot import build_snapshot_manifest, compute_snapshot_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout without any commits."""
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
    subdir: str | None = None,
) -> None:
    """Create muse-work/ with the given files (defaults to two sample files)."""
    workdir = root / "muse-work"
    if subdir:
        workdir = workdir / subdir
    workdir.mkdir(parents=True, exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-DATA", "lead.mp3": b"MP3-DATA"}
    for name, content in files.items():
        (workdir / name).write_bytes(content)


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_write_tree_returns_snapshot_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """_write_tree_async returns a 64-char hex snapshot_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    snapshot_id = await _write_tree_async(root=tmp_path, session=muse_cli_db_session)

    assert len(snapshot_id) == 64
    assert all(c in "0123456789abcdef" for c in snapshot_id)


@pytest.mark.anyio
async def test_write_tree_idempotent_same_content_same_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Calling write-tree twice on identical content yields the same snapshot_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"track.mid": b"CONSTANT"})

    id1 = await _write_tree_async(root=tmp_path, session=muse_cli_db_session)
    id2 = await _write_tree_async(root=tmp_path, session=muse_cli_db_session)

    assert id1 == id2


@pytest.mark.anyio
async def test_write_tree_different_content_different_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Changing a file produces a different snapshot_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"track.mid": b"VERSION1"})

    id1 = await _write_tree_async(root=tmp_path, session=muse_cli_db_session)

    (tmp_path / "muse-work" / "track.mid").write_bytes(b"VERSION2")

    id2 = await _write_tree_async(root=tmp_path, session=muse_cli_db_session)

    assert id1 != id2


@pytest.mark.anyio
async def test_write_tree_snapshot_id_matches_snapshot_module(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """snapshot_id returned by _write_tree_async equals compute_snapshot_id(manifest)."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"a.mid": b"ALPHA", "b.mp3": b"BETA"})

    snapshot_id = await _write_tree_async(root=tmp_path, session=muse_cli_db_session)

    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    expected = compute_snapshot_id(manifest)

    assert snapshot_id == expected


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_write_tree_persists_snapshot_row(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """A MuseCliSnapshot row is written to the DB."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"MIDI"})

    snapshot_id = await _write_tree_async(root=tmp_path, session=muse_cli_db_session)
    await muse_cli_db_session.flush()

    snap = await muse_cli_db_session.get(MuseCliSnapshot, snapshot_id)
    assert snap is not None
    assert "beat.mid" in snap.manifest


@pytest.mark.anyio
async def test_write_tree_persists_object_rows(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """A MuseCliObject row is written for every unique file."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"drums.mid": b"DRUM-BYTES", "bass.mid": b"BASS-BYTES"})

    await _write_tree_async(root=tmp_path, session=muse_cli_db_session)
    await muse_cli_db_session.flush()

    result = await muse_cli_db_session.execute(select(MuseCliObject))
    objects = result.scalars().all()
    assert len(objects) == 2


@pytest.mark.anyio
async def test_write_tree_objects_are_deduplicated(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Running write-tree twice does not create duplicate object rows."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"track.mid": b"SHARED-CONTENT"})

    await _write_tree_async(root=tmp_path, session=muse_cli_db_session)
    await _write_tree_async(root=tmp_path, session=muse_cli_db_session)
    await muse_cli_db_session.flush()

    result = await muse_cli_db_session.execute(select(MuseCliObject))
    objects = result.scalars().all()
    # Only one unique file → only one object row
    assert len(objects) == 1


@pytest.mark.anyio
async def test_write_tree_does_not_create_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """write-tree must NOT create a MuseCliCommit row."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _write_tree_async(root=tmp_path, session=muse_cli_db_session)
    await muse_cli_db_session.flush()

    result = await muse_cli_db_session.execute(select(MuseCliCommit))
    commits = result.scalars().all()
    assert len(commits) == 0, "write-tree must not create commit rows"


@pytest.mark.anyio
async def test_write_tree_does_not_modify_branch_ref(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """The branch HEAD ref file must be unchanged after write-tree."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    ref_path = tmp_path / ".muse" / "refs" / "heads" / "main"
    before = ref_path.read_text()

    await _write_tree_async(root=tmp_path, session=muse_cli_db_session)

    after = ref_path.read_text()
    assert before == after, "write-tree must not update the branch HEAD pointer"


# ---------------------------------------------------------------------------
# --prefix filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_write_tree_prefix_includes_matching_files_only(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--prefix restricts the snapshot to files under the given subdirectory."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, subdir="drums", files={"kick.mid": b"KICK"})
    _populate_workdir(tmp_path, subdir="bass", files={"bassline.mid": b"BASS"})

    snapshot_id = await _write_tree_async(
        root=tmp_path,
        session=muse_cli_db_session,
        prefix="drums",
    )
    await muse_cli_db_session.flush()

    snap = await muse_cli_db_session.get(MuseCliSnapshot, snapshot_id)
    assert snap is not None
    assert any("kick.mid" in k for k in snap.manifest.keys())
    assert not any("bassline.mid" in k for k in snap.manifest.keys())


@pytest.mark.anyio
async def test_write_tree_prefix_with_trailing_slash(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--prefix 'drums/' and --prefix 'drums' produce the same snapshot_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, subdir="drums", files={"hi_hat.mid": b"HH"})
    _populate_workdir(tmp_path, subdir="bass", files={"bassline.mid": b"BASS"})

    id_without_slash = await _write_tree_async(
        root=tmp_path, session=muse_cli_db_session, prefix="drums"
    )
    id_with_slash = await _write_tree_async(
        root=tmp_path, session=muse_cli_db_session, prefix="drums/"
    )

    assert id_without_slash == id_with_slash


@pytest.mark.anyio
async def test_write_tree_prefix_no_match_exits_1_by_default(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--prefix that matches no files exits USER_ERROR when --missing-ok is absent."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, subdir="drums", files={"kick.mid": b"KICK"})

    with pytest.raises(typer.Exit) as exc_info:
        await _write_tree_async(
            root=tmp_path,
            session=muse_cli_db_session,
            prefix="nonexistent-subdir",
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_write_tree_prefix_no_match_missing_ok_succeeds(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--prefix that matches no files + --missing-ok returns an empty snapshot_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, subdir="drums", files={"kick.mid": b"KICK"})

    snapshot_id = await _write_tree_async(
        root=tmp_path,
        session=muse_cli_db_session,
        prefix="nonexistent-subdir",
        missing_ok=True,
    )

    # Empty manifest → deterministic empty snapshot_id
    expected = compute_snapshot_id({})
    assert snapshot_id == expected


# ---------------------------------------------------------------------------
# --missing-ok flag
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_write_tree_missing_workdir_exits_1_by_default(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Missing muse-work/ exits USER_ERROR (1) without --missing-ok."""
    _init_muse_repo(tmp_path)
    # Deliberately do NOT create muse-work/

    with pytest.raises(typer.Exit) as exc_info:
        await _write_tree_async(root=tmp_path, session=muse_cli_db_session)

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_write_tree_empty_workdir_exits_1_by_default(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Empty muse-work/ exits USER_ERROR (1) without --missing-ok."""
    _init_muse_repo(tmp_path)
    (tmp_path / "muse-work").mkdir()

    with pytest.raises(typer.Exit) as exc_info:
        await _write_tree_async(root=tmp_path, session=muse_cli_db_session)

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_write_tree_missing_workdir_missing_ok_returns_empty_snapshot(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """With --missing-ok, absent muse-work/ returns the empty snapshot_id."""
    _init_muse_repo(tmp_path)
    # No muse-work/ directory

    snapshot_id = await _write_tree_async(
        root=tmp_path, session=muse_cli_db_session, missing_ok=True
    )

    expected = compute_snapshot_id({})
    assert snapshot_id == expected


@pytest.mark.anyio
async def test_write_tree_empty_workdir_missing_ok_returns_empty_snapshot(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """With --missing-ok, an empty muse-work/ returns the empty snapshot_id."""
    _init_muse_repo(tmp_path)
    (tmp_path / "muse-work").mkdir()

    snapshot_id = await _write_tree_async(
        root=tmp_path, session=muse_cli_db_session, missing_ok=True
    )

    expected = compute_snapshot_id({})
    assert snapshot_id == expected


# ---------------------------------------------------------------------------
# CLI surface (repo detection, output format)
# ---------------------------------------------------------------------------


def test_write_tree_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """muse write-tree exits REPO_NOT_FOUND (2) when not inside a Muse repo."""
    import os

    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()
    # Invoke without setting MUSE_REPO_ROOT so it falls back to cwd discovery.
    env = {**os.environ, "MUSE_REPO_ROOT": str(tmp_path)}
    result = runner.invoke(cli, ["write-tree"], env=env, catch_exceptions=False)
    assert result.exit_code == ExitCode.REPO_NOT_FOUND
