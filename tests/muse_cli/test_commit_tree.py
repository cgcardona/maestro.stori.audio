"""Tests for ``muse commit-tree``.

Tests exercise ``_commit_tree_async`` directly with an in-memory SQLite session
so no real Postgres instance is required.  The ``muse_cli_db_session`` fixture
(from tests/muse_cli/conftest.py) provides the isolated SQLite session.

All async tests use ``@pytest.mark.anyio``.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.commands.commit_tree import _commit_tree_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.muse_cli.snapshot import compute_commit_tree_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_snapshot(session: AsyncSession, files: int = 2) -> str:
    """Insert a minimal MuseCliSnapshot row and return its snapshot_id."""
    snapshot_id = "a" * 64  # fixed deterministic value for test simplicity
    manifest: dict[str, str] = {f"track{i}.mid": "b" * 64 for i in range(files)}
    snap = MuseCliSnapshot(snapshot_id=snapshot_id, manifest=manifest)
    session.add(snap)
    await session.flush()
    return snapshot_id


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_tree_creates_commit_row(
    muse_cli_db_session: AsyncSession,
) -> None:
    """commit-tree inserts a MuseCliCommit row with correct fields."""
    snapshot_id = await _seed_snapshot(muse_cli_db_session)

    commit_id = await _commit_tree_async(
        snapshot_id=snapshot_id,
        message="raw commit",
        parent_ids=[],
        author="Alice",
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one_or_none()
    assert row is not None, "commit row must exist after _commit_tree_async"
    assert row.message == "raw commit"
    assert row.author == "Alice"
    assert row.snapshot_id == snapshot_id
    assert row.parent_commit_id is None
    assert row.parent2_commit_id is None


@pytest.mark.anyio
async def test_commit_tree_returns_64_char_hex(
    muse_cli_db_session: AsyncSession,
) -> None:
    """commit_id returned by commit-tree is a valid 64-char hex SHA-256."""
    snapshot_id = await _seed_snapshot(muse_cli_db_session)

    commit_id = await _commit_tree_async(
        snapshot_id=snapshot_id,
        message="hex check",
        parent_ids=[],
        author="",
        session=muse_cli_db_session,
    )

    assert len(commit_id) == 64
    assert all(c in "0123456789abcdef" for c in commit_id)


@pytest.mark.anyio
async def test_commit_tree_does_not_update_any_ref(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """commit-tree must NOT write to .muse/refs/ or .muse/HEAD."""
    # Set up a minimal .muse layout so we can verify no ref is written
    muse_dir = tmp_path / ".muse"
    refs_dir = muse_dir / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    (muse_dir / "HEAD").write_text("refs/heads/main")
    (refs_dir / "main").write_text("deadbeef" * 8)  # fake HEAD SHA

    snapshot_id = await _seed_snapshot(muse_cli_db_session)

    await _commit_tree_async(
        snapshot_id=snapshot_id,
        message="should not move ref",
        parent_ids=[],
        author="",
        session=muse_cli_db_session,
    )

    # Ref must be unchanged
    head_ref = (refs_dir / "main").read_text()
    assert head_ref == "deadbeef" * 8, "commit-tree must not update branch ref"
    # HEAD must be unchanged
    head_content = (muse_dir / "HEAD").read_text()
    assert head_content == "refs/heads/main"


@pytest.mark.anyio
async def test_commit_tree_single_parent(
    muse_cli_db_session: AsyncSession,
) -> None:
    """With one -p flag, parent_commit_id is set and parent2 remains None."""
    snapshot_id = await _seed_snapshot(muse_cli_db_session)
    parent_id = "c" * 64

    commit_id = await _commit_tree_async(
        snapshot_id=snapshot_id,
        message="has parent",
        parent_ids=[parent_id],
        author="",
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one()
    assert row.parent_commit_id == parent_id
    assert row.parent2_commit_id is None


@pytest.mark.anyio
async def test_commit_tree_merge_commit_two_parents(
    muse_cli_db_session: AsyncSession,
) -> None:
    """With two -p flags, both parent columns are populated (merge commit)."""
    snapshot_id = await _seed_snapshot(muse_cli_db_session)
    parent1 = "d" * 64
    parent2 = "e" * 64

    commit_id = await _commit_tree_async(
        snapshot_id=snapshot_id,
        message="merge commit",
        parent_ids=[parent1, parent2],
        author="",
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one()
    assert row.parent_commit_id == parent1
    assert row.parent2_commit_id == parent2


@pytest.mark.anyio
async def test_commit_tree_idempotent_same_inputs(
    muse_cli_db_session: AsyncSession,
) -> None:
    """Calling commit-tree twice with identical inputs returns the same commit_id
    without inserting a duplicate row."""
    snapshot_id = await _seed_snapshot(muse_cli_db_session)

    async def _call() -> str:
        return await _commit_tree_async(
            snapshot_id=snapshot_id,
            message="idempotent",
            parent_ids=[],
            author="Bob",
            session=muse_cli_db_session,
        )

    id1 = await _call()
    id2 = await _call()

    assert id1 == id2, "same inputs must produce the same commit_id"

    # Only one row must exist
    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == id1)
    )
    rows = result.scalars().all()
    assert len(rows) == 1, "idempotent call must not insert a duplicate row"


@pytest.mark.anyio
async def test_commit_tree_deterministic_hash(
    muse_cli_db_session: AsyncSession,
) -> None:
    """compute_commit_tree_id is deterministic: same inputs → same digest."""
    snapshot_id = "f" * 64
    parent = "0" * 64

    h1 = compute_commit_tree_id(
        parent_ids=[parent],
        snapshot_id=snapshot_id,
        message="determinism",
        author="Carol",
    )
    h2 = compute_commit_tree_id(
        parent_ids=[parent],
        snapshot_id=snapshot_id,
        message="determinism",
        author="Carol",
    )
    assert h1 == h2
    assert len(h1) == 64


@pytest.mark.anyio
async def test_commit_tree_different_messages_different_ids(
    muse_cli_db_session: AsyncSession,
) -> None:
    """Different messages produce different commit_ids for the same snapshot."""
    snapshot_id = await _seed_snapshot(muse_cli_db_session)

    # Use a second unique snapshot for the second call
    snap2_id = "b" * 64
    snap2 = MuseCliSnapshot(snapshot_id=snap2_id, manifest={"x.mid": "c" * 64})
    muse_cli_db_session.add(snap2)
    await muse_cli_db_session.flush()

    id1 = await _commit_tree_async(
        snapshot_id=snapshot_id,
        message="message A",
        parent_ids=[],
        author="",
        session=muse_cli_db_session,
    )
    id2 = await _commit_tree_async(
        snapshot_id=snap2_id,
        message="message B",
        parent_ids=[],
        author="",
        session=muse_cli_db_session,
    )

    assert id1 != id2


@pytest.mark.anyio
async def test_commit_tree_branch_is_empty_string(
    muse_cli_db_session: AsyncSession,
) -> None:
    """commit-tree stores branch as empty string (not associated with any ref)."""
    snapshot_id = await _seed_snapshot(muse_cli_db_session)

    commit_id = await _commit_tree_async(
        snapshot_id=snapshot_id,
        message="branch check",
        parent_ids=[],
        author="",
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one()
    assert row.branch == "", "commit-tree must not associate with any branch"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_tree_unknown_snapshot_exits_1(
    muse_cli_db_session: AsyncSession,
) -> None:
    """When snapshot_id is not in the DB, commit-tree exits USER_ERROR."""
    nonexistent_snapshot = "9" * 64

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_tree_async(
            snapshot_id=nonexistent_snapshot,
            message="ghost snapshot",
            parent_ids=[],
            author="",
            session=muse_cli_db_session,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_commit_tree_too_many_parents_exits_1(
    muse_cli_db_session: AsyncSession,
) -> None:
    """Supplying more than 2 parent IDs exits USER_ERROR (DB only stores 2)."""
    snapshot_id = await _seed_snapshot(muse_cli_db_session)

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_tree_async(
            snapshot_id=snapshot_id,
            message="octopus merge",
            parent_ids=["a" * 64, "b" * 64, "c" * 64],
            author="",
            session=muse_cli_db_session,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


def test_commit_tree_no_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """Typer CLI runner: commit-tree outside a repo exits REPO_NOT_FOUND."""
    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["commit-tree", "a" * 64, "-m", "no repo"],
        catch_exceptions=False,
    )
    assert result.exit_code == ExitCode.REPO_NOT_FOUND
