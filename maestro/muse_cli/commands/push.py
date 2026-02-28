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
   If ``--set-upstream`` was given, record the upstream tracking in config.

Flags
-----
- ``--force / -f``: overwrite remote branch even on non-fast-forward.
- ``--force-with-lease``: overwrite only if remote HEAD matches the last
  known local tracking pointer (safer than ``--force``; the Hub must reject
  if the remote has advanced since we last fetched).
- ``--tags``: push all VCS-style tag refs from ``.muse/refs/tags/`` alongside
  the branch commits.
- ``--set-upstream / -u``: after a successful push, record the remote as the
  upstream for this branch in ``.muse/config.toml``.

Exit codes:
  0 — success
  1 — user error (no remote, no commits, bad args, force-with-lease mismatch)
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
    set_remote_head,
    set_upstream,
)
from maestro.muse_cli.db import get_commits_for_branch, get_all_object_ids, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.hub_client import (
    MuseHubClient,
    PushCommitPayload,
    PushObjectPayload,
    PushRequest,
    PushTagPayload,
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


def _collect_tag_refs(root: pathlib.Path) -> list[PushTagPayload]:
    """Enumerate VCS-style tag refs from ``.muse/refs/tags/``.

    Each file under ``.muse/refs/tags/`` is a lightweight tag: the filename
    is the tag name and the file content is the commit ID it points to.

    Returns an empty list when the directory does not exist or contains no
    readable tag files.

    Args:
        root: Repository root path.

    Returns:
        List of :class:`PushTagPayload` dicts, one per tag file found.
    """
    tags_dir = root / ".muse" / "refs" / "tags"
    if not tags_dir.is_dir():
        return []

    payloads: list[PushTagPayload] = []
    for tag_file in sorted(tags_dir.iterdir()):
        if not tag_file.is_file():
            continue
        commit_id = tag_file.read_text(encoding="utf-8").strip()
        if commit_id:
            payloads.append(PushTagPayload(tag_name=tag_file.name, commit_id=commit_id))

    return payloads


def _build_push_request(
    branch: str,
    head_commit_id: str,
    delta: list[MuseCliCommit],
    all_object_ids: list[str],
    *,
    force: bool = False,
    force_with_lease: bool = False,
    expected_remote_head: str | None = None,
    tag_payloads: list[PushTagPayload] | None = None,
) -> PushRequest:
    """Serialize the push payload from local ORM objects.

    ``objects`` includes all object IDs known to this repo so the Hub can
    store references even if it already has the blobs (deduplication is the
    Hub's responsibility).

    When ``force_with_lease`` is ``True``, ``expected_remote_head`` is the
    commit ID we believe the remote HEAD to be.  The Hub must reject the push
    if its current HEAD differs.

    Args:
        branch: Branch name being pushed.
        head_commit_id: Local branch HEAD commit ID.
        delta: Commits not yet on the remote (oldest-first).
        all_object_ids: All known object IDs in this repo.
        force: If ``True``, allow non-fast-forward overwrite.
        force_with_lease: If ``True``, include expected remote HEAD for
            lease-based safety check.
        expected_remote_head: Commit ID the caller believes the remote HEAD
            to be (used with ``force_with_lease``).
        tag_payloads: VCS tag refs to include (from ``--tags``).

    Returns:
        A :class:`PushRequest` TypedDict ready to be JSON-serialised.
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

    request = PushRequest(
        branch=branch,
        head_commit_id=head_commit_id,
        commits=commits,
        objects=objects,
    )

    if force:
        request["force"] = True
    if force_with_lease:
        request["force_with_lease"] = True
        request["expected_remote_head"] = expected_remote_head
    if tag_payloads:
        request["tags"] = tag_payloads

    return request


# ---------------------------------------------------------------------------
# Async push core
# ---------------------------------------------------------------------------


async def _push_async(
    *,
    root: pathlib.Path,
    remote_name: str,
    branch: str | None,
    force: bool = False,
    force_with_lease: bool = False,
    include_tags: bool = False,
    set_upstream_flag: bool = False,
) -> None:
    """Execute the push pipeline.

    Raises :class:`typer.Exit` with the appropriate code on all error paths
    so the Typer callback surfaces clean messages instead of tracebacks.

    When ``force_with_lease`` is ``True`` and the Hub returns HTTP 409
    (conflict), the push is rejected because the remote has advanced beyond
    our last-known tracking pointer — the user must fetch and retry.

    When ``set_upstream_flag`` is ``True``, a successful push writes the
    upstream tracking entry to ``.muse/config.toml``.
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

    if not delta and remote_head == head_commit_id and not include_tags:
        typer.echo(f"✅ Everything up to date — {remote_name}/{effective_branch} is current.")
        return

    # ── Collect tag refs if requested ────────────────────────────────────
    tag_payloads = _collect_tag_refs(root) if include_tags else []

    payload = _build_push_request(
        branch=effective_branch,
        head_commit_id=head_commit_id,
        delta=delta,
        all_object_ids=all_object_ids,
        force=force,
        force_with_lease=force_with_lease,
        expected_remote_head=remote_head if force_with_lease else None,
        tag_payloads=tag_payloads if tag_payloads else None,
    )

    extra_flags = []
    if force:
        extra_flags.append("--force")
    elif force_with_lease:
        extra_flags.append("--force-with-lease")
    if include_tags and tag_payloads:
        extra_flags.append(f"--tags ({len(tag_payloads)} tag(s))")

    flags_desc = f" [{', '.join(extra_flags)}]" if extra_flags else ""
    typer.echo(
        f"⬆️  Pushing {len(delta)} commit(s) to {remote_name}/{effective_branch}{flags_desc} …"
    )

    # ── HTTP push ────────────────────────────────────────────────────────
    try:
        async with MuseHubClient(base_url=remote_url, repo_root=root) as hub:
            response = await hub.post("/push", json=payload)

        if response.status_code == 200:
            set_remote_head(remote_name, effective_branch, head_commit_id, root)
            if set_upstream_flag:
                set_upstream(effective_branch, remote_name, root)
                typer.echo(
                    f"✅ Branch '{effective_branch}' set to track '{remote_name}/{effective_branch}'"
                )
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
        elif response.status_code == 409 and force_with_lease:
            typer.echo(
                f"❌ Push rejected: remote {remote_name}/{effective_branch} has advanced "
                f"since last fetch. Run `muse pull` then retry, or use `--force` to override."
            )
            logger.warning(
                "⚠️ muse push --force-with-lease rejected: remote has advanced beyond %s",
                remote_head[:8] if remote_head else "None",
            )
            raise typer.Exit(code=int(ExitCode.USER_ERROR))
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
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help=(
            "Overwrite the remote branch even if the push is non-fast-forward. "
            "Use with caution — this discards remote history. "
            "Prefer --force-with-lease for a safer alternative."
        ),
    ),
    force_with_lease: bool = typer.Option(
        False,
        "--force-with-lease",
        help=(
            "Overwrite the remote branch only if its current HEAD matches the "
            "last commit we fetched from it. Safer than --force because it "
            "prevents overwriting commits pushed by others after our last fetch."
        ),
    ),
    tags: bool = typer.Option(
        False,
        "--tags",
        help=(
            "Push all VCS-style tag refs from .muse/refs/tags/ alongside the "
            "branch commits. Tags are lightweight refs (filename = tag name, "
            "content = commit ID)."
        ),
    ),
    set_upstream: bool = typer.Option(
        False,
        "--set-upstream",
        "-u",
        help=(
            "After a successful push, record this remote as the upstream for "
            "the current branch in .muse/config.toml. Subsequent push/pull "
            "commands can then default to this remote."
        ),
    ),
) -> None:
    """Push local commits to the configured remote Muse Hub.

    Sends commits that the remote does not yet have, then updates the local
    remote-tracking pointer (``.muse/remotes/<remote>/<branch>``).

    Example::

        muse push
        muse push --branch feature/groove-v2
        muse push --remote staging
        muse push --force-with-lease
        muse push --set-upstream origin main
        muse push --tags
    """
    root = require_repo()

    try:
        asyncio.run(
            _push_async(
                root=root,
                remote_name=remote,
                branch=branch,
                force=force,
                force_with_lease=force_with_lease,
                include_tags=tags,
                set_upstream_flag=set_upstream,
            )
        )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse push failed: {exc}")
        logger.error("❌ muse push unexpected error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
