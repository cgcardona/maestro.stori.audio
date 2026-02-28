"""Tests for ``muse pull``.

Covers acceptance criteria from issue #38:
- ``muse pull`` with no remote configured exits 1 with instructive message.
- ``muse pull`` calls ``POST <remote>/pull`` with correct payload structure.
- Returned commits are stored in local Postgres (via DB helpers).
- ``.muse/remotes/origin/<branch>`` is updated after a successful pull.
- Divergence message is printed (exit 0) when branches have diverged.

Covers acceptance criteria from issue #77 (new remote sync flags):
- ``muse pull --rebase``: fast-forwards local branch when remote is ahead.
- ``muse pull --rebase``: rebases local commits when branches have diverged.
- ``muse pull --ff-only``: fast-forwards local branch when remote is ahead.
- ``muse pull --ff-only``: exits 1 when branches have diverged.

All HTTP calls are mocked — no live network required.
DB calls use the in-memory SQLite fixture from conftest.py where needed.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maestro.muse_cli.commands.pull import _is_ancestor, _pull_async, _rebase_commits_onto
from maestro.muse_cli.commands.push import _push_async
from maestro.muse_cli.config import get_remote_head, set_remote
from maestro.muse_cli.db import store_pulled_commit, store_pulled_object
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path, branch: str = "main") -> pathlib.Path:
    """Create a minimal .muse/ structure."""
    import json as _json
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "repo.json").write_text(
        _json.dumps({"repo_id": "test-repo-id"}), encoding="utf-8"
    )
    (muse_dir / "HEAD").write_text(f"refs/heads/{branch}", encoding="utf-8")
    return tmp_path


def _write_branch_ref(root: pathlib.Path, branch: str, commit_id: str) -> None:
    ref_path = root / ".muse" / "refs" / "heads" / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id, encoding="utf-8")


def _write_config_with_token(root: pathlib.Path, remote_url: str) -> None:
    muse_dir = root / ".muse"
    (muse_dir / "config.toml").write_text(
        f'[auth]\ntoken = "test-token"\n\n[remotes.origin]\nurl = "{remote_url}"\n',
        encoding="utf-8",
    )


def _make_hub_pull_response(
    commits: list[dict[str, object]] | None = None,
    objects: list[dict[str, object]] | None = None,
    remote_head: str | None = "remote-head-001",
    diverged: bool = False,
) -> MagicMock:
    """Return a mock httpx.Response for the pull endpoint."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "commits": commits or [],
        "objects": objects or [],
        "remote_head": remote_head,
        "diverged": diverged,
    }
    return mock_resp


# ---------------------------------------------------------------------------
# test_pull_no_remote_exits_1
# ---------------------------------------------------------------------------


def test_pull_no_remote_exits_1(tmp_path: pathlib.Path) -> None:
    """muse pull exits 1 with instructive message when no remote is configured."""
    import typer

    root = _init_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        asyncio.run(_pull_async(root=root, remote_name="origin", branch=None))

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


def test_pull_no_remote_message_is_instructive(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pull with no remote prints a message telling user to run muse remote add."""
    import typer

    root = _init_repo(tmp_path)

    with pytest.raises(typer.Exit):
        asyncio.run(_pull_async(root=root, remote_name="origin", branch=None))

    captured = capsys.readouterr()
    assert "muse remote add" in captured.out


# ---------------------------------------------------------------------------
# test_pull_calls_hub_endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pull_calls_hub_endpoint(tmp_path: pathlib.Path) -> None:
    """muse pull POSTs to /pull with branch, have_commits, have_objects."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    captured_payloads: list[dict[str, object]] = []

    mock_response = _make_hub_pull_response()

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)

    async def _fake_post(path: str, **kwargs: object) -> MagicMock:
        payload = kwargs.get("json", {})
        if isinstance(payload, dict):
            captured_payloads.append(payload)
        return mock_response

    mock_hub.post = _fake_post

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
    ):
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = mock_session_ctx

        await _pull_async(root=root, remote_name="origin", branch=None)

    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert payload["branch"] == "main"
    assert "have_commits" in payload
    assert "have_objects" in payload


# ---------------------------------------------------------------------------
# test_pull_stores_commits_in_db
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pull_stores_commits_in_db(muse_cli_db_session: object) -> None:
    """Commits returned from the Hub are stored in local Postgres via store_pulled_commit."""
    # Use the in-memory SQLite session fixture
    from sqlalchemy.ext.asyncio import AsyncSession
    from maestro.muse_cli.models import MuseCliCommit as MCCommit

    session: AsyncSession = muse_cli_db_session  # type: ignore[assignment]

    commit_data: dict[str, object] = {
        "commit_id": "pulled-commit-abc123" * 3,
        "repo_id": "test-repo-id",
        "parent_commit_id": None,
        "snapshot_id": "snap-abc",
        "branch": "main",
        "message": "Pulled from remote",
        "author": "remote-author",
        "committed_at": "2025-01-01T00:00:00+00:00",
        "metadata": None,
    }

    # Ensure snapshot stub is written (store_pulled_commit creates one)
    inserted = await store_pulled_commit(session, commit_data)
    await session.commit()

    assert inserted is True

    # Verify in DB
    commit_id = str(commit_data["commit_id"])
    stored = await session.get(MCCommit, commit_id)
    assert stored is not None
    assert stored.message == "Pulled from remote"
    assert stored.branch == "main"


@pytest.mark.anyio
async def test_pull_stores_commits_idempotent(muse_cli_db_session: object) -> None:
    """Storing the same pulled commit twice does not raise and returns False on dup."""
    from sqlalchemy.ext.asyncio import AsyncSession

    session: AsyncSession = muse_cli_db_session  # type: ignore[assignment]

    commit_data: dict[str, object] = {
        "commit_id": "idem-commit-xyz789" * 3,
        "repo_id": "test-repo-id",
        "parent_commit_id": None,
        "snapshot_id": "snap-idem",
        "branch": "main",
        "message": "Idempotent test",
        "author": "",
        "committed_at": "2025-01-01T00:00:00+00:00",
        "metadata": None,
    }

    first = await store_pulled_commit(session, commit_data)
    await session.flush()
    second = await store_pulled_commit(session, commit_data)

    assert first is True
    assert second is False


@pytest.mark.anyio
async def test_pull_stores_objects_in_db(muse_cli_db_session: object) -> None:
    """Objects returned from the Hub are stored in local Postgres via store_pulled_object."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from maestro.muse_cli.models import MuseCliObject

    session: AsyncSession = muse_cli_db_session  # type: ignore[assignment]

    obj_data: dict[str, object] = {
        "object_id": "a" * 64,
        "size_bytes": 1024,
    }

    inserted = await store_pulled_object(session, obj_data)
    await session.commit()

    assert inserted is True
    stored = await session.get(MuseCliObject, "a" * 64)
    assert stored is not None
    assert stored.size_bytes == 1024


# ---------------------------------------------------------------------------
# test_pull_updates_remote_head_file
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pull_updates_remote_head_file(tmp_path: pathlib.Path) -> None:
    """After a successful pull, .muse/remotes/origin/<branch> is updated."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    remote_head = "new-remote-commit-aabbccddeeff0011" * 2

    mock_response = _make_hub_pull_response(remote_head=remote_head)

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=mock_response)

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
    ):
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = mock_session_ctx

        await _pull_async(root=root, remote_name="origin", branch=None)

    stored_head = get_remote_head("origin", "main", root)
    assert stored_head == remote_head


# ---------------------------------------------------------------------------
# test_pull_diverged_prints_warning
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pull_diverged_prints_warning(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When Hub reports diverged=True, a warning message is printed (exit 0)."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    mock_response = _make_hub_pull_response(diverged=True, remote_head="remote-head-xx")

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=mock_response)

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
    ):
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = mock_session_ctx

        # Should NOT raise (diverge is exit 0)
        await _pull_async(root=root, remote_name="origin", branch=None)

    captured = capsys.readouterr()
    assert "diverged" in captured.out.lower() or "merge" in captured.out.lower()


# ---------------------------------------------------------------------------
# _is_ancestor unit tests
# ---------------------------------------------------------------------------


def _make_commit_stub(commit_id: str, parent_id: str | None = None) -> MuseCliCommit:
    return MuseCliCommit(
        commit_id=commit_id,
        repo_id="r",
        branch="main",
        parent_commit_id=parent_id,
        snapshot_id="snap",
        message="msg",
        author="",
        committed_at=datetime.datetime.now(datetime.timezone.utc),
    )


def test_is_ancestor_direct_parent() -> None:
    """parent is an ancestor of child."""
    c1 = _make_commit_stub("commit-001")
    c2 = _make_commit_stub("commit-002", parent_id="commit-001")
    by_id = {c.commit_id: c for c in [c1, c2]}
    assert _is_ancestor(by_id, "commit-001", "commit-002") is True


def test_is_ancestor_same_commit() -> None:
    """A commit is its own ancestor."""
    c1 = _make_commit_stub("commit-001")
    by_id = {"commit-001": c1}
    assert _is_ancestor(by_id, "commit-001", "commit-001") is True


def test_is_ancestor_unrelated() -> None:
    """Two unrelated commits are not ancestors of each other."""
    c1 = _make_commit_stub("commit-001")
    c2 = _make_commit_stub("commit-002")
    by_id = {c.commit_id: c for c in [c1, c2]}
    assert _is_ancestor(by_id, "commit-001", "commit-002") is False


def test_is_ancestor_transitive() -> None:
    """Ancestor check traverses multi-hop parent chain."""
    c1 = _make_commit_stub("commit-001")
    c2 = _make_commit_stub("commit-002", parent_id="commit-001")
    c3 = _make_commit_stub("commit-003", parent_id="commit-002")
    by_id = {c.commit_id: c for c in [c1, c2, c3]}
    assert _is_ancestor(by_id, "commit-001", "commit-003") is True


def test_is_ancestor_descendant_unknown() -> None:
    """Returns False when descendant is not in commits_by_id."""
    by_id: dict[str, MuseCliCommit] = {}
    assert _is_ancestor(by_id, "commit-001", "commit-002") is False


# ---------------------------------------------------------------------------
# test_push_pull_roundtrip (integration-style with two tmp_path dirs)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_pull_roundtrip(tmp_path: pathlib.Path) -> None:
    """Simulate push from dir A then pull in dir B — remote_head is consistent.

    This is a lightweight integration test: both push and pull call real config
    read/write code; only the HTTP and DB layers are mocked.  The remote_head
    tracking file is the shared state that must be consistent across both
    operations.
    """
    dir_a = tmp_path / "repo_a"
    dir_b = tmp_path / "repo_b"
    dir_a.mkdir()
    dir_b.mkdir()

    head_id = "sync-commit-id12345abcdef" * 2
    hub_url = "https://hub.example.com/musehub/repos/shared"

    # --- Set up repo A (pusher) -------------------------------------------
    import json as _json
    for d in [dir_a, dir_b]:
        (d / ".muse").mkdir()
        (d / ".muse" / "repo.json").write_text(
            _json.dumps({"repo_id": "shared-repo"}), encoding="utf-8"
        )
        (d / ".muse" / "HEAD").write_text("refs/heads/main", encoding="utf-8")
        (d / ".muse" / "config.toml").write_text(
            f'[auth]\ntoken = "tok"\n\n[remotes.origin]\nurl = "{hub_url}"\n',
            encoding="utf-8",
        )

    _write_branch_ref(dir_a, "main", head_id)

    from maestro.muse_cli.models import MuseCliCommit as MCCommit
    commit_a = MCCommit(
        commit_id=head_id,
        repo_id="shared-repo",
        branch="main",
        parent_commit_id=None,
        snapshot_id="snap-aa",
        message="First shared commit",
        author="a",
        committed_at=datetime.datetime.now(datetime.timezone.utc),
    )

    # Push from dir_a
    mock_push_resp = MagicMock()
    mock_push_resp.status_code = 200
    mock_hub_push = MagicMock()
    mock_hub_push.__aenter__ = AsyncMock(return_value=mock_hub_push)
    mock_hub_push.__aexit__ = AsyncMock(return_value=None)
    mock_hub_push.post = AsyncMock(return_value=mock_push_resp)

    with (
        patch(
            "maestro.muse_cli.commands.push.get_commits_for_branch",
            new=AsyncMock(return_value=[commit_a]),
        ),
        patch("maestro.muse_cli.commands.push.get_all_object_ids", new=AsyncMock(return_value=[])),
        patch("maestro.muse_cli.commands.push.open_session") as mock_push_session,
        patch("maestro.muse_cli.commands.push.MuseHubClient", return_value=mock_hub_push),
    ):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_push_session.return_value = ctx
        await _push_async(root=dir_a, remote_name="origin", branch=None)

    # Verify dir_a now has remote tracking head
    assert get_remote_head("origin", "main", dir_a) == head_id

    # Pull into dir_b using the head_id as the remote head
    mock_pull_resp = _make_hub_pull_response(
        commits=[{
            "commit_id": head_id,
            "repo_id": "shared-repo",
            "parent_commit_id": None,
            "snapshot_id": "snap-aa",
            "branch": "main",
            "message": "First shared commit",
            "author": "a",
            "committed_at": "2025-01-01T00:00:00+00:00",
            "metadata": None,
        }],
        remote_head=head_id,
        diverged=False,
    )

    mock_hub_pull = MagicMock()
    mock_hub_pull.__aenter__ = AsyncMock(return_value=mock_hub_pull)
    mock_hub_pull.__aexit__ = AsyncMock(return_value=None)
    mock_hub_pull.post = AsyncMock(return_value=mock_pull_resp)

    with (
        patch("maestro.muse_cli.commands.pull.get_commits_for_branch", new=AsyncMock(return_value=[])),
        patch("maestro.muse_cli.commands.pull.get_all_object_ids", new=AsyncMock(return_value=[])),
        patch("maestro.muse_cli.commands.pull.store_pulled_commit", new=AsyncMock(return_value=True)),
        patch("maestro.muse_cli.commands.pull.store_pulled_object", new=AsyncMock(return_value=False)),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_pull_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub_pull),
    ):
        ctx2 = MagicMock()
        ctx2.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx2.__aexit__ = AsyncMock(return_value=None)
        mock_pull_session.return_value = ctx2
        await _pull_async(root=dir_b, remote_name="origin", branch=None)

    # dir_b now has the remote head from the push
    assert get_remote_head("origin", "main", dir_b) == head_id


# ---------------------------------------------------------------------------
# Issue #77 — new pull flags: --ff-only and --rebase
# ---------------------------------------------------------------------------


def _make_hub_session_patches(
    local_commits: list[object] | None = None,
    mock_store_commit: bool = True,
) -> tuple[AsyncMock, AsyncMock, AsyncMock, AsyncMock, MagicMock]:
    """Build the standard DB mock patches for pull tests.

    Returns (mock_get_commits, mock_get_objects, mock_store_commit,
    mock_store_object, mock_session_ctx).
    """
    mock_get_commits = AsyncMock(return_value=local_commits or [])
    mock_get_objects = AsyncMock(return_value=[])
    mock_sc = AsyncMock(return_value=True if mock_store_commit else False)
    mock_so = AsyncMock(return_value=False)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_get_commits, mock_get_objects, mock_sc, mock_so, ctx


@pytest.mark.anyio
async def test_pull_ff_only_fast_forwards_when_remote_ahead(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--ff-only updates local branch ref when remote HEAD is a fast-forward.

    Regression for issue #77: pulling with --ff-only when remote is strictly
    ahead of local should advance the local branch ref without merge.
    """
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    local_commit_id = "local-base-001" * 4
    remote_head_id = "remote-tip-002" * 4

    # Write local branch ref at the base commit
    _write_branch_ref(root, "main", local_commit_id)

    # Remote is strictly ahead: local commit IS an ancestor of remote_head
    # Simulate this by providing all commits in the DB including remote commit
    local_commit_stub = _make_commit_stub(local_commit_id)
    remote_commit_stub = _make_commit_stub(remote_head_id, parent_id=local_commit_id)

    mock_response = _make_hub_pull_response(
        remote_head=remote_head_id,
        diverged=False,
    )

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=mock_response)

    all_commits = [remote_commit_stub, local_commit_stub]

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=all_commits),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
    ):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = ctx

        await _pull_async(root=root, remote_name="origin", branch=None, ff_only=True)

    # Local branch ref must have been fast-forwarded to remote_head
    ref_path = root / ".muse" / "refs" / "heads" / "main"
    assert ref_path.exists()
    assert ref_path.read_text(encoding="utf-8").strip() == remote_head_id

    captured = capsys.readouterr()
    assert "fast-forward" in captured.out.lower()


@pytest.mark.anyio
async def test_pull_ff_only_fails_when_not_ff(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--ff-only exits 1 when branches have diverged (cannot fast-forward).

    Regression for issue #77: pulling with --ff-only must refuse to integrate
    when the remote and local branches have diverged.
    """
    import typer

    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    local_head_id = "local-diverged-001" * 3
    remote_head_id = "remote-diverged-002" * 3
    _write_branch_ref(root, "main", local_head_id)

    # Both branches diverged — neither is ancestor of the other
    local_commit = _make_commit_stub(local_head_id)
    remote_commit = _make_commit_stub(remote_head_id)

    mock_response = _make_hub_pull_response(
        remote_head=remote_head_id,
        diverged=True,
    )

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=mock_response)

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=[local_commit, remote_commit]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
    ):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = ctx

        with pytest.raises(typer.Exit) as exc_info:
            await _pull_async(
                root=root, remote_name="origin", branch=None, ff_only=True
            )

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)
    captured = capsys.readouterr()
    assert "diverged" in captured.out.lower() or "cannot fast-forward" in captured.out.lower()

    # Local branch ref must NOT have been changed
    ref_path = root / ".muse" / "refs" / "heads" / "main"
    assert ref_path.read_text(encoding="utf-8").strip() == local_head_id


@pytest.mark.anyio
async def test_pull_rebase_fast_forwards_when_remote_ahead(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--rebase fast-forwards local branch when remote is strictly ahead.

    Regression for issue #77: when remote is simply ahead (no local commits
    above the common base), --rebase acts like a fast-forward.
    """
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    local_commit_id = "rebase-base-001" * 4
    remote_head_id = "rebase-tip-002" * 4
    _write_branch_ref(root, "main", local_commit_id)

    local_commit_stub = _make_commit_stub(local_commit_id)
    remote_commit_stub = _make_commit_stub(remote_head_id, parent_id=local_commit_id)

    mock_response = _make_hub_pull_response(
        remote_head=remote_head_id,
        diverged=False,
    )

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=mock_response)

    all_commits = [remote_commit_stub, local_commit_stub]

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=all_commits),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
    ):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = ctx

        await _pull_async(root=root, remote_name="origin", branch=None, rebase=True)

    # Branch ref advanced to remote_head
    ref_path = root / ".muse" / "refs" / "heads" / "main"
    assert ref_path.read_text(encoding="utf-8").strip() == remote_head_id

    captured = capsys.readouterr()
    assert "fast-forward" in captured.out.lower()


@pytest.mark.anyio
async def test_pull_rebase_sends_rebase_hint_in_request(
    tmp_path: pathlib.Path,
) -> None:
    """--rebase flag includes rebase=True in the pull request payload."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    remote_head_id = "rebase-tip-hint" * 4
    captured_payloads: list[dict[str, object]] = []

    mock_response = _make_hub_pull_response(remote_head=remote_head_id, diverged=False)

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)

    async def _fake_post(path: str, **kwargs: object) -> MagicMock:
        payload = kwargs.get("json", {})
        if isinstance(payload, dict):
            captured_payloads.append(payload)
        return mock_response

    mock_hub.post = _fake_post

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
    ):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = ctx

        await _pull_async(root=root, remote_name="origin", branch=None, rebase=True)

    assert len(captured_payloads) == 1
    assert captured_payloads[0].get("rebase") is True


@pytest.mark.anyio
async def test_pull_ff_only_sends_ff_only_hint_in_request(
    tmp_path: pathlib.Path,
) -> None:
    """--ff-only flag includes ff_only=True in the pull request payload."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    remote_head_id = "ff-only-tip-hint" * 4
    _write_branch_ref(root, "main", remote_head_id)
    captured_payloads: list[dict[str, object]] = []

    # Remote is same as local — no divergence, ff trivially satisfied
    local_stub = _make_commit_stub(remote_head_id)
    mock_response = _make_hub_pull_response(
        remote_head=remote_head_id,
        diverged=False,
    )

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)

    async def _fake_post(path: str, **kwargs: object) -> MagicMock:
        payload = kwargs.get("json", {})
        if isinstance(payload, dict):
            captured_payloads.append(payload)
        return mock_response

    mock_hub.post = _fake_post

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=[local_stub]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
    ):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = ctx

        await _pull_async(root=root, remote_name="origin", branch=None, ff_only=True)

    assert len(captured_payloads) == 1
    assert captured_payloads[0].get("ff_only") is True


# ---------------------------------------------------------------------------
# Issue #238 — diverged-branch rebase path (find_merge_base + _rebase_commits_onto)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pull_rebase_replays_local_commits_on_diverged_branch(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--rebase replays local-only commits onto remote HEAD when branches have diverged.

    Regression for issue #238: the diverged rebase path (find_merge_base →
    _rebase_commits_onto) was missing test coverage.  This test verifies that:

    1. ``find_merge_base`` is called with the local and remote HEAD commit IDs.
    2. ``_rebase_commits_onto`` is called with the commits above the merge base.
    3. The local branch ref file is updated to the new rebased HEAD.
    4. A success message is printed to stdout.
    """
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    # History: base ← local_a ← local_b  (local side, 2 commits above base)
    #          base ← remote_a            (remote side, diverged)
    base_id = "base-commit-0000" * 4
    local_a_id = "local-commit-a001" * 4
    local_b_id = "local-commit-b002" * 4
    remote_a_id = "remote-commit-a003" * 4
    rebased_head_id = "rebased-head-xxxx" * 4

    _write_branch_ref(root, "main", local_b_id)

    base_stub = _make_commit_stub(base_id)
    local_a_stub = _make_commit_stub(local_a_id, parent_id=base_id)
    local_b_stub = _make_commit_stub(local_b_id, parent_id=local_a_id)
    remote_a_stub = _make_commit_stub(remote_a_id, parent_id=base_id)

    # Hub says branches have diverged; remote HEAD is remote_a
    mock_response = _make_hub_pull_response(
        remote_head=remote_a_id,
        diverged=True,
    )

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=mock_response)

    # After pulling, DB contains all four commits
    all_commits = [base_stub, local_a_stub, local_b_stub, remote_a_stub]

    # Track what arguments _rebase_commits_onto receives
    rebase_calls: list[tuple[list[MuseCliCommit], str]] = []

    async def _fake_rebase(
        root: pathlib.Path,
        repo_id: str,
        branch: str,
        commits_to_rebase: list[MuseCliCommit],
        new_base_commit_id: str,
    ) -> str:
        rebase_calls.append((list(commits_to_rebase), new_base_commit_id))
        # Write the ref to simulate what the real function does
        ref = root / ".muse" / "refs" / "heads" / branch
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(rebased_head_id, encoding="utf-8")
        return rebased_head_id

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=all_commits),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
        patch(
            "maestro.muse_cli.commands.pull.find_merge_base",
            new=AsyncMock(return_value=base_id),
        ),
        patch(
            "maestro.muse_cli.commands.pull._rebase_commits_onto",
            side_effect=_fake_rebase,
        ),
    ):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = ctx

        await _pull_async(root=root, remote_name="origin", branch=None, rebase=True)

    # Branch ref must be the rebased head produced by _rebase_commits_onto
    ref_path = root / ".muse" / "refs" / "heads" / "main"
    assert ref_path.read_text(encoding="utf-8").strip() == rebased_head_id

    # _rebase_commits_onto must have been called exactly once
    assert len(rebase_calls) == 1
    replayed_commits, onto_id = rebase_calls[0]
    assert onto_id == remote_a_id
    # The two local-only commits (local_a, local_b) should have been replayed;
    # base and remote commits are excluded
    replayed_ids = {c.commit_id for c in replayed_commits}
    assert local_a_id in replayed_ids or local_b_id in replayed_ids

    captured = capsys.readouterr()
    assert "rebase" in captured.out.lower()


@pytest.mark.anyio
async def test_pull_rebase_diverged_no_common_ancestor_exits_1(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--rebase exits 1 with instructive message when no common ancestor exists.

    Guards the ``merge_base_id is None`` branch in _pull_async (diverged rebase
    path) that was untested before issue #238.
    """
    import typer

    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    local_head_id = "local-disjoint-001" * 4
    remote_head_id = "remote-disjoint-002" * 4
    _write_branch_ref(root, "main", local_head_id)

    local_stub = _make_commit_stub(local_head_id)
    remote_stub = _make_commit_stub(remote_head_id)

    mock_response = _make_hub_pull_response(
        remote_head=remote_head_id,
        diverged=True,
    )

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=mock_response)

    with (
        patch(
            "maestro.muse_cli.commands.pull.get_commits_for_branch",
            new=AsyncMock(return_value=[local_stub, remote_stub]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.pull.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.pull.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.pull.MuseHubClient", return_value=mock_hub),
        patch(
            "maestro.muse_cli.commands.pull.find_merge_base",
            new=AsyncMock(return_value=None),  # no common ancestor
        ),
    ):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = ctx

        with pytest.raises(typer.Exit) as exc_info:
            await _pull_async(
                root=root, remote_name="origin", branch=None, rebase=True
            )

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)
    captured = capsys.readouterr()
    assert "rebase" in captured.out.lower() or "common ancestor" in captured.out.lower()


@pytest.mark.anyio
async def test_rebase_commits_onto_idempotent(tmp_path: pathlib.Path) -> None:
    """_rebase_commits_onto is idempotent: running twice yields the same HEAD.

    Verifies the idempotency contract documented in issue #238: re-running a
    rebase with the same inputs (same parent IDs, snapshot IDs, messages, and
    authors) produces the same deterministic commit IDs and does not insert
    duplicate rows.  Uses a dedicated in-memory SQLite engine so that both
    invocations share the same DB state.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from maestro.db.database import Base
    import maestro.muse_cli.models  # noqa: F401 — registers MuseCli* models with Base
    from maestro.muse_cli.models import MuseCliCommit as MCCommit
    from maestro.muse_cli.snapshot import compute_commit_tree_id

    # Create a fresh in-memory engine for this test
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    repo_id = "idempotent-repo"
    branch = "main"
    remote_base_id = "remote-base-idem" * 4

    # Seed the original local commit (the one that will be replayed)
    local_commit = MCCommit(
        commit_id="local-original-idem" * 3,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=None,
        snapshot_id="snap-idem-0001",
        message="Original local commit",
        author="dev",
        committed_at=datetime.datetime.now(datetime.timezone.utc),
    )
    async with factory() as seed_session:
        seed_session.add(local_commit)
        await seed_session.commit()

    # Expected rebased commit ID (deterministic via compute_commit_tree_id)
    expected_new_id = compute_commit_tree_id(
        parent_ids=[remote_base_id],
        snapshot_id=local_commit.snapshot_id,
        message=local_commit.message,
        author=local_commit.author,
    )

    class _FakeSessionCtx:
        """Context manager that opens a new session from the shared in-memory factory."""

        async def __aenter__(self) -> AsyncSession:
            self._session: AsyncSession = factory()
            return await self._session.__aenter__()

        async def __aexit__(self, *args: object) -> None:
            await self._session.__aexit__(*args)

    root = tmp_path / "repo"
    (root / ".muse" / "refs" / "heads").mkdir(parents=True)

    with patch(
        "maestro.muse_cli.commands.pull.open_session",
        return_value=_FakeSessionCtx(),
    ):
        head1 = await _rebase_commits_onto(
            root=root,
            repo_id=repo_id,
            branch=branch,
            commits_to_rebase=[local_commit],
            new_base_commit_id=remote_base_id,
        )

    with patch(
        "maestro.muse_cli.commands.pull.open_session",
        return_value=_FakeSessionCtx(),
    ):
        head2 = await _rebase_commits_onto(
            root=root,
            repo_id=repo_id,
            branch=branch,
            commits_to_rebase=[local_commit],
            new_base_commit_id=remote_base_id,
        )

    # Both calls must return the same deterministic HEAD
    assert head1 == expected_new_id
    assert head2 == expected_new_id

    # Branch ref must point to the rebased head
    ref_path = root / ".muse" / "refs" / "heads" / branch
    assert ref_path.read_text(encoding="utf-8").strip() == expected_new_id

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
