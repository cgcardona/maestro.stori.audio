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
8. Apply post-fetch integration strategy based on flags:
   - Default: print divergence warning if branches diverged.
   - ``--ff-only``: fast-forward if possible; fail if not.
   - ``--rebase``: fast-forward if remote is simply ahead; rebase local
     commits onto remote HEAD if branches have diverged.

Flags
-----
- ``--rebase``: after fetching, rebase local commits on top of the fetched
  remote HEAD rather than merging. For linear divergence this replays each
  local commit with the same snapshot but a new parent ID. For complex
  divergence, it falls back to an advisory error.
- ``--ff-only``: after fetching, only integrate if the result would be a
  fast-forward (remote HEAD is a direct descendant of local HEAD). Fails
  with exit code 1 if the branches have diverged.

Exit codes:
  0 — success (including ff and rebase cases)
  1 — user error (no remote, bad args, ff-only on diverged branch)
  2 — not a Muse repository
  3 — network / server error
"""
from __future__ import annotations

import asyncio
import datetime
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
    insert_commit,
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
from maestro.muse_cli.merge_engine import find_merge_base
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import compute_commit_tree_id

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
# Rebase helper
# ---------------------------------------------------------------------------


async def _rebase_commits_onto(
    root: pathlib.Path,
    repo_id: str,
    branch: str,
    commits_to_rebase: list[MuseCliCommit],
    new_base_commit_id: str,
) -> str:
    """Replay *commits_to_rebase* (oldest-first) on top of *new_base_commit_id*.

    Creates new MuseCliCommit rows with updated parent IDs but the same
    snapshot, message, and author.  Commit IDs are recomputed deterministically
    via :func:`~maestro.muse_cli.snapshot.compute_commit_tree_id` so that
    running the same rebase twice does not insert duplicate rows.

    This implements a linear rebase: no conflict detection is performed.  When
    the caller needs path-level conflict handling it should use ``muse merge``
    instead.

    Args:
        root: Repository root (for writing the branch ref).
        repo_id: Repository ID to tag the new commit rows.
        branch: Local branch name whose HEAD will be updated.
        commits_to_rebase: Local commits above the merge base, oldest-first.
        new_base_commit_id: The remote HEAD onto which we replay.

    Returns:
        The new local branch HEAD commit ID (last replayed commit).
    """
    current_parent_id: str = new_base_commit_id

    async with open_session() as session:
        for commit in commits_to_rebase:
            new_commit_id = compute_commit_tree_id(
                parent_ids=[current_parent_id],
                snapshot_id=commit.snapshot_id,
                message=commit.message,
                author=commit.author,
            )

            # Idempotency: skip if this rebased commit already exists
            existing = await session.get(MuseCliCommit, new_commit_id)
            if existing is None:
                rebased = MuseCliCommit(
                    commit_id=new_commit_id,
                    repo_id=repo_id,
                    branch=branch,
                    parent_commit_id=current_parent_id,
                    snapshot_id=commit.snapshot_id,
                    message=commit.message,
                    author=commit.author,
                    committed_at=datetime.datetime.now(datetime.timezone.utc),
                    commit_metadata=commit.commit_metadata,
                )
                await insert_commit(session, rebased)

            current_parent_id = new_commit_id

        await session.commit()

    # Update local branch ref to the last rebased commit
    ref_path = root / ".muse" / "refs" / "heads" / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(current_parent_id, encoding="utf-8")

    return current_parent_id


# ---------------------------------------------------------------------------
# Divergence detection
# ---------------------------------------------------------------------------


def _is_ancestor(
    commits_by_id: Mapping[str, MuseCliCommit],
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
    rebase: bool = False,
    ff_only: bool = False,
) -> None:
    """Execute the pull pipeline.

    Raises :class:`typer.Exit` with the appropriate code on all error paths.

    After fetching remote commits, the post-fetch integration strategy is
    determined by *rebase* and *ff_only*:

    - Default (both False): print divergence warning when branches have
      diverged; do not touch the local branch ref.
    - ``ff_only=True``: fast-forward the local branch ref to remote_head if
      possible; fail with exit 1 if the branches have diverged.
    - ``rebase=True``: fast-forward if remote is simply ahead; replay local
      commits onto remote_head when branches have diverged (linear rebase).

    When both *rebase* and *ff_only* are True, *ff_only* takes precedence.
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

    mode_hint = " (--rebase)" if rebase else " (--ff-only)" if ff_only else ""
    typer.echo(f"⬇️  Pulling {remote_name}/{effective_branch}{mode_hint} …")

    pull_request = PullRequest(
        branch=effective_branch,
        have_commits=have_commits,
        have_objects=have_objects,
    )
    if rebase:
        pull_request["rebase"] = True
    if ff_only:
        pull_request["ff_only"] = True

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

    # ── Determine local HEAD and divergence ───────────────────────────────
    ref_path = muse_dir / "refs" / "heads" / effective_branch
    local_head: str | None = None
    if ref_path.exists():
        raw = ref_path.read_text(encoding="utf-8").strip()
        local_head = raw if raw else None

    diverged = pull_response["diverged"]

    # Re-check divergence locally using the updated commit graph
    async with open_session() as session:
        commits_after = await get_commits_for_branch(session, repo_id, effective_branch)

    commits_by_id: dict[str, MuseCliCommit] = {c.commit_id: c for c in commits_after}

    if (
        not diverged
        and remote_head_from_hub
        and local_head
        and remote_head_from_hub != local_head
    ):
        if not _is_ancestor(commits_by_id, remote_head_from_hub, local_head):
            diverged = True

    # ── Fast-forward check (common to --ff-only and --rebase) ────────────
    can_fast_forward = (
        remote_head_from_hub is not None
        and (
            local_head is None
            or _is_ancestor(commits_by_id, local_head, remote_head_from_hub)
        )
    )

    # ── Apply post-fetch integration strategy ─────────────────────────────
    if ff_only:
        if can_fast_forward and remote_head_from_hub:
            # Advance local branch ref to remote HEAD
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            ref_path.write_text(remote_head_from_hub, encoding="utf-8")
            typer.echo(
                f"✅ Fast-forwarded {effective_branch} → {remote_head_from_hub[:8]}"
            )
            logger.info(
                "✅ muse pull --ff-only: fast-forwarded %s → %s",
                effective_branch,
                remote_head_from_hub[:8],
            )
        elif not diverged and remote_head_from_hub and local_head == remote_head_from_hub:
            typer.echo(f"✅ Already up to date — {effective_branch} is current.")
        else:
            typer.echo(
                f"❌ Cannot fast-forward: {effective_branch} has diverged from "
                f"{remote_name}/{effective_branch}. "
                f"Run `muse merge {remote_name}/{effective_branch}` or use "
                f"`muse pull --rebase` to integrate."
            )
            logger.warning(
                "⚠️ muse pull --ff-only: branches have diverged, refusing to merge",
            )
            raise typer.Exit(code=int(ExitCode.USER_ERROR))

    elif rebase:
        if can_fast_forward and remote_head_from_hub:
            # Simple fast-forward — remote is strictly ahead of us
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            ref_path.write_text(remote_head_from_hub, encoding="utf-8")
            typer.echo(
                f"✅ Fast-forwarded {effective_branch} → {remote_head_from_hub[:8]}"
            )
            logger.info(
                "✅ muse pull --rebase: fast-forwarded %s → %s",
                effective_branch,
                remote_head_from_hub[:8],
            )
        elif diverged and remote_head_from_hub and local_head:
            # Diverged — attempt linear rebase
            async with open_session() as session:
                merge_base_id = await find_merge_base(
                    session, local_head, remote_head_from_hub
                )

            if merge_base_id is None:
                typer.echo(
                    "❌ Cannot rebase: no common ancestor found between "
                    f"{effective_branch} and {remote_name}/{effective_branch}. "
                    "Use `muse merge` instead."
                )
                raise typer.Exit(code=int(ExitCode.USER_ERROR))

            # Collect local commits above the merge base (oldest-first)
            local_above_base: list[MuseCliCommit] = []
            for c in reversed(commits_after):
                if c.commit_id == merge_base_id:
                    break
                # Only include commits that are NOT in the remote history
                if not _is_ancestor(commits_by_id, c.commit_id, remote_head_from_hub):
                    local_above_base.append(c)

            if not local_above_base:
                # Remote is already at or past local — fast-forward
                ref_path.parent.mkdir(parents=True, exist_ok=True)
                ref_path.write_text(remote_head_from_hub, encoding="utf-8")
                typer.echo(
                    f"✅ Fast-forwarded {effective_branch} → {remote_head_from_hub[:8]}"
                )
            else:
                typer.echo(
                    f"⟳  Rebasing {len(local_above_base)} local commit(s) onto "
                    f"{remote_head_from_hub[:8]} …"
                )
                new_head = await _rebase_commits_onto(
                    root=root,
                    repo_id=repo_id,
                    branch=effective_branch,
                    commits_to_rebase=local_above_base,
                    new_base_commit_id=remote_head_from_hub,
                )
                typer.echo(
                    f"✅ Rebase complete — {effective_branch} → {new_head[:8]}"
                )
                logger.info(
                    "✅ muse pull --rebase: rebased %d commit(s) onto %s, new HEAD %s",
                    len(local_above_base),
                    remote_head_from_hub[:8],
                    new_head[:8],
                )
        elif local_head == remote_head_from_hub:
            typer.echo(f"✅ Already up to date — {effective_branch} is current.")

    else:
        # Default: print divergence warning (do not touch local branch ref)
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
    rebase: bool = typer.Option(
        False,
        "--rebase",
        help=(
            "After fetching, rebase local commits on top of the remote HEAD "
            "instead of merging. For a simple case where remote is ahead, this "
            "fast-forwards the local branch. For diverged branches, each local "
            "commit above the merge base is replayed with the remote HEAD as "
            "the new base, preserving a linear history."
        ),
    ),
    ff_only: bool = typer.Option(
        False,
        "--ff-only",
        help=(
            "Refuse to integrate remote commits unless the result would be a "
            "fast-forward (i.e. local branch is a direct ancestor of the remote "
            "HEAD). Exits 1 with an instructive message when branches have "
            "diverged, keeping the local branch unchanged."
        ),
    ),
) -> None:
    """Download commits from the remote Muse Hub into the local repository.

    Contacts the remote Hub, receives commits and objects that are not yet in
    the local database, and stores them.  Post-fetch integration depends on flags:

    - Default: warn if diverged, suggest ``muse merge``.
    - ``--ff-only``: fast-forward or fail.
    - ``--rebase``: rebase local commits onto remote HEAD.

    Example::

        muse pull
        muse pull --rebase
        muse pull --ff-only
        muse pull --branch feature/groove-v2
        muse pull --remote staging
    """
    root = require_repo()

    try:
        asyncio.run(
            _pull_async(
                root=root,
                remote_name=remote,
                branch=branch,
                rebase=rebase,
                ff_only=ff_only,
            )
        )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse pull failed: {exc}")
        logger.error("❌ muse pull unexpected error: %s", exc, exc_info=True)
        raise typer.Exit(code=int(ExitCode.INTERNAL_ERROR))
