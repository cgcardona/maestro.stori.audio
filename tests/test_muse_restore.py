"""Tests for ``muse restore`` — surgical file-level restore from a snapshot.

Verifies:
- test_muse_restore_from_head                   — default restore from HEAD
- test_muse_restore_staged_equivalent           — --staged behaves like --worktree (current model)
- test_muse_restore_source_commit               — --source <commit> extracts from historical snapshot
- test_muse_restore_multiple_paths              — multiple paths restored in one call
- test_muse_restore_muse_work_prefix_stripped   — "muse-work/" prefix is normalised away
- test_muse_restore_errors_on_missing_path      — PathNotInSnapshotError when path absent
- test_muse_restore_source_missing_path         — PathNotInSnapshotError on historical commit
- test_muse_restore_missing_object_store        — MissingObjectError when blob absent
- test_muse_restore_ref_not_found               — unknown source ref exits USER_ERROR
- test_muse_restore_no_commits                  — branch with no commits exits USER_ERROR
- test_muse_restore_result_fields               — RestoreResult fields are correct
- test_muse_restore_result_frozen               — RestoreResult is immutable
- test_boundary_no_forbidden_imports            — AST boundary seal
- test_restore_service_has_future_import        — from __future__ import annotations present
- test_restore_command_has_future_import        — CLI command has future import
"""
from __future__ import annotations

import ast
import datetime
import json
import pathlib
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.db.database import Base
from maestro.muse_cli import models as cli_models  # noqa: F401 — register tables
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot
from maestro.services.muse_reset import MissingObjectError, object_store_path
from maestro.services.muse_restore import (
    PathNotInSnapshotError,
    RestoreResult,
    perform_restore,
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
def repo_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def repo_root(tmp_path: pathlib.Path, repo_id: str) -> pathlib.Path:
    """Minimal Muse repository structure with repo.json."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "HEAD").write_text("refs/heads/main")
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "refs" / "heads" / "main").write_text("")
    (muse_dir / "repo.json").write_text(json.dumps({"repo_id": repo_id}))
    return tmp_path


def _sha(prefix: str, length: int = 64) -> str:
    """Build a deterministic fake SHA of exactly *length* hex chars."""
    return (prefix * (length // len(prefix) + 1))[:length]


async def _add_commit(
    session: AsyncSession,
    *,
    repo_id: str,
    branch: str = "main",
    message: str = "commit",
    manifest: dict[str, str] | None = None,
    parent_commit_id: str | None = None,
    committed_at: datetime.datetime | None = None,
) -> MuseCliCommit:
    """Insert a commit + its snapshot into the in-memory DB and return the commit."""
    snapshot_id = _sha(str(uuid.uuid4()).replace("-", ""))
    commit_id = _sha(str(uuid.uuid4()).replace("-", ""))
    file_manifest: dict[str, str] = manifest or {"track.mid": _sha("ab")}

    for object_id in file_manifest.values():
        existing = await session.get(MuseCliObject, object_id)
        if existing is None:
            session.add(MuseCliObject(object_id=object_id, size_bytes=10))

    session.add(MuseCliSnapshot(snapshot_id=snapshot_id, manifest=file_manifest))
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


def _write_ref(root: pathlib.Path, branch: str, commit_id: str) -> None:
    """Update .muse/refs/heads/<branch> with *commit_id*."""
    ref_path = root / ".muse" / "refs" / "heads" / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id)


def _seed_object_store(root: pathlib.Path, object_id: str, content: bytes) -> None:
    """Manually write a blob into the .muse/objects/ store."""
    dest = object_store_path(root, object_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)


# ---------------------------------------------------------------------------
# Core restore tests
# ---------------------------------------------------------------------------


class TestRestoreFromHead:

    @pytest.mark.anyio
    async def test_muse_restore_from_head(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Default restore (no source) extracts the file from HEAD snapshot."""
        object_id = "aa" * 32
        content = b"MIDI bass take 7"
        _seed_object_store(repo_root, object_id, content)

        commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            message="take 7",
            manifest={"bass/bassline.mid": object_id},
        )
        _write_ref(repo_root, "main", commit.commit_id)

        # muse-work/ currently has stale content
        workdir = repo_root / "muse-work" / "bass"
        workdir.mkdir(parents=True)
        (workdir / "bassline.mid").write_bytes(b"stale content")

        result = await perform_restore(
            root=repo_root,
            session=async_session,
            paths=["bass/bassline.mid"],
            source_ref=None,
            staged=False,
        )

        assert result.source_commit_id == commit.commit_id
        assert result.paths_restored == ["bass/bassline.mid"]
        assert result.staged is False
        assert (repo_root / "muse-work" / "bass" / "bassline.mid").read_bytes() == content

    @pytest.mark.anyio
    async def test_muse_restore_staged_equivalent(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """--staged behaves identically to --worktree in the current Muse model."""
        object_id = "bb" * 32
        content = b"drums take 3"
        _seed_object_store(repo_root, object_id, content)

        commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            message="take 3",
            manifest={"drums/kick.mid": object_id},
        )
        _write_ref(repo_root, "main", commit.commit_id)

        workdir = repo_root / "muse-work" / "drums"
        workdir.mkdir(parents=True)
        (workdir / "kick.mid").write_bytes(b"wrong version")

        result = await perform_restore(
            root=repo_root,
            session=async_session,
            paths=["drums/kick.mid"],
            source_ref=None,
            staged=True,
        )

        assert result.staged is True
        assert result.paths_restored == ["drums/kick.mid"]
        assert (repo_root / "muse-work" / "drums" / "kick.mid").read_bytes() == content


class TestRestoreSourceCommit:

    @pytest.mark.anyio
    async def test_muse_restore_source_commit(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """--source <commit> restores a file from a historical snapshot."""
        obj_take3 = "cc" * 32
        content_take3 = b"bass take 3"
        _seed_object_store(repo_root, obj_take3, content_take3)

        obj_take7 = "dd" * 32
        content_take7 = b"bass take 7"
        _seed_object_store(repo_root, obj_take7, content_take7)

        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        take3 = await _add_commit(
            async_session,
            repo_id=repo_id,
            message="take 3",
            manifest={"bass/bassline.mid": obj_take3},
            committed_at=t0,
        )

        t1 = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        take7 = await _add_commit(
            async_session,
            repo_id=repo_id,
            message="take 7",
            manifest={"bass/bassline.mid": obj_take7},
            parent_commit_id=take3.commit_id,
            committed_at=t1,
        )
        _write_ref(repo_root, "main", take7.commit_id)

        # muse-work/ currently has take7 content
        workdir = repo_root / "muse-work" / "bass"
        workdir.mkdir(parents=True)
        (workdir / "bassline.mid").write_bytes(content_take7)

        # Restore take3's bass file while HEAD is at take7
        result = await perform_restore(
            root=repo_root,
            session=async_session,
            paths=["bass/bassline.mid"],
            source_ref=take3.commit_id,
            staged=False,
        )

        assert result.source_commit_id == take3.commit_id
        assert result.paths_restored == ["bass/bassline.mid"]
        # The bass file is now take3's content
        assert (repo_root / "muse-work" / "bass" / "bassline.mid").read_bytes() == content_take3

    @pytest.mark.anyio
    async def test_muse_restore_source_abbreviated_sha(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """An abbreviated SHA is accepted as a --source ref."""
        object_id = "ee" * 32
        content = b"abbreviated"
        _seed_object_store(repo_root, object_id, content)

        commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            message="v1",
            manifest={"track.mid": object_id},
        )
        _write_ref(repo_root, "main", commit.commit_id)

        workdir = repo_root / "muse-work"
        workdir.mkdir()
        (workdir / "track.mid").write_bytes(b"old")

        result = await perform_restore(
            root=repo_root,
            session=async_session,
            paths=["track.mid"],
            source_ref=commit.commit_id[:10],
            staged=False,
        )

        assert result.source_commit_id == commit.commit_id
        assert (repo_root / "muse-work" / "track.mid").read_bytes() == content


class TestRestoreMultiplePaths:

    @pytest.mark.anyio
    async def test_muse_restore_multiple_paths(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Multiple paths are restored atomically in one call."""
        obj_bass = "11" * 32
        obj_drums = "22" * 32
        _seed_object_store(repo_root, obj_bass, b"bass v1")
        _seed_object_store(repo_root, obj_drums, b"drums v1")

        commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            message="v1",
            manifest={"bass.mid": obj_bass, "drums.mid": obj_drums},
        )
        _write_ref(repo_root, "main", commit.commit_id)

        workdir = repo_root / "muse-work"
        workdir.mkdir()
        (workdir / "bass.mid").write_bytes(b"stale")
        (workdir / "drums.mid").write_bytes(b"stale")

        result = await perform_restore(
            root=repo_root,
            session=async_session,
            paths=["bass.mid", "drums.mid"],
            source_ref=None,
            staged=False,
        )

        assert sorted(result.paths_restored) == ["bass.mid", "drums.mid"]
        assert (workdir / "bass.mid").read_bytes() == b"bass v1"
        assert (workdir / "drums.mid").read_bytes() == b"drums v1"

    @pytest.mark.anyio
    async def test_muse_restore_muse_work_prefix_stripped(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Paths given with 'muse-work/' prefix are normalised correctly."""
        object_id = "33" * 32
        content = b"prefix test"
        _seed_object_store(repo_root, object_id, content)

        commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            manifest={"lead.mid": object_id},
        )
        _write_ref(repo_root, "main", commit.commit_id)

        workdir = repo_root / "muse-work"
        workdir.mkdir()
        (workdir / "lead.mid").write_bytes(b"old")

        result = await perform_restore(
            root=repo_root,
            session=async_session,
            paths=["muse-work/lead.mid"],  # with prefix
            source_ref=None,
            staged=False,
        )

        assert result.paths_restored == ["lead.mid"]
        assert (workdir / "lead.mid").read_bytes() == content


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestRestoreErrors:

    @pytest.mark.anyio
    async def test_muse_restore_errors_on_missing_path(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """PathNotInSnapshotError raised when path absent from HEAD snapshot."""
        object_id = "44" * 32
        _seed_object_store(repo_root, object_id, b"only track")

        commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            manifest={"track.mid": object_id},
        )
        _write_ref(repo_root, "main", commit.commit_id)

        with pytest.raises(PathNotInSnapshotError) as exc_info:
            await perform_restore(
                root=repo_root,
                session=async_session,
                paths=["nonexistent.mid"],
                source_ref=None,
                staged=False,
            )

        assert "nonexistent.mid" in str(exc_info.value)
        assert exc_info.value.rel_path == "nonexistent.mid"
        assert exc_info.value.source_commit_id == commit.commit_id

    @pytest.mark.anyio
    async def test_muse_restore_source_missing_path(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """PathNotInSnapshotError when path absent from historical commit."""
        obj_old = "55" * 32
        _seed_object_store(repo_root, obj_old, b"old track")

        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        old_commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            message="old",
            manifest={"old_track.mid": obj_old},
            committed_at=t0,
        )

        obj_new = "66" * 32
        _seed_object_store(repo_root, obj_new, b"new track")
        t1 = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        new_commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            message="new",
            manifest={"new_track.mid": obj_new},
            parent_commit_id=old_commit.commit_id,
            committed_at=t1,
        )
        _write_ref(repo_root, "main", new_commit.commit_id)

        with pytest.raises(PathNotInSnapshotError) as exc_info:
            await perform_restore(
                root=repo_root,
                session=async_session,
                paths=["new_track.mid"],  # not in old_commit's snapshot
                source_ref=old_commit.commit_id,
                staged=False,
            )

        assert "new_track.mid" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_muse_restore_missing_object_store(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """MissingObjectError raised when blob absent from object store."""
        missing_id = "77" * 32
        # Intentionally NOT seeding the object store

        commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            manifest={"lead.mid": missing_id},
        )
        _write_ref(repo_root, "main", commit.commit_id)

        with pytest.raises(MissingObjectError) as exc_info:
            await perform_restore(
                root=repo_root,
                session=async_session,
                paths=["lead.mid"],
                source_ref=None,
                staged=False,
            )

        assert missing_id[:8] in str(exc_info.value)

    @pytest.mark.anyio
    async def test_muse_restore_ref_not_found(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """An unknown source ref exits with USER_ERROR via typer.Exit."""
        import typer

        object_id = "88" * 32
        _seed_object_store(repo_root, object_id, b"content")
        commit = await _add_commit(
            async_session,
            repo_id=repo_id,
            manifest={"track.mid": object_id},
        )
        _write_ref(repo_root, "main", commit.commit_id)

        with pytest.raises(typer.Exit) as exc_info:
            await perform_restore(
                root=repo_root,
                session=async_session,
                paths=["track.mid"],
                source_ref="deadbeef1234",
                staged=False,
            )

        assert exc_info.value.exit_code == ExitCode.USER_ERROR

    @pytest.mark.anyio
    async def test_muse_restore_no_commits(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Branch with no commits exits with USER_ERROR."""
        import typer

        # repo_root fixture has empty main ref
        with pytest.raises(typer.Exit) as exc_info:
            await perform_restore(
                root=repo_root,
                session=async_session,
                paths=["track.mid"],
                source_ref=None,
                staged=False,
            )

        assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# RestoreResult type
# ---------------------------------------------------------------------------


class TestRestoreResult:

    def test_muse_restore_result_fields(self) -> None:
        """RestoreResult carries commit ID, paths, and staged flag."""
        r = RestoreResult(
            source_commit_id="a" * 64,
            paths_restored=["bass.mid", "drums.mid"],
            staged=True,
        )
        assert r.source_commit_id == "a" * 64
        assert r.paths_restored == ["bass.mid", "drums.mid"]
        assert r.staged is True

    def test_muse_restore_result_frozen(self) -> None:
        """RestoreResult is immutable (frozen dataclass)."""
        r = RestoreResult(source_commit_id="b" * 64)
        with pytest.raises(Exception):
            r.staged = True  # type: ignore[misc]

    def test_muse_restore_result_defaults(self) -> None:
        """RestoreResult has sensible defaults for optional fields."""
        r = RestoreResult(source_commit_id="c" * 64)
        assert r.paths_restored == []
        assert r.staged is False


# ---------------------------------------------------------------------------
# PathNotInSnapshotError type
# ---------------------------------------------------------------------------


class TestPathNotInSnapshotError:

    def test_error_message_contains_path_and_commit(self) -> None:
        """Error message references the missing path and abbreviated commit ID."""
        exc = PathNotInSnapshotError("bass/bassline.mid", "abcd1234" * 8)
        msg = str(exc)
        assert "bass/bassline.mid" in msg
        assert "abcd1234" in msg


# ---------------------------------------------------------------------------
# Boundary seal — AST checks
# ---------------------------------------------------------------------------


class TestBoundarySeals:

    def _parse(self, rel_path: str) -> ast.Module:
        root = pathlib.Path(__file__).resolve().parent.parent
        return ast.parse((root / rel_path).read_text())

    def test_boundary_no_forbidden_imports(self) -> None:
        """muse_restore service must not import executor, state_store, mcp, or maestro_handlers."""
        tree = self._parse("maestro/services/muse_restore.py")
        forbidden = {"state_store", "executor", "maestro_handlers", "mcp"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_restore imports forbidden module: {node.module}"
                    )

    def test_restore_service_has_future_import(self) -> None:
        """muse_restore.py starts with 'from __future__ import annotations'."""
        tree = self._parse("maestro/services/muse_restore.py")
        first_import = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)),
            None,
        )
        assert first_import is not None
        assert first_import.module == "__future__"

    def test_restore_command_has_future_import(self) -> None:
        """restore.py CLI command starts with 'from __future__ import annotations'."""
        tree = self._parse("maestro/muse_cli/commands/restore.py")
        first_import = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)),
            None,
        )
        assert first_import is not None
        assert first_import.module == "__future__"
