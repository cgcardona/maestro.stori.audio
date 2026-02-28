"""muse status — show working-tree state relative to HEAD.

Output modes
------------

**Default (verbose, human-readable)**::

    On branch main

    Changes since last commit:
      (use "muse commit -m <msg>" to record changes)

            modified:   beat.mid
            new file:   lead.mp3
            deleted:    scratch.mid

**--short** (condensed, one file per line)::

    M beat.mid
    A lead.mp3
    D scratch.mid

**--porcelain** (machine-readable, stable for scripting, like git status --porcelain)::

    ## main
     M beat.mid
     A lead.mp3
     D scratch.mid

**--branch** (branch and tracking info only)::

    On branch main

**--sections** (group output by first directory component — musical sections)::

    On branch main

    ## chorus
    M chorus/bass.mid
    A chorus/drums.mid

    ## verse
    M verse/bass.mid

**--tracks** (group output by first directory component — instrument tracks)::

    On branch main

    ## bass
    M bass/verse.mid
    A bass/chorus.mid

    ## drums
    M drums/verse.mid

Flags are combinable where it makes sense:
- ``--short --sections`` → short-format codes within section groups
- ``--porcelain --tracks`` → porcelain codes within track groups
- ``--branch`` → emits only the branch line regardless of other flags
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import get_head_snapshot_manifest, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import read_merge_state
from maestro.muse_cli.snapshot import diff_workdir_vs_snapshot, walk_workdir

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Status code maps
# ---------------------------------------------------------------------------

# One-character codes for --short
_SHORT_CODES: dict[str, str] = {
    "modified": "M",
    "added": "A",
    "deleted": "D",
    "untracked": "?",
}

# Two-character codes for --porcelain (index + working-tree columns)
_PORCELAIN_CODES: dict[str, str] = {
    "modified": " M",
    "added": " A",
    "deleted": " D",
    "untracked": "??",
}

# Verbose labels for default output
_VERBOSE_LABELS: dict[str, str] = {
    "modified": "modified:  ",
    "added": "new file:  ",
    "deleted": "deleted:   ",
    "untracked": "untracked: ",
}


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _status_entries(
    added: set[str],
    modified: set[str],
    deleted: set[str],
    untracked: set[str],
) -> list[tuple[str, str]]:
    """Return a sorted list of (status_type, path) pairs.

    Ordering: modified first, then added, deleted, untracked — mirroring
    git's display convention of most-relevant changes first.
    """
    entries: list[tuple[str, str]] = []
    for path in sorted(modified):
        entries.append(("modified", path))
    for path in sorted(added):
        entries.append(("added", path))
    for path in sorted(deleted):
        entries.append(("deleted", path))
    for path in sorted(untracked):
        entries.append(("untracked", path))
    return entries


def _format_line(status: str, path: str, *, short: bool, porcelain: bool) -> str:
    """Format a single file line according to the active output mode.

    Priority: porcelain → short → verbose.

    Args:
        status:    One of ``"modified"``, ``"added"``, ``"deleted"``, ``"untracked"``.
        path:      Repo-relative path (POSIX separators).
        short:     Emit condensed ``X path`` format.
        porcelain: Emit stable ``XY path`` format.

    Returns:
        A formatted line string (no trailing newline).
    """
    if porcelain:
        code = _PORCELAIN_CODES[status]
        return f"{code} {path}"
    if short:
        code = _SHORT_CODES[status]
        return f"{code} {path}"
    label = _VERBOSE_LABELS[status]
    return f"\t{label} {path}"


def _group_by_first_dir(entries: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Group ``(status, path)`` entries by the first directory component of *path*.

    Files that live directly in the working-tree root (no sub-directory) are
    placed under the key ``"(root)"``.  This allows section/track grouping to
    degrade gracefully when users have files at the top level.
    """
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for status, path in entries:
        slash = path.find("/")
        key = path[:slash] if slash != -1 else "(root)"
        groups[key].append((status, path))
    return dict(groups)


def _render_flat(
    entries: list[tuple[str, str]],
    *,
    short: bool,
    porcelain: bool,
) -> None:
    """Write all entries to stdout in flat (non-grouped) order."""
    for status, path in entries:
        typer.echo(_format_line(status, path, short=short, porcelain=porcelain))


def _render_grouped(
    entries: list[tuple[str, str]],
    *,
    short: bool,
    porcelain: bool,
) -> None:
    """Write entries to stdout grouped under ``## <section>`` headers.

    Grouping is by the first directory component of each path.  Within each
    group the entries are sorted by path.  An empty line follows each group
    to improve readability.
    """
    groups = _group_by_first_dir(entries)
    for group_name in sorted(groups.keys()):
        typer.echo(f"## {group_name}")
        for status, path in sorted(groups[group_name], key=lambda t: t[1]):
            typer.echo(_format_line(status, path, short=short, porcelain=porcelain))
        typer.echo("")


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _status_async(
    *,
    root: Path,
    session: AsyncSession,
    short: bool = False,
    branch_only: bool = False,
    porcelain: bool = False,
    sections: bool = False,
    tracks: bool = False,
) -> None:
    """Core status logic — fully injectable for tests.

    Reads repo state from ``.muse/``, queries the DB session for the HEAD
    snapshot manifest, diffs the working tree, and writes formatted output
    via :func:`typer.echo`.

    Output mode selection (evaluated in priority order):

    1. ``branch_only`` → emit only the branch line and return.
    2. ``porcelain`` → machine-readable ``XY path`` format (stable for scripts).
    3. ``short`` → condensed ``X path`` format.
    4. ``sections`` or ``tracks`` → group under ``## <dir>`` headers.
    5. Default → verbose human-readable format.

    ``sections`` and ``tracks`` are orthogonal to ``short``/``porcelain`` and
    can be combined with them: e.g. ``--short --sections`` emits short-format
    lines grouped by section.

    Args:
        root:        Repository root (directory containing ``.muse/``).
        session:     An open async DB session used to load the HEAD snapshot.
        short:       Emit condensed one-line-per-file output.
        branch_only: Emit only the branch/tracking line; skip file listing.
        porcelain:   Emit machine-readable ``XY path`` format with ``## branch`` header.
        sections:    Group output by first directory component (musical sections).
        tracks:      Group output by first directory component (instrument tracks).
    """
    muse_dir = root / ".muse"
    grouped = sections or tracks

    # -- Branch name --
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip()          # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref

    # --branch: emit only the branch line and return.
    if branch_only:
        typer.echo(f"On branch {branch}")
        return

    # -- In-progress merge --
    merge_state = read_merge_state(root)
    if merge_state is not None and merge_state.conflict_paths:
        typer.echo(f"On branch {branch}")
        typer.echo("")
        typer.echo("You have unmerged paths.")
        typer.echo('  (fix conflicts and run "muse commit")')
        typer.echo("")
        typer.echo("Unmerged paths:")
        for conflict_path in sorted(merge_state.conflict_paths):
            typer.echo(f"\tboth modified:   {conflict_path}")
        typer.echo("")
        return

    # -- Check for any commits on this branch --
    ref_path = muse_dir / head_ref
    head_commit_id = ""
    if ref_path.exists():
        head_commit_id = ref_path.read_text().strip()

    if not head_commit_id:
        # No commits yet — show untracked working-tree files if any.
        workdir = root / "muse-work"
        untracked_files: set[str] = set()
        if workdir.exists():
            manifest = walk_workdir(workdir)
            untracked_files = set(manifest.keys())

        if untracked_files:
            entries = _status_entries(set(), set(), set(), untracked_files)
            if porcelain:
                typer.echo(f"## {branch}")
                _render_flat(entries, short=False, porcelain=True)
            elif short:
                typer.echo(f"On branch {branch}, no commits yet")
                _render_flat(entries, short=True, porcelain=False)
            elif grouped:
                typer.echo(f"On branch {branch}, no commits yet")
                typer.echo("")
                _render_grouped(entries, short=False, porcelain=False)
            else:
                typer.echo(f"On branch {branch}, no commits yet")
                typer.echo("")
                typer.echo("Untracked files:")
                typer.echo('  (use "muse commit -m <msg>" to record changes)')
                typer.echo("")
                for path in sorted(untracked_files):
                    typer.echo(f"\t{path}")
                typer.echo("")
        else:
            if porcelain:
                typer.echo(f"## {branch}")
            else:
                typer.echo(f"On branch {branch}, no commits yet")
        return

    # -- Load HEAD snapshot manifest from DB --
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    last_manifest = await get_head_snapshot_manifest(session, repo_id, branch) or {}

    # -- Diff workdir vs HEAD snapshot --
    workdir = root / "muse-work"
    added, modified, deleted, _ = diff_workdir_vs_snapshot(workdir, last_manifest)

    if not added and not modified and not deleted:
        if porcelain:
            typer.echo(f"## {branch}")
        else:
            typer.echo(f"On branch {branch}")
            typer.echo("nothing to commit, working tree clean")
        return

    # -- Render based on active output mode --
    entries = _status_entries(added, modified, deleted, set())

    if porcelain:
        typer.echo(f"## {branch}")
        if grouped:
            _render_grouped(entries, short=False, porcelain=True)
        else:
            _render_flat(entries, short=False, porcelain=True)
        return

    if short:
        typer.echo(f"On branch {branch}")
        if grouped:
            _render_grouped(entries, short=True, porcelain=False)
        else:
            _render_flat(entries, short=True, porcelain=False)
        return

    if grouped:
        typer.echo(f"On branch {branch}")
        typer.echo("")
        _render_grouped(entries, short=False, porcelain=False)
        return

    # -- Default verbose format --
    typer.echo(f"On branch {branch}")
    typer.echo("")
    typer.echo("Changes since last commit:")
    typer.echo('  (use "muse commit -m <msg>" to record changes)')
    typer.echo("")
    for path in sorted(modified):
        typer.echo(f"\tmodified:   {path}")
    for path in sorted(added):
        typer.echo(f"\tnew file:   {path}")
    for path in sorted(deleted):
        typer.echo(f"\tdeleted:    {path}")
    typer.echo("")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def status(
    ctx: typer.Context,
    short: bool = typer.Option(
        False,
        "--short",
        "-s",
        help="Condensed one-line-per-file output (M=modified, A=added, D=deleted, ?=untracked).",
    ),
    branch: bool = typer.Option(
        False,
        "--branch",
        "-b",
        help="Show only the branch and tracking info line.",
    ),
    porcelain: bool = typer.Option(
        False,
        "--porcelain",
        help="Machine-readable output format (stable for scripting, like git status --porcelain).",
    ),
    sections: bool = typer.Option(
        False,
        "--sections",
        help="Group output by musical section directory (first path component under muse-work/).",
    ),
    tracks: bool = typer.Option(
        False,
        "--tracks",
        help="Group output by instrument track directory (first path component under muse-work/).",
    ),
) -> None:
    """Show the current branch and working-tree state relative to HEAD."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _status_async(
                root=root,
                session=session,
                short=short,
                branch_only=branch,
                porcelain=porcelain,
                sections=sections,
                tracks=tracks,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"muse status failed: {exc}")
        logger.error("muse status error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
