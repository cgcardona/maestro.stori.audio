"""muse checkout — create and switch local branches, update .muse/HEAD.

Behavior
--------
* ``muse checkout <branch>`` — switch to an existing branch.
* ``muse checkout -b <branch>`` — create a new branch forked from the
  current HEAD commit and switch to it.
* Dirty working-tree guard: if ``muse-work/`` differs from the last
  committed snapshot the command exits ``1`` with a message, unless
  ``--force`` / ``-f`` is supplied.

Dirty detection
---------------
The working tree is considered dirty when:

1. ``muse-work/`` exists **and** contains at least one file, **and**
2. Its computed ``snapshot_id`` (``sha256`` of the sorted
   ``path:object_id`` pairs) differs from the ``snapshot_id`` of the
   most recent commit on the *current* branch.

If the branch has no commits yet (empty branch) the tree is never
considered dirty — there is nothing to diverge from.

Branch state
------------
Branches are tracked purely on the local filesystem under
``.muse/refs/heads/<name>``.  A DB-level branch table is deferred to
the ``muse merge`` iteration (issue #35) when multi-branch DAG queries
will require it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import get_head_snapshot_id, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.snapshot import build_snapshot_manifest, compute_snapshot_id

logger = logging.getLogger(__name__)

app = typer.Typer()

# Branch names follow the same rules as Git: no spaces, no control chars,
# no leading dots, no double dots, no trailing slash or dot.
_BRANCH_RE = re.compile(r"^[a-zA-Z0-9._\-/]+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_branch_name(name: str) -> None:
    """Exit ``1`` if *name* is not a valid branch identifier."""
    if not _BRANCH_RE.match(name) or ".." in name or name.startswith("."):
        typer.echo(
            f"❌ Invalid branch name '{name}'. "
            "Use letters, digits, hyphens, underscores, dots, or forward slashes."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)


async def _is_dirty(
    session: AsyncSession,
    root: pathlib.Path,
    repo_id: str,
    branch: str,
) -> bool:
    """Return ``True`` if ``muse-work/`` has uncommitted changes.

    Compares the on-disk snapshot against the last committed snapshot on
    *branch*.  Returns ``False`` when the branch has no commits yet.
    """
    workdir = root / "muse-work"
    if not workdir.exists():
        return False
    manifest = build_snapshot_manifest(workdir)
    if not manifest:
        return False
    current_sid = compute_snapshot_id(manifest)
    last_sid = await get_head_snapshot_id(session, repo_id, branch)
    if last_sid is None:
        return False  # no commits on this branch — nothing to dirty against
    return current_sid != last_sid


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _checkout_async(
    *,
    branch_name: str,
    create: bool,
    force: bool,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Core checkout logic, fully injectable for tests.

    Raises ``typer.Exit`` on every terminal condition (success or error)
    so callers do not need to distinguish between return paths.
    """
    _validate_branch_name(branch_name)

    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    head_ref = (muse_dir / "HEAD").read_text().strip()   # "refs/heads/main"
    current_branch = head_ref.rsplit("/", 1)[-1]          # "main"

    ref_path = muse_dir / "refs" / "heads" / branch_name

    if create:
        # ── muse checkout -b <branch> ────────────────────────────────────
        if ref_path.exists() and ref_path.read_text().strip():
            typer.echo(f"❌ Branch '{branch_name}' already exists.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        if not force and await _is_dirty(session, root, repo_id, current_branch):
            typer.echo(
                "❌ Uncommitted changes in muse-work/. "
                "Commit your changes or use --force to override."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        # Fork from current HEAD commit
        current_ref = muse_dir / "refs" / "heads" / current_branch
        current_commit_id = (
            current_ref.read_text().strip() if current_ref.exists() else ""
        )
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(current_commit_id)
        (muse_dir / "HEAD").write_text(f"refs/heads/{branch_name}")

        origin = f" (from commit {current_commit_id[:8]})" if current_commit_id else ""
        typer.echo(f"✅ Switched to a new branch '{branch_name}'{origin}")
        logger.info("✅ Created and switched to branch %r at %s", branch_name, current_commit_id[:8] if current_commit_id else "empty")

    else:
        # ── muse checkout <branch> ───────────────────────────────────────
        if not ref_path.exists():
            typer.echo(
                f"❌ Branch '{branch_name}' does not exist. "
                "Use -b to create it."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        if branch_name == current_branch:
            typer.echo(f"Already on '{branch_name}'")
            raise typer.Exit(code=ExitCode.SUCCESS)

        if not force and await _is_dirty(session, root, repo_id, current_branch):
            typer.echo(
                "❌ Uncommitted changes in muse-work/. "
                "Commit your changes or use --force to override."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        (muse_dir / "HEAD").write_text(f"refs/heads/{branch_name}")
        typer.echo(f"✅ Switched to branch '{branch_name}'")
        logger.info("✅ Switched to branch %r", branch_name)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def checkout(
    ctx: typer.Context,
    branch: str = typer.Argument(..., help="Branch name to checkout or create."),
    create: bool = typer.Option(
        False,
        "-b",
        "--create",
        help="Create a new branch at the current HEAD and switch to it.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Ignore uncommitted changes in muse-work/.",
    ),
) -> None:
    """Create or switch branches; update .muse/HEAD."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _checkout_async(
                branch_name=branch,
                create=create,
                force=force,
                root=root,
                session=session,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse checkout failed: {exc}")
        logger.error("❌ muse checkout error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
