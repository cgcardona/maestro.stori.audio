"""Tests for ``muse cat-object``.

All async tests call ``_cat_object_async`` and ``_lookup_object`` directly
with an in-memory SQLite session and a ``tmp_path`` repo root — no real
Postgres or running process required.  ORM rows are seeded directly so the
lookup tests are independent of ``muse commit``.
"""
from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, timezone

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.cat_object import (
    CatObjectResult,
    _cat_object_async,
    _lookup_object,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc


def _fake_hash(prefix: str = "a") -> str:
    """Generate a deterministic 64-char hex string for use as an object ID."""
    return (prefix * 64)[:64]


def _init_muse_repo(root: pathlib.Path) -> str:
    """Create a minimal ``.muse/`` directory so ``require_repo()`` succeeds."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


async def _seed_object(session: AsyncSession, object_id: str, size: int = 1024) -> MuseCliObject:
    obj = MuseCliObject(
        object_id=object_id,
        size_bytes=size,
        created_at=datetime(2026, 1, 1, tzinfo=_UTC),
    )
    session.add(obj)
    await session.flush()
    return obj


async def _seed_snapshot(
    session: AsyncSession,
    snapshot_id: str,
    manifest: dict[str, str] | None = None,
) -> MuseCliSnapshot:
    snap = MuseCliSnapshot(
        snapshot_id=snapshot_id,
        manifest=manifest or {"beat.mid": _fake_hash("b")},
        created_at=datetime(2026, 1, 2, tzinfo=_UTC),
    )
    session.add(snap)
    await session.flush()
    return snap


async def _seed_commit(
    session: AsyncSession,
    commit_id: str,
    snapshot_id: str,
    repo_id: str = "test-repo",
) -> MuseCliCommit:
    commit = MuseCliCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch="main",
        parent_commit_id=None,
        parent2_commit_id=None,
        snapshot_id=snapshot_id,
        message="initial take",
        author="test-author",
        committed_at=datetime(2026, 1, 3, tzinfo=_UTC),
        created_at=datetime(2026, 1, 3, tzinfo=_UTC),
    )
    session.add(commit)
    await session.flush()
    return commit


# ---------------------------------------------------------------------------
# _lookup_object — type resolution tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lookup_object_finds_object_row(
    muse_cli_db_session: AsyncSession,
) -> None:
    """_lookup_object returns type='object' when the ID is a MuseCliObject."""
    oid = _fake_hash("a")
    await _seed_object(muse_cli_db_session, oid)

    result = await _lookup_object(muse_cli_db_session, oid)

    assert result is not None
    assert result.object_type == "object"
    assert isinstance(result.row, MuseCliObject)


@pytest.mark.anyio
async def test_lookup_object_finds_snapshot_row(
    muse_cli_db_session: AsyncSession,
) -> None:
    """_lookup_object returns type='snapshot' when the ID is a MuseCliSnapshot."""
    sid = _fake_hash("c")
    await _seed_snapshot(muse_cli_db_session, sid)

    result = await _lookup_object(muse_cli_db_session, sid)

    assert result is not None
    assert result.object_type == "snapshot"
    assert isinstance(result.row, MuseCliSnapshot)


@pytest.mark.anyio
async def test_lookup_object_finds_commit_row(
    muse_cli_db_session: AsyncSession,
) -> None:
    """_lookup_object returns type='commit' when the ID is a MuseCliCommit."""
    sid = _fake_hash("c")
    cid = _fake_hash("d")
    await _seed_snapshot(muse_cli_db_session, sid)
    await _seed_commit(muse_cli_db_session, cid, sid)

    result = await _lookup_object(muse_cli_db_session, cid)

    assert result is not None
    assert result.object_type == "commit"
    assert isinstance(result.row, MuseCliCommit)


@pytest.mark.anyio
async def test_lookup_object_returns_none_for_unknown_id(
    muse_cli_db_session: AsyncSession,
) -> None:
    """_lookup_object returns None when the ID is not in any table."""
    result = await _lookup_object(muse_cli_db_session, _fake_hash("z"))
    assert result is None


# ---------------------------------------------------------------------------
# _cat_object_async — default (metadata) output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cat_object_default_prints_object_metadata(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default output shows type, object_id, size, and created_at for blob objects."""
    oid = _fake_hash("a")
    await _seed_object(muse_cli_db_session, oid, size=2048)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=oid,
        type_only=False,
        pretty=False,
    )

    out = capsys.readouterr().out
    assert "type:       object" in out
    assert oid in out
    assert "2048 bytes" in out


@pytest.mark.anyio
async def test_cat_object_default_prints_snapshot_metadata(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default output shows type, snapshot_id, and file count for snapshots."""
    sid = _fake_hash("c")
    manifest = {"beat.mid": _fake_hash("b"), "keys.mid": _fake_hash("e")}
    await _seed_snapshot(muse_cli_db_session, sid, manifest=manifest)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=sid,
        type_only=False,
        pretty=False,
    )

    out = capsys.readouterr().out
    assert "type:        snapshot" in out
    assert sid in out
    assert "files:       2" in out


@pytest.mark.anyio
async def test_cat_object_default_prints_commit_metadata(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default output shows type, commit_id, branch, message for commits."""
    sid = _fake_hash("c")
    cid = _fake_hash("d")
    await _seed_snapshot(muse_cli_db_session, sid)
    await _seed_commit(muse_cli_db_session, cid, sid)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=cid,
        type_only=False,
        pretty=False,
    )

    out = capsys.readouterr().out
    assert "type:       commit" in out
    assert cid in out
    assert "main" in out
    assert "initial take" in out


# ---------------------------------------------------------------------------
# _cat_object_async — -t / --type flag
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cat_object_type_only_prints_object(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """-t prints only 'object' for a MuseCliObject row."""
    oid = _fake_hash("a")
    await _seed_object(muse_cli_db_session, oid)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=oid,
        type_only=True,
        pretty=False,
    )

    out = capsys.readouterr().out.strip()
    assert out == "object"


@pytest.mark.anyio
async def test_cat_object_type_only_prints_snapshot(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """-t prints only 'snapshot' for a MuseCliSnapshot row."""
    sid = _fake_hash("c")
    await _seed_snapshot(muse_cli_db_session, sid)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=sid,
        type_only=True,
        pretty=False,
    )

    out = capsys.readouterr().out.strip()
    assert out == "snapshot"


@pytest.mark.anyio
async def test_cat_object_type_only_prints_commit(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """-t prints only 'commit' for a MuseCliCommit row."""
    sid = _fake_hash("c")
    cid = _fake_hash("d")
    await _seed_snapshot(muse_cli_db_session, sid)
    await _seed_commit(muse_cli_db_session, cid, sid)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=cid,
        type_only=True,
        pretty=False,
    )

    out = capsys.readouterr().out.strip()
    assert out == "commit"


# ---------------------------------------------------------------------------
# _cat_object_async — -p / --pretty flag
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cat_object_pretty_prints_snapshot_manifest(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """-p emits valid JSON containing the manifest dict for snapshots."""
    sid = _fake_hash("c")
    manifest = {"beat.mid": _fake_hash("b"), "keys.mid": _fake_hash("e")}
    await _seed_snapshot(muse_cli_db_session, sid, manifest=manifest)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=sid,
        type_only=False,
        pretty=True,
    )

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["type"] == "snapshot"
    assert data["snapshot_id"] == sid
    assert data["manifest"] == manifest


@pytest.mark.anyio
async def test_cat_object_pretty_prints_commit_fields(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """-p emits valid JSON with all commit fields."""
    sid = _fake_hash("c")
    cid = _fake_hash("d")
    await _seed_snapshot(muse_cli_db_session, sid)
    await _seed_commit(muse_cli_db_session, cid, sid)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=cid,
        type_only=False,
        pretty=True,
    )

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["type"] == "commit"
    assert data["commit_id"] == cid
    assert data["branch"] == "main"
    assert data["message"] == "initial take"
    assert data["snapshot_id"] == sid


@pytest.mark.anyio
async def test_cat_object_pretty_prints_object_fields(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """-p emits valid JSON with size and created_at for blob objects."""
    oid = _fake_hash("a")
    await _seed_object(muse_cli_db_session, oid, size=512)

    await _cat_object_async(
        session=muse_cli_db_session,
        object_id=oid,
        type_only=False,
        pretty=True,
    )

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["type"] == "object"
    assert data["object_id"] == oid
    assert data["size_bytes"] == 512


# ---------------------------------------------------------------------------
# _cat_object_async — not found error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cat_object_not_found_exits_user_error(
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown object ID exits with USER_ERROR and a clear message."""
    with pytest.raises(typer.Exit) as exc_info:
        await _cat_object_async(
            session=muse_cli_db_session,
            object_id=_fake_hash("z"),
            type_only=False,
            pretty=False,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
    out = capsys.readouterr().out
    assert "Object not found" in out


# ---------------------------------------------------------------------------
# CatObjectResult.to_dict — serialisation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cat_object_result_to_dict_object(
    muse_cli_db_session: AsyncSession,
) -> None:
    """CatObjectResult.to_dict() includes all expected keys for an object row."""
    oid = _fake_hash("a")
    obj = await _seed_object(muse_cli_db_session, oid, size=128)
    result = CatObjectResult(object_type="object", row=obj)
    d = result.to_dict()
    assert set(d.keys()) == {"type", "object_id", "size_bytes", "created_at"}
    assert d["type"] == "object"
    assert d["size_bytes"] == 128


@pytest.mark.anyio
async def test_cat_object_result_to_dict_commit(
    muse_cli_db_session: AsyncSession,
) -> None:
    """CatObjectResult.to_dict() includes all expected keys for a commit row."""
    sid = _fake_hash("c")
    cid = _fake_hash("d")
    await _seed_snapshot(muse_cli_db_session, sid)
    commit = await _seed_commit(muse_cli_db_session, cid, sid)
    result = CatObjectResult(object_type="commit", row=commit)
    d = result.to_dict()
    assert "commit_id" in d
    assert "branch" in d
    assert "message" in d
    assert "snapshot_id" in d


# ---------------------------------------------------------------------------
# CLI integration — mutually exclusive flags
# ---------------------------------------------------------------------------


def test_cat_object_type_and_pretty_mutually_exclusive(
    tmp_path: pathlib.Path,
) -> None:
    """Passing both -t and -p exits with USER_ERROR."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["cat-object", "-t", "-p", _fake_hash("a")],
            catch_exceptions=False,
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.USER_ERROR


def test_cat_object_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse cat-object`` outside a .muse/ directory exits with code 2."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["cat-object", _fake_hash("a")],
            catch_exceptions=False,
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND
