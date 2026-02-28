"""muse pull — download remote commits from the configured Muse Hub.

Pull algorithm
--------------
1. Resolve repo root and read ``repo_id`` from ``.muse/repo.json``.
2. Read current branch from ``.muse/HEAD``.
3. Resolve ``origin`` URL from ``.muse/config.toml``.
   Exits 1 with an instructive message if no remote is configured.
4. Collect ``have_commits`` (commit IDs already in local DB) and
   ``have_objects`` (object IDs already stored) to avoid re-downloading.
5. POST to ``<remote_url>/pull`` with Bearer auth.
6. Store returned commits and object descriptors in local Postgres.
7. Update ``.muse/remotes/origin/<branch>`` tracking pointer.
8. If the remote HEAD is not an ancestor of the local branch HEAD, print a
   divergence warning and advise ``muse merge origin/<branch>``.
   Exit code is **0** even on divergence — the warning is informational.

Exit codes:
  0 — success (including the divergence warning case)
  1 — user error (no remote, bad args)
  2 — not a Muse repository
  3 — network / server error
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from collections.abc import Mapping

import httpx
import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.config import get_remote, get_remote_head, set_remote_head
from maestro.muse_cli.db import (
    get_all_object_ids,
    get_commits_for_branch,
    open_session,
    store_pulled_commit,
    store_pulled_object,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.hub_client import (
    MuseHubClient,
    PullRequest,
    PullResponse,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_NO_REMOTE_MSG = (
    "No remote named 'origin'. "
    "Run `muse remote add origin <url>` to configure one."
)

_DIVERGED_MSG = (
    "⚠️  Local branch has diverged from {remote}/{branch}.\n"
    "   Run `muse merge {remote}/{branch}` to integrate remote changes."
)


# ---------------------------------------------------------------------------
# Divergence detection
# ---------------------------------------------------------------------------


def _is_ancestor(
    commits_by_id: Mapping[str, object],
    ancestor_id: str,
    descendant_id: str,
) -> bool:
    """Return True if *ancestor_id* is a reachable ancestor of *descendant_id*.

    Walks the parent chain starting from *descendant_id* and returns ``True``
    if *ancestor_id* is encountered.  Returns ``False`` if the chain ends
    without finding the candidate (including when either ID is unknown).

    ``commits_by_id`` maps commit_id → MuseCliCommit (or any object with a
    ``parent_commit_id`` attribute).
    """
    if ancestor_id == descendant_id:
        return True
    visited: set[str] = set()
    current_id: str | None = descendant_id
    while current_id is not None and current_id not in visited:
        visited.add(current_id)
        commit = commits_by_id.get(current_id)
        if commit is None:
            break
        parent_raw = getattr(commit, "parent_commit_id", None)
        current_id = str(parent_raw) if parent_raw is not None else None
        if current_id == ancestor_id:
            return True
    return False


# ---------------------------------------------------------------------------
# Async pull core
# ---------------------------------------------------------------------------


async def _pull_async(
    *,
    root: pathlib.Path,
    remote_name: str,
    branch: str | None,
) -> None:
    """Execute the pull pipeline.

    Raises :class:`typer.Exit` with the appropriate code on all error paths.
    """
    muse_dir = root / ".muse"

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # ── Branch resolution ────────────────────────────────────────────────
    head_ref = (muse_dir / "HEAD").read_text().strip()
    effective_branch = branch or head_ref.rsplit("/", 1)[-1]

    # ── Remote URL ───────────────────────────────────────────────────────
    remote_url = get_remote(remote_name, root)
    if not remote_url:
        typer.echo(_NO_REMOTE_MSG)
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    # ── Collect have-sets for delta pull ─────────────────────────────────
    async with open_session() as session:
        local_commits = await get_commits_for_branch(session, repo_id, effective_branch)
        have_commits = [c.commit_id for c in local_commits]
        have_objects = await get_all_object_ids(session, repo_id)

    typer.echo(f"⬇️  Pulling {remote_name}/{effective_branch} …")

    pull_request = PullRequest(
        branch=effective_branch,
        have_commits=have_commits,
        have_objects=have_objects,
    )

    # ── HTTP pull ────────────────────────────────────────────────────────
    try:
        async with MuseHubClient(base_url=remote_url, repo_root=root) as hub:
            response = await hub.post("/pull", json=pull_request)

        if response.status_code != 200:
            typer.echo(
                f"❌ Hub rejected pull (HTTP {response.status_code}): {response.text}"
            )
            logger.error(
                "❌ muse pull failed: HTTP %d — %s",
                response.status_code,
                response.text,
            )
            raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    except typer.Exit:
        raise
    except httpx.TimeoutException:
        typer.echo(f"❌ Pull timed out connecting to {remote_url}")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
    except httpx.HTTPError as exc:
        typer.echo(f"❌ Network error during pull: {exc}")
        logger.error("❌ muse pull network error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    # ── Parse response ───────────────────────────────────────────────────
    raw_body: object = response.json()
    if not isinstance(raw_body, dict):
        typer.echo("❌ Hub returned unexpected pull response shape.")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    raw_remote_head = raw_body.get("remote_head")
    pull_response = PullResponse(
        commits=list(raw_body.get("commits", [])),
        objects=list(raw_body.get("objects", [])),
        remote_head=str(raw_remote_head) if isinstance(raw_remote_head, str) else None,
        diverged=bool(raw_body.get("diverged", False)),
    )

    new_commits_count = 0
    new_objects_count = 0

    # ── Store pulled data in DB ───────────────────────────────────────────
    async with open_session() as session:
        for commit_data in pull_response["commits"]:
            if isinstance(commit_data, dict):
                # Inject repo_id since Hub response may omit it
                commit_data_with_repo = dict(commit_data)
                commit_data_with_repo.setdefault("repo_id", repo_id)
                inserted = await store_pulled_commit(session, commit_data_with_repo)
                if inserted:
                    new_commits_count += 1

        for obj_data in pull_response["objects"]:
            if isinstance(obj_data, dict):
                inserted = await store_pulled_object(session, dict(obj_data))
                if inserted:
                    new_objects_count += 1

    # ── Update remote tracking head ───────────────────────────────────────
    remote_head_from_hub = pull_response["remote_head"]
    if remote_head_from_hub:
        set_remote_head(remote_name, effective_branch, remote_head_from_hub, root)

    # ── Divergence check ─────────────────────────────────────────────────
    ref_path = muse_dir / "refs" / "heads" / effective_branch
    local_head: str | None = None
    if ref_path.exists():
        raw = ref_path.read_text(encoding="utf-8").strip()
        local_head = raw if raw else None

    diverged = pull_response["diverged"]
    if (
        not diverged
        and remote_head_from_hub
        and local_head
        and remote_head_from_hub != local_head
    ):
        # Double-check locally: if remote_head is not an ancestor of local_head
        # (or vice versa) then the branches have diverged.
        async with open_session() as session:
            commits_after = await get_commits_for_branch(session, repo_id, effective_branch)
        commits_by_id = {c.commit_id: c for c in commits_after}
        if not _is_ancestor(commits_by_id, remote_head_from_hub, local_head):
            diverged = True

    if diverged:
        typer.echo(
            _DIVERGED_MSG.format(remote=remote_name, branch=effective_branch)
        )

    typer.echo(
        f"✅ Pulled {new_commits_count} new commit(s), "
        f"{new_objects_count} new object(s) from {remote_name}/{effective_branch}"
    )
    logger.info(
        "✅ muse pull %s/%s: +%d commits, +%d objects",
        remote_name,
        effective_branch,
        new_commits_count,
        new_objects_count,
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def pull(
    ctx: typer.Context,
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Branch to pull. Defaults to the current branch.",
    ),
    remote: str = typer.Option(
        "origin",
        "--remote",
        help="Remote name to pull from.",
    ),
) -> None:
    """Download commits from the remote Muse Hub into the local repository.

    Contacts the remote Hub, receives commits and objects that are not yet in
    the local database, and stores them.  If the local branch has diverged
    from the remote, prints a warning and suggests ``muse merge``.

    Exit code is always 0 on success — the divergence warning does not count
    as an error.

    Example::

        muse pull
        muse pull --branch feature/groove-v2
        muse pull --remote staging
    """
    root = require_repo()

    try:
        asyncio.run(_pull_async(root=root, remote_name=remote, branch=branch))
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse pull failed: {exc}")
        logger.error("❌ muse pull unexpected error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
