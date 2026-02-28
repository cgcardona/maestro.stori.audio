"""muse resolve — mark a conflicted file as resolved.

Workflow
--------
When ``muse merge`` encounters conflicts it writes ``.muse/MERGE_STATE.json``
and exits.  The user then inspects the listed conflict paths and resolves each
one — either by keeping their current working-tree version (``--ours``) or by
manually editing the file to the desired content and then marking it resolved.

After resolving each file, call::

    muse resolve muse-work/meta/section-1.json --ours

When all conflicts are cleared, run ``muse merge --continue`` to create the
merge commit.

Resolution strategies
---------------------
- ``--ours``: Accept the current branch's version as-is.  No file is changed;
              the path is simply removed from ``MERGE_STATE.json``'s conflict
              list.
- ``--theirs``: Accept the incoming branch's version.  Because the Muse object
               store is content-addressed and does not persist raw bytes in the
               database, the caller must manually copy or write the desired
               content into ``muse-work/<path>`` before running this command.
               ``muse resolve --theirs`` marks the path resolved after the file
               is in place.

In both cases the path is removed from ``conflict_paths`` in
``MERGE_STATE.json``.  When the list reaches zero, the merge state is cleared
and ``muse merge --continue`` can proceed.
"""
from __future__ import annotations

import logging
import pathlib

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import read_merge_state, write_merge_state

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable core — no Typer coupling
# ---------------------------------------------------------------------------


def resolve_conflict(
    *,
    file_path: str,
    ours: bool,
    root: pathlib.Path,
) -> None:
    """Mark *file_path* as resolved in ``.muse/MERGE_STATE.json``.

    For ``--ours`` no file change is made.  For ``--theirs`` the caller is
    responsible for ensuring the desired content is already in
    ``muse-work/<file_path>`` before calling this function.

    Args:
        file_path: Path of the conflicted file.  Accepted as:
                   - absolute path (converted to relative to ``muse-work/``)
                   - path relative to ``muse-work/`` (e.g. ``meta/foo.json``)
                   - path relative to repo root (e.g. ``muse-work/meta/foo.json``)
        ours:      ``True`` to accept ours, ``False`` to accept theirs (file
                   must already be edited to the desired content).
        root:      Repository root containing ``.muse/``.

    Raises:
        :class:`typer.Exit`: On user errors (no merge in progress, path not
                             in conflict list).
    """
    merge_state = read_merge_state(root)
    if merge_state is None:
        typer.echo("❌ No merge in progress. Nothing to resolve.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Normalise path to be relative to muse-work/.
    workdir = root / "muse-work"
    abs_target = pathlib.Path(file_path)
    if not abs_target.is_absolute():
        # Try treating as relative to repo root first, then fall back to muse-work.
        candidate = root / file_path
        if candidate.exists() or str(file_path).startswith("muse-work/"):
            abs_target = candidate
        else:
            abs_target = workdir / file_path

    try:
        rel_path = abs_target.relative_to(workdir).as_posix()
    except ValueError:
        # File may be given as a bare relative path already relative to muse-work/
        rel_path = file_path.lstrip("/")

    if rel_path not in merge_state.conflict_paths:
        typer.echo(
            f"❌ '{rel_path}' is not listed as a conflict.\n"
            f"   Current conflicts: {merge_state.conflict_paths}"
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    side = "ours" if ours else "theirs"
    typer.echo(f"✅ Resolved '{rel_path}' — keeping {side}")
    logger.info("✅ muse resolve %r ---%s", rel_path, side)

    remaining = [p for p in merge_state.conflict_paths if p != rel_path]

    # Always rewrite MERGE_STATE with the updated (possibly empty) conflict list.
    # Keeping the file even when conflict_paths=[] lets `muse merge --continue`
    # read the stored commit IDs (ours_commit, theirs_commit) to build the merge
    # commit.  `muse merge --continue` is responsible for clearing this file.
    write_merge_state(
        root,
        base_commit=merge_state.base_commit or "",
        ours_commit=merge_state.ours_commit or "",
        theirs_commit=merge_state.theirs_commit or "",
        conflict_paths=remaining,
        other_branch=merge_state.other_branch,
    )

    if remaining:
        typer.echo(
            f"   {len(remaining)} conflict(s) remaining. "
            "Resolve all, then run 'muse merge --continue'."
        )
    else:
        typer.echo(
            "✅ All conflicts resolved. Run 'muse merge --continue' to create the merge commit."
        )
        logger.info("✅ muse resolve: all conflicts cleared, ready for --continue")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def resolve(
    ctx: typer.Context,
    file_path: str = typer.Argument(
        ...,
        help="Conflicted file path (relative to muse-work/ or repo root).",
    ),
    ours: bool = typer.Option(
        False,
        "--ours",
        help="Keep the current branch's version (no file change required).",
    ),
    theirs: bool = typer.Option(
        False,
        "--theirs",
        help="Accept the incoming branch's version (edit the file first, then mark resolved).",
    ),
) -> None:
    """Mark a conflicted file as resolved using --ours or --theirs."""
    if ours == theirs:
        typer.echo("❌ Specify exactly one of --ours or --theirs.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    resolve_conflict(file_path=file_path, ours=ours, root=root)
