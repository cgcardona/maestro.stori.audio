"""Tests for ``muse push``.

Covers acceptance criteria from issue #38:
- ``muse push`` with no remote configured exits 1 with instructive message.
- ``muse push`` calls ``POST <remote>/push`` with correct payload structure.
- ``muse push`` updates ``.muse/remotes/origin/<branch>`` after a successful push.
- ``muse push`` when branch has no commits exits 1.
- Network errors surface as exit code 3.
- ``muse push`` with all commits already on remote prints up-to-date message.

Covers acceptance criteria from issue #77 (new remote sync flags):
- ``muse push --force`` sends ``force=True`` in the payload.
- ``muse push --force-with-lease`` sends ``force_with_lease=True`` and
  ``expected_remote_head`` in the payload; a 409 response exits 1.
- ``muse push --tags`` includes tag refs from ``.muse/refs/tags/``.
- ``muse push --set-upstream`` writes upstream tracking to config after push.

All HTTP calls are mocked with unittest.mock — no live network required.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maestro.muse_cli.commands.push import (
    _build_push_request,
    _collect_tag_refs,
    _compute_push_delta,
    _push_async,
)
from maestro.muse_cli.config import get_remote_head, get_upstream, set_remote
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


# ---------------------------------------------------------------------------
# Issue #77 — new remote sync flags
# ---------------------------------------------------------------------------


def test_build_push_request_force_flag() -> None:
    """_build_push_request includes force=True when requested."""
    c1 = _make_commit("commit-aaa")
    request = _build_push_request(
        branch="main",
        head_commit_id="commit-aaa",
        delta=[c1],
        all_object_ids=[],
        force=True,
    )
    assert request.get("force") is True
    assert "force_with_lease" not in request


def test_build_push_request_force_with_lease() -> None:
    """_build_push_request includes force_with_lease and expected_remote_head."""
    c1 = _make_commit("commit-aaa")
    expected_head = "old-remote-sha" * 4
    request = _build_push_request(
        branch="main",
        head_commit_id="commit-aaa",
        delta=[c1],
        all_object_ids=[],
        force_with_lease=True,
        expected_remote_head=expected_head,
    )
    assert request.get("force_with_lease") is True
    assert request.get("expected_remote_head") == expected_head
    assert "force" not in request


def test_build_push_request_no_force_flags_by_default() -> None:
    """_build_push_request does not include force flags unless explicitly set."""
    c1 = _make_commit("commit-bbb")
    request = _build_push_request(
        branch="main",
        head_commit_id="commit-bbb",
        delta=[c1],
        all_object_ids=[],
    )
    assert "force" not in request
    assert "force_with_lease" not in request
    assert "tags" not in request


def test_build_push_request_tags() -> None:
    """_build_push_request includes tags when tag_payloads is provided."""
    from maestro.muse_cli.hub_client import PushTagPayload

    c1 = _make_commit("commit-ccc")
    tags = [PushTagPayload(tag_name="v1.0", commit_id="commit-ccc")]
    request = _build_push_request(
        branch="main",
        head_commit_id="commit-ccc",
        delta=[c1],
        all_object_ids=[],
        tag_payloads=tags,
    )
    assert "tags" in request
    assert len(request["tags"]) == 1
    assert request["tags"][0]["tag_name"] == "v1.0"
    assert request["tags"][0]["commit_id"] == "commit-ccc"


# ---------------------------------------------------------------------------
# _collect_tag_refs unit tests
# ---------------------------------------------------------------------------


def test_collect_tag_refs_empty_when_no_tags_dir(tmp_path: pathlib.Path) -> None:
    """_collect_tag_refs returns empty list when .muse/refs/tags/ does not exist."""
    result = _collect_tag_refs(tmp_path)
    assert result == []


def test_collect_tag_refs_returns_tags_from_dir(tmp_path: pathlib.Path) -> None:
    """_collect_tag_refs reads each tag file and returns name+commit pairs."""
    tags_dir = tmp_path / ".muse" / "refs" / "tags"
    tags_dir.mkdir(parents=True)
    commit_v1 = "aaaa" * 16
    commit_v2 = "bbbb" * 16
    (tags_dir / "v1.0").write_text(commit_v1, encoding="utf-8")
    (tags_dir / "v2.0").write_text(commit_v2, encoding="utf-8")

    result = _collect_tag_refs(tmp_path)
    assert len(result) == 2
    tag_map = {t["tag_name"]: t["commit_id"] for t in result}
    assert tag_map["v1.0"] == commit_v1
    assert tag_map["v2.0"] == commit_v2


def test_collect_tag_refs_ignores_empty_files(tmp_path: pathlib.Path) -> None:
    """_collect_tag_refs skips tag files with empty content."""
    tags_dir = tmp_path / ".muse" / "refs" / "tags"
    tags_dir.mkdir(parents=True)
    (tags_dir / "empty-tag").write_text("", encoding="utf-8")
    (tags_dir / "v1.0").write_text("aabbccdd" * 8, encoding="utf-8")

    result = _collect_tag_refs(tmp_path)
    assert len(result) == 1
    assert result[0]["tag_name"] == "v1.0"


# ---------------------------------------------------------------------------
# test_push_force_with_lease_rejects_mismatch (regression from issue #77)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_force_with_lease_rejects_mismatch(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When Hub returns 409, --force-with-lease exits 1 with instructive message.

    Regression for issue #77: the hub rejects the push because the remote HEAD
    has advanced beyond our last-known tracking pointer.
    """
    import typer

    head_id = "localtip1234" * 5
    root = _init_repo(tmp_path)
    _write_branch_ref(root, "main", head_id)

    muse_dir = root / ".muse"
    (muse_dir / "config.toml").write_text(
        '[auth]\ntoken = "tok"\n\n[remotes.origin]\nurl = "https://hub.example.com/r"\n',
        encoding="utf-8",
    )
    # Record a known remote head (the "lease" value)
    from maestro.muse_cli.config import set_remote_head as _set_rh
    old_remote_head = "oldremotehead" * 5
    _set_rh("origin", "main", old_remote_head, root)

    commit = _make_commit(head_id)

    # Hub responds with 409 — remote has advanced
    mock_response = MagicMock()
    mock_response.status_code = 409
    mock_response.text = "remote has advanced"

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

        with pytest.raises(typer.Exit) as exc_info:
            await _push_async(
                root=root,
                remote_name="origin",
                branch=None,
                force_with_lease=True,
            )

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)
    captured = capsys.readouterr()
    assert "fetch" in captured.out.lower() or "advanced" in captured.out.lower()


# ---------------------------------------------------------------------------
# test_push_tags_includes_tags (regression from issue #77)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_tags_includes_tags(tmp_path: pathlib.Path) -> None:
    """--tags flag includes VCS tag refs in the push payload."""
    head_id = "tagpushhead1" * 5
    root = _init_repo(tmp_path)
    _write_branch_ref(root, "main", head_id)

    muse_dir = root / ".muse"
    (muse_dir / "config.toml").write_text(
        '[auth]\ntoken = "tok"\n\n[remotes.origin]\nurl = "https://hub.example.com/r"\n',
        encoding="utf-8",
    )

    # Write a tag ref
    tags_dir = muse_dir / "refs" / "tags"
    tags_dir.mkdir(parents=True)
    (tags_dir / "v1.0").write_text(head_id, encoding="utf-8")

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
            new=AsyncMock(return_value=[]),
        ),
        patch("maestro.muse_cli.commands.push.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.push.MuseHubClient", return_value=mock_hub),
    ):
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = mock_session_ctx

        await _push_async(root=root, remote_name="origin", branch=None, include_tags=True)

    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert "tags" in payload
    tags = payload["tags"]
    assert isinstance(tags, list)
    assert len(tags) == 1
    assert tags[0]["tag_name"] == "v1.0"
    assert tags[0]["commit_id"] == head_id


# ---------------------------------------------------------------------------
# test_push_set_upstream_writes_config (regression from issue #77)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_set_upstream_writes_config(tmp_path: pathlib.Path) -> None:
    """--set-upstream writes upstream tracking to .muse/config.toml after push."""
    head_id = "upstreamtest" * 5
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

        await _push_async(
            root=root,
            remote_name="origin",
            branch=None,
            set_upstream_flag=True,
        )

    # After push with --set-upstream, config should record tracking branch
    upstream = get_upstream("main", root)
    assert upstream == "origin"


@pytest.mark.anyio
async def test_push_force_flag_sends_force_in_payload(tmp_path: pathlib.Path) -> None:
    """--force flag sends force=True in the push payload."""
    head_id = "forcetest1234" * 4
    root = _init_repo(tmp_path)
    _write_branch_ref(root, "main", head_id)

    muse_dir = root / ".muse"
    (muse_dir / "config.toml").write_text(
        '[auth]\ntoken = "tok"\n\n[remotes.origin]\nurl = "https://hub.example.com/r"\n',
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
            new=AsyncMock(return_value=[]),
        ),
        patch("maestro.muse_cli.commands.push.open_session") as mock_open_session,
        patch("maestro.muse_cli.commands.push.MuseHubClient", return_value=mock_hub),
    ):
        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_open_session.return_value = mock_session_ctx

        await _push_async(root=root, remote_name="origin", branch=None, force=True)

    assert len(captured_payloads) == 1
    assert captured_payloads[0].get("force") is True
