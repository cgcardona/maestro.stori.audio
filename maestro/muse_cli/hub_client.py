"""Muse Hub HTTP client with JWT bearer authentication.

Reads the auth token from ``.muse/config.toml`` and injects it into every
outbound request as ``Authorization: Bearer <token>``.  The token value is
never written to logs — log lines use ``"Bearer ***"`` as a placeholder.

Usage::

    async with MuseHubClient(base_url="https://hub.example.com", repo_root=root) as hub:
        response = await hub.post("/push", json=payload)

If ``[auth] token`` is missing or empty in ``.muse/config.toml``, the client
raises :class:`typer.Exit` with exit-code ``1`` and prints an actionable
error message via :func:`typer.echo` before raising.

Security note: ``.muse/config.toml`` should be added to ``.gitignore`` to
prevent the token from being committed to version control.
"""
from __future__ import annotations

import logging
import pathlib
import types
from typing import TypedDict

import httpx
import typer

from maestro.muse_cli.config import get_auth_token
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

_MISSING_TOKEN_MSG = (
    "No auth token configured. "
    'Add `token = "..."` under `[auth]` in `.muse/config.toml`.'
)


# ---------------------------------------------------------------------------
# Push / Pull typed payload contracts
# ---------------------------------------------------------------------------


class PushCommitPayload(TypedDict):
    """A single commit record sent to the Hub during a push.

    All datetime fields are ISO-8601 strings (UTC).  ``metadata`` carries
    music-domain annotations (tempo_bpm, key, meter, etc.).
    """

    commit_id: str
    parent_commit_id: str | None
    snapshot_id: str
    branch: str
    message: str
    author: str
    committed_at: str
    metadata: dict[str, object] | None


class PushObjectPayload(TypedDict):
    """A content-addressed object descriptor sent during a push."""

    object_id: str
    size_bytes: int


class PushTagPayload(TypedDict):
    """A VCS-style tag ref sent during a push with ``--tags``.

    Represents a lightweight ref stored in ``.muse/refs/tags/<tag_name>``
    that points to a commit ID.
    """

    tag_name: str
    commit_id: str


class _PushRequestRequired(TypedDict):
    """Required fields for every push request."""

    branch: str
    head_commit_id: str
    commits: list[PushCommitPayload]
    objects: list[PushObjectPayload]


class PushRequest(_PushRequestRequired, total=False):
    """Payload sent to ``POST /musehub/repos/{repo_id}/push``.

    Optional flags control override behaviour and extra data:

    - ``force``: overwrite remote branch even on non-fast-forward.
    - ``force_with_lease``: overwrite only if remote HEAD matches
      ``expected_remote_head``; the Hub must reject if the remote has
      advanced since we last fetched.
    - ``expected_remote_head``: the commit ID we believe the remote HEAD to
      be (required when ``force_with_lease`` is ``True``).
    - ``tags``: VCS-style tag refs from ``.muse/refs/tags/`` to push alongside
      the branch commits.
    """

    force: bool
    force_with_lease: bool
    expected_remote_head: str | None
    tags: list[PushTagPayload]


class PushResponse(TypedDict):
    """Response from the Hub push endpoint."""

    accepted: bool
    message: str


class _PullRequestRequired(TypedDict):
    """Required fields for every pull request."""

    branch: str
    have_commits: list[str]
    have_objects: list[str]


class PullRequest(_PullRequestRequired, total=False):
    """Payload sent to ``POST /musehub/repos/{repo_id}/pull``.

    Optional flags are informational hints for the Hub (and drive local
    post-fetch behaviour):

    - ``rebase``: caller intends to rebase local commits onto the fetched
      remote HEAD rather than merge.
    - ``ff_only``: caller will refuse to integrate if the result would not be
      a fast-forward; the Hub may use this to gate the response.
    """

    rebase: bool
    ff_only: bool


class PullCommitPayload(TypedDict):
    """A single commit record received from the Hub during a pull."""

    commit_id: str
    parent_commit_id: str | None
    snapshot_id: str
    branch: str
    message: str
    author: str
    committed_at: str
    metadata: dict[str, object] | None


class PullObjectPayload(TypedDict):
    """A content-addressed object descriptor received during a pull."""

    object_id: str
    size_bytes: int


class PullResponse(TypedDict):
    """Response from the Hub pull endpoint.

    ``diverged`` is ``True`` when the remote HEAD is not an ancestor of the
    local branch HEAD — the caller should display a divergence warning.
    """

    commits: list[PullCommitPayload]
    objects: list[PullObjectPayload]
    remote_head: str | None
    diverged: bool


# ---------------------------------------------------------------------------
# MuseHubClient
# ---------------------------------------------------------------------------


class MuseHubClient:
    """Async HTTP client for the Muse Hub API.

    Wraps :class:`httpx.AsyncClient` and injects the Bearer token read from
    ``.muse/config.toml`` into every request.  All auth logic is handled at
    construction time — if the token is absent the caller never reaches the
    first network call.

    Args:
        base_url: Muse Hub base URL (e.g. ``"https://hub.example.com"``).
        repo_root: Repository root to search for ``.muse/config.toml``.
                   Defaults to ``Path.cwd()``.
        timeout: Request timeout in seconds (default 30).
    """

    def __init__(
        self,
        base_url: str,
        repo_root: pathlib.Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url
        self._repo_root = repo_root
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_auth_headers(self) -> dict[str, str]:
        """Return ``{"Authorization": "Bearer <token>"}`` or exit 1.

        Reads the token from ``.muse/config.toml`` via
        :func:`~maestro.muse_cli.config.get_auth_token`.  If the token is
        absent or empty, prints an actionable message and raises
        :class:`typer.Exit` with code 1.

        The raw token value is never logged.
        """
        token = get_auth_token(self._repo_root)
        if not token:
            typer.echo(_MISSING_TOKEN_MSG)
            raise typer.Exit(code=int(ExitCode.USER_ERROR))
        logger.debug("✅ MuseHubClient auth header set (Bearer ***)")
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> MuseHubClient:
        headers = self._build_auth_headers()
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # HTTP verb helpers (thin wrappers around httpx.AsyncClient)
    # ------------------------------------------------------------------

    def _require_client(self) -> httpx.AsyncClient:
        """Return the underlying client or raise if not inside context manager."""
        if self._client is None:
            raise RuntimeError(
                "MuseHubClient must be used as an async context manager."
            )
        return self._client

    async def get(self, path: str, **kwargs: object) -> httpx.Response:
        """Issue a GET request to *path*."""
        return await self._require_client().get(path, **kwargs)  # type: ignore[arg-type]  # httpx stubs use Any for kwargs

    async def post(self, path: str, **kwargs: object) -> httpx.Response:
        """Issue a POST request to *path*."""
        return await self._require_client().post(path, **kwargs)  # type: ignore[arg-type]  # httpx stubs use Any for kwargs

    async def put(self, path: str, **kwargs: object) -> httpx.Response:
        """Issue a PUT request to *path*."""
        return await self._require_client().put(path, **kwargs)  # type: ignore[arg-type]  # httpx stubs use Any for kwargs

    async def delete(self, path: str, **kwargs: object) -> httpx.Response:
        """Issue a DELETE request to *path*."""
        return await self._require_client().delete(path, **kwargs)  # type: ignore[arg-type]  # httpx stubs use Any for kwargs


__all__ = [
    "MuseHubClient",
    "PushCommitPayload",
    "PushObjectPayload",
    "PushTagPayload",
    "PushRequest",
    "PushResponse",
    "PullRequest",
    "PullCommitPayload",
    "PullObjectPayload",
    "PullResponse",
]
