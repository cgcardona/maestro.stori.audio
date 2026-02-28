"""muse commit-tree — create a raw commit object from an existing snapshot.

This is a git-plumbing-style command that creates a commit row in the database
directly from a known ``snapshot_id`` plus explicit metadata.  Unlike
``muse commit``, it does NOT walk the filesystem, does NOT update any branch
ref, and does NOT touch ``.muse/HEAD``.

Why this exists
---------------
Scripting and advanced history manipulation require the ability to construct
commits programmatically — for example when replaying a merge, synthesising
history from an external source, or building tooling on top of Muse's commit
graph.  Separating "create commit object" from "advance branch pointer" mirrors
the design of ``git commit-tree`` + ``git update-ref``.

Idempotency contract
--------------------
``commit_id`` is derived deterministically from
``(parent_ids, snapshot_id, message, author)`` with no timestamp component.
Repeating the same call returns the same ``commit_id`` without inserting a
duplicate row.

Usage::

    muse commit-tree <snapshot_id> -m "feat: re-record verse" \\
        -p <parent_commit_id>

For a merge commit supply two ``-p`` flags::

    muse commit-tree <snapshot_id> -m "Merge groove branch" \\
        -p <parent1> -p <parent2>
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import insert_commit, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.muse_cli.snapshot import compute_commit_tree_id

logger = logging.getLogger(__name__)

app = typer.Typer()

_MAX_PARENTS = 2


def _read_author_from_config(repo_root_str: str) -> str:
    """Read ``[user] name`` from ``.muse/config.toml``, returning ``""`` on miss.

    Config.toml is optional — when absent or when ``[user] name`` is not set
    the author field falls back to an empty string, which matches the behaviour
    of ``muse commit``.
    """
    import pathlib
    import tomllib

    config_path = pathlib.Path(repo_root_str) / ".muse" / "config.toml"
    if not config_path.is_file():
        return ""
    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
        name: object = data.get("user", {}).get("name", "")
        return str(name).strip() if isinstance(name, str) else ""
    except Exception:
        return ""


async def _commit_tree_async(
    *,
    snapshot_id: str,
    message: str,
    parent_ids: list[str],
    author: str,
    session: AsyncSession,
) -> str:
    """Create a raw commit object from an existing snapshot.

    Looks up *snapshot_id* in the database to verify it exists, computes a
    deterministic ``commit_id``, and inserts a ``MuseCliCommit`` row if one
    does not already exist.

    Args:
        snapshot_id: Must reference an existing ``muse_cli_snapshots`` row.
        message: Human-readable commit message (required, non-empty).
        parent_ids: Zero, one, or two parent commit IDs.  Order is irrelevant
            for hashing (sorted internally) but at most two are stored in
            ``parent_commit_id`` / ``parent2_commit_id``.
        author: Author name string.  Empty string is valid.
        session: An open async DB session (committed by the caller).

    Returns:
        The deterministic ``commit_id`` (64-char hex SHA-256).

    Raises:
        ``typer.Exit(USER_ERROR)`` when *snapshot_id* is not found or inputs
        are invalid.
        ``typer.Exit(INTERNAL_ERROR)`` on unexpected DB failures.
    """
    if len(parent_ids) > _MAX_PARENTS:
        typer.echo(
            f"❌ At most {_MAX_PARENTS} parent IDs are supported "
            f"(got {len(parent_ids)})."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Verify snapshot exists
    snapshot = await session.get(MuseCliSnapshot, snapshot_id)
    if snapshot is None:
        typer.echo(
            f"❌ Snapshot {snapshot_id[:12]!r} not found in the database.\n"
            "   Run 'muse commit' first to create a snapshot, or check the ID."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Derive deterministic commit_id (no timestamp → truly idempotent)
    commit_id = compute_commit_tree_id(
        parent_ids=parent_ids,
        snapshot_id=snapshot_id,
        message=message,
        author=author,
    )

    # Idempotency: if the commit already exists, return its ID without re-inserting
    existing = await session.get(MuseCliCommit, commit_id)
    if existing is not None:
        logger.debug("⚠️ commit-tree: commit %s already exists — skipping insert", commit_id[:8])
        typer.echo(commit_id)
        return commit_id

    # Derive parent columns
    parent1: str | None = parent_ids[0] if len(parent_ids) >= 1 else None
    parent2: str | None = parent_ids[1] if len(parent_ids) >= 2 else None

    # Branch is empty: commit-tree does not associate with any branch.
    # Association is deferred to `muse update-ref` (a separate plumbing command).
    new_commit = MuseCliCommit(
        commit_id=commit_id,
        repo_id="",  # plumbing commits carry no repo_id until linked via update-ref
        branch="",  # not associated with any branch ref
        parent_commit_id=parent1,
        parent2_commit_id=parent2,
        snapshot_id=snapshot_id,
        message=message,
        author=author,
        committed_at=datetime.datetime.now(datetime.timezone.utc),
    )
    await insert_commit(session, new_commit)

    logger.info("✅ muse commit-tree created %s", commit_id[:8])
    typer.echo(commit_id)
    return commit_id


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def commit_tree(
    ctx: typer.Context,
    snapshot_id: str = typer.Argument(
        ..., help="The snapshot_id to wrap in a new commit object."
    ),
    message: str = typer.Option(
        ..., "-m", "--message", help="Commit message (required)."
    ),
    parents: Optional[list[str]] = typer.Option(
        None,
        "-p",
        "--parent",
        help=(
            "Parent commit ID.  Specify once for a regular commit, "
            "twice for a merge commit."
        ),
    ),
    author: Optional[str] = typer.Option(
        None,
        "--author",
        help="Author name.  Defaults to [user] name from .muse/config.toml.",
    ),
) -> None:
    """Create a raw commit object from an existing snapshot_id.

    Prints the new (or pre-existing) commit_id to stdout.  Does NOT
    update .muse/HEAD or any branch ref.
    """
    root = require_repo()

    resolved_author = author if author is not None else _read_author_from_config(str(root))
    resolved_parents: list[str] = list(parents) if parents else []

    async def _run() -> None:
        async with open_session() as session:
            await _commit_tree_async(
                snapshot_id=snapshot_id,
                message=message,
                parent_ids=resolved_parents,
                author=resolved_author,
                session=session,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse commit-tree failed: {exc}")
        logger.error("❌ muse commit-tree error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
