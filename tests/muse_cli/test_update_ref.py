"""Tests for ``muse update-ref`` — write or delete a ref (branch or tag pointer).

Verifies:
- update_ref: writes new commit_id to refs/heads/<branch>.
- update_ref: writes new commit_id to refs/tags/<tag>.
- update_ref: validates commit exists before writing.
- update_ref: CAS guard (--old-value) passes when current matches expected.
- update_ref: CAS guard exits USER_ERROR when current does not match expected.
- update_ref: CAS guard handles missing ref (current=None vs provided old-value).
- delete_ref: removes an existing ref file.
- delete_ref: exits USER_ERROR when ref file does not exist.
- update_ref: exits USER_ERROR for invalid ref format.
- delete_ref: exits USER_ERROR for invalid ref format.
- Boundary seal (AST): ``from __future__ import annotations`` present.
"""
from __future__ import annotations

import ast
import datetime
import json
import pathlib
import uuid
from collections.abc import AsyncGenerator

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.db.database import Base
from maestro.muse_cli import models as cli_models  # noqa: F401 — register tables
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot
from maestro.muse_cli.commands.update_ref import (
    _delete_ref_async,
    _update_ref_async,
    _validate_ref_format,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all CLI tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def repo_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal Muse repo structure under *tmp_path*."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "HEAD").write_text("refs/heads/main")
    refs_heads = muse_dir / "refs" / "heads"
    refs_heads.mkdir(parents=True)
    refs_tags = muse_dir / "refs" / "tags"
    refs_tags.mkdir(parents=True)
    repo_id = str(uuid.uuid4())
    (muse_dir / "repo.json").write_text(json.dumps({"repo_id": repo_id}))
    return tmp_path


async def _insert_commit(
    session: AsyncSession,
    repo_root: pathlib.Path,
    commit_suffix: str = "b",
    branch: str = "main",
) -> str:
    """Insert a minimal MuseCliCommit and return its commit_id."""
    repo_json = repo_root / ".muse" / "repo.json"
    repo_id: str = json.loads(repo_json.read_text())["repo_id"]

    obj_id = (commit_suffix * 2)[:2] + "c" * 62
    snap_id = (commit_suffix * 2)[:2] + "a" * 62
    commit_id = commit_suffix * 64

    session.add(MuseCliObject(object_id=obj_id, size_bytes=1))
    session.add(MuseCliSnapshot(snapshot_id=snap_id, manifest={"f.mid": obj_id}))
    await session.flush()

    session.add(
        MuseCliCommit(
            commit_id=commit_id,
            repo_id=repo_id,
            branch=branch,
            parent_commit_id=None,
            parent2_commit_id=None,
            snapshot_id=snap_id,
            message="initial",
            author="",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    await session.flush()
    return commit_id


# ---------------------------------------------------------------------------
# _validate_ref_format
# ---------------------------------------------------------------------------


def test_validate_ref_format_accepts_heads() -> None:
    """refs/heads/<name> must not raise."""
    _validate_ref_format("refs/heads/main")  # no exception


def test_validate_ref_format_accepts_tags() -> None:
    """refs/tags/<name> must not raise."""
    _validate_ref_format("refs/tags/v1.0")  # no exception


def test_validate_ref_format_rejects_bare_name() -> None:
    """A bare name without prefix must exit USER_ERROR."""
    with pytest.raises(typer.Exit) as exc_info:
        _validate_ref_format("main")
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


def test_validate_ref_format_rejects_wrong_prefix() -> None:
    """An arbitrary prefix must exit USER_ERROR."""
    with pytest.raises(typer.Exit) as exc_info:
        _validate_ref_format("refs/remotes/origin/main")
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# _update_ref_async — basic write
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_ref_writes_commit_id_to_heads(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """update-ref writes the commit_id to refs/heads/<branch>."""
    commit_id = await _insert_commit(async_session, repo_root)

    await _update_ref_async(
        ref="refs/heads/main",
        new_value=commit_id,
        old_value=None,
        root=repo_root,
        session=async_session,
    )

    ref_path = repo_root / ".muse" / "refs" / "heads" / "main"
    assert ref_path.read_text().strip() == commit_id


@pytest.mark.anyio
async def test_update_ref_writes_commit_id_to_tags(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """update-ref writes the commit_id to refs/tags/<tag>."""
    commit_id = await _insert_commit(async_session, repo_root)

    await _update_ref_async(
        ref="refs/tags/v1.0",
        new_value=commit_id,
        old_value=None,
        root=repo_root,
        session=async_session,
    )

    ref_path = repo_root / ".muse" / "refs" / "tags" / "v1.0"
    assert ref_path.read_text().strip() == commit_id


@pytest.mark.anyio
async def test_update_ref_creates_parent_dirs(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """update-ref creates intermediate directories if they don't exist."""
    commit_id = await _insert_commit(async_session, repo_root)
    # Remove the refs/tags dir to force creation
    import shutil
    shutil.rmtree(repo_root / ".muse" / "refs" / "tags")

    await _update_ref_async(
        ref="refs/tags/v2.0",
        new_value=commit_id,
        old_value=None,
        root=repo_root,
        session=async_session,
    )

    ref_path = repo_root / ".muse" / "refs" / "tags" / "v2.0"
    assert ref_path.exists()
    assert ref_path.read_text().strip() == commit_id


@pytest.mark.anyio
async def test_update_ref_validates_commit_exists(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """update-ref exits USER_ERROR when the commit_id is not in the DB."""
    fake_commit_id = "d" * 64

    with pytest.raises(typer.Exit) as exc_info:
        await _update_ref_async(
            ref="refs/heads/main",
            new_value=fake_commit_id,
            old_value=None,
            root=repo_root,
            session=async_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# _update_ref_async — CAS guard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_ref_cas_passes_when_current_matches(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """CAS succeeds and writes the new value when old-value matches current."""
    commit_id_v1 = await _insert_commit(async_session, repo_root, commit_suffix="b")
    commit_id_v2 = await _insert_commit(async_session, repo_root, commit_suffix="c")

    # Prime the ref with v1.
    ref_path = repo_root / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_id_v1)

    await _update_ref_async(
        ref="refs/heads/main",
        new_value=commit_id_v2,
        old_value=commit_id_v1,
        root=repo_root,
        session=async_session,
    )

    assert ref_path.read_text().strip() == commit_id_v2


@pytest.mark.anyio
async def test_update_ref_cas_fails_when_current_differs(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """CAS exits USER_ERROR when old-value does not match current ref."""
    commit_id_v1 = await _insert_commit(async_session, repo_root, commit_suffix="b")
    commit_id_v2 = await _insert_commit(async_session, repo_root, commit_suffix="c")
    commit_id_v3 = await _insert_commit(async_session, repo_root, commit_suffix="e")

    # Prime the ref with v3 (not v1).
    ref_path = repo_root / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_id_v3)

    with pytest.raises(typer.Exit) as exc_info:
        await _update_ref_async(
            ref="refs/heads/main",
            new_value=commit_id_v2,
            old_value=commit_id_v1,  # expects v1, but current is v3
            root=repo_root,
            session=async_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR
    # Ref must not have been modified.
    assert ref_path.read_text().strip() == commit_id_v3


@pytest.mark.anyio
async def test_update_ref_cas_fails_when_ref_missing_and_old_value_given(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """CAS exits USER_ERROR when old-value is provided but the ref doesn't exist yet."""
    commit_id = await _insert_commit(async_session, repo_root)

    # Ref file does not exist — current is None.
    ref_path = repo_root / ".muse" / "refs" / "heads" / "new-branch"
    assert not ref_path.exists()

    with pytest.raises(typer.Exit) as exc_info:
        await _update_ref_async(
            ref="refs/heads/new-branch",
            new_value=commit_id,
            old_value="a" * 64,  # expects something, but ref is absent
            root=repo_root,
            session=async_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# _delete_ref_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_ref_removes_existing_ref(
    repo_root: pathlib.Path,
) -> None:
    """delete_ref removes the ref file when it exists."""
    ref_path = repo_root / ".muse" / "refs" / "heads" / "feature"
    ref_path.write_text("b" * 64)

    await _delete_ref_async(ref="refs/heads/feature", root=repo_root)

    assert not ref_path.exists()


@pytest.mark.anyio
async def test_delete_ref_removes_tag_ref(
    repo_root: pathlib.Path,
) -> None:
    """delete_ref works for tag refs (refs/tags/*)."""
    ref_path = repo_root / ".muse" / "refs" / "tags" / "v1.0"
    ref_path.write_text("c" * 64)

    await _delete_ref_async(ref="refs/tags/v1.0", root=repo_root)

    assert not ref_path.exists()


@pytest.mark.anyio
async def test_delete_ref_exits_user_error_when_missing(
    repo_root: pathlib.Path,
) -> None:
    """delete_ref exits USER_ERROR when the ref file does not exist."""
    with pytest.raises(typer.Exit) as exc_info:
        await _delete_ref_async(ref="refs/heads/ghost", root=repo_root)
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_delete_ref_exits_user_error_invalid_format(
    repo_root: pathlib.Path,
) -> None:
    """delete_ref exits USER_ERROR when the ref format is invalid."""
    with pytest.raises(typer.Exit) as exc_info:
        await _delete_ref_async(ref="HEAD", root=repo_root)
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Regression: update overwrites existing value
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_ref_overwrites_existing_value(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """A second call to update-ref overwrites the first value (no CAS)."""
    commit_id_v1 = await _insert_commit(async_session, repo_root, commit_suffix="b")
    commit_id_v2 = await _insert_commit(async_session, repo_root, commit_suffix="c")

    ref_path = repo_root / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_id_v1)

    await _update_ref_async(
        ref="refs/heads/main",
        new_value=commit_id_v2,
        old_value=None,
        root=repo_root,
        session=async_session,
    )
    assert ref_path.read_text().strip() == commit_id_v2


# ---------------------------------------------------------------------------
# Boundary seal
# ---------------------------------------------------------------------------


def test_update_ref_module_future_annotations_present() -> None:
    """update_ref.py must start with 'from __future__ import annotations'."""
    import maestro.muse_cli.commands.update_ref as module

    assert module.__file__ is not None
    source_path = pathlib.Path(module.__file__)
    tree = ast.parse(source_path.read_text())
    first_import = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        ),
        None,
    )
    assert first_import is not None, "Missing 'from __future__ import annotations'"
    names = [alias.name for alias in first_import.names]
    assert "annotations" in names
