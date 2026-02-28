"""muse checkout — switch branches or create a new branch from current HEAD.

Two usage patterns
------------------
Create + switch (``-b``)::

    muse checkout -b <new-branch>

Writes ``.muse/refs/heads/<new-branch>`` with the current HEAD commit ID,
then updates ``.muse/HEAD`` to point at the new branch.  Aborts if the
branch already exists.

Switch to existing branch::

    muse checkout <branch>

Updates ``.muse/HEAD`` to point at ``refs/heads/<branch>``.  Aborts if the
branch does not exist.

Both operations are purely local filesystem writes — no DB interaction is
required at checkout time.  The DAG is intact in the DB; subsequent ``muse
log`` and ``muse commit`` commands operate correctly from the new HEAD.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Optional

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable core — no Typer coupling
# ---------------------------------------------------------------------------


def checkout_branch(
    *,
    root: pathlib.Path,
    branch: str,
    create: bool,
) -> None:
    """Switch to *branch*, optionally creating it from the current HEAD.

    Raises :class:`typer.Exit` on user errors (branch exists when creating,
    branch absent when switching).

    Args:
        root:   Repository root (directory containing ``.muse/``).
        branch: Target branch name (must be a simple identifier — no slashes
                beyond the ``feature/`` prefix convention).
        create: When ``True``, create the branch from HEAD then switch to it.
                When ``False``, switch to the existing branch.
    """
    muse_dir = root / ".muse"
    target_ref_path = muse_dir / "refs" / "heads" / branch

    if create:
        # Guard: branch must not already exist.
        if target_ref_path.exists() and target_ref_path.read_text().strip():
            typer.echo(
                f"❌ Branch '{branch}' already exists.\n"
                f"   Use 'muse checkout {branch}' to switch to it."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        # Resolve current HEAD commit to seed the new branch pointer.
        head_ref = (muse_dir / "HEAD").read_text().strip()  # "refs/heads/main"
        current_ref_path = muse_dir / pathlib.Path(head_ref)
        current_commit_id = ""
        if current_ref_path.exists():
            current_commit_id = current_ref_path.read_text().strip()

        # Write the new branch ref.
        target_ref_path.parent.mkdir(parents=True, exist_ok=True)
        target_ref_path.write_text(current_commit_id)

        # Update HEAD.
        (muse_dir / "HEAD").write_text(f"refs/heads/{branch}\n")

        typer.echo(f"✅ Switched to a new branch '{branch}'")
        logger.info(
            "✅ muse checkout -b %r (HEAD=%s)", branch, current_commit_id[:8] or "(empty)"
        )

    else:
        # Guard: branch must already exist.
        if not target_ref_path.exists():
            typer.echo(
                f"❌ Branch '{branch}' does not exist.\n"
                f"   Use 'muse checkout -b {branch}' to create it."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        # Update HEAD only — the ref file is already correct.
        (muse_dir / "HEAD").write_text(f"refs/heads/{branch}\n")

        commit_id = target_ref_path.read_text().strip()
        typer.echo(f"✅ Switched to branch '{branch}' [{commit_id[:8] or 'no commits'}]")
        logger.info(
            "✅ muse checkout %r (HEAD=%s)", branch, commit_id[:8] or "(empty)"
        )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def checkout(
    ctx: typer.Context,
    branch: str = typer.Argument(..., help="Branch name to switch to (or create with -b)."),
    create: Optional[bool] = typer.Option(
        None,
        "-b/-B",
        help="Create the branch from the current HEAD and switch to it.",
    ),
) -> None:
    """Switch branches or create a new branch from the current HEAD."""
    root = require_repo()
    checkout_branch(root=root, branch=branch, create=bool(create))
