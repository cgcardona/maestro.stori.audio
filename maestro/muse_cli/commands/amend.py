"""muse amend — fold working-tree changes into the most recent commit.

Equivalent to ``git commit --amend``.  The original HEAD commit is replaced
by a new commit that shares the same *parent* as the original, effectively
orphaning the original HEAD.  The amended commit is a fresh object with a
new deterministic ``commit_id``.

Flag summary
------------
- ``-m / --message TEXT``  — use TEXT as the new commit message.
- ``--no-edit``            — keep the original commit message (default when
                             ``-m`` is omitted).  When both ``-m`` and
                             ``--no-edit`` are supplied, ``--no-edit`` wins.
- ``--reset-author``       — reset the author field to the current user
                             (stub: sets author to empty string until a user
                             identity system is implemented).

Behaviour
---------
1. A new snapshot is taken of ``muse-work/`` using the same content-addressed
   logic as ``muse commit``.
2. A new ``commit_id`` is computed with the *original commit's parent* as the
   parent, the current timestamp, and the effective message.
3. ``.muse/refs/heads/<branch>`` is updated to the new commit ID.
4. Blocked when a merge is in progress (``.muse/MERGE_STATE.json`` exists).
5. Blocked when there are no commits yet on the current branch.

The original HEAD commit becomes an orphan: it is no longer reachable from
any branch ref but remains in the database for forensic traceability.  A
future ``muse gc`` pass may prune it.
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
    insert_commit,
    open_session,
    upsert_object,
    upsert_snapshot,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import read_merge_state
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import (
    build_snapshot_manifest,
    compute_commit_id,
    compute_snapshot_id,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _amend_async(
    *,
    message: str | None,
    no_edit: bool,
    reset_author: bool,
    root: pathlib.Path,
    session: AsyncSession,
) -> str:
    """Run the amend pipeline and return the new ``commit_id``.

    All filesystem and DB side-effects are isolated here so tests can inject
    an in-memory SQLite session and a ``tmp_path`` root without touching a
    real database.

    Args:
        message:      New commit message, or ``None`` to keep the original.
                      Ignored when *no_edit* is ``True``.
        no_edit:      When ``True``, keep the original commit message even if
                      *message* is also supplied.
        reset_author: When ``True``, reset the author field (stub: empty string
                      until a user-identity system is introduced).
        root:         Repository root (directory containing ``.muse/``).
        session:      An open async DB session.

    Returns:
        The new ``commit_id`` (64-char sha256 hex string).

    Raises:
        typer.Exit: On any user-facing error (merge in progress, no commits,
                    empty working tree, DB inconsistency).
    """
    muse_dir = root / ".muse"

    # ── Guard: block amend while a conflicted merge is in progress ──────
    merge_state = read_merge_state(root)
    if merge_state is not None:
        typer.echo(
            "❌ A merge is in progress — amend is not allowed.\n"
            "   Resolve any conflicts and run 'muse commit', or abort the merge first."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # ── Current branch ───────────────────────────────────────────────────
    head_ref = (muse_dir / "HEAD").read_text().strip()   # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1]                 # "main"
    ref_path = muse_dir / pathlib.Path(head_ref)

    if not ref_path.exists() or not ref_path.read_text().strip():
        typer.echo(
            "❌ Nothing to amend — no commits yet on this branch.\n"
            "   Run 'muse commit -m <message>' to create the first commit."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    head_commit_id = ref_path.read_text().strip()

    # ── Load HEAD commit to get its parent and original message ──────────
    head_commit = await session.get(MuseCliCommit, head_commit_id)
    if head_commit is None:
        typer.echo(
            f"❌ HEAD commit {head_commit_id[:8]} not found in database.\n"
            "   Repository may be in an inconsistent state."
        )
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # ── Determine effective commit message ────────────────────────────────
    # --no-edit (or no -m supplied) → keep original; -m TEXT → use TEXT.
    if no_edit or message is None:
        effective_message = head_commit.message
    else:
        effective_message = message

    # ── Build new snapshot from muse-work/ ───────────────────────────────
    workdir = root / "muse-work"
    if not workdir.exists():
        typer.echo(
            "⚠️  No muse-work/ directory found — nothing to snapshot.\n"
            "     Generate some artifacts before running 'muse amend'."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = build_snapshot_manifest(workdir)
    if not manifest:
        typer.echo("⚠️  muse-work/ is empty — cannot amend with an empty snapshot.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot_id = compute_snapshot_id(manifest)

    # ── Compute new commit ID (same parent as the original HEAD) ─────────
    # The amended commit inherits the original commit's *parent*, keeping
    # the linear chain intact and orphaning the original HEAD.
    parent_commit_id = head_commit.parent_commit_id
    parent_ids = [parent_commit_id] if parent_commit_id else []

    committed_at = datetime.datetime.now(datetime.timezone.utc)
    new_commit_id = compute_commit_id(
        parent_ids=parent_ids,
        snapshot_id=snapshot_id,
        message=effective_message,
        committed_at_iso=committed_at.isoformat(),
    )

    # ── Persist objects ──────────────────────────────────────────────────
    for rel_path, object_id in manifest.items():
        file_path = workdir / rel_path
        size = file_path.stat().st_size
        await upsert_object(session, object_id=object_id, size_bytes=size)

    # ── Persist snapshot ─────────────────────────────────────────────────
    await upsert_snapshot(session, manifest=manifest, snapshot_id=snapshot_id)
    # Flush so the snapshot FK constraint is satisfied before inserting the commit.
    await session.flush()

    # ── Persist amended commit ────────────────────────────────────────────
    author = "" if not reset_author else ""  # stub: no user-identity system yet
    new_commit = MuseCliCommit(
        commit_id=new_commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=parent_commit_id,
        snapshot_id=snapshot_id,
        message=effective_message,
        author=author,
        committed_at=committed_at,
    )
    await insert_commit(session, new_commit)

    # ── Update branch HEAD pointer ─────────────────────────────────────────
    ref_path.write_text(new_commit_id)

    typer.echo(f"✅ [{branch} {new_commit_id[:8]}] {effective_message} (amended)")
    logger.info(
        "✅ muse amend %s → %s on %r: %s",
        head_commit_id[:8],
        new_commit_id[:8],
        branch,
        effective_message,
    )
    return new_commit_id


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def amend(
    ctx: typer.Context,
    message: Optional[str] = typer.Option(
        None, "-m", "--message", help="Replace the commit message."
    ),
    no_edit: bool = typer.Option(
        False,
        "--no-edit",
        help="Keep the original commit message. Takes precedence over -m.",
    ),
    reset_author: bool = typer.Option(
        False,
        "--reset-author",
        help="Reset the author field to the current user.",
    ),
) -> None:
    """Fold working-tree changes into the most recent commit."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _amend_async(
                message=message,
                no_edit=no_edit,
                reset_author=reset_author,
                root=root,
                session=session,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse amend failed: {exc}")
        logger.error("❌ muse amend error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
