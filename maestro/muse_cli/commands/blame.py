"""muse blame <path> — annotate a file with the commit that last changed it.

For each file path in the current HEAD snapshot (filtered by the positional
``<path>`` argument and optional ``--track``/``--section`` flags), walks the
commit graph to find the most recent commit that touched that file.

In music production, blame answers:

- "Whose idea was this bass line?"
- "Which take introduced this change?"
- "Which commit first added the bridge strings?"

Output is per-file (not per-line) because MIDI/audio files are binary — the
meaningful unit of change is a whole file, not a byte offset.

**Algorithm:**

1. Load all commits from HEAD, following ``parent_commit_id`` links.
2. For each adjacent pair ``(C_i, C_{i-1})`` (newest to oldest), load their
   snapshot manifests and compare ``object_id`` values per path.
3. The first pair where a path differs (object_id changed, added, or removed)
   identifies the most recent commit to have touched that path.
4. Paths present in the initial commit (no parent) are attributed to it.

This is O(N × F) in commits × files, which is acceptable for DAW session
history (typically <1 000 commits, <100 files per snapshot).

Flags
-----
PATH TEXT         Positional — relative path within muse-work/ to annotate.
                  Omit to blame all tracked files.
--track TEXT      Filter to paths whose last component matches this pattern
                  (fnmatch-style glob, e.g. ``bass*`` or ``*.mid``).
--section TEXT    Filter to paths whose first directory component equals this
                  section name (e.g. ``chorus`` or ``bridge``).
--line-range N,M  Note: MIDI/audio are binary; line-range is recorded in the
                  output for annotation purposes but does not slice the file.
--json            Emit structured JSON for agent consumption.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class BlameEntry(TypedDict):
    """Blame annotation for a single file path.

    ``change_type`` describes how the path changed in ``commit_id``:

    - ``"added"``     — first commit to include this path
    - ``"modified"``  — object_id changed compared to the parent snapshot
    - ``"unchanged"`` — fallback when the graph walk finds no modification
                        (should not occur in a consistent database)
    """

    path: str
    commit_id: str
    commit_short: str
    author: str
    committed_at: str
    message: str
    change_type: str


class BlameResult(TypedDict):
    """Full output of ``muse blame``.

    ``entries`` is ordered by path (ascending alphabetical).
    """

    path_filter: Optional[str]
    track_filter: Optional[str]
    section_filter: Optional[str]
    line_range: Optional[str]
    entries: list[BlameEntry]


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


async def _load_commit_chain(
    session: AsyncSession,
    head_commit_id: str,
    limit: int = 10_000,
) -> list[MuseCliCommit]:
    """Walk the parent chain from *head_commit_id*, returning newest-first.

    Stops when the chain is exhausted or *limit* is reached.
    """
    commits: list[MuseCliCommit] = []
    current_id: str | None = head_commit_id
    while current_id and len(commits) < limit:
        commit = await session.get(MuseCliCommit, current_id)
        if commit is None:
            logger.warning("⚠️ Commit %s not found — chain broken", current_id[:8])
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


async def _load_snapshot_manifest(
    session: AsyncSession,
    snapshot_id: str,
) -> dict[str, str]:
    """Return the manifest dict for *snapshot_id*, or an empty dict on miss."""
    snapshot = await session.get(MuseCliSnapshot, snapshot_id)
    if snapshot is None:
        logger.warning("⚠️ Snapshot %s not found in DB", snapshot_id[:8])
        return {}
    return dict(snapshot.manifest)


def _matches_filters(
    path: str,
    path_filter: str | None,
    track_filter: str | None,
    section_filter: str | None,
) -> bool:
    """Return True when *path* passes all active filter criteria.

    Filters are AND-combined: all supplied filters must match.

    - *path_filter*: substring / exact match on the full path string.
    - *track_filter*: fnmatch pattern applied to the basename.
    - *section_filter*: exact match on the first directory component.
    """
    if path_filter is not None:
        # Accept both exact match and sub-path match (e.g. "bass" matches
        # "muse-work/bass/bassline.mid" as a substring)
        if path_filter not in path and not path.endswith(path_filter):
            return False

    if track_filter is not None:
        basename = pathlib.PurePosixPath(path).name
        if not fnmatch.fnmatch(basename, track_filter):
            return False

    if section_filter is not None:
        parts = pathlib.PurePosixPath(path).parts
        # parts might be ("muse-work", "chorus", "piano.mid") or ("chorus", "piano.mid")
        # We match the first non-"muse-work" directory component
        dirs = [p for p in parts[:-1] if p != "muse-work"]
        if not dirs or dirs[0] != section_filter:
            return False

    return True


async def _blame_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    path_filter: str | None,
    track_filter: str | None,
    section_filter: str | None,
    line_range: str | None,
) -> BlameResult:
    """Compute blame annotations for all matching paths.

    Walks the commit graph from HEAD, comparing snapshot manifests between
    adjacent commits to attribute each file to the most recent commit that
    touched it.  Returns a :class:`BlameResult` suitable for both human-
    readable rendering and JSON serialisation.
    """
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]  # noqa: F841

    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1]
    ref_path = muse_dir / pathlib.Path(head_ref)

    if not ref_path.exists():
        typer.echo(f"No commits yet on branch {branch}")
        raise typer.Exit(code=ExitCode.SUCCESS)

    head_commit_id = ref_path.read_text().strip()
    if not head_commit_id:
        typer.echo(f"No commits yet on branch {branch}")
        raise typer.Exit(code=ExitCode.SUCCESS)

    # Load all commits newest-first
    commits = await _load_commit_chain(session, head_commit_id)
    if not commits:
        typer.echo(f"No commits yet on branch {branch}")
        raise typer.Exit(code=ExitCode.SUCCESS)

    # Load all manifests up-front (one DB query per snapshot)
    manifests: list[dict[str, str]] = []
    for commit in commits:
        manifest = await _load_snapshot_manifest(session, commit.snapshot_id)
        manifests.append(manifest)

    # HEAD snapshot defines which paths exist right now
    head_manifest = manifests[0]

    # blame_map: path → commit (newest commit that changed this path)
    blame_map: dict[str, tuple[MuseCliCommit, str]] = {}  # path → (commit, change_type)

    # Walk pairs newest→oldest: (commits[i], commits[i+1])
    for i in range(len(commits) - 1):
        newer_commit = commits[i]
        newer_manifest = manifests[i]
        older_manifest = manifests[i + 1]

        for path in newer_manifest:
            if path in blame_map:
                continue  # already attributed to a more recent commit
            newer_oid = newer_manifest[path]
            older_oid = older_manifest.get(path)
            if older_oid is None:
                # Path was added by newer_commit
                blame_map[path] = (newer_commit, "added")
            elif newer_oid != older_oid:
                # Path was modified by newer_commit
                blame_map[path] = (newer_commit, "modified")

    # Any path still unattributed was present in the initial commit (C_0)
    # and never changed after — attribute it to the oldest commit
    oldest_commit = commits[-1]
    for path in head_manifest:
        if path not in blame_map:
            blame_map[path] = (oldest_commit, "added")

    # Build entries, applying filters
    entries: list[BlameEntry] = []
    for path in sorted(head_manifest.keys()):
        if not _matches_filters(path, path_filter, track_filter, section_filter):
            continue
        commit, change_type = blame_map.get(path, (oldest_commit, "unchanged"))
        entries.append(
            BlameEntry(
                path=path,
                commit_id=commit.commit_id,
                commit_short=commit.commit_id[:8],
                author=commit.author or "(unknown)",
                committed_at=commit.committed_at.strftime("%Y-%m-%d %H:%M:%S"),
                message=commit.message,
                change_type=change_type,
            )
        )

    if not entries:
        typer.echo("No matching paths found.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    return BlameResult(
        path_filter=path_filter,
        track_filter=track_filter,
        section_filter=section_filter,
        line_range=line_range,
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_blame(result: BlameResult) -> str:
    """Format blame output as a human-readable annotated file list.

    Each line shows the short commit ID, author, date, change type, and
    the file path — analogous to ``git blame`` but per-file rather than
    per-line, since MIDI and audio files are binary.
    """
    lines: list[str] = []
    if result["line_range"]:
        lines.append(f"(line-range: {result['line_range']} — informational only for binary files)")
        lines.append("")
    for entry in result["entries"]:
        lines.append(
            f"{entry['commit_short']}  {entry['author']:<20}  "
            f"{entry['committed_at']}  ({entry['change_type']:>10})  {entry['path']}"
        )
        lines.append(f"    {entry['message']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def blame(
    ctx: typer.Context,
    path: Optional[str] = typer.Argument(
        None,
        help="Relative path within muse-work/ to annotate. Omit to blame all tracked files.",
        metavar="PATH",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Filter to files whose basename matches this fnmatch pattern (e.g. 'bass*' or '*.mid').",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Filter to files within this section directory (first directory component).",
    ),
    line_range: Optional[str] = typer.Option(
        None,
        "--line-range",
        help="Annotate sub-range N,M (informational for binary MIDI/audio files).",
        metavar="N,M",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON for agent consumption.",
    ),
) -> None:
    """Annotate files with the commit that last changed each one.

    Walks the commit graph from HEAD to find the most recent commit that
    touched each file, answering "whose idea was this bass line?" or
    "which take introduced this change?"

    Output is per-file (not per-line) because MIDI and audio files are
    binary — the meaningful unit of change is a whole file.
    """
    if ctx.invoked_subcommand is not None:
        return

    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            result = await _blame_async(
                root=root,
                session=session,
                path_filter=path,
                track_filter=track,
                section_filter=section,
                line_range=line_range,
            )
            if as_json:
                typer.echo(json.dumps(dict(result), indent=2))
            else:
                typer.echo(_render_blame(result))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse blame failed: {exc}")
        logger.error("❌ muse blame error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
