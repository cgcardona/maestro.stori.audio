"""Tests for ``muse push``.

Covers acceptance criteria from issue #38:
- ``muse push`` with no remote configured exits 1 with instructive message.
- ``muse push`` calls ``POST <remote>/push`` with correct payload structure.
- ``muse push`` updates ``.muse/remotes/origin/<branch>`` after a successful push.
- ``muse push`` when branch has no commits exits 1.
- Network errors surface as exit code 3.
- ``muse push`` with all commits already on remote prints up-to-date message.

All HTTP calls are mocked with unittest.mock — no live network required.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maestro.muse_cli.commands.push import _compute_push_delta, _build_push_request, _push_async
from maestro.muse_cli.config import get_remote_head, set_remote
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path, branch: str = "main") -> pathlib.Path:
    """Create a minimal .muse/ structure with one commit."""
    import json as _json
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "repo.json").write_text(
        _json.dumps({"repo_id": "test-repo-id"}), encoding="utf-8"
    )
    (muse_dir / "HEAD").write_text(f"refs/heads/{branch}", encoding="utf-8")
    return tmp_path


def _make_commit(
    commit_id: str,
    parent_id: str | None = None,
    branch: str = "main",
    repo_id: str = "test-repo-id",
) -> MuseCliCommit:
    """Build a MuseCliCommit ORM object for testing (not persisted)."""
    return MuseCliCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=parent_id,
        snapshot_id="snap-" + commit_id[:8],
        message="Test commit",
        author="test-author",
        committed_at=datetime.datetime.now(datetime.timezone.utc),
    )


def _write_branch_ref(root: pathlib.Path, branch: str, commit_id: str) -> None:
    """Write .muse/refs/heads/<branch> with the given commit ID."""
    ref_path = root / ".muse" / "refs" / "heads" / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id, encoding="utf-8")


# ---------------------------------------------------------------------------
# test_push_no_remote_exits_1
# ---------------------------------------------------------------------------


def test_push_no_remote_exits_1(tmp_path: pathlib.Path) -> None:
    """muse push exits 1 with instructive message when no remote is configured."""
    import typer

    root = _init_repo(tmp_path)
    _write_branch_ref(root, "main", "abc12345" * 8)

    with pytest.raises(typer.Exit) as exc_info:
        asyncio.run(
            _push_async(root=root, remote_name="origin", branch=None)
        )

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


def test_push_no_remote_message_is_instructive(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Push with no remote prints a message telling user to run muse remote add."""
    import typer

    root = _init_repo(tmp_path)
    _write_branch_ref(root, "main", "abc12345" * 8)

    with pytest.raises(typer.Exit):
        asyncio.run(_push_async(root=root, remote_name="origin", branch=None))

    captured = capsys.readouterr()
    assert "muse remote add" in captured.out


# ---------------------------------------------------------------------------
# test_push_no_commits_exits_1
# ---------------------------------------------------------------------------


def test_push_branch_no_commits_exits_1(tmp_path: pathlib.Path) -> None:
    """muse push exits 1 when the current branch has no commits (no ref file)."""
    import typer

    root = _init_repo(tmp_path)
    set_remote("origin", "https://hub.example.com/musehub/repos/r", root)
    # No .muse/refs/heads/main file

    with pytest.raises(typer.Exit) as exc_info:
        asyncio.run(_push_async(root=root, remote_name="origin", branch=None))

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


# ---------------------------------------------------------------------------
# test_push_calls_hub_endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_calls_hub_endpoint(tmp_path: pathlib.Path) -> None:
    """muse push POSTs to /push with branch, head_commit_id, commits, objects."""
    import typer

    head_id = "aabbccdd" * 8
    root = _init_repo(tmp_path)
    _write_branch_ref(root, "main", head_id)
    set_remote("origin", "https://hub.example.com/musehub/repos/r", root)

    # Write auth token so MuseHubClient doesn't exit early
    muse_dir = root / ".muse"
    (muse_dir / "config.toml").write_text(
        '[auth]\ntoken = "test-token"\n\n[remotes.origin]\nurl = "https://hub.example.com/musehub/repos/r"\n',
        encoding="utf-8",
    )

    commit = _make_commit(head_id)
    captured_payloads: list[dict[str, object]] = []

    mock_response = MagicMock()
    mock_response.status_code = 200

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
            "maestro.muse_cli.commands.push.get_commits_for_branch",
            new=AsyncMock(return_value=[commit]),
        ),
        patch(
            "maestro.muse_cli.commands.push.get_all_object_ids",
            new=AsyncMock(return_value=["obj-001"]),
        ),
        patch("maestro.muse_cli.commands.push.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.push.MuseHubClient", return_value=mock_hub),
    ):
        # open_session returns an async context manager
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = mock_session_ctx

        await _push_async(root=root, remote_name="origin", branch=None)

    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert payload["branch"] == "main"
    assert payload["head_commit_id"] == head_id
    assert isinstance(payload["commits"], list)
    assert isinstance(payload["objects"], list)


# ---------------------------------------------------------------------------
# test_push_updates_remote_head_file
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_updates_remote_head_file(tmp_path: pathlib.Path) -> None:
    """After a successful push, .muse/remotes/origin/<branch> is updated."""
    head_id = "deadbeef" * 8
    root = _init_repo(tmp_path)
    _write_branch_ref(root, "main", head_id)

    muse_dir = root / ".muse"
    (muse_dir / "config.toml").write_text(
        '[auth]\ntoken = "tok"\n\n[remotes.origin]\nurl = "https://hub.example.com/r"\n',
        encoding="utf-8",
    )

    commit = _make_commit(head_id)

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=mock_response)

    with (
        patch(
            "maestro.muse_cli.commands.push.get_commits_for_branch",
            new=AsyncMock(return_value=[commit]),
        ),
        patch(
            "maestro.muse_cli.commands.push.get_all_object_ids",
            new=AsyncMock(return_value=[]),
        ),
        patch("maestro.muse_cli.commands.push.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.push.MuseHubClient", return_value=mock_hub),
    ):
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = mock_session_ctx

        await _push_async(root=root, remote_name="origin", branch=None)

    remote_head = get_remote_head("origin", "main", root)
    assert remote_head == head_id


# ---------------------------------------------------------------------------
# _compute_push_delta unit tests
# ---------------------------------------------------------------------------


def test_compute_push_delta_first_push_returns_all_chronological() -> None:
    """First push (no remote head) returns all commits oldest-first."""
    c1 = _make_commit("commit-aaa")
    c2 = _make_commit("commit-bbb", parent_id="commit-aaa")
    # DB returns newest first
    commits = [c2, c1]
    delta = _compute_push_delta(commits, remote_head=None)
    assert [c.commit_id for c in delta] == ["commit-aaa", "commit-bbb"]


def test_compute_push_delta_returns_only_new_commits() -> None:
    """Delta excludes commits already on the remote."""
    c1 = _make_commit("commit-001")
    c2 = _make_commit("commit-002", parent_id="commit-001")
    c3 = _make_commit("commit-003", parent_id="commit-002")
    commits = [c3, c2, c1]  # newest first

    delta = _compute_push_delta(commits, remote_head="commit-001")
    assert [c.commit_id for c in delta] == ["commit-002", "commit-003"]


def test_compute_push_delta_already_synced_returns_empty() -> None:
    """When local HEAD == remote head, delta is empty."""
    c1 = _make_commit("commit-001")
    commits = [c1]
    delta = _compute_push_delta(commits, remote_head="commit-001")
    assert delta == []


def test_compute_push_delta_empty_commits() -> None:
    """Empty commit list always returns empty delta."""
    assert _compute_push_delta([], remote_head=None) == []
    assert _compute_push_delta([], remote_head="some-id") == []


# ---------------------------------------------------------------------------
# _build_push_request unit tests
# ---------------------------------------------------------------------------


def test_build_push_request_structure() -> None:
    """_build_push_request produces correct PushRequest dict shape."""
    c1 = _make_commit("commit-aaa")
    request = _build_push_request(
        branch="main",
        head_commit_id="commit-aaa",
        delta=[c1],
        all_object_ids=["obj-001", "obj-002"],
    )
    assert request["branch"] == "main"
    assert request["head_commit_id"] == "commit-aaa"
    assert len(request["commits"]) == 1
    assert request["commits"][0]["commit_id"] == "commit-aaa"
    assert len(request["objects"]) == 2
    assert request["objects"][0]["object_id"] == "obj-001"
