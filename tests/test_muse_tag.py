"""Tests for ``muse tag`` — music-semantic tagging of commits.

Verifies:
- tag add: attaches a tag to a commit; idempotent on duplicate.
- tag remove: removes an existing tag; exits USER_ERROR when not found.
- tag list: returns sorted tags; prints "No tags" when empty.
- tag search: exact match and prefix (namespace) match.
- tag add: exits USER_ERROR when commit does not exist.
- tag add on HEAD resolved from .muse/HEAD when no commit_ref is given.
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.db.database import Base
from maestro.muse_cli import models as cli_models  # noqa: F401 — register tables
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot, MuseCliTag
from maestro.muse_cli.commands.tag import (
    _tag_add_async,
    _tag_list_async,
    _tag_remove_async,
    _tag_search_async,
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
    refs_dir = muse_dir / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def repo_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def write_repo_json(repo_root: pathlib.Path, repo_id: str) -> None:
    """Write .muse/repo.json with a stable repo_id."""
    (repo_root / ".muse" / "repo.json").write_text(json.dumps({"repo_id": repo_id}))


async def _insert_commit(
    session: AsyncSession,
    repo_id: str,
    repo_root: pathlib.Path,
) -> str:
    """Insert a minimal MuseCliCommit and return its commit_id.

    Also updates .muse/refs/heads/main so HEAD resolution works.
    """
    snapshot_id = "a" * 64
    commit_id = "b" * 64

    session.add(MuseCliObject(object_id="c" * 64, size_bytes=1))
    session.add(MuseCliSnapshot(snapshot_id=snapshot_id, manifest={"f.mid": "c" * 64}))
    await session.flush()

    session.add(
        MuseCliCommit(
            commit_id=commit_id,
            repo_id=repo_id,
            branch="main",
            parent_commit_id=None,
            parent2_commit_id=None,
            snapshot_id=snapshot_id,
            message="initial",
            author="",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    await session.flush()

    # Update HEAD pointer so _resolve_commit_id works without explicit ref
    ref_path = repo_root / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_id)

    return commit_id


# ---------------------------------------------------------------------------
# tag add
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tag_add_attaches_tag(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """tag add stores a MuseCliTag row for the target commit."""
    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    await _tag_add_async(
        tag="emotion:melancholic",
        commit_ref=commit_id,
        root=repo_root,
        session=async_session,
    )
    await async_session.flush()

    from sqlalchemy import select

    result = await async_session.execute(
        select(MuseCliTag).where(MuseCliTag.commit_id == commit_id)
    )
    tags = result.scalars().all()
    assert len(tags) == 1
    assert tags[0].tag == "emotion:melancholic"
    assert tags[0].repo_id == repo_id


@pytest.mark.anyio
async def test_tag_add_is_idempotent(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """Adding the same tag twice must not create a duplicate row."""
    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    await _tag_add_async(
        tag="stage:rough-mix",
        commit_ref=commit_id,
        root=repo_root,
        session=async_session,
    )
    await _tag_add_async(
        tag="stage:rough-mix",
        commit_ref=commit_id,
        root=repo_root,
        session=async_session,
    )
    await async_session.flush()

    from sqlalchemy import select

    result = await async_session.execute(
        select(MuseCliTag).where(
            MuseCliTag.commit_id == commit_id, MuseCliTag.tag == "stage:rough-mix"
        )
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.anyio
async def test_tag_add_missing_commit_exits_user_error(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """tag add on a non-existent commit must exit with USER_ERROR."""
    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _tag_add_async(
            tag="tempo:120bpm",
            commit_ref="d" * 64,
            root=repo_root,
            session=async_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_tag_add_uses_head_when_no_commit_ref(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """When commit_ref is None, the current HEAD commit is tagged."""
    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    await _tag_add_async(
        tag="key:Am",
        commit_ref=None,  # use HEAD
        root=repo_root,
        session=async_session,
    )
    await async_session.flush()

    from sqlalchemy import select

    result = await async_session.execute(
        select(MuseCliTag).where(MuseCliTag.commit_id == commit_id, MuseCliTag.tag == "key:Am")
    )
    assert result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# tag remove
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tag_remove_deletes_existing_tag(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """tag remove deletes the row and succeeds."""
    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    await _tag_add_async(
        tag="ref:beatles",
        commit_ref=commit_id,
        root=repo_root,
        session=async_session,
    )
    await async_session.flush()

    await _tag_remove_async(
        tag="ref:beatles",
        commit_ref=commit_id,
        root=repo_root,
        session=async_session,
    )
    await async_session.flush()

    from sqlalchemy import select

    result = await async_session.execute(
        select(MuseCliTag).where(MuseCliTag.commit_id == commit_id, MuseCliTag.tag == "ref:beatles")
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.anyio
async def test_tag_remove_missing_tag_exits_user_error(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """Removing a tag that was never added must exit with USER_ERROR."""
    import typer

    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    with pytest.raises(typer.Exit) as exc_info:
        await _tag_remove_async(
            tag="nonexistent",
            commit_ref=commit_id,
            root=repo_root,
            session=async_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# tag list
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tag_list_returns_sorted_tags(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """tag list returns all tags sorted alphabetically."""
    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    for t in ["stage:master", "emotion:hopeful", "tempo:90bpm"]:
        await _tag_add_async(
            tag=t,
            commit_ref=commit_id,
            root=repo_root,
            session=async_session,
        )
    await async_session.flush()

    tags = await _tag_list_async(commit_ref=commit_id, root=repo_root, session=async_session)
    assert tags == sorted(["stage:master", "emotion:hopeful", "tempo:90bpm"])


@pytest.mark.anyio
async def test_tag_list_empty_commit(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """tag list on a commit with no tags returns an empty list."""
    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    tags = await _tag_list_async(commit_ref=commit_id, root=repo_root, session=async_session)
    assert tags == []


# ---------------------------------------------------------------------------
# tag search
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tag_search_exact_match(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """tag search with an exact string returns matching (commit_id, tag) pairs."""
    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    await _tag_add_async(
        tag="emotion:melancholic",
        commit_ref=commit_id,
        root=repo_root,
        session=async_session,
    )
    await _tag_add_async(
        tag="stage:rough-mix",
        commit_ref=commit_id,
        root=repo_root,
        session=async_session,
    )
    await async_session.flush()

    pairs = await _tag_search_async(
        tag="emotion:melancholic", root=repo_root, session=async_session
    )
    assert pairs == [(commit_id, "emotion:melancholic")]


@pytest.mark.anyio
async def test_tag_search_prefix_match(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """tag search with a namespace prefix (trailing colon) finds all matching tags."""
    commit_id = await _insert_commit(async_session, repo_id, repo_root)

    for t in ["emotion:melancholic", "emotion:hopeful", "stage:rough-mix"]:
        await _tag_add_async(
            tag=t,
            commit_ref=commit_id,
            root=repo_root,
            session=async_session,
        )
    await async_session.flush()

    pairs = await _tag_search_async(tag="emotion:", root=repo_root, session=async_session)
    found_tags = {tag for _, tag in pairs}
    assert found_tags == {"emotion:melancholic", "emotion:hopeful"}


@pytest.mark.anyio
async def test_tag_search_no_results(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    repo_id: str,
    write_repo_json: None,
) -> None:
    """tag search returns empty list when no commits carry the requested tag."""
    await _insert_commit(async_session, repo_id, repo_root)

    pairs = await _tag_search_async(tag="ref:nobody", root=repo_root, session=async_session)
    assert pairs == []


@pytest.mark.anyio
async def test_tag_search_scoped_to_repo(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
    write_repo_json: None,
) -> None:
    """Tags from a different repo are not returned by search."""
    # Tag under first repo (already set up via write_repo_json)
    repo_id_1: str = json.loads((repo_root / ".muse" / "repo.json").read_text())["repo_id"]
    commit_id_1 = await _insert_commit(async_session, repo_id_1, repo_root)
    await _tag_add_async(
        tag="stage:master",
        commit_ref=commit_id_1,
        root=repo_root,
        session=async_session,
    )
    await async_session.flush()

    # Switch repo.json to a different repo_id, insert a second commit
    repo_id_2 = str(uuid.uuid4())
    (repo_root / ".muse" / "repo.json").write_text(json.dumps({"repo_id": repo_id_2}))

    snapshot_id_2 = "e" * 64
    commit_id_2 = "f" * 64
    async_session.add(MuseCliObject(object_id="g" * 64, size_bytes=1))
    async_session.add(MuseCliSnapshot(snapshot_id=snapshot_id_2, manifest={"x.mid": "g" * 64}))
    await async_session.flush()
    async_session.add(
        MuseCliCommit(
            commit_id=commit_id_2,
            repo_id=repo_id_2,
            branch="main",
            parent_commit_id=None,
            parent2_commit_id=None,
            snapshot_id=snapshot_id_2,
            message="second repo initial",
            author="",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    await async_session.flush()

    # Search from second repo — should NOT find the tag belonging to first repo
    pairs = await _tag_search_async(tag="stage:master", root=repo_root, session=async_session)
    assert pairs == []


# ---------------------------------------------------------------------------
# Boundary seal
# ---------------------------------------------------------------------------


def test_tag_module_future_annotations_present() -> None:
    """tag.py must start with 'from __future__ import annotations'."""
    import maestro.muse_cli.commands.tag as tag_module

    assert tag_module.__file__ is not None
    source_path = pathlib.Path(tag_module.__file__)
    tree = ast.parse(source_path.read_text())
    first_import = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
        ),
        None,
    )
    assert first_import is not None, "Missing 'from __future__ import annotations'"
    names = [alias.name for alias in first_import.names]
    assert "annotations" in names
