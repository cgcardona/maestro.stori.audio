"""muse log — commit history display with full flag set for history navigation.

Walks the commit parent chain from the current branch HEAD and prints
each commit newest-first with configurable formatting and filtering.

Output modes (combinable with filters):

Default (``git log`` style)::

    commit a1b2c3d4  (HEAD -> main)
    Parent: f9e8d7c6
    Date:   2026-02-27 17:30:00

        boom bap demo take 1

``--oneline``::

    a1b2c3d4 (HEAD -> main) boom bap demo take 1
    f9e8d7c6 initial take

``--graph``::

    * a1b2c3d4 boom bap demo take 1 (HEAD)
    * f9e8d7c6 initial take

``--stat``::

    commit a1b2c3d4  (HEAD -> main)
    Date:   2026-02-27 17:30:00

        boom bap demo take 1

     muse-work/drums/jazz.mid | added
     2 files changed, 1 added, 1 removed

``--patch``::

    commit a1b2c3d4  (HEAD -> main)
    Date:   2026-02-27 17:30:00

        boom bap demo take 1

    --- /dev/null
    +++ muse-work/drums/jazz.mid
    --- muse-work/bass/old.mid
    +++ /dev/null

Filters (all combinable with each other and with output modes):

- ``--since DATE`` / ``--until DATE`` — ISO date or relative ("2 weeks ago")
- ``--author TEXT`` — case-insensitive substring match on author field
- ``--emotion TEXT`` — commits tagged ``emotion:<TEXT>``
- ``--section TEXT`` — commits tagged ``section:<TEXT>``
- ``--track TEXT`` — commits tagged ``track:<TEXT>``

``--graph`` reuses ``maestro.services.muse_log_render.render_ascii_graph``
by adapting ``MuseCliCommit`` rows to the ``MuseLogGraph``/``MuseLogNode``
dataclasses that the renderer expects.

Merge commits (two parents) will be supported once ``muse merge`` lands
in issue #35.  The current data model stores a single ``parent_commit_id``;
``parent2_commit_id`` is reserved for that iteration.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot, MuseCliTag

logger = logging.getLogger(__name__)

app = typer.Typer()

_DEFAULT_LIMIT = 1000


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


def parse_date_filter(text: str) -> datetime:
    """Parse a human-readable date string into a timezone-aware UTC datetime.

    Accepts ISO dates and relative English expressions so that producers can
    write ``--since "2 weeks ago"`` without computing exact timestamps.

    Supported formats:
    - ISO: ``"2026-01-01"``, ``"2026-01-01T12:00:00"``, ``"2026-01-01 12:00:00"``
    - Relative: ``"N days ago"``, ``"N weeks ago"``, ``"N months ago"``,
      ``"N years ago"``, ``"yesterday"``, ``"today"``

    Raises:
        ValueError: When no supported format matches.
    """
    text = text.strip().lower()
    now = datetime.now(timezone.utc)

    if text == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if text == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    m = re.match(r"^(\d+)\s+(day|week|month|year)s?\s+ago$", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta: timedelta
        if unit == "day":
            delta = timedelta(days=n)
        elif unit == "week":
            delta = timedelta(weeks=n)
        elif unit == "month":
            delta = timedelta(days=n * 30)
        else:  # year
            delta = timedelta(days=n * 365)
        return now - delta

    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    raise ValueError(
        f"Cannot parse date: {text!r}. "
        "Use ISO format (YYYY-MM-DD) or relative ('N days/weeks/months ago')."
    )


# ---------------------------------------------------------------------------
# Snapshot diff
# ---------------------------------------------------------------------------


@dataclass
class CommitDiff:
    """File-level diff between a commit and its parent.

    Computed by comparing snapshot manifests (path → object_id maps).
    Used by --stat and --patch renderers to describe what changed in each commit.
    """

    added: list[str]
    removed: list[str]
    changed: list[str]

    @property
    def total_files(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)


async def _compute_diff(session: AsyncSession, commit: MuseCliCommit) -> CommitDiff:
    """Compare *commit*'s snapshot with its parent's snapshot.

    Returns a :class:`CommitDiff` with lists of added, removed, and modified paths.
    For the root commit (no parent) all files are treated as added.
    """
    current_snap = await session.get(MuseCliSnapshot, commit.snapshot_id)
    current_manifest: dict[str, str] = dict(current_snap.manifest) if current_snap else {}

    parent_manifest: dict[str, str] = {}
    if commit.parent_commit_id:
        parent_commit = await session.get(MuseCliCommit, commit.parent_commit_id)
        if parent_commit:
            parent_snap = await session.get(MuseCliSnapshot, parent_commit.snapshot_id)
            if parent_snap:
                parent_manifest = dict(parent_snap.manifest)

    current_paths = set(current_manifest.keys())
    parent_paths = set(parent_manifest.keys())

    return CommitDiff(
        added=sorted(current_paths - parent_paths),
        removed=sorted(parent_paths - current_paths),
        changed=sorted(
            p for p in current_paths & parent_paths
            if current_manifest[p] != parent_manifest[p]
        ),
    )


# ---------------------------------------------------------------------------
# Commit loading with inline filters
# ---------------------------------------------------------------------------


async def _load_commits(
    session: AsyncSession,
    head_commit_id: str,
    limit: int,
    since: datetime | None = None,
    until: datetime | None = None,
    author: str | None = None,
) -> list[MuseCliCommit]:
    """Walk the parent chain from *head_commit_id*, returning newest-first.

    Applies date and author filters inline while walking so we stop early when
    walking past the ``--since`` boundary.  Tag-based filters (emotion, section,
    track) are applied afterward by ``_filter_by_tags`` to keep this function
    focused on chain traversal.

    Date comparison uses ``committed_at`` (UTC-aware).  Both ``since`` and
    ``until`` should be UTC-aware datetimes (produced by :func:`parse_date_filter`).
    """
    commits: list[MuseCliCommit] = []
    current_id: str | None = head_commit_id
    while current_id and len(commits) < limit:
        commit = await session.get(MuseCliCommit, current_id)
        if commit is None:
            logger.warning("⚠️ Commit %s not found in DB — chain broken", current_id[:8])
            break

        ts = commit.committed_at
        # Normalise to UTC-aware for comparison
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        # --until: skip commits after the cutoff but keep walking (older commits may qualify)
        if until is not None:
            until_aware = until if until.tzinfo else until.replace(tzinfo=timezone.utc)
            if ts > until_aware:
                current_id = commit.parent_commit_id
                continue

        # --since: stop walking — everything older is also out of range
        if since is not None:
            since_aware = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            if ts < since_aware:
                break

        # --author: case-insensitive substring match
        if author is not None and author.lower() not in commit.author.lower():
            current_id = commit.parent_commit_id
            continue

        commits.append(commit)
        current_id = commit.parent_commit_id

    return commits


async def _filter_by_tags(
    session: AsyncSession,
    commits: list[MuseCliCommit],
    emotion: str | None,
    section: str | None,
    track: str | None,
) -> list[MuseCliCommit]:
    """Retain only commits that have ALL of the requested music-native tags.

    Tags are stored in the ``muse_cli_tags`` table with ``emotion:<value>``,
    ``section:<value>``, and ``track:<value>`` conventions.  A commit must
    carry every specified tag to pass the filter — filters are AND-combined.

    When no tag filters are specified the input list is returned unchanged.
    """
    required_tags: list[str] = []
    if emotion:
        required_tags.append(f"emotion:{emotion}")
    if section:
        required_tags.append(f"section:{section}")
    if track:
        required_tags.append(f"track:{track}")

    if not required_tags:
        return commits

    matched: list[MuseCliCommit] = []
    for commit in commits:
        cid = commit.commit_id
        has_all = True
        for tag_val in required_tags:
            result = await session.execute(
                select(MuseCliTag).where(
                    MuseCliTag.commit_id == cid,
                    MuseCliTag.tag == tag_val,
                )
            )
            if result.scalar_one_or_none() is None:
                has_all = False
                break
        if has_all:
            matched.append(commit)

    return matched


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_log(
    commits: list[MuseCliCommit],
    *,
    head_commit_id: str,
    branch: str,
) -> None:
    """Print commits in ``git log`` style, newest-first."""
    for commit in commits:
        head_marker = f"  (HEAD -> {branch})" if commit.commit_id == head_commit_id else ""
        typer.echo(f"commit {commit.commit_id}{head_marker}")
        if commit.parent_commit_id:
            typer.echo(f"Parent: {commit.parent_commit_id[:8]}")
        ts = commit.committed_at.strftime("%Y-%m-%d %H:%M:%S")
        typer.echo(f"Date:   {ts}")
        typer.echo("")
        typer.echo(f"    {commit.message}")
        typer.echo("")


def _render_oneline(
    commits: list[MuseCliCommit],
    *,
    head_commit_id: str,
    branch: str,
) -> None:
    """Print one line per commit: ``<short_id> [HEAD marker] <message>``."""
    for commit in commits:
        short = commit.commit_id[:8]
        head_marker = f" (HEAD -> {branch})" if commit.commit_id == head_commit_id else ""
        typer.echo(f"{short}{head_marker} {commit.message}")


def _render_graph(commits: list[MuseCliCommit], *, head_commit_id: str) -> None:
    """Render ASCII DAG via ``render_ascii_graph``.

    Adapts ``MuseCliCommit`` rows to ``MuseLogGraph``/``MuseLogNode`` so
    the existing renderer can be reused without modification.

    Commits are passed in newest-first (as returned by ``_load_commits``);
    the renderer expects oldest-first, so the list is reversed before
    building the graph.
    """
    from maestro.services.muse_log_graph import MuseLogGraph, MuseLogNode
    from maestro.services.muse_log_render import render_ascii_graph

    nodes = tuple(
        MuseLogNode(
            variation_id=c.commit_id,
            parent=c.parent_commit_id,
            parent2=None,           # merge parent — added in issue #35
            is_head=(c.commit_id == head_commit_id),
            timestamp=c.committed_at.timestamp(),
            intent=c.message,
            affected_regions=(),
        )
        for c in reversed(commits)  # oldest → newest for the DAG walker
    )
    graph_obj = MuseLogGraph(project_id="muse-cli", head=head_commit_id, nodes=nodes)
    typer.echo(render_ascii_graph(graph_obj))


def _render_stat(
    commits: list[MuseCliCommit],
    diffs: list[CommitDiff],
    *,
    head_commit_id: str,
    branch: str,
) -> None:
    """Print commits with per-commit file change statistics.

    Each commit block shows the standard header followed by a compact
    file-change summary: one line per changed path and a totals line.
    Mirrors ``git log --stat`` output style.
    """
    for commit, diff in zip(commits, diffs):
        head_marker = f"  (HEAD -> {branch})" if commit.commit_id == head_commit_id else ""
        typer.echo(f"commit {commit.commit_id}{head_marker}")
        if commit.parent_commit_id:
            typer.echo(f"Parent: {commit.parent_commit_id[:8]}")
        ts = commit.committed_at.strftime("%Y-%m-%d %H:%M:%S")
        typer.echo(f"Date:   {ts}")
        typer.echo("")
        typer.echo(f"    {commit.message}")
        typer.echo("")

        # File stats
        for path in diff.added:
            typer.echo(f" {path} | added")
        for path in diff.changed:
            typer.echo(f" {path} | modified")
        for path in diff.removed:
            typer.echo(f" {path} | removed")

        total = diff.total_files
        if total:
            parts = []
            if diff.added or diff.changed:
                parts.append(f"{len(diff.added) + len(diff.changed)} added")
            if diff.removed or diff.changed:
                parts.append(f"{len(diff.removed) + len(diff.changed)} removed")
            typer.echo(f" {total} file{'s' if total != 1 else ''} changed, {', '.join(parts)}")
        else:
            typer.echo(" (no file changes)")
        typer.echo("")


def _render_patch(
    commits: list[MuseCliCommit],
    diffs: list[CommitDiff],
    *,
    head_commit_id: str,
    branch: str,
) -> None:
    """Print commits with a path-level diff block.

    Shows which files were added, removed, or modified relative to the
    parent commit.  This is a structural diff (path-level, not byte-level)
    since Muse tracks MIDI/audio blobs that are not line-diffable.
    Mirrors the visual intent of ``git log --patch``.
    """
    for commit, diff in zip(commits, diffs):
        head_marker = f"  (HEAD -> {branch})" if commit.commit_id == head_commit_id else ""
        typer.echo(f"commit {commit.commit_id}{head_marker}")
        if commit.parent_commit_id:
            typer.echo(f"Parent: {commit.parent_commit_id[:8]}")
        ts = commit.committed_at.strftime("%Y-%m-%d %H:%M:%S")
        typer.echo(f"Date:   {ts}")
        typer.echo("")
        typer.echo(f"    {commit.message}")
        typer.echo("")

        if diff.total_files == 0:
            typer.echo("(no file changes)")
            typer.echo("")
            continue

        for path in diff.added:
            typer.echo(f"--- /dev/null")
            typer.echo(f"+++ {path}")
        for path in diff.changed:
            typer.echo(f"--- {path}")
            typer.echo(f"+++ {path}")
        for path in diff.removed:
            typer.echo(f"--- {path}")
            typer.echo(f"+++ /dev/null")
        typer.echo("")


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _log_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    limit: int,
    graph: bool,
    oneline: bool = False,
    stat: bool = False,
    patch: bool = False,
    since: datetime | None = None,
    until: datetime | None = None,
    author: str | None = None,
    emotion: str | None = None,
    section: str | None = None,
    track: str | None = None,
) -> None:
    """Core log logic — fully injectable for tests.

    Reads repo state from ``.muse/``, loads and filters commits from the DB
    session, then dispatches to the appropriate renderer based on output mode
    flags.  All flags are combinable: filters narrow the commit set, output
    mode flags control formatting.

    Priority when multiple output modes are specified:
    ``--graph`` > ``--oneline`` > ``--stat`` > ``--patch`` > default.
    """
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]  # noqa: F841 — kept for future remote filtering

    head_ref = (muse_dir / "HEAD").read_text().strip()   # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1]                 # "main"
    ref_path = muse_dir / pathlib.Path(head_ref)

    head_commit_id = ""
    if ref_path.exists():
        head_commit_id = ref_path.read_text().strip()

    if not head_commit_id:
        typer.echo(f"No commits yet on branch {branch}")
        raise typer.Exit(code=ExitCode.SUCCESS)

    commits = await _load_commits(
        session,
        head_commit_id=head_commit_id,
        limit=limit,
        since=since,
        until=until,
        author=author,
    )

    # Apply tag-based filters (emotion, section, track)
    commits = await _filter_by_tags(session, commits, emotion=emotion, section=section, track=track)

    if not commits:
        typer.echo(f"No commits yet on branch {branch}")
        raise typer.Exit(code=ExitCode.SUCCESS)

    if graph:
        _render_graph(commits, head_commit_id=head_commit_id)
    elif oneline:
        _render_oneline(commits, head_commit_id=head_commit_id, branch=branch)
    elif stat:
        diffs = [await _compute_diff(session, c) for c in commits]
        _render_stat(commits, diffs, head_commit_id=head_commit_id, branch=branch)
    elif patch:
        diffs = [await _compute_diff(session, c) for c in commits]
        _render_patch(commits, diffs, head_commit_id=head_commit_id, branch=branch)
    else:
        _render_log(commits, head_commit_id=head_commit_id, branch=branch)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def log(
    ctx: typer.Context,
    limit: int = typer.Option(
        _DEFAULT_LIMIT,
        "--limit",
        "-n",
        help="Maximum number of commits to show.",
        min=1,
    ),
    graph: bool = typer.Option(
        False,
        "--graph",
        help="Show ASCII DAG (git log --graph style).",
    ),
    oneline: bool = typer.Option(
        False,
        "--oneline",
        help="One line per commit: <short_id> [HEAD] <message>.",
    ),
    stat: bool = typer.Option(
        False,
        "--stat",
        help="Show file-change statistics per commit (files added/removed/modified).",
    ),
    patch: bool = typer.Option(
        False,
        "--patch",
        "-p",
        help="Show path-level diff per commit (files added/removed/modified).",
    ),
    since: Optional[str] = typer.Option(
        None,
        "--since",
        help="Only show commits after DATE (ISO or '2 weeks ago').",
        metavar="DATE",
    ),
    until: Optional[str] = typer.Option(
        None,
        "--until",
        help="Only show commits before DATE (ISO or '2 weeks ago').",
        metavar="DATE",
    ),
    author: Optional[str] = typer.Option(
        None,
        "--author",
        help="Filter commits by author (case-insensitive substring match).",
        metavar="TEXT",
    ),
    emotion: Optional[str] = typer.Option(
        None,
        "--emotion",
        help="Filter commits tagged with emotion:<TEXT> (e.g. 'melancholic').",
        metavar="TEXT",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Filter commits tagged with section:<TEXT> (e.g. 'chorus').",
        metavar="TEXT",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Filter commits tagged with track:<TEXT> (e.g. 'drums').",
        metavar="TEXT",
    ),
) -> None:
    """Display the commit history for the current branch.

    Supports filtering by date, author, and music-native metadata tags,
    and multiple output formats including oneline, graph, stat, and patch.
    All flags are combinable.
    """
    root = require_repo()

    # Parse date filters eagerly so CLI errors surface before any DB work
    since_dt: datetime | None = None
    until_dt: datetime | None = None
    if since:
        try:
            since_dt = parse_date_filter(since)
        except ValueError as exc:
            typer.echo(f"❌ --since: {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)
    if until:
        try:
            until_dt = parse_date_filter(until)
        except ValueError as exc:
            typer.echo(f"❌ --until: {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> None:
        async with open_session() as session:
            await _log_async(
                root=root,
                session=session,
                limit=limit,
                graph=graph,
                oneline=oneline,
                stat=stat,
                patch=patch,
                since=since_dt,
                until=until_dt,
                author=author,
                emotion=emotion,
                section=section,
                track=track,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse log failed: {exc}")
        logger.error("❌ muse log error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
