"""muse find — search commit history by musical properties.

This is the musical grep. Queries the full commit history for the current
repository and returns commits whose messages match the requested musical
criteria. All filters combine with AND logic.

Examples::

    muse find --harmony "key=F minor"
    muse find --rhythm "tempo=120-130" --since "2026-01-01"
    muse find --emotion melancholic --structure "has=bridge" --json
    muse find --track "bass" --limit 10

Output modes
------------
Default: one commit per line, ``git log``-style.
``--json``: machine-readable JSON array of commit objects.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from datetime import datetime, timezone

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_find import (
    MuseFindCommitResult,
    MuseFindQuery,
    MuseFindResults,
    search_commits,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_DEFAULT_LIMIT = 20


def _load_repo_id(root: pathlib.Path) -> str:
    """Read ``repo_id`` from ``.muse/repo.json``."""
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    return repo_data["repo_id"]


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _find_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    query: MuseFindQuery,
    output_json: bool,
) -> MuseFindResults:
    """Execute the find query and render output.

    Injectable for tests: callers pass a session and tmp_path root.
    Returns the :class:`MuseFindResults` so tests can inspect matches
    without parsing printed output.
    """
    repo_id = _load_repo_id(root)
    results = await search_commits(session, repo_id, query)

    if output_json:
        _render_json(results)
    else:
        _render_text(results)

    return results


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _commit_to_dict(commit: MuseFindCommitResult) -> dict[str, object]:
    """Serialise a :class:`MuseFindCommitResult` to a JSON-serialisable dict."""
    return {
        "commit_id": commit.commit_id,
        "branch": commit.branch,
        "message": commit.message,
        "author": commit.author,
        "committed_at": commit.committed_at.isoformat(),
        "parent_commit_id": commit.parent_commit_id,
        "snapshot_id": commit.snapshot_id,
    }


def _render_json(results: MuseFindResults) -> None:
    """Print matching commits as a JSON array."""
    payload: list[dict[str, object]] = [
        _commit_to_dict(c) for c in results.matches
    ]
    typer.echo(json.dumps(payload, indent=2))


def _render_text(results: MuseFindResults) -> None:
    """Print matching commits in ``git log``-style, newest-first."""
    if not results.matches:
        typer.echo("No commits match the given criteria.")
        return

    for commit in results.matches:
        typer.echo(f"commit {commit.commit_id}")
        if commit.parent_commit_id:
            typer.echo(f"Branch: {commit.branch}")
            typer.echo(f"Parent: {commit.parent_commit_id[:8]}")
        ts = commit.committed_at.strftime("%Y-%m-%d %H:%M:%S")
        typer.echo(f"Date:   {ts}")
        typer.echo("")
        typer.echo(f"    {commit.message}")
        typer.echo("")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def find(
    ctx: typer.Context,
    harmony: str | None = typer.Option(
        None, "--harmony", help='Harmonic filter, e.g. "key=Eb" or "mode=minor".'
    ),
    rhythm: str | None = typer.Option(
        None, "--rhythm", help='Rhythmic filter, e.g. "tempo=120-130" or "meter=7/8".'
    ),
    melody: str | None = typer.Option(
        None, "--melody", help='Melodic filter, e.g. "range>2oct" or "shape=arch".'
    ),
    structure: str | None = typer.Option(
        None, "--structure", help='Structural filter, e.g. "has=bridge" or "form=AABA".'
    ),
    dynamic: str | None = typer.Option(
        None, "--dynamic", help='Dynamic filter, e.g. "avg_vel>80" or "arc=crescendo".'
    ),
    emotion: str | None = typer.Option(
        None, "--emotion", help="Emotion tag, e.g. melancholic."
    ),
    section: str | None = typer.Option(
        None, "--section", help="Find commits containing a named section."
    ),
    track: str | None = typer.Option(
        None, "--track", help="Find commits where a specific track was present."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Restrict to commits after this date (YYYY-MM-DD)."
    ),
    until: str | None = typer.Option(
        None, "--until", help="Restrict to commits before this date (YYYY-MM-DD)."
    ),
    limit: int = typer.Option(
        _DEFAULT_LIMIT,
        "--limit",
        "-n",
        help="Maximum number of results to return.",
        min=1,
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Output results as JSON."
    ),
) -> None:
    """Search commit history by musical properties."""
    # Validate that at least one filter is provided
    all_filters = [harmony, rhythm, melody, structure, dynamic, emotion, section, track, since, until]
    if all(f is None for f in all_filters):
        typer.echo("❌ Provide at least one filter flag. See --help for options.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Parse dates
    since_dt: datetime | None = None
    until_dt: datetime | None = None

    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        except ValueError:
            typer.echo(f"❌ Invalid --since date: {since!r}. Use YYYY-MM-DD format.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    if until is not None:
        try:
            until_dt = datetime.fromisoformat(until).replace(tzinfo=timezone.utc)
        except ValueError:
            typer.echo(f"❌ Invalid --until date: {until!r}. Use YYYY-MM-DD format.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    query = MuseFindQuery(
        harmony=harmony,
        rhythm=rhythm,
        melody=melody,
        structure=structure,
        dynamic=dynamic,
        emotion=emotion,
        section=section,
        track=track,
        since=since_dt,
        until=until_dt,
        limit=limit,
    )

    async def _run() -> None:
        async with open_session() as session:
            await _find_async(root=root, session=session, query=query, output_json=output_json)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse find failed: {exc}")
        logger.error("❌ muse find error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
