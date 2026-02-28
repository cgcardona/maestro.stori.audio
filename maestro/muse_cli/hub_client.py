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


class PushRequest(TypedDict):
    """Payload sent to ``POST /musehub/repos/{repo_id}/push``."""

    branch: str
    head_commit_id: str
    commits: list[PushCommitPayload]
    objects: list[PushObjectPayload]


class PushResponse(TypedDict):
    """Response from the Hub push endpoint."""

    accepted: bool
    message: str


class PullRequest(TypedDict):
    """Payload sent to ``POST /musehub/repos/{repo_id}/pull``."""

    branch: str
    have_commits: list[str]
    have_objects: list[str]


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
# Fetch typed payload contracts
# ---------------------------------------------------------------------------


class FetchRequest(TypedDict):
    """Payload sent to ``POST /musehub/repos/{repo_id}/fetch``.

    ``branches`` lists the specific branch names to fetch.  An empty list
    means "fetch all branches" — the Hub returns all it knows about.
    """

    branches: list[str]


class FetchBranchInfo(TypedDict):
    """A single branch's current state on the remote, returned by fetch.

    ``is_new`` is ``True`` when the branch does not yet exist in the local
    remote-tracking refs (so the CLI can print "(new branch)" in the report).
    ``head_commit_id`` is the short-form commit ID suitable for display.
    """

    branch: str
    head_commit_id: str
    is_new: bool


class FetchResponse(TypedDict):
    """Response from the Hub fetch endpoint.

    ``branches`` lists every branch the Hub knows about (filtered by the
    request's ``branches`` list when non-empty).  The caller uses this to
    update local remote-tracking refs and, when ``--prune`` is active, to
    identify stale local refs that should be removed.
    """

    branches: list[FetchBranchInfo]


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


class CloneRequest(TypedDict):
    """Payload sent to ``POST /musehub/repos/{repo_id}/clone``.

    ``branch`` selects which branch HEAD to seed the clone from.  When
    ``depth`` is set the Hub returns only the last *N* commits (shallow
    clone).  ``single_track`` restricts returned file paths to those
    whose first path component matches the given instrument track name
    (e.g. ``"drums"``).
    """

    branch: str | None
    depth: int | None
    single_track: str | None


class CloneCommitPayload(TypedDict):
    """A single commit record received from the Hub during a clone."""

    commit_id: str
    parent_commit_id: str | None
    snapshot_id: str
    branch: str
    message: str
    author: str
    committed_at: str
    metadata: dict[str, object] | None


class CloneObjectPayload(TypedDict):
    """A content-addressed object descriptor received during a clone."""

    object_id: str
    size_bytes: int


class CloneResponse(TypedDict):
    """Response from the Hub clone endpoint.

    ``repo_id`` is the canonical Hub identifier for the cloned repository —
    stored in ``<target>/.muse/repo.json`` so subsequent push/pull calls can
    address the correct Hub repo.  ``default_branch`` is the branch that
    ``remote_head`` belongs to.  ``commits`` and ``objects`` carry the
    payload to seed the local database.
    """

    repo_id: str
    default_branch: str
    remote_head: str | None
    commits: list[CloneCommitPayload]
    objects: list[CloneObjectPayload]


__all__ = [
    "MuseHubClient",
    "PushCommitPayload",
    "PushObjectPayload",
    "PushRequest",
    "PushResponse",
    "PullRequest",
    "PullCommitPayload",
    "PullObjectPayload",
    "PullResponse",
    "CloneRequest",
    "CloneCommitPayload",
    "CloneObjectPayload",
    "CloneResponse",
    "FetchRequest",
    "FetchBranchInfo",
    "FetchResponse",
]
