"""muse fetch — download remote refs without modifying local branches.

Fetch algorithm
---------------
1. Resolve repo root and read ``repo_id`` from ``.muse/repo.json``.
2. Resolve remote(s) to fetch from ``--all`` → all remotes in ``.muse/config.toml``,
   or the single ``--remote`` name (default: ``origin``).
3. For each remote, POST to ``<remote_url>/fetch`` with the list of branches
   to fetch (empty list = all branches).
4. Store returned remote-tracking refs in ``.muse/remotes/<remote>/<branch>``
   without touching local branches or muse-work/.
5. If ``--prune`` is active, remove any ``.muse/remotes/<remote>/<branch>``
   files whose branch no longer exists on the remote.
6. Print a per-branch report line modelled on git's output format::

       From origin: + abc1234 feature/guitar -> origin/feature/guitar (new branch)

Exit codes:
  0 — success (all remotes fetched without error)
  1 — user error (no remote configured, bad args)
  2 — not a Muse repository
  3 — network / server error
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib

import httpx
import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.config import (
    get_remote,
    get_remote_head,
    list_remotes,
    set_remote_head,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.hub_client import (
    FetchBranchInfo,
    FetchRequest,
    FetchResponse,
    MuseHubClient,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_NO_REMOTE_MSG = (
    "No remote named 'origin'. "
    "Run `muse remote add origin <url>` to configure one."
)

_NO_REMOTES_MSG = (
    "No remotes configured. "
    "Run `muse remote add <name> <url>` to add one."
)


# ---------------------------------------------------------------------------
# Remote-tracking ref filesystem helpers
# ---------------------------------------------------------------------------


def _list_local_remote_tracking_branches(
    remote_name: str,
    root: pathlib.Path,
) -> list[str]:
    """Return branch names for which local remote-tracking refs exist.

    Recursively walks ``.muse/remotes/<remote_name>/`` and returns the
    relative path of every *file* found, which corresponds to the branch
    name (including namespace prefixes such as ``feature/groove``).
    Returns an empty list when the directory does not yet exist.

    Branch names containing ``/`` are stored as nested directories by
    :func:`~maestro.muse_cli.config.set_remote_head`, so a simple
    ``iterdir()`` is insufficient — a recursive walk is required.

    Args:
        remote_name: Remote name (e.g. ``"origin"``).
        root: Repository root.

    Returns:
        Sorted list of branch name strings (relative paths).
    """
    remotes_dir = root / ".muse" / "remotes" / remote_name
    if not remotes_dir.is_dir():
        return []
    return sorted(
        str(p.relative_to(remotes_dir))
        for p in remotes_dir.rglob("*")
        if p.is_file()
    )


def _remove_remote_tracking_ref(
    remote_name: str,
    branch: str,
    root: pathlib.Path,
) -> None:
    """Delete the local remote-tracking pointer for *remote_name*/*branch*.

    Silently ignores missing files — pruning is idempotent.

    Args:
        remote_name: Remote name (e.g. ``"origin"``).
        branch: Branch name to prune.
        root: Repository root.
    """
    pointer = root / ".muse" / "remotes" / remote_name / branch
    if pointer.is_file():
        pointer.unlink()
        logger.debug("✅ Pruned stale ref %s/%s", remote_name, branch)


# ---------------------------------------------------------------------------
# Fetch report formatting
# ---------------------------------------------------------------------------


def _format_fetch_line(
    remote_name: str,
    info: FetchBranchInfo,
) -> str:
    """Format a single fetch report line in git-style output.

    Example output::

        From origin: + abc1234 feature/guitar -> origin/feature/guitar (new branch)
        From origin: + def5678 main -> origin/main

    Args:
        remote_name: The remote that was fetched from.
        info: Branch info returned by the Hub.

    Returns:
        A human-readable status line.
    """
    short_id = info["head_commit_id"][:8]
    branch = info["branch"]
    suffix = " (new branch)" if info["is_new"] else ""
    return f"From {remote_name}: + {short_id} {branch} -> {remote_name}/{branch}{suffix}"


# ---------------------------------------------------------------------------
# Single-remote fetch core
# ---------------------------------------------------------------------------


async def _fetch_remote_async(
    *,
    root: pathlib.Path,
    remote_name: str,
    branches: list[str],
    prune: bool,
) -> int:
    """Fetch refs from a single remote and update local remote-tracking pointers.

    Does NOT touch local branches or muse-work/.

    Args:
        root: Repository root path.
        remote_name: Name of the remote to fetch from (e.g. ``"origin"``).
        branches: Specific branches to fetch.  Empty list means all branches.
        prune: When ``True``, remove stale local remote-tracking refs after
               fetching.

    Returns:
        Number of branches updated (new or moved).

    Raises:
        :class:`typer.Exit`: On unrecoverable errors (network, config, server).
    """
    remote_url = get_remote(remote_name, root)
    if not remote_url:
        if remote_name == "origin":
            typer.echo(_NO_REMOTE_MSG)
        else:
            typer.echo(
                f"No remote named '{remote_name}'. "
                "Run `muse remote add` to configure it."
            )
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    fetch_request = FetchRequest(branches=branches)

    try:
        async with MuseHubClient(base_url=remote_url, repo_root=root) as hub:
            response = await hub.post("/fetch", json=fetch_request)

        if response.status_code != 200:
            typer.echo(
                f"❌ Hub rejected fetch (HTTP {response.status_code}): {response.text}"
            )
            logger.error(
                "❌ muse fetch failed: HTTP %d — %s",
                response.status_code,
                response.text,
            )
            raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    except typer.Exit:
        raise
    except httpx.TimeoutException:
        typer.echo(f"❌ Fetch timed out connecting to {remote_url}")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
    except httpx.HTTPError as exc:
        typer.echo(f"❌ Network error during fetch: {exc}")
        logger.error("❌ muse fetch network error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    # ── Parse response ───────────────────────────────────────────────────
    raw_body: object = response.json()
    if not isinstance(raw_body, dict):
        typer.echo("❌ Hub returned unexpected fetch response shape.")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    raw_branches = raw_body.get("branches", [])
    if not isinstance(raw_branches, list):
        raw_branches = []

    fetch_response: FetchResponse = FetchResponse(
        branches=[
            FetchBranchInfo(
                branch=str(b.get("branch", "")),
                head_commit_id=str(b.get("head_commit_id", "")),
                is_new=bool(b.get("is_new", False)),
            )
            for b in raw_branches
            if isinstance(b, dict)
        ]
    )

    # ── Determine which branches are new locally ──────────────────────────
    # Override is_new from the Hub: the Hub may not know whether we have a
    # local tracking ref.  We always check ourselves.
    updated_count = 0
    remote_branch_names: set[str] = set()

    for branch_info in fetch_response["branches"]:
        branch = branch_info["branch"]
        head_id = branch_info["head_commit_id"]
        if not branch or not head_id:
            continue

        remote_branch_names.add(branch)

        # Determine newness from local state, not the Hub's hint
        existing_local_head = get_remote_head(remote_name, branch, root)
        is_new = existing_local_head is None
        branch_info = FetchBranchInfo(
            branch=branch,
            head_commit_id=head_id,
            is_new=is_new,
        )

        # Only update (and count) if the remote HEAD actually moved
        if existing_local_head != head_id:
            set_remote_head(remote_name, branch, head_id, root)
            updated_count += 1
            typer.echo(_format_fetch_line(remote_name, branch_info))
        else:
            logger.debug("✅ %s/%s already up to date [%s]", remote_name, branch, head_id[:8])

    # ── Prune stale refs ──────────────────────────────────────────────────
    if prune:
        local_branches = _list_local_remote_tracking_branches(remote_name, root)
        for local_branch in local_branches:
            if local_branch not in remote_branch_names:
                _remove_remote_tracking_ref(remote_name, local_branch, root)
                typer.echo(
                    f"✂️  Pruned {remote_name}/{local_branch} "
                    "(no longer exists on remote)"
                )

    return updated_count


# ---------------------------------------------------------------------------
# Multi-remote fetch entry point
# ---------------------------------------------------------------------------


async def _fetch_async(
    *,
    root: pathlib.Path,
    remote_name: str,
    fetch_all: bool,
    prune: bool,
    branches: list[str],
) -> None:
    """Orchestrate fetch across one or all remotes.

    Args:
        root: Repository root path.
        remote_name: Remote to fetch from (ignored when ``fetch_all`` is ``True``).
        fetch_all: When ``True``, fetch from every remote in ``.muse/config.toml``.
        prune: When ``True``, remove stale remote-tracking refs after fetching.
        branches: Specific branches to request.  Empty = all branches.

    Raises:
        :class:`typer.Exit`: On any unrecoverable error.
    """
    if fetch_all:
        remotes = list_remotes(root)
        if not remotes:
            typer.echo(_NO_REMOTES_MSG)
            raise typer.Exit(code=int(ExitCode.USER_ERROR))

        total_updated = 0
        for remote_cfg in remotes:
            r_name = remote_cfg["name"]
            count = await _fetch_remote_async(
                root=root,
                remote_name=r_name,
                branches=branches,
                prune=prune,
            )
            total_updated += count

        if total_updated == 0:
            typer.echo("✅ Everything up to date — all remotes are current.")
        else:
            typer.echo(f"✅ Fetched {total_updated} branch update(s) across all remotes.")
    else:
        count = await _fetch_remote_async(
            root=root,
            remote_name=remote_name,
            branches=branches,
            prune=prune,
        )
        if count == 0:
            typer.echo(f"✅ {remote_name} is already up to date.")

    logger.info("✅ muse fetch complete")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def fetch(
    ctx: typer.Context,
    remote: str = typer.Option(
        "origin",
        "--remote",
        help="Remote name to fetch from.",
    ),
    fetch_all: bool = typer.Option(
        False,
        "--all",
        help="Fetch from all configured remotes.",
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        "-p",
        help="Remove local remote-tracking refs that no longer exist on the remote.",
    ),
    branch: list[str] = typer.Option(
        [],
        "--branch",
        "-b",
        help="Branch to fetch (repeatable). Defaults to all branches.",
    ),
) -> None:
    """Fetch refs and objects from remote without merging.

    Updates ``.muse/remotes/<remote>/<branch>`` tracking pointers to reflect
    the current state of the remote without modifying the local branch or
    muse-work/.  Use ``muse pull`` to fetch AND merge in one step.

    Examples::

        muse fetch
        muse fetch --all
        muse fetch --prune
        muse fetch --remote staging --branch main --branch feature/bass-v2
    """
    root = require_repo()

    try:
        asyncio.run(
            _fetch_async(
                root=root,
                remote_name=remote,
                fetch_all=fetch_all,
                prune=prune,
                branches=list(branch),
            )
        )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse fetch failed: {exc}")
        logger.error("❌ muse fetch unexpected error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
