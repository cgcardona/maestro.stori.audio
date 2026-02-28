"""Tests for ``muse clone``.

Covers acceptance criteria from issue #73:
- ``muse clone <url>`` creates a new directory with .muse/ initialised.
- ``muse clone <url> my-project`` creates ./my-project/.
- Directory name is derived from the URL when no directory argument is given.
- ``--depth N`` is forwarded to the Hub in the clone request.
- ``--branch feature/guitar`` is forwarded and applied to HEAD.
- ``--single-track drums`` is forwarded in the request payload.
- ``--no-checkout`` skips creation of muse-work/.
- ``.muse/config.toml`` has origin remote set to source URL.
- Commits returned from Hub are stored in local Postgres.
- Remote tracking pointer is written after a successful clone.
- Cloning into an existing directory exits 1 with an instructive message.

All HTTP calls are mocked — no live network required.
DB calls are mocked for integration tests; unit-level DB tests use the
in-memory SQLite fixture from conftest.py.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maestro.muse_cli.commands.clone import (
    _clone_async,
    _derive_directory_name,
    _init_muse_dir,
)
from maestro.muse_cli.config import get_remote, get_remote_head
from maestro.muse_cli.db import store_pulled_commit
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clone_response(
    repo_id: str = "hub-repo-abc123",
    default_branch: str = "main",
    remote_head: str | None = "remote-commit-001",
    commits: list[dict[str, object]] | None = None,
    objects: list[dict[str, object]] | None = None,
) -> MagicMock:
    """Return a mock httpx.Response for the clone endpoint."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "repo_id": repo_id,
        "default_branch": default_branch,
        "remote_head": remote_head,
        "commits": commits or [],
        "objects": objects or [],
    }
    return mock_resp


def _write_token_config(root: pathlib.Path, url: str) -> None:
    """Write a config.toml with auth token and origin remote."""
    muse_dir = root / ".muse"
    muse_dir.mkdir(parents=True, exist_ok=True)
    (muse_dir / "config.toml").write_text(
        f'[auth]\ntoken = "test-token"\n\n[remotes.origin]\nurl = "{url}"\n',
        encoding="utf-8",
    )


def _mock_hub(mock_response: MagicMock) -> MagicMock:
    """Build a mock MuseHubClient that returns *mock_response* for POST /clone."""
    hub = MagicMock()
    hub.__aenter__ = AsyncMock(return_value=hub)
    hub.__aexit__ = AsyncMock(return_value=None)
    hub.post = AsyncMock(return_value=mock_response)
    return hub


def _run_clone(
    *,
    url: str,
    directory: str | None,
    depth: int | None = None,
    branch: str | None = None,
    single_track: str | None = None,
    no_checkout: bool = False,
    mock_response: MagicMock | None = None,
) -> None:
    """Run _clone_async with all DB/HTTP layers mocked."""
    response = mock_response or _make_clone_response()
    mock_hub = _mock_hub(response)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "maestro.muse_cli.commands.clone.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.clone.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.clone.open_session", return_value=mock_session_ctx),
        patch("maestro.muse_cli.commands.clone.MuseHubClient", return_value=mock_hub),
    ):
        asyncio.run(
            _clone_async(
                url=url,
                directory=directory,
                depth=depth,
                branch=branch,
                single_track=single_track,
                no_checkout=no_checkout,
            )
        )


# ---------------------------------------------------------------------------
# Unit tests: _derive_directory_name
# ---------------------------------------------------------------------------


def test_derive_directory_name_simple_path() -> None:
    """Last URL path component is used as directory name."""
    assert _derive_directory_name("https://hub.stori.app/repos/my-project") == "my-project"


def test_derive_directory_name_trailing_slash() -> None:
    """Trailing slashes are stripped before extraction."""
    assert _derive_directory_name("https://hub.stori.app/repos/my-project/") == "my-project"


def test_derive_directory_name_bare_root() -> None:
    """Fallback to 'muse-clone' when URL has no useful last segment."""
    assert _derive_directory_name("https://hub.stori.app/repos") == "muse-clone"


def test_derive_directory_name_with_uuid_segment() -> None:
    """UUID-style repo paths preserve the identifier."""
    result = _derive_directory_name("https://hub.stori.app/repos/abc-123-def")
    assert result == "abc-123-def"


# ---------------------------------------------------------------------------
# Unit tests: _init_muse_dir
# ---------------------------------------------------------------------------


def test_init_muse_dir_creates_dot_muse(tmp_path: pathlib.Path) -> None:
    """_init_muse_dir creates the .muse/ directory tree."""
    _init_muse_dir(tmp_path, "main", "https://hub.stori.app/repos/foo")
    assert (tmp_path / ".muse").is_dir()
    assert (tmp_path / ".muse" / "refs" / "heads").is_dir()
    assert (tmp_path / ".muse" / "repo.json").is_file()
    assert (tmp_path / ".muse" / "HEAD").is_file()
    assert (tmp_path / ".muse" / "config.toml").is_file()


def test_init_muse_dir_head_points_at_branch(tmp_path: pathlib.Path) -> None:
    """HEAD is written as refs/heads/<branch>."""
    _init_muse_dir(tmp_path, "feature/guitar", "https://hub.example.com/r")
    head = (tmp_path / ".muse" / "HEAD").read_text(encoding="utf-8").strip()
    assert head == "refs/heads/feature/guitar"


def test_init_muse_dir_origin_remote_set(tmp_path: pathlib.Path) -> None:
    """Origin remote URL is written to config.toml."""
    url = "https://hub.stori.app/repos/my-project"
    _init_muse_dir(tmp_path, "main", url)
    stored = get_remote("origin", tmp_path)
    assert stored == url


def test_init_muse_dir_repo_json_has_schema_version(tmp_path: pathlib.Path) -> None:
    """repo.json contains schema_version field."""
    _init_muse_dir(tmp_path, "main", "https://hub.example.com/r")
    data = json.loads((tmp_path / ".muse" / "repo.json").read_text())
    assert "schema_version" in data
    assert "repo_id" in data


# ---------------------------------------------------------------------------
# Regression test: test_clone_creates_repo_with_origin_remote
# ---------------------------------------------------------------------------


def test_clone_creates_repo_with_origin_remote(tmp_path: pathlib.Path) -> None:
    """muse clone creates .muse/ and sets origin remote to the source URL.

    Regression for issue #73 — collaborators had no way to create a local
    copy of a remote Muse Hub repo from scratch.
    """
    url = "https://hub.stori.app/repos/my-project"
    target = tmp_path / "my-project"

    _run_clone(url=url, directory=str(target))

    assert target.is_dir()
    assert (target / ".muse").is_dir()
    stored_origin = get_remote("origin", target)
    assert stored_origin == url


def test_clone_creates_directory_from_url_name(tmp_path: pathlib.Path) -> None:
    """muse clone without explicit directory uses URL last component as name."""
    import os

    url = "https://hub.stori.app/repos/producer-beats"

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        _run_clone(url=url, directory=None)
    finally:
        os.chdir(old_cwd)

    assert (tmp_path / "producer-beats").is_dir()
    assert (tmp_path / "producer-beats" / ".muse").is_dir()


def test_clone_explicit_directory(tmp_path: pathlib.Path) -> None:
    """muse clone <url> my-project creates ./my-project/."""
    url = "https://hub.stori.app/repos/some-repo"
    target = tmp_path / "my-custom-name"

    _run_clone(url=url, directory=str(target))

    assert target.is_dir()
    assert (target / ".muse").is_dir()


def test_clone_existing_directory_exits_1(tmp_path: pathlib.Path) -> None:
    """muse clone into an existing directory exits 1."""
    import typer

    url = "https://hub.stori.app/repos/foo"
    existing = tmp_path / "foo"
    existing.mkdir()

    with pytest.raises(typer.Exit) as exc_info:
        _run_clone(url=url, directory=str(existing))

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


def test_clone_existing_directory_message(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Clone into existing directory prints an instructive error."""
    import typer

    url = "https://hub.stori.app/repos/foo"
    existing = tmp_path / "foo"
    existing.mkdir()

    with pytest.raises(typer.Exit):
        _run_clone(url=url, directory=str(existing))

    captured = capsys.readouterr()
    assert "already exists" in captured.out


# ---------------------------------------------------------------------------
# Hub interaction tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_clone_calls_hub_post_clone(tmp_path: pathlib.Path) -> None:
    """muse clone POSTs to /clone with the correct payload fields."""
    url = "https://hub.stori.app/repos/collab-track"
    target = tmp_path / "collab-track"

    captured_payloads: list[dict[str, object]] = []
    mock_response = _make_clone_response()

    hub = MagicMock()
    hub.__aenter__ = AsyncMock(return_value=hub)
    hub.__aexit__ = AsyncMock(return_value=None)

    async def fake_post(path: str, **kwargs: object) -> MagicMock:
        payload = kwargs.get("json", {})
        if isinstance(payload, dict):
            captured_payloads.append(payload)
        return mock_response

    hub.post = fake_post

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "maestro.muse_cli.commands.clone.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.clone.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.clone.open_session", return_value=mock_session_ctx),
        patch("maestro.muse_cli.commands.clone.MuseHubClient", return_value=hub),
    ):
        await _clone_async(
            url=url,
            directory=str(target),
            depth=5,
            branch="main",
            single_track="drums",
            no_checkout=False,
        )

    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert payload["depth"] == 5
    assert payload["branch"] == "main"
    assert payload["single_track"] == "drums"


def test_clone_depth_forwarded_in_request(tmp_path: pathlib.Path) -> None:
    """--depth N is forwarded to Hub in the clone request payload."""
    url = "https://hub.stori.app/repos/shallow-repo"
    target = tmp_path / "shallow-repo"

    captured: list[dict[str, object]] = []
    mock_response = _make_clone_response()

    hub = MagicMock()
    hub.__aenter__ = AsyncMock(return_value=hub)
    hub.__aexit__ = AsyncMock(return_value=None)

    async def fake_post(path: str, **kwargs: object) -> MagicMock:
        payload = kwargs.get("json", {})
        if isinstance(payload, dict):
            captured.append(payload)
        return mock_response

    hub.post = fake_post

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "maestro.muse_cli.commands.clone.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.clone.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.clone.open_session", return_value=mock_session_ctx),
        patch("maestro.muse_cli.commands.clone.MuseHubClient", return_value=hub),
    ):
        asyncio.run(
            _clone_async(
                url=url,
                directory=str(target),
                depth=1,
                branch=None,
                single_track=None,
                no_checkout=False,
            )
        )

    assert captured[0]["depth"] == 1


def test_clone_single_track_forwarded(tmp_path: pathlib.Path) -> None:
    """--single-track TEXT is forwarded to Hub in the clone request."""
    url = "https://hub.stori.app/repos/full-band"
    target = tmp_path / "full-band"

    captured: list[dict[str, object]] = []
    mock_response = _make_clone_response()

    hub = MagicMock()
    hub.__aenter__ = AsyncMock(return_value=hub)
    hub.__aexit__ = AsyncMock(return_value=None)

    async def fake_post(path: str, **kwargs: object) -> MagicMock:
        payload = kwargs.get("json", {})
        if isinstance(payload, dict):
            captured.append(payload)
        return mock_response

    hub.post = fake_post

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "maestro.muse_cli.commands.clone.store_pulled_commit",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "maestro.muse_cli.commands.clone.store_pulled_object",
            new=AsyncMock(return_value=False),
        ),
        patch("maestro.muse_cli.commands.clone.open_session", return_value=mock_session_ctx),
        patch("maestro.muse_cli.commands.clone.MuseHubClient", return_value=hub),
    ):
        asyncio.run(
            _clone_async(
                url=url,
                directory=str(target),
                depth=None,
                branch=None,
                single_track="keys",
                no_checkout=False,
            )
        )

    assert captured[0]["single_track"] == "keys"


# ---------------------------------------------------------------------------
# Filesystem structure tests
# ---------------------------------------------------------------------------


def test_clone_writes_repo_id_from_hub(tmp_path: pathlib.Path) -> None:
    """repo_id returned by Hub is written into .muse/repo.json."""
    url = "https://hub.stori.app/repos/hub-id-test"
    target = tmp_path / "hub-id-test"
    response = _make_clone_response(repo_id="canonical-hub-uuid-9999")

    _run_clone(url=url, directory=str(target), mock_response=response)

    data = json.loads((target / ".muse" / "repo.json").read_text())
    assert data["repo_id"] == "canonical-hub-uuid-9999"


def test_clone_updates_branch_ref_with_remote_head(tmp_path: pathlib.Path) -> None:
    """After clone, .muse/refs/heads/<branch> contains the remote HEAD commit ID."""
    url = "https://hub.stori.app/repos/ref-test"
    target = tmp_path / "ref-test"
    head_id = "commit-abc123def456" * 3
    response = _make_clone_response(remote_head=head_id, default_branch="main")

    _run_clone(url=url, directory=str(target), mock_response=response)

    ref_path = target / ".muse" / "refs" / "heads" / "main"
    assert ref_path.is_file()
    assert ref_path.read_text(encoding="utf-8").strip() == head_id


def test_clone_writes_remote_tracking_head(tmp_path: pathlib.Path) -> None:
    """After clone, .muse/remotes/origin/<branch> is written with remote HEAD."""
    url = "https://hub.stori.app/repos/tracking-test"
    target = tmp_path / "tracking-test"
    head_id = "tracking-head-id99887766" * 2
    response = _make_clone_response(remote_head=head_id, default_branch="main")

    _run_clone(url=url, directory=str(target), mock_response=response)

    stored = get_remote_head("origin", "main", target)
    assert stored == head_id


def test_clone_no_checkout_skips_muse_work(tmp_path: pathlib.Path) -> None:
    """--no-checkout leaves muse-work/ unpopulated."""
    url = "https://hub.stori.app/repos/no-co-test"
    target = tmp_path / "no-co-test"

    _run_clone(url=url, directory=str(target), no_checkout=True)

    assert not (target / "muse-work").is_dir()


def test_clone_without_no_checkout_creates_muse_work(tmp_path: pathlib.Path) -> None:
    """Without --no-checkout, muse-work/ is created."""
    url = "https://hub.stori.app/repos/co-test"
    target = tmp_path / "co-test"

    _run_clone(url=url, directory=str(target), no_checkout=False)

    assert (target / "muse-work").is_dir()


def test_clone_branch_sets_head(tmp_path: pathlib.Path) -> None:
    """--branch sets HEAD to the specified branch name."""
    url = "https://hub.stori.app/repos/branch-test"
    target = tmp_path / "branch-test"
    response = _make_clone_response(default_branch="feature/guitar")

    _run_clone(url=url, directory=str(target), branch="feature/guitar", mock_response=response)

    head = (target / ".muse" / "HEAD").read_text(encoding="utf-8").strip()
    assert head == "refs/heads/feature/guitar"


# ---------------------------------------------------------------------------
# DB storage tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_clone_stores_commits_in_db(muse_cli_db_session: object) -> None:
    """Commits returned from Hub clone are stored via store_pulled_commit."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from maestro.muse_cli.models import MuseCliCommit

    session: AsyncSession = muse_cli_db_session  # type: ignore[assignment]

    commit_data: dict[str, object] = {
        "commit_id": "cloned-commit-aabbcc" * 3,
        "repo_id": "test-hub-repo",
        "parent_commit_id": None,
        "snapshot_id": "snap-clone-001",
        "branch": "main",
        "message": "Initial commit from Hub",
        "author": "producer@example.com",
        "committed_at": "2025-03-01T12:00:00+00:00",
        "metadata": None,
    }

    inserted = await store_pulled_commit(session, commit_data)
    await session.commit()

    assert inserted is True

    commit_id = str(commit_data["commit_id"])
    stored = await session.get(MuseCliCommit, commit_id)
    assert stored is not None
    assert stored.branch == "main"
    assert stored.message == "Initial commit from Hub"


# ---------------------------------------------------------------------------
# Hub error handling
# ---------------------------------------------------------------------------


def test_clone_hub_non_200_exits_3(tmp_path: pathlib.Path) -> None:
    """Hub returning non-200 causes muse clone to exit 3."""
    import typer

    url = "https://hub.stori.app/repos/error-test"
    target = tmp_path / "error-test"

    error_response = MagicMock()
    error_response.status_code = 404
    error_response.text = "Not found"

    hub = _mock_hub(error_response)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("maestro.muse_cli.commands.clone.open_session", return_value=mock_session_ctx),
        patch("maestro.muse_cli.commands.clone.MuseHubClient", return_value=hub),
        pytest.raises(typer.Exit) as exc_info,
    ):
        asyncio.run(
            _clone_async(
                url=url,
                directory=str(target),
                depth=None,
                branch=None,
                single_track=None,
                no_checkout=False,
            )
        )

    assert exc_info.value.exit_code == int(ExitCode.INTERNAL_ERROR)


def test_clone_network_error_exits_3(tmp_path: pathlib.Path) -> None:
    """Network error during clone causes exit 3."""
    import httpx
    import typer

    url = "https://hub.stori.app/repos/net-err"
    target = tmp_path / "net-err"

    hub = MagicMock()
    hub.__aenter__ = AsyncMock(return_value=hub)
    hub.__aexit__ = AsyncMock(return_value=None)
    hub.post = AsyncMock(side_effect=httpx.NetworkError("connection refused"))

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("maestro.muse_cli.commands.clone.open_session", return_value=mock_session_ctx),
        patch("maestro.muse_cli.commands.clone.MuseHubClient", return_value=hub),
        pytest.raises(typer.Exit) as exc_info,
    ):
        asyncio.run(
            _clone_async(
                url=url,
                directory=str(target),
                depth=None,
                branch=None,
                single_track=None,
                no_checkout=False,
            )
        )

    assert exc_info.value.exit_code == int(ExitCode.INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# CLI skeleton test (app registration)
# ---------------------------------------------------------------------------


def test_clone_registered_in_cli() -> None:
    """'clone' command is registered in the muse CLI app."""
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "clone" in result.output
