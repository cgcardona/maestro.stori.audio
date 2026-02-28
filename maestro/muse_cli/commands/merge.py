"""muse merge — fast-forward and 3-way merge with path-level conflict detection.

Algorithm
---------
1. Block if ``.muse/MERGE_STATE.json`` already exists (merge in progress).
2. Resolve ``ours_commit_id`` from ``.muse/refs/heads/<current_branch>``.
3. Resolve ``theirs_commit_id`` from ``.muse/refs/heads/<target_branch>``.
4. Find merge base: LCA of the two commits via BFS over the commit graph.
5. **Fast-forward** — if ``base == ours``, target is strictly ahead: move the
   current branch pointer to ``theirs`` (no new commit).
6. **Already up-to-date** — if ``base == theirs``, current branch is already
   ahead of target: exit 0.
7. **3-way merge** — branches have diverged:
   a. Compute ``diff(base → ours)`` and ``diff(base → theirs)``.
   b. Detect conflicts (paths changed on both sides).
   c. If conflicts exist: write ``.muse/MERGE_STATE.json`` and exit 1.
   d. Otherwise: build merged manifest, persist snapshot, insert merge commit
      with two parent IDs, advance branch pointer.

``--continue``
--------------
After resolving all conflicts via ``muse resolve``, run::

    muse merge --continue

This reads the persisted ``MERGE_STATE.json``, verifies all conflicts are
cleared, builds a merge commit from the current ``muse-work/`` contents, and
advances the branch pointer.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import (
    get_commit_snapshot_manifest,
    insert_commit,
    open_session,
    upsert_object,
    upsert_snapshot,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import (
    apply_merge,
    apply_resolution,
    clear_merge_state,
    detect_conflicts,
    diff_snapshots,
    find_merge_base,
    read_merge_state,
    write_merge_state,
)
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import build_snapshot_manifest, compute_commit_id, compute_snapshot_id

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _merge_async(
    *,
    branch: str,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Run the merge pipeline.

    All filesystem and DB side-effects are isolated here so tests can inject
    an in-memory SQLite session and a ``tmp_path`` root without touching a
    real database.

    Raises :class:`typer.Exit` with the appropriate exit code on every
    terminal condition (success, conflict, or user error) so the Typer
    callback surfaces a clean message.

    Args:
        branch:  Name of the branch to merge into the current branch.
        root:    Repository root (directory containing ``.muse/``).
        session: Open async DB session.
    """
    muse_dir = root / ".muse"

    # ── Guard: merge already in progress ────────────────────────────────
    if read_merge_state(root) is not None:
        typer.echo(
            'Merge in progress. Resolve conflicts and run "muse merge --continue".'
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # ── Current branch ───────────────────────────────────────────────────
    head_ref = (muse_dir / "HEAD").read_text().strip()   # "refs/heads/main"
    current_branch = head_ref.rsplit("/", 1)[-1]         # "main"
    our_ref_path = muse_dir / pathlib.Path(head_ref)

    ours_commit_id = our_ref_path.read_text().strip() if our_ref_path.exists() else ""
    if not ours_commit_id:
        typer.echo("❌ Current branch has no commits. Cannot merge.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Target branch ────────────────────────────────────────────────────
    their_ref_path = muse_dir / "refs" / "heads" / branch
    theirs_commit_id = (
        their_ref_path.read_text().strip() if their_ref_path.exists() else ""
    )
    if not theirs_commit_id:
        typer.echo(f"❌ Branch '{branch}' has no commits or does not exist.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Already up-to-date (same HEAD) ───────────────────────────────────
    if ours_commit_id == theirs_commit_id:
        typer.echo("Already up-to-date.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    # ── Find merge base (LCA) ────────────────────────────────────────────
    base_commit_id = await find_merge_base(session, ours_commit_id, theirs_commit_id)

    # ── Fast-forward: ours IS the base → theirs is ahead ─────────────────
    if base_commit_id == ours_commit_id:
        our_ref_path.write_text(theirs_commit_id)
        typer.echo(
            f"✅ Fast-forward: {current_branch} → {theirs_commit_id[:8]}"
        )
        logger.info(
            "✅ muse merge fast-forward %r to %s", current_branch, theirs_commit_id[:8]
        )
        return

    # ── Already up-to-date: theirs IS the base → we are ahead ────────────
    if base_commit_id == theirs_commit_id:
        typer.echo("Already up-to-date.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    # ── 3-way merge ──────────────────────────────────────────────────────
    # Load snapshot manifests for base, ours, and theirs.
    base_manifest: dict[str, str] = {}
    if base_commit_id is not None:
        loaded_base = await get_commit_snapshot_manifest(session, base_commit_id)
        base_manifest = loaded_base or {}

    ours_manifest = await get_commit_snapshot_manifest(session, ours_commit_id) or {}
    theirs_manifest = (
        await get_commit_snapshot_manifest(session, theirs_commit_id) or {}
    )

    ours_changed = diff_snapshots(base_manifest, ours_manifest)
    theirs_changed = diff_snapshots(base_manifest, theirs_manifest)
    conflict_paths = detect_conflicts(ours_changed, theirs_changed)

    if conflict_paths:
        write_merge_state(
            root,
            base_commit=base_commit_id or "",
            ours_commit=ours_commit_id,
            theirs_commit=theirs_commit_id,
            conflict_paths=sorted(conflict_paths),
            other_branch=branch,
        )
        typer.echo(f"❌ Merge conflict in {len(conflict_paths)} file(s):")
        for path in sorted(conflict_paths):
            typer.echo(f"\tboth modified:   {path}")
        typer.echo('Fix conflicts and run "muse commit" to conclude the merge.')
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Build merged snapshot ─────────────────────────────────────────────
    merged_manifest = apply_merge(
        base_manifest,
        ours_manifest,
        theirs_manifest,
        ours_changed,
        theirs_changed,
        conflict_paths,
    )

    merged_snapshot_id = compute_snapshot_id(merged_manifest)
    await upsert_snapshot(session, manifest=merged_manifest, snapshot_id=merged_snapshot_id)
    await session.flush()

    # ── Build merge commit ────────────────────────────────────────────────
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    merge_message = f"Merge branch '{branch}' into {current_branch}"
    parent_ids = sorted([ours_commit_id, theirs_commit_id])
    merge_commit_id = compute_commit_id(
        parent_ids=parent_ids,
        snapshot_id=merged_snapshot_id,
        message=merge_message,
        committed_at_iso=committed_at.isoformat(),
    )

    merge_commit = MuseCliCommit(
        commit_id=merge_commit_id,
        repo_id=repo_id,
        branch=current_branch,
        parent_commit_id=ours_commit_id,
        parent2_commit_id=theirs_commit_id,
        snapshot_id=merged_snapshot_id,
        message=merge_message,
        author="",
        committed_at=committed_at,
    )
    await insert_commit(session, merge_commit)

    # ── Advance branch pointer ────────────────────────────────────────────
    our_ref_path.write_text(merge_commit_id)

    typer.echo(
        f"✅ Merge commit [{current_branch} {merge_commit_id[:8]}] "
        f"— merged '{branch}' into '{current_branch}'"
    )
    logger.info(
        "✅ muse merge commit %s on %r (parents: %s, %s)",
        merge_commit_id[:8],
        current_branch,
        ours_commit_id[:8],
        theirs_commit_id[:8],
    )


# ---------------------------------------------------------------------------
# --continue: complete a conflicted merge after all paths are resolved
# ---------------------------------------------------------------------------


async def _merge_continue_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Finalize a merge that was paused due to conflicts.

    Reads ``MERGE_STATE.json``, verifies all conflicts are cleared, builds a
    snapshot from the current ``muse-work/`` contents, inserts a merge commit
    with two parent IDs, advances the branch pointer, and clears
    ``MERGE_STATE.json``.

    Args:
        root:    Repository root.
        session: Open async DB session.

    Raises:
        :class:`typer.Exit`: If no merge is in progress, if unresolved
            conflicts remain, or if ``muse-work/`` is empty.
    """
    merge_state = read_merge_state(root)
    if merge_state is None:
        typer.echo("❌ No merge in progress. Nothing to continue.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if merge_state.conflict_paths:
        typer.echo(
            f"❌ {len(merge_state.conflict_paths)} conflict(s) not yet resolved:\n"
            + "\n".join(f"\tboth modified:   {p}" for p in merge_state.conflict_paths)
            + "\nRun 'muse resolve <path> --ours/--theirs' for each file."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    head_ref = (muse_dir / "HEAD").read_text().strip()
    current_branch = head_ref.rsplit("/", 1)[-1]
    our_ref_path = muse_dir / pathlib.Path(head_ref)

    ours_commit_id = merge_state.ours_commit or ""
    theirs_commit_id = merge_state.theirs_commit or ""
    other_branch = merge_state.other_branch or "unknown"

    if not ours_commit_id or not theirs_commit_id:
        typer.echo("❌ MERGE_STATE.json is missing commit references. Cannot continue.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # Build snapshot from current muse-work/ contents (conflicts already resolved).
    workdir = root / "muse-work"
    if not workdir.exists():
        typer.echo("⚠️  muse-work/ is missing. Cannot create merge snapshot.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = build_snapshot_manifest(workdir)
    if not manifest:
        typer.echo("⚠️  muse-work/ is empty. Nothing to commit for the merge.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot_id = compute_snapshot_id(manifest)

    # Persist objects and snapshot.
    for rel_path, object_id in manifest.items():
        file_path = workdir / rel_path
        size = file_path.stat().st_size
        await upsert_object(session, object_id=object_id, size_bytes=size)

    await upsert_snapshot(session, manifest=manifest, snapshot_id=snapshot_id)
    await session.flush()

    # Build merge commit.
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    merge_message = f"Merge branch '{other_branch}' into {current_branch}"
    parent_ids = sorted([ours_commit_id, theirs_commit_id])
    merge_commit_id = compute_commit_id(
        parent_ids=parent_ids,
        snapshot_id=snapshot_id,
        message=merge_message,
        committed_at_iso=committed_at.isoformat(),
    )

    merge_commit = MuseCliCommit(
        commit_id=merge_commit_id,
        repo_id=repo_id,
        branch=current_branch,
        parent_commit_id=ours_commit_id,
        parent2_commit_id=theirs_commit_id,
        snapshot_id=snapshot_id,
        message=merge_message,
        author="",
        committed_at=committed_at,
    )
    await insert_commit(session, merge_commit)

    # Advance branch pointer.
    our_ref_path.write_text(merge_commit_id)

    # Clear merge state.
    clear_merge_state(root)

    typer.echo(
        f"✅ Merge commit [{current_branch} {merge_commit_id[:8]}] "
        f"— merged '{other_branch}' into '{current_branch}'"
    )
    logger.info(
        "✅ muse merge --continue: commit %s on %r (parents: %s, %s)",
        merge_commit_id[:8],
        current_branch,
        ours_commit_id[:8],
        theirs_commit_id[:8],
    )


# ---------------------------------------------------------------------------
# --abort: cancel an in-progress merge and restore pre-merge state
# ---------------------------------------------------------------------------


async def _merge_abort_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Cancel an in-progress merge and restore each conflicted path to its pre-merge version.

    Reads ``MERGE_STATE.json``, fetches the ours_commit snapshot manifest, and
    restores the ours version of each conflicted file from the local object
    store to ``muse-work/``.  Clears ``MERGE_STATE.json`` on success.

    Files that existed only on the theirs branch (i.e. path absent from ours
    manifest) are removed from ``muse-work/`` — they should not exist in the
    pre-merge state.

    Args:
        root:    Repository root.
        session: Open async DB session used to look up the ours commit's
                 snapshot manifest.

    Raises:
        :class:`typer.Exit`: If no merge is in progress or if the merge state
            is missing required commit IDs.
    """
    merge_state = read_merge_state(root)
    if merge_state is None:
        typer.echo("❌ No merge in progress. Nothing to abort.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    ours_commit_id = merge_state.ours_commit
    if not ours_commit_id:
        typer.echo("❌ MERGE_STATE.json is missing ours_commit. Cannot abort.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    ours_manifest = await get_commit_snapshot_manifest(session, ours_commit_id) or {}

    restored_count = 0
    for rel_path in merge_state.conflict_paths:
        object_id = ours_manifest.get(rel_path)
        if object_id is None:
            # Path was added by theirs (not present before the merge) — remove it.
            dest = root / "muse-work" / rel_path
            if dest.exists():
                dest.unlink()
                logger.debug("✅ Removed '%s' (not in pre-merge snapshot)", rel_path)
            continue
        try:
            apply_resolution(root, rel_path, object_id)
            restored_count += 1
        except FileNotFoundError as exc:
            logger.warning("⚠️ Could not restore '%s': %s", rel_path, exc)

    clear_merge_state(root)

    typer.echo(f"✅ Merge aborted. Restored {restored_count} conflicted file(s).")
    logger.info(
        "✅ muse merge --abort: cleared merge state, restored %d file(s)", restored_count
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def merge(
    ctx: typer.Context,
    branch: Optional[str] = typer.Argument(
        None,
        help="Name of the branch to merge into HEAD. Omit when using --continue or --abort.",
    ),
    cont: bool = typer.Option(
        False,
        "--continue",
        is_flag=True,
        help="Finalize a paused merge after resolving all conflicts.",
    ),
    abort: bool = typer.Option(
        False,
        "--abort",
        is_flag=True,
        help="Cancel the in-progress merge and restore the pre-merge state.",
    ),
) -> None:
    """Merge a branch into the current branch (fast-forward or 3-way).

    Use ``--continue`` after resolving conflicts to create the merge commit.
    Use ``--abort`` to cancel and restore the pre-merge working-tree state.
    """
    root = require_repo()

    if cont and abort:
        typer.echo("❌ Cannot use --continue and --abort together.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if cont:
        async def _run_continue() -> None:
            async with open_session() as session:
                await _merge_continue_async(root=root, session=session)

        try:
            asyncio.run(_run_continue())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse merge --continue failed: {exc}")
            logger.error("❌ muse merge --continue error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    if abort:
        async def _run_abort() -> None:
            async with open_session() as session:
                await _merge_abort_async(root=root, session=session)

        try:
            asyncio.run(_run_abort())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse merge --abort failed: {exc}")
            logger.error("❌ muse merge --abort error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    if not branch:
        typer.echo(
            "❌ Branch name required "
            "(or use --continue / --abort to manage a paused merge)."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> None:
        async with open_session() as session:
            await _merge_async(branch=branch, root=root, session=session)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse merge failed: {exc}")
        logger.error("❌ muse merge error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
