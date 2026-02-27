"""muse log — commit history display with parent chain and ASCII DAG.

Walks the commit parent chain from the current branch HEAD and prints
each commit newest-first.  Two output modes:

Default (``git log`` style)::

    commit a1b2c3d4  (HEAD -> main)
    Parent: f9e8d7c6
    Date:   2026-02-27 17:30:00

        boom bap demo take 1

Graph mode (``--graph``)::

    * a1b2c3d4 boom bap demo take 1 (HEAD)
    * f9e8d7c6 initial take

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

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

app = typer.Typer()

_DEFAULT_LIMIT = 1000


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _load_commits(
    session: AsyncSession,
    head_commit_id: str,
    limit: int,
) -> list[MuseCliCommit]:
    """Walk the parent chain from *head_commit_id*, returning newest-first.

    Stops when the chain is exhausted or *limit* is reached.  Each commit
    is fetched individually by primary key — O(N) round-trips — which is
    acceptable for the typical log depth of a DAW session.
    """
    commits: list[MuseCliCommit] = []
    current_id: str | None = head_commit_id
    while current_id and len(commits) < limit:
        commit = await session.get(MuseCliCommit, current_id)
        if commit is None:
            logger.warning("⚠️ Commit %s not found in DB — chain broken", current_id[:8])
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


async def _log_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    limit: int,
    graph: bool,
) -> None:
    """Core log logic — fully injectable for tests.

    Reads repo state from ``.muse/``, loads commits from the DB session,
    and writes formatted output via ``typer.echo``.
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

    commits = await _load_commits(session, head_commit_id=head_commit_id, limit=limit)
    if not commits:
        typer.echo(f"No commits yet on branch {branch}")
        raise typer.Exit(code=ExitCode.SUCCESS)

    if graph:
        _render_graph(commits, head_commit_id=head_commit_id)
    else:
        _render_log(commits, head_commit_id=head_commit_id, branch=branch)


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
) -> None:
    """Display the commit history for the current branch."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _log_async(root=root, session=session, limit=limit, graph=graph)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse log failed: {exc}")
        logger.error("❌ muse log error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
