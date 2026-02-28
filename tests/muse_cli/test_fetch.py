"""Tests for ``muse fetch``.

Covers acceptance criteria from issue #80:
- ``muse fetch`` with no remote configured exits 1 with instructive message.
- ``muse fetch`` POSTs to ``/fetch`` with correct payload structure.
- Remote-tracking refs (``.muse/remotes/origin/<branch>``) are updated.
- Local branches and muse-work/ are NOT modified.
- ``muse fetch --all`` iterates all configured remotes.
- ``muse fetch --prune`` removes stale remote-tracking refs.
- New-branch report lines include "(new branch)" suffix.
- Up-to-date branches are silently skipped (no redundant output).

All HTTP calls are mocked — no live network required.
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from maestro.muse_cli.commands.fetch import (
    _fetch_async,
    _fetch_remote_async,
    _format_fetch_line,
    _list_local_remote_tracking_branches,
    _remove_remote_tracking_ref,
)
from maestro.muse_cli.config import get_remote_head, set_remote_head
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.hub_client import FetchBranchInfo


# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path, branch: str = "main") -> pathlib.Path:
    """Create a minimal .muse/ structure for testing."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo-id"}), encoding="utf-8"
    )
    (muse_dir / "HEAD").write_text(f"refs/heads/{branch}", encoding="utf-8")
    return tmp_path


def _write_config_with_token(
    root: pathlib.Path,
    remote_url: str,
    remote_name: str = "origin",
) -> None:
    muse_dir = root / ".muse"
    (muse_dir / "config.toml").write_text(
        f'[auth]\ntoken = "test-token"\n\n[remotes.{remote_name}]\nurl = "{remote_url}"\n',
        encoding="utf-8",
    )


def _write_config_multi_remote(
    root: pathlib.Path,
    remotes: dict[str, str],
) -> None:
    """Write a config.toml with multiple remotes."""
    muse_dir = root / ".muse"
    lines = ['[auth]\ntoken = "test-token"\n']
    for name, url in remotes.items():
        lines.append(f'\n[remotes.{name}]\nurl = "{url}"\n')
    (muse_dir / "config.toml").write_text("".join(lines), encoding="utf-8")


def _make_hub_fetch_response(
    branches: list[dict[str, object]] | None = None,
    status_code: int = 200,
) -> MagicMock:
    """Return a mock httpx.Response for the fetch endpoint."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {"branches": branches or []}
    mock_resp.text = "OK"
    return mock_resp


def _make_branch_info(
    branch: str = "main",
    head_commit_id: str = "abc1234567890000",
    is_new: bool = False,
) -> dict[str, object]:
    return {
        "branch": branch,
        "head_commit_id": head_commit_id,
        "is_new": is_new,
    }


def _make_mock_hub(response: MagicMock) -> MagicMock:
    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(return_value=response)
    return mock_hub


# ---------------------------------------------------------------------------
# Regression test: fetch updates remote-tracking refs without modifying workdir
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_updates_remote_tracking_refs_without_modifying_workdir(
    tmp_path: pathlib.Path,
) -> None:
    """Regression: fetch must update .muse/remotes/origin/main but NOT touch HEAD or refs/heads/."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    head_commit_id = "deadbeef1234567890abcdef01234567"
    mock_response = _make_hub_fetch_response(
        branches=[_make_branch_info("main", head_commit_id, is_new=True)]
    )
    mock_hub = _make_mock_hub(mock_response)

    with patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub):
        await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=[],
            prune=False,
        )

    # Remote-tracking ref must be updated
    stored = get_remote_head("origin", "main", root)
    assert stored == head_commit_id

    # Local HEAD must NOT be modified
    head_content = (root / ".muse" / "HEAD").read_text(encoding="utf-8").strip()
    assert head_content == "refs/heads/main"

    # Local refs/heads/ must NOT be created
    local_ref = root / ".muse" / "refs" / "heads" / "main"
    assert not local_ref.exists()

    # muse-work/ must NOT be created
    assert not (root / "muse-work").exists()


# ---------------------------------------------------------------------------
# test_fetch_no_remote_exits_1
# ---------------------------------------------------------------------------


def test_fetch_no_remote_exits_1(tmp_path: pathlib.Path) -> None:
    """muse fetch exits 1 with instructive message when no remote is configured."""
    import asyncio

    root = _init_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        asyncio.run(
            _fetch_remote_async(
                root=root,
                remote_name="origin",
                branches=[],
                prune=False,
            )
        )

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


def test_fetch_no_remote_message_is_instructive(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fetch with no remote prints a message directing user to run muse remote add."""
    import asyncio

    root = _init_repo(tmp_path)

    with pytest.raises(typer.Exit):
        asyncio.run(
            _fetch_remote_async(
                root=root,
                remote_name="origin",
                branches=[],
                prune=False,
            )
        )

    captured = capsys.readouterr()
    assert "muse remote add" in captured.out


# ---------------------------------------------------------------------------
# test_fetch_posts_to_hub_fetch_endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_posts_to_hub_fetch_endpoint(tmp_path: pathlib.Path) -> None:
    """muse fetch POSTs to /fetch with the branches list."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    captured_paths: list[str] = []
    captured_payloads: list[dict[str, object]] = []

    async def _fake_post(path: str, **kwargs: object) -> MagicMock:
        captured_paths.append(path)
        payload = kwargs.get("json", {})
        if isinstance(payload, dict):
            captured_payloads.append(payload)
        return _make_hub_fetch_response()

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = _fake_post

    with patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub):
        await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=["main", "feature/bass"],
            prune=False,
        )

    assert len(captured_paths) == 1
    assert captured_paths[0] == "/fetch"
    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert "branches" in payload
    branches_val = payload["branches"]
    assert isinstance(branches_val, list)
    assert "main" in branches_val
    assert "feature/bass" in branches_val


# ---------------------------------------------------------------------------
# test_fetch_updates_remote_head_for_each_branch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_updates_remote_head_for_each_branch(tmp_path: pathlib.Path) -> None:
    """Each branch returned by the Hub gets its remote-tracking ref updated."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    main_id = "aabbccddeeff001122334455" * 2
    feature_id = "112233445566778899aabbcc" * 2

    mock_response = _make_hub_fetch_response(
        branches=[
            _make_branch_info("main", main_id, is_new=True),
            _make_branch_info("feature/guitar", feature_id, is_new=True),
        ]
    )
    mock_hub = _make_mock_hub(mock_response)

    with patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub):
        count = await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=[],
            prune=False,
        )

    assert count == 2
    assert get_remote_head("origin", "main", root) == main_id
    assert get_remote_head("origin", "feature/guitar", root) == feature_id


# ---------------------------------------------------------------------------
# test_fetch_skips_already_up_to_date_branches
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_skips_already_up_to_date_branches(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Branches whose remote HEAD hasn't moved are silently skipped (count stays 0)."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    existing_head = "existing-head-commit-id1234567890ab"
    # Pre-write the remote-tracking ref so fetch sees it as already known
    set_remote_head("origin", "main", existing_head, root)

    mock_response = _make_hub_fetch_response(
        branches=[_make_branch_info("main", existing_head, is_new=False)]
    )
    mock_hub = _make_mock_hub(mock_response)

    with patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub):
        count = await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=[],
            prune=False,
        )

    assert count == 0
    captured = capsys.readouterr()
    # No "From origin" line for an up-to-date branch
    assert "From origin" not in captured.out


# ---------------------------------------------------------------------------
# test_fetch_new_branch_report_includes_new_branch_suffix
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_new_branch_report_includes_new_branch_suffix(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """New branches get a '(new branch)' suffix in the fetch report line."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    new_id = "cafebabe123456789abcdef0" * 2

    mock_response = _make_hub_fetch_response(
        branches=[_make_branch_info("feature/new-bass", new_id, is_new=True)]
    )
    mock_hub = _make_mock_hub(mock_response)

    with patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub):
        await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=[],
            prune=False,
        )

    captured = capsys.readouterr()
    assert "new branch" in captured.out
    assert "feature/new-bass" in captured.out
    assert "origin/feature/new-bass" in captured.out


# ---------------------------------------------------------------------------
# test_fetch_prune_removes_stale_remote_tracking_refs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_prune_removes_stale_remote_tracking_refs(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--prune deletes remote-tracking refs for branches no longer on the remote."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    # Pre-write two remote-tracking refs: one still exists remotely, one is stale
    set_remote_head("origin", "main", "active-commit-id", root)
    set_remote_head("origin", "deleted-branch", "old-commit-id", root)

    # Remote only reports "main" — "deleted-branch" has been removed on the remote
    mock_response = _make_hub_fetch_response(
        branches=[_make_branch_info("main", "active-commit-id-v2", is_new=False)]
    )
    mock_hub = _make_mock_hub(mock_response)

    with patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub):
        await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=[],
            prune=True,
        )

    # Stale ref must be gone
    assert get_remote_head("origin", "deleted-branch", root) is None

    # Active ref must still be present (and updated)
    assert get_remote_head("origin", "main", root) == "active-commit-id-v2"

    # Prune message must appear
    captured = capsys.readouterr()
    assert "deleted-branch" in captured.out


@pytest.mark.anyio
async def test_fetch_prune_noop_when_no_stale_refs(tmp_path: pathlib.Path) -> None:
    """--prune is a no-op when all local remote-tracking refs exist on the remote."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    set_remote_head("origin", "main", "some-commit-id", root)

    mock_response = _make_hub_fetch_response(
        branches=[_make_branch_info("main", "some-commit-id-v2", is_new=False)]
    )
    mock_hub = _make_mock_hub(mock_response)

    with patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub):
        await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=[],
            prune=True,
        )

    # main ref is updated
    assert get_remote_head("origin", "main", root) == "some-commit-id-v2"


# ---------------------------------------------------------------------------
# test_fetch_all_iterates_all_remotes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_all_iterates_all_remotes(tmp_path: pathlib.Path) -> None:
    """--all causes fetch to contact every configured remote."""
    root = _init_repo(tmp_path)
    _write_config_multi_remote(
        root,
        {
            "origin": "https://hub.example.com/musehub/repos/r",
            "staging": "https://staging.example.com/musehub/repos/r",
        },
    )

    calls: list[str] = []

    async def _fetch_remote_spy(
        *,
        root: pathlib.Path,
        remote_name: str,
        branches: list[str],
        prune: bool,
    ) -> int:
        calls.append(remote_name)
        return 1

    with patch(
        "maestro.muse_cli.commands.fetch._fetch_remote_async",
        side_effect=_fetch_remote_spy,
    ):
        await _fetch_async(
            root=root,
            remote_name="origin",
            fetch_all=True,
            prune=False,
            branches=[],
        )

    assert sorted(calls) == ["origin", "staging"]


@pytest.mark.anyio
async def test_fetch_all_no_remotes_exits_1(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--all with no remotes configured exits 1 with instructive message."""
    root = _init_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _fetch_async(
            root=root,
            remote_name="origin",
            fetch_all=True,
            prune=False,
            branches=[],
        )

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)
    captured = capsys.readouterr()
    assert "muse remote add" in captured.out


# ---------------------------------------------------------------------------
# test_fetch_server_error_exits_3
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_server_error_exits_3(tmp_path: pathlib.Path) -> None:
    """Hub returning non-200 causes fetch to exit with code 3."""
    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    error_response = _make_hub_fetch_response(status_code=500)
    mock_hub = _make_mock_hub(error_response)

    with (
        patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub),
        pytest.raises(typer.Exit) as exc_info,
    ):
        await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=[],
            prune=False,
        )

    assert exc_info.value.exit_code == int(ExitCode.INTERNAL_ERROR)


@pytest.mark.anyio
async def test_fetch_network_error_exits_3(tmp_path: pathlib.Path) -> None:
    """Network error during fetch causes exit with code 3."""
    import httpx

    root = _init_repo(tmp_path)
    _write_config_with_token(root, "https://hub.example.com/musehub/repos/r")

    mock_hub = MagicMock()
    mock_hub.__aenter__ = AsyncMock(return_value=mock_hub)
    mock_hub.__aexit__ = AsyncMock(return_value=None)
    mock_hub.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with (
        patch("maestro.muse_cli.commands.fetch.MuseHubClient", return_value=mock_hub),
        pytest.raises(typer.Exit) as exc_info,
    ):
        await _fetch_remote_async(
            root=root,
            remote_name="origin",
            branches=[],
            prune=False,
        )

    assert exc_info.value.exit_code == int(ExitCode.INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# Unit tests: _list_local_remote_tracking_branches
# ---------------------------------------------------------------------------


def test_list_local_remote_tracking_branches_empty_when_no_dir(
    tmp_path: pathlib.Path,
) -> None:
    """Returns empty list when no remotes directory exists."""
    root = _init_repo(tmp_path)
    result = _list_local_remote_tracking_branches("origin", root)
    assert result == []


def test_list_local_remote_tracking_branches_returns_branch_names(
    tmp_path: pathlib.Path,
) -> None:
    """Returns all branch names from remote-tracking ref files."""
    root = _init_repo(tmp_path)
    set_remote_head("origin", "main", "abc", root)
    set_remote_head("origin", "feature/groove", "def", root)
    result = _list_local_remote_tracking_branches("origin", root)
    assert sorted(result) == ["feature/groove", "main"]


# ---------------------------------------------------------------------------
# Unit tests: _remove_remote_tracking_ref
# ---------------------------------------------------------------------------


def test_remove_remote_tracking_ref_deletes_file(tmp_path: pathlib.Path) -> None:
    """Removes the tracking pointer file for a specific branch."""
    root = _init_repo(tmp_path)
    set_remote_head("origin", "old-branch", "commit-id", root)
    assert get_remote_head("origin", "old-branch", root) == "commit-id"

    _remove_remote_tracking_ref("origin", "old-branch", root)
    assert get_remote_head("origin", "old-branch", root) is None


def test_remove_remote_tracking_ref_noop_when_missing(tmp_path: pathlib.Path) -> None:
    """Removing a non-existent ref is a no-op (does not raise)."""
    root = _init_repo(tmp_path)
    _remove_remote_tracking_ref("origin", "nonexistent-branch", root)


# ---------------------------------------------------------------------------
# Unit tests: _format_fetch_line
# ---------------------------------------------------------------------------


def test_format_fetch_line_new_branch() -> None:
    """New branches include '(new branch)' suffix."""
    info = FetchBranchInfo(
        branch="feature/guitar",
        head_commit_id="abc1234567890000",
        is_new=True,
    )
    line = _format_fetch_line("origin", info)
    assert "origin" in line
    assert "feature/guitar" in line
    assert "origin/feature/guitar" in line
    assert "(new branch)" in line
    assert "abc12345" in line


def test_format_fetch_line_existing_branch() -> None:
    """Existing branches do NOT have '(new branch)' suffix."""
    info = FetchBranchInfo(
        branch="main",
        head_commit_id="def0987654321000",
        is_new=False,
    )
    line = _format_fetch_line("origin", info)
    assert "origin/main" in line
    assert "(new branch)" not in line
    assert "def09876" in line
