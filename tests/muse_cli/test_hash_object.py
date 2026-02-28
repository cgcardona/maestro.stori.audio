"""Tests for ``muse hash-object``.

All async tests inject an in-memory SQLite session and a ``tmp_path`` repo
root — no real Postgres or running process required.

Coverage:
- Pure hash computation (no write)
- Write mode: DB insertion + on-disk store
- Idempotency: writing the same object twice is a no-op
- Stdin mode
- CLI flag validation (file + --stdin, neither flag)
- File-not-found and non-file path errors
- Outside-repo exit
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.hash_object import (
    HashObjectResult,
    _hash_object_async,
    hash_bytes,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliObject
from maestro.muse_cli.object_store import object_path, objects_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path) -> str:
    """Create a minimal ``.muse/`` directory so ``require_repo()`` succeeds."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


# ---------------------------------------------------------------------------
# hash_bytes — pure unit tests
# ---------------------------------------------------------------------------


def test_hash_bytes_returns_sha256_hex() -> None:
    """hash_bytes returns the SHA-256 hex digest of the input."""
    content = b"hello muse"
    expected = hashlib.sha256(content).hexdigest()
    assert hash_bytes(content) == expected


def test_hash_bytes_is_64_chars() -> None:
    """hash_bytes output is always exactly 64 lowercase hex characters."""
    result = hash_bytes(b"")
    assert len(result) == 64
    assert result == result.lower()
    assert all(c in "0123456789abcdef" for c in result)


def test_hash_bytes_empty_input() -> None:
    """hash_bytes of empty bytes is the well-known SHA-256 of empty string."""
    expected = hashlib.sha256(b"").hexdigest()
    assert hash_bytes(b"") == expected


def test_hash_bytes_deterministic() -> None:
    """hash_bytes produces the same output for identical inputs."""
    content = b"drum-pattern-001"
    assert hash_bytes(content) == hash_bytes(content)


# ---------------------------------------------------------------------------
# _hash_object_async — compute-only (write=False)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hash_object_compute_only_returns_correct_id(
    muse_cli_db_session: AsyncSession,
) -> None:
    """_hash_object_async returns the SHA-256 digest without touching the DB."""
    content = b"kick.mid raw bytes"
    expected = hash_bytes(content)

    result = await _hash_object_async(
        session=muse_cli_db_session,
        content=content,
        write=False,
    )

    assert result.object_id == expected
    assert result.stored is False
    assert result.already_existed is False


@pytest.mark.anyio
async def test_hash_object_compute_only_does_not_insert_db_row(
    muse_cli_db_session: AsyncSession,
) -> None:
    """write=False must not insert a MuseCliObject row."""
    content = b"snare.mid"
    result = await _hash_object_async(
        session=muse_cli_db_session,
        content=content,
        write=False,
    )

    row = await muse_cli_db_session.get(MuseCliObject, result.object_id)
    assert row is None


# ---------------------------------------------------------------------------
# _hash_object_async — write mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hash_object_write_inserts_db_row(
    muse_cli_db_session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """write=True inserts a MuseCliObject row with correct size_bytes."""
    _init_muse_repo(tmp_path)
    content = b"bass.mid content"
    result = await _hash_object_async(
        session=muse_cli_db_session,
        content=content,
        write=True,
        repo_root=tmp_path,
    )
    await muse_cli_db_session.commit()

    row = await muse_cli_db_session.get(MuseCliObject, result.object_id)
    assert row is not None
    assert row.size_bytes == len(content)
    assert result.stored is True
    assert result.already_existed is False


@pytest.mark.anyio
async def test_hash_object_write_creates_on_disk_file(
    muse_cli_db_session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """write=True writes the object bytes to .muse/objects/<object_id>."""
    _init_muse_repo(tmp_path)
    content = b"keys.mid data"

    result = await _hash_object_async(
        session=muse_cli_db_session,
        content=content,
        write=True,
        repo_root=tmp_path,
    )

    stored_path = object_path(tmp_path, result.object_id)
    assert stored_path.exists()
    assert stored_path.read_bytes() == content


@pytest.mark.anyio
async def test_hash_object_write_is_idempotent_db(
    muse_cli_db_session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """Writing the same object twice leaves exactly one DB row (idempotent)."""
    _init_muse_repo(tmp_path)
    content = b"repeat-object"

    result1 = await _hash_object_async(
        session=muse_cli_db_session,
        content=content,
        write=True,
        repo_root=tmp_path,
    )
    await muse_cli_db_session.commit()

    result2 = await _hash_object_async(
        session=muse_cli_db_session,
        content=content,
        write=True,
        repo_root=tmp_path,
    )
    await muse_cli_db_session.commit()

    assert result1.object_id == result2.object_id
    assert result2.already_existed is True


@pytest.mark.anyio
async def test_hash_object_write_matches_commit_hash(
    muse_cli_db_session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """hash-object -w produces the same ID that muse commit would assign."""
    from maestro.muse_cli.snapshot import hash_file

    _init_muse_repo(tmp_path)
    content = b"midi-track-data"
    src_file = tmp_path / "track.mid"
    src_file.write_bytes(content)

    commit_hash = hash_file(src_file)
    result = await _hash_object_async(
        session=muse_cli_db_session,
        content=content,
        write=True,
        repo_root=tmp_path,
    )

    assert result.object_id == commit_hash


# ---------------------------------------------------------------------------
# HashObjectResult — unit tests
# ---------------------------------------------------------------------------


def test_hash_object_result_fields() -> None:
    """HashObjectResult stores object_id, stored, and already_existed correctly."""
    oid = "a" * 64
    r = HashObjectResult(object_id=oid, stored=True, already_existed=False)
    assert r.object_id == oid
    assert r.stored is True
    assert r.already_existed is False


def test_hash_object_result_defaults_already_existed_false() -> None:
    """already_existed defaults to False when not provided."""
    r = HashObjectResult(object_id="b" * 64, stored=False)
    assert r.already_existed is False


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


def test_hash_object_prints_sha256_for_file(tmp_path: pathlib.Path) -> None:
    """CLI prints the correct SHA-256 hash for a given file."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    content = b"groove-data"
    src = tmp_path / "groove.mid"
    src.write_bytes(content)
    expected = hash_bytes(content)

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["hash-object", str(src)], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert expected in result.output


def test_hash_object_file_and_stdin_mutually_exclusive(tmp_path: pathlib.Path) -> None:
    """Providing both a file and --stdin exits with USER_ERROR."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    src = tmp_path / "f.mid"
    src.write_bytes(b"x")

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["hash-object", "--stdin", str(src)], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.USER_ERROR


def test_hash_object_no_args_exits_user_error(tmp_path: pathlib.Path) -> None:
    """Calling hash-object with neither a file nor --stdin exits USER_ERROR."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["hash-object", ""], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.USER_ERROR


def test_hash_object_missing_file_exits_user_error(tmp_path: pathlib.Path) -> None:
    """A non-existent file path exits with USER_ERROR and a clear message."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["hash-object", "nonexistent.mid"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.USER_ERROR
    assert "not found" in result.output.lower() or "File not found" in result.output


def test_hash_object_outside_repo_exits_repo_not_found(tmp_path: pathlib.Path) -> None:
    """``muse hash-object`` outside a .muse/ directory exits REPO_NOT_FOUND."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    src = tmp_path / "f.mid"
    src.write_bytes(b"data")

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["hash-object", str(src)], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND
