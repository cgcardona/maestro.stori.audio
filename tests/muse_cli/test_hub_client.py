"""Tests for MuseHubClient — JWT auth injection and error handling.

Covers acceptance criteria from issue #47:
- Token from config.toml is sent in Authorization header on every request.
- Missing/empty token causes exit 1 with an actionable message.
- The raw token value never appears in log output.

All tests are fully isolated: they use ``tmp_path`` to create
``.muse/config.toml`` without touching the real filesystem, and
``unittest.mock`` to avoid real HTTP requests.
"""
from __future__ import annotations

import logging
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from maestro.muse_cli.hub_client import MuseHubClient, _MISSING_TOKEN_MSG
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(muse_dir: pathlib.Path, token: str) -> None:
    """Write a minimal .muse/config.toml with the given token."""
    muse_dir.mkdir(parents=True, exist_ok=True)
    (muse_dir / "config.toml").write_text(
        f'[auth]\ntoken = "{token}"\n',
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# test_hub_client_reads_token_from_config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_client_reads_token_from_config(tmp_path: pathlib.Path) -> None:
    """Token from config.toml appears in Authorization header of every request.

    The mock captures the headers passed to httpx.AsyncClient.__init__ so we
    can assert without making a real network call.
    """
    _write_config(tmp_path / ".muse", "super-secret-token-abc123")

    captured_headers: dict[str, str] = {}

    mock_async_client = MagicMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=None)
    mock_async_client.aclose = AsyncMock()

    def _fake_client_init(**kwargs: object) -> MagicMock:
        raw = kwargs.get("headers", {})
        if isinstance(raw, dict):
            captured_headers.update(raw)
        return mock_async_client

    with patch(
        "maestro.muse_cli.hub_client.httpx.AsyncClient",
        side_effect=_fake_client_init,
    ):
        hub = MuseHubClient(base_url="https://hub.example.com", repo_root=tmp_path)
        async with hub:
            pass

    assert "Authorization" in captured_headers
    assert captured_headers["Authorization"] == "Bearer super-secret-token-abc123"


# ---------------------------------------------------------------------------
# test_hub_client_missing_token_exits_1
# ---------------------------------------------------------------------------


def test_hub_client_missing_token_exits_1(tmp_path: pathlib.Path) -> None:
    """_build_auth_headers raises typer.Exit(1) when [auth] token is absent.

    Creates a .muse dir but no config.toml, so get_auth_token returns None.
    The client must print the instructive message and exit with code 1.
    """
    (tmp_path / ".muse").mkdir()

    hub = MuseHubClient(base_url="https://hub.example.com", repo_root=tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        hub._build_auth_headers()

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


def test_hub_client_missing_token_message_is_instructive(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The error message tells the user exactly how to fix the problem."""
    (tmp_path / ".muse").mkdir()

    hub = MuseHubClient(base_url="https://hub.example.com", repo_root=tmp_path)

    with pytest.raises(typer.Exit):
        hub._build_auth_headers()

    # typer.echo writes to stdout
    captured = capsys.readouterr()
    assert "No auth token configured" in captured.out
    assert "config.toml" in captured.out


def test_hub_client_empty_token_exits_1(tmp_path: pathlib.Path) -> None:
    """_build_auth_headers exits 1 when token is present but empty string."""
    _write_config(tmp_path / ".muse", "")

    hub = MuseHubClient(base_url="https://hub.example.com", repo_root=tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        hub._build_auth_headers()

    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


# ---------------------------------------------------------------------------
# test_hub_client_token_not_logged
# ---------------------------------------------------------------------------


def test_hub_client_token_not_logged(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The raw token value never appears in any log record.

    Uses caplog to capture all log records at DEBUG level and asserts that
    the actual token string is absent from every message.
    """
    secret_token = "my-very-secret-jwt-token-xyz789"
    _write_config(tmp_path / ".muse", secret_token)

    hub = MuseHubClient(base_url="https://hub.example.com", repo_root=tmp_path)

    with caplog.at_level(logging.DEBUG, logger="maestro.muse_cli.hub_client"):
        hub._build_auth_headers()

    for record in caplog.records:
        assert secret_token not in record.getMessage(), (
            f"Token value leaked into log record: {record.getMessage()!r}"
        )

    # Also assert the masked placeholder is used (positive signal)
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Bearer ***" in log_text


# ---------------------------------------------------------------------------
# test_hub_client_requires_context_manager
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_client_requires_context_manager(tmp_path: pathlib.Path) -> None:
    """Calling .get() outside async context manager raises RuntimeError."""
    _write_config(tmp_path / ".muse", "some-token")

    hub = MuseHubClient(base_url="https://hub.example.com", repo_root=tmp_path)

    with pytest.raises(RuntimeError, match="async context manager"):
        await hub.get("/api/v1/musehub/repos/test")


# ---------------------------------------------------------------------------
# test_hub_client_closes_on_exit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_client_closes_http_session_on_exit(tmp_path: pathlib.Path) -> None:
    """The underlying httpx.AsyncClient is closed on context manager exit."""
    _write_config(tmp_path / ".muse", "close-test-token")

    aclose_called = False

    class _FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def aclose(self) -> None:
            nonlocal aclose_called
            aclose_called = True

    with patch("maestro.muse_cli.hub_client.httpx.AsyncClient", _FakeAsyncClient):
        hub = MuseHubClient(base_url="https://hub.example.com", repo_root=tmp_path)
        async with hub:
            pass

    assert aclose_called, "httpx.AsyncClient.aclose() must be called on exit"
