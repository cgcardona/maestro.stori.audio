"""muse push — upload local commits to the configured remote Muse Hub.

Push algorithm
--------------
1. Resolve repo root and read ``repo_id`` from ``.muse/repo.json``.
2. Read current branch from ``.muse/HEAD``.
3. Read local branch HEAD commit ID from ``.muse/refs/heads/<branch>``.
   Exits 1 if the branch has no commits.
4. Resolve ``origin`` URL from ``.muse/config.toml``.
   Exits 1 with an instructive message if no remote is configured.
5. Read last known remote HEAD from ``.muse/remotes/origin/<branch>``
   (may not exist on first push).
6. Query Postgres for all commits on the branch; compute the delta since
   the last known remote HEAD (or all commits if no prior push).
7. Build :class:`~maestro.muse_cli.hub_client.PushRequest` payload.
8. POST to ``<remote_url>/push`` with Bearer auth.
9. On success, update ``.muse/remotes/origin/<branch>`` to the new HEAD.

Exit codes:
  0 — success
  1 — user error (no remote, no commits, bad args)
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
from maestro.muse_cli.config import get_remote, get_remote_head, set_remote_head
from maestro.muse_cli.db import get_commits_for_branch, get_all_object_ids, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.hub_client import (
    MuseHubClient,
    PushCommitPayload,
    PushObjectPayload,
    PushRequest,
)
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

app = typer.Typer()

_NO_REMOTE_MSG = (
    "No remote named 'origin'. "
    "Run `muse remote add origin <url>` to configure one."
)


# ---------------------------------------------------------------------------
# Push delta helper
# ---------------------------------------------------------------------------


def _compute_push_delta(
    commits: list[MuseCliCommit],
    remote_head: str | None,
) -> list[MuseCliCommit]:
    """Return the commits that are missing from the remote.

    *commits* is the full branch history (newest-first from the DB query).
    If *remote_head* is ``None`` (first push), all commits are included.

    We include every commit from the local HEAD down to—but not including—the
    known remote HEAD.  The list is returned in chronological order (oldest
    first) so the Hub can apply them in ancestry order.
    """
    if not commits:
        return []
    if remote_head is None:
        # First push — send all commits, chronological order
        return list(reversed(commits))

    # Walk from newest to oldest; stop when we hit the remote head
    delta: list[MuseCliCommit] = []
    for commit in commits:
        if commit.commit_id == remote_head:
            break
        delta.append(commit)

    # Return chronological order (oldest first)
    return list(reversed(delta))


def _build_push_request(
    branch: str,
    head_commit_id: str,
    delta: list[MuseCliCommit],
    all_object_ids: list[str],
) -> PushRequest:
    """Serialize the push payload from local ORM objects.

    ``objects`` includes all object IDs known to this repo so the Hub can
    store references even if it already has the blobs (deduplication is the
    Hub's responsibility).
    """
    commits: list[PushCommitPayload] = [
        PushCommitPayload(
            commit_id=c.commit_id,
            parent_commit_id=c.parent_commit_id,
            snapshot_id=c.snapshot_id,
            branch=c.branch,
            message=c.message,
            author=c.author,
            committed_at=c.committed_at.isoformat(),
            metadata=dict(c.commit_metadata) if c.commit_metadata else None,
        )
        for c in delta
    ]

    objects: list[PushObjectPayload] = [
        PushObjectPayload(object_id=oid, size_bytes=0)
        for oid in all_object_ids
    ]

    return PushRequest(
        branch=branch,
        head_commit_id=head_commit_id,
        commits=commits,
        objects=objects,
    )


# ---------------------------------------------------------------------------
# Async push core
# ---------------------------------------------------------------------------


async def _push_async(
    *,
    root: pathlib.Path,
    remote_name: str,
    branch: str | None,
) -> None:
    """Execute the push pipeline.

    Raises :class:`typer.Exit` with the appropriate code on all error paths
    so the Typer callback surfaces clean messages instead of tracebacks.
    """
    muse_dir = root / ".muse"

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # ── Branch resolution ────────────────────────────────────────────────
    head_ref = (muse_dir / "HEAD").read_text().strip()
    effective_branch = branch or head_ref.rsplit("/", 1)[-1]
    ref_path = muse_dir / "refs" / "heads" / effective_branch

    if not ref_path.exists() or not ref_path.read_text().strip():
        typer.echo(f"❌ Branch '{effective_branch}' has no commits. Run `muse commit` first.")
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    head_commit_id = ref_path.read_text().strip()

    # ── Remote URL ───────────────────────────────────────────────────────
    remote_url = get_remote(remote_name, root)
    if not remote_url:
        typer.echo(_NO_REMOTE_MSG)
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    # ── Known remote head ────────────────────────────────────────────────
    remote_head = get_remote_head(remote_name, effective_branch, root)

    # ── Build push payload ───────────────────────────────────────────────
    async with open_session() as session:
        commits = await get_commits_for_branch(session, repo_id, effective_branch)
        all_object_ids = await get_all_object_ids(session, repo_id)

    delta = _compute_push_delta(commits, remote_head)

    if not delta and remote_head == head_commit_id:
        typer.echo(f"✅ Everything up to date — {remote_name}/{effective_branch} is current.")
        return

    payload = _build_push_request(
        branch=effective_branch,
        head_commit_id=head_commit_id,
        delta=delta,
        all_object_ids=all_object_ids,
    )

    typer.echo(
        f"⬆️  Pushing {len(delta)} commit(s) to {remote_name}/{effective_branch} …"
    )

    # ── HTTP push ────────────────────────────────────────────────────────
    try:
        async with MuseHubClient(base_url=remote_url, repo_root=root) as hub:
            response = await hub.post("/push", json=payload)

        if response.status_code == 200:
            set_remote_head(remote_name, effective_branch, head_commit_id, root)
            typer.echo(
                f"✅ Pushed {len(delta)} commit(s) → "
                f"{remote_name}/{effective_branch} [{head_commit_id[:8]}]"
            )
            logger.info(
                "✅ muse push %s → %s/%s [%s] (%d commits)",
                repo_id,
                remote_name,
                effective_branch,
                head_commit_id[:8],
                len(delta),
            )
        else:
            typer.echo(
                f"❌ Hub rejected push (HTTP {response.status_code}): {response.text}"
            )
            logger.error(
                "❌ muse push failed: HTTP %d — %s",
                response.status_code,
                response.text,
            )
            raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))

    except typer.Exit:
        raise
    except httpx.TimeoutException:
        typer.echo(f"❌ Push timed out connecting to {remote_url}")
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
    except httpx.HTTPError as exc:
        typer.echo(f"❌ Network error during push: {exc}")
        logger.error("❌ muse push network error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def push(
    ctx: typer.Context,
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Branch to push. Defaults to the current branch.",
    ),
    remote: str = typer.Option(
        "origin",
        "--remote",
        help="Remote name to push to.",
    ),
) -> None:
    """Push local commits to the configured remote Muse Hub.

    Sends commits that the remote does not yet have, then updates the local
    remote-tracking pointer (``.muse/remotes/<remote>/<branch>``).

    Example::

        muse push
        muse push --branch feature/groove-v2
        muse push --remote staging
    """
    root = require_repo()

    try:
        asyncio.run(_push_async(root=root, remote_name=remote, branch=branch))
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse push failed: {exc}")
        logger.error("❌ muse push unexpected error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
