"""Tests for ``muse reset`` — branch pointer reset with soft/mixed/hard modes.

Verifies:
- test_muse_reset_soft_moves_ref_only             — soft: ref moved, muse-work/ unchanged
- test_muse_reset_mixed_resets_index              — mixed: behaves like soft in current model
- test_muse_reset_hard_overwrites_worktree        — hard: ref moved + files restored
- test_muse_reset_hard_deletes_extra_files        — hard: files not in target snapshot deleted
- test_muse_reset_head_minus_n                    — HEAD~N syntax resolves correctly
- test_muse_reset_head_minus_n_too_far            — HEAD~N beyond root returns error
- test_muse_reset_blocked_during_merge            — blocked when MERGE_STATE.json exists
- test_muse_reset_hard_missing_object             — hard fails cleanly on missing blob
- test_muse_reset_ref_not_found                  — unknown ref returns USER_ERROR
- test_muse_reset_hard_confirmation               — hard prompts for confirmation
- test_muse_reset_abbreviated_sha                 — abbreviated SHA prefix resolves
- test_store_object_idempotent                    — store_object is idempotent
- test_resolve_ref_head                           — resolve_ref("HEAD")
- test_resolve_ref_head_tilde_zero                — resolve_ref("HEAD~0") = HEAD
- test_boundary_no_forbidden_imports              — AST boundary seal
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
from maestro.muse_cli.merge_engine import write_merge_state
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot
from maestro.services.muse_reset import (
    MissingObjectError,
    ResetMode,
    ResetResult,
    object_store_path,
    perform_reset,
    resolve_ref,
    store_object,
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


def _read_ref(root: pathlib.Path, branch: str = "main") -> str:
    """Read the current commit SHA from .muse/refs/heads/<branch>."""
    return (root / ".muse" / "refs" / "heads" / branch).read_text().strip()


def _seed_object_store(root: pathlib.Path, object_id: str, content: bytes) -> None:
    """Manually write a blob into the .muse/objects/ store."""
    dest = object_store_path(root, object_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)


# ---------------------------------------------------------------------------
# Unit tests — pure helpers
# ---------------------------------------------------------------------------


class TestStoreObject:

    def test_store_object_idempotent(self, tmp_path: pathlib.Path) -> None:
        """store_object() does not raise or overwrite when called twice."""
        root = tmp_path
        (root / ".muse").mkdir()
        object_id = "a" * 64
        src = tmp_path / "file.mid"
        src.write_bytes(b"MIDI content")

        store_object(root, object_id, src)
        dest = object_store_path(root, object_id)
        assert dest.exists()
        mtime_first = dest.stat().st_mtime

        store_object(root, object_id, src)
        assert dest.stat().st_mtime == mtime_first  # not touched


class TestObjectStorePath:

    def test_sharded_by_first_two_chars(self, tmp_path: pathlib.Path) -> None:
        """object_store_path shards by the first two hex characters."""
        root = tmp_path
        object_id = "ab" + "cd" * 31
        result = object_store_path(root, object_id)
        assert result.parent.name == "ab"
        assert result.name == object_id[2:]


# ---------------------------------------------------------------------------
# resolve_ref tests
# ---------------------------------------------------------------------------


class TestResolveRef:

    @pytest.mark.anyio
    async def test_resolve_ref_head(
        self, async_session: AsyncSession, repo_id: str, repo_root: pathlib.Path
    ) -> None:
        """resolve_ref('HEAD') returns the most recent commit on the branch."""
        c1 = await _add_commit(async_session, repo_id=repo_id, message="first")
        _write_ref(repo_root, "main", c1.commit_id)
        result = await resolve_ref(async_session, repo_id, "main", "HEAD")
        assert result is not None
        assert result.commit_id == c1.commit_id

    @pytest.mark.anyio
    async def test_resolve_ref_head_tilde_zero(
        self, async_session: AsyncSession, repo_id: str, repo_root: pathlib.Path
    ) -> None:
        """HEAD~0 resolves to HEAD itself."""
        c1 = await _add_commit(async_session, repo_id=repo_id, message="first")
        result = await resolve_ref(async_session, repo_id, "main", "HEAD~0")
        assert result is not None
        assert result.commit_id == c1.commit_id

    @pytest.mark.anyio
    async def test_resolve_ref_head_tilde_one(
        self, async_session: AsyncSession, repo_id: str
    ) -> None:
        """HEAD~1 resolves to the parent commit."""
        import datetime
        t0 = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        c1 = await _add_commit(async_session, repo_id=repo_id, message="parent",
                               committed_at=t0)
        t1 = datetime.datetime(2024, 1, 2, 0, 0, 0, tzinfo=datetime.timezone.utc)
        c2 = await _add_commit(async_session, repo_id=repo_id, message="child",
                               parent_commit_id=c1.commit_id, committed_at=t1)

        result = await resolve_ref(async_session, repo_id, "main", "HEAD~1")
        assert result is not None
        assert result.commit_id == c1.commit_id

    @pytest.mark.anyio
    async def test_resolve_ref_abbreviated_sha(
        self, async_session: AsyncSession, repo_id: str
    ) -> None:
        """A SHA prefix resolves to the matching commit."""
        c1 = await _add_commit(async_session, repo_id=repo_id, message="first")
        prefix = c1.commit_id[:8]
        result = await resolve_ref(async_session, repo_id, "main", prefix)
        assert result is not None
        assert result.commit_id == c1.commit_id

    @pytest.mark.anyio
    async def test_resolve_ref_full_sha(
        self, async_session: AsyncSession, repo_id: str
    ) -> None:
        """A full 64-char SHA resolves to the matching commit."""
        c1 = await _add_commit(async_session, repo_id=repo_id, message="first")
        result = await resolve_ref(async_session, repo_id, "main", c1.commit_id)
        assert result is not None
        assert result.commit_id == c1.commit_id

    @pytest.mark.anyio
    async def test_resolve_ref_nonexistent_returns_none(
        self, async_session: AsyncSession, repo_id: str
    ) -> None:
        """An unknown ref returns None."""
        result = await resolve_ref(async_session, repo_id, "main", "deadbeef")
        assert result is None


# ---------------------------------------------------------------------------
# perform_reset — soft / mixed
# ---------------------------------------------------------------------------


class TestResetSoft:

    @pytest.mark.anyio
    async def test_muse_reset_soft_moves_ref_only(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Soft reset moves the branch ref; muse-work/ is untouched."""
        import datetime
        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        t1 = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        c1 = await _add_commit(async_session, repo_id=repo_id, message="v1", committed_at=t0)
        c2 = await _add_commit(async_session, repo_id=repo_id, message="v2",
                               parent_commit_id=c1.commit_id, committed_at=t1)
        _write_ref(repo_root, "main", c2.commit_id)

        # Create a file in muse-work/ that should NOT be touched
        workdir = repo_root / "muse-work"
        workdir.mkdir()
        sentinel = workdir / "sentinel.mid"
        sentinel.write_bytes(b"untouched")

        result = await perform_reset(
            root=repo_root,
            session=async_session,
            ref=c1.commit_id,
            mode=ResetMode.SOFT,
        )

        assert result.target_commit_id == c1.commit_id
        assert result.mode is ResetMode.SOFT
        assert result.branch == "main"
        assert result.files_restored == 0
        assert result.files_deleted == 0
        # Ref updated
        assert _read_ref(repo_root) == c1.commit_id
        # muse-work/ untouched
        assert sentinel.read_bytes() == b"untouched"


class TestResetMixed:

    @pytest.mark.anyio
    async def test_muse_reset_mixed_resets_index(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Mixed reset (default) behaves like soft in the current Muse model."""
        import datetime
        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        t1 = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        c1 = await _add_commit(async_session, repo_id=repo_id, message="v1", committed_at=t0)
        c2 = await _add_commit(async_session, repo_id=repo_id, message="v2",
                               parent_commit_id=c1.commit_id, committed_at=t1)
        _write_ref(repo_root, "main", c2.commit_id)

        workdir = repo_root / "muse-work"
        workdir.mkdir()
        (workdir / "track.mid").write_bytes(b"original")

        result = await perform_reset(
            root=repo_root,
            session=async_session,
            ref=c1.commit_id,
            mode=ResetMode.MIXED,
        )

        assert result.target_commit_id == c1.commit_id
        assert result.mode is ResetMode.MIXED
        assert _read_ref(repo_root) == c1.commit_id
        # Files untouched
        assert (workdir / "track.mid").read_bytes() == b"original"


# ---------------------------------------------------------------------------
# perform_reset — hard
# ---------------------------------------------------------------------------


class TestResetHard:

    @pytest.mark.anyio
    async def test_muse_reset_hard_overwrites_worktree(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Hard reset restores muse-work/ files from the target snapshot."""
        import datetime
        object_id_v1 = "11" * 32
        content_v1 = b"MIDI v1"
        _seed_object_store(repo_root, object_id_v1, content_v1)

        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        c1 = await _add_commit(
            async_session, repo_id=repo_id, message="v1",
            manifest={"track.mid": object_id_v1}, committed_at=t0,
        )

        object_id_v2 = "22" * 32
        content_v2 = b"MIDI v2 - newer"
        _seed_object_store(repo_root, object_id_v2, content_v2)

        t1 = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        c2 = await _add_commit(
            async_session, repo_id=repo_id, message="v2",
            manifest={"track.mid": object_id_v2},
            parent_commit_id=c1.commit_id, committed_at=t1,
        )
        _write_ref(repo_root, "main", c2.commit_id)

        # Current working tree has v2 content
        workdir = repo_root / "muse-work"
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "track.mid").write_bytes(content_v2)

        result = await perform_reset(
            root=repo_root,
            session=async_session,
            ref=c1.commit_id,
            mode=ResetMode.HARD,
        )

        assert result.target_commit_id == c1.commit_id
        assert result.mode is ResetMode.HARD
        assert result.files_restored == 1
        assert _read_ref(repo_root) == c1.commit_id
        # muse-work/ now contains v1 content
        assert (workdir / "track.mid").read_bytes() == content_v1

    @pytest.mark.anyio
    async def test_muse_reset_hard_deletes_extra_files(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Hard reset deletes files in muse-work/ not present in target snapshot."""
        import datetime
        object_id = "33" * 32
        _seed_object_store(repo_root, object_id, b"bass only")

        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        c1 = await _add_commit(
            async_session, repo_id=repo_id, message="bass only",
            manifest={"bass.mid": object_id}, committed_at=t0,
        )
        _write_ref(repo_root, "main", c1.commit_id)

        workdir = repo_root / "muse-work"
        workdir.mkdir()
        (workdir / "bass.mid").write_bytes(b"bass only")
        (workdir / "extra.mid").write_bytes(b"should be deleted")

        result = await perform_reset(
            root=repo_root,
            session=async_session,
            ref=c1.commit_id,
            mode=ResetMode.HARD,
        )

        assert result.files_deleted == 1
        assert (workdir / "bass.mid").exists()
        assert not (workdir / "extra.mid").exists()

    @pytest.mark.anyio
    async def test_muse_reset_hard_missing_object(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Hard reset raises MissingObjectError when blob is absent from object store."""
        import datetime
        missing_object_id = "ff" * 32
        # Intentionally NOT seeding the object store

        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        c1 = await _add_commit(
            async_session, repo_id=repo_id, message="v1",
            manifest={"lead.mid": missing_object_id}, committed_at=t0,
        )
        _write_ref(repo_root, "main", c1.commit_id)

        with pytest.raises(MissingObjectError) as exc_info:
            await perform_reset(
                root=repo_root,
                session=async_session,
                ref=c1.commit_id,
                mode=ResetMode.HARD,
            )
        assert missing_object_id[:8] in str(exc_info.value)


# ---------------------------------------------------------------------------
# HEAD~N syntax
# ---------------------------------------------------------------------------


class TestResetHeadMinusN:

    @pytest.mark.anyio
    async def test_muse_reset_head_minus_n(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """HEAD~2 walks back two parents from the most recent commit."""
        import datetime
        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        t1 = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        t2 = datetime.datetime(2024, 1, 3, tzinfo=datetime.timezone.utc)
        c0 = await _add_commit(async_session, repo_id=repo_id, message="root", committed_at=t0)
        c1 = await _add_commit(async_session, repo_id=repo_id, message="child",
                               parent_commit_id=c0.commit_id, committed_at=t1)
        c2 = await _add_commit(async_session, repo_id=repo_id, message="grandchild",
                               parent_commit_id=c1.commit_id, committed_at=t2)
        _write_ref(repo_root, "main", c2.commit_id)

        result = await perform_reset(
            root=repo_root,
            session=async_session,
            ref="HEAD~2",
            mode=ResetMode.SOFT,
        )

        assert result.target_commit_id == c0.commit_id
        assert _read_ref(repo_root) == c0.commit_id

    @pytest.mark.anyio
    async def test_muse_reset_head_minus_n_too_far(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """HEAD~N where N exceeds history depth surfaces USER_ERROR via typer.Exit."""
        import datetime
        import typer
        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        c0 = await _add_commit(async_session, repo_id=repo_id, message="root", committed_at=t0)
        _write_ref(repo_root, "main", c0.commit_id)

        with pytest.raises(typer.Exit) as exc_info:
            await perform_reset(
                root=repo_root,
                session=async_session,
                ref="HEAD~5",  # only 0 parents exist
                mode=ResetMode.SOFT,
            )
        assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Merge-in-progress guard
# ---------------------------------------------------------------------------


class TestResetBlockedDuringMerge:

    @pytest.mark.anyio
    async def test_muse_reset_blocked_during_merge(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """Reset is blocked when MERGE_STATE.json exists."""
        import datetime
        import typer
        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        c0 = await _add_commit(async_session, repo_id=repo_id, message="root", committed_at=t0)
        _write_ref(repo_root, "main", c0.commit_id)

        # Simulate an in-progress merge
        write_merge_state(
            repo_root,
            base_commit=c0.commit_id,
            ours_commit=c0.commit_id,
            theirs_commit="x" * 64,
            conflict_paths=["bass.mid"],
        )

        with pytest.raises(typer.Exit) as exc_info:
            await perform_reset(
                root=repo_root,
                session=async_session,
                ref=c0.commit_id,
                mode=ResetMode.SOFT,
            )
        assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Ref resolution edge cases
# ---------------------------------------------------------------------------


class TestResetRefNotFound:

    @pytest.mark.anyio
    async def test_muse_reset_ref_not_found(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """An unknown ref string exits with USER_ERROR."""
        import datetime
        import typer
        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        c0 = await _add_commit(async_session, repo_id=repo_id, message="root", committed_at=t0)
        _write_ref(repo_root, "main", c0.commit_id)

        with pytest.raises(typer.Exit) as exc_info:
            await perform_reset(
                root=repo_root,
                session=async_session,
                ref="nonexistent-ref",
                mode=ResetMode.SOFT,
            )
        assert exc_info.value.exit_code == ExitCode.USER_ERROR

    @pytest.mark.anyio
    async def test_muse_reset_abbreviated_sha(
        self,
        async_session: AsyncSession,
        repo_id: str,
        repo_root: pathlib.Path,
    ) -> None:
        """An abbreviated SHA prefix resolves to the correct commit."""
        import datetime
        t0 = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        t1 = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        c1 = await _add_commit(async_session, repo_id=repo_id, message="first", committed_at=t0)
        c2 = await _add_commit(async_session, repo_id=repo_id, message="second",
                               parent_commit_id=c1.commit_id, committed_at=t1)
        _write_ref(repo_root, "main", c2.commit_id)

        result = await perform_reset(
            root=repo_root,
            session=async_session,
            ref=c1.commit_id[:12],  # abbreviated prefix
            mode=ResetMode.SOFT,
        )

        assert result.target_commit_id == c1.commit_id


# ---------------------------------------------------------------------------
# ResetResult type
# ---------------------------------------------------------------------------


class TestResetResult:

    def test_reset_result_defaults(self) -> None:
        """ResetResult has sensible defaults for files_restored and files_deleted."""
        r = ResetResult(
            target_commit_id="a" * 64,
            mode=ResetMode.SOFT,
            branch="main",
        )
        assert r.files_restored == 0
        assert r.files_deleted == 0

    def test_reset_result_frozen(self) -> None:
        """ResetResult is immutable (frozen dataclass)."""
        r = ResetResult(
            target_commit_id="a" * 64,
            mode=ResetMode.HARD,
            branch="main",
            files_restored=5,
            files_deleted=2,
        )
        with pytest.raises(Exception):
            r.files_restored = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Boundary seal — AST checks
# ---------------------------------------------------------------------------


class TestBoundarySeals:

    def _parse(self, rel_path: str) -> ast.Module:
        root = pathlib.Path(__file__).resolve().parent.parent
        return ast.parse((root / rel_path).read_text())

    def test_boundary_no_forbidden_imports(self) -> None:
        """muse_reset service must not import executor, state_store, mcp, or maestro_handlers."""
        tree = self._parse("maestro/services/muse_reset.py")
        forbidden = {"state_store", "executor", "maestro_handlers", "mcp"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for fb in forbidden:
                    assert fb not in node.module, (
                        f"muse_reset imports forbidden module: {node.module}"
                    )

    def test_reset_service_has_future_import(self) -> None:
        """muse_reset.py starts with 'from __future__ import annotations'."""
        tree = self._parse("maestro/services/muse_reset.py")
        first_import = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)),
            None,
        )
        assert first_import is not None
        assert first_import.module == "__future__"

    def test_reset_command_has_future_import(self) -> None:
        """reset.py CLI command starts with 'from __future__ import annotations'."""
        tree = self._parse("maestro/muse_cli/commands/reset.py")
        first_import = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)),
            None,
        )
        assert first_import is not None
        assert first_import.module == "__future__"
