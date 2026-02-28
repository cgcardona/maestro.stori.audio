"""Tests for ``muse pull``.

Covers acceptance criteria from issue #38:
- ``muse pull`` with no remote configured exits 1 with instructive message.
- ``muse pull`` calls ``POST <remote>/pull`` with correct payload structure.
- Returned commits are stored in local Postgres (via DB helpers).
- ``.muse/remotes/origin/<branch>`` is updated after a successful pull.
- Divergence message is printed (exit 0) when branches have diverged.

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

from maestro.muse_cli.commands.pull import _is_ancestor, _pull_async
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
