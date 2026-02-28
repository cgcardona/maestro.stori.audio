"""muse recall — keyword search over musical commit history.

Accepts a natural-language query string and returns the top-N commits from
history ranked by keyword overlap against commit messages.

Usage::

    muse recall "dark jazz bassline"
    muse recall "drum fill" --limit 3 --threshold 0.5 --branch main
    muse recall "piano" --since 2026-01-01 --until 2026-02-01 --json

Scoring algorithm (stub — vector search planned):
    Each commit message is tokenized (lowercase, split on whitespace/punctuation).
    The score is the normalised overlap coefficient between the query tokens and
    the message tokens:

        score = |query_tokens ∩ message_tokens| / |query_tokens|

    This gives 1.0 when every query word appears in the message, and 0.0 when
    none do.  Commits with score < ``--threshold`` are excluded.

Note:
    Full vector embedding search via Qdrant is a planned enhancement (see muse
    context / issue backlog).  When implemented, the scoring function will be
    replaced by cosine similarity over pre-computed embeddings, with no change
    to the CLI interface.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
from datetime import datetime, timezone
from typing import Annotated, TypedDict

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

app = typer.Typer()

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


class RecallResult(TypedDict):
    """A single ranked recall result entry."""

    rank: int
    score: float
    commit_id: str
    date: str
    branch: str
    message: str


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Return a set of lowercase word tokens from *text*."""
    return {m.group().lower() for m in _TOKEN_RE.finditer(text)}


def _score(query_tokens: set[str], message: str) -> float:
    """Return a [0, 1] keyword overlap score.

    Uses the overlap coefficient: |Q ∩ M| / |Q| so that a short, precise
    query can match a verbose commit message without penalty.

    Returns 0.0 when *query_tokens* is empty (avoids division by zero).
    """
    if not query_tokens:
        return 0.0
    message_tokens = _tokenize(message)
    return len(query_tokens & message_tokens) / len(query_tokens)


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _fetch_commits(
    session: AsyncSession,
    *,
    repo_id: str,
    branch: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list[MuseCliCommit]:
    """Fetch all candidate commits from the DB, optionally filtered.

    Filters are applied at the SQL level to minimise in-memory work.  The
    caller ranks and limits the result set.
    """
    stmt = select(MuseCliCommit).where(MuseCliCommit.repo_id == repo_id)

    if branch is not None:
        stmt = stmt.where(MuseCliCommit.branch == branch)
    if since is not None:
        stmt = stmt.where(MuseCliCommit.committed_at >= since)
    if until is not None:
        stmt = stmt.where(MuseCliCommit.committed_at <= until)

    stmt = stmt.order_by(MuseCliCommit.committed_at.desc())

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _recall_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    query: str,
    limit: int,
    threshold: float,
    branch: str | None,
    since: datetime | None,
    until: datetime | None,
    as_json: bool,
) -> list[RecallResult]:
    """Core recall logic — fully injectable for tests.

    Returns the list of ranked result dicts (also echoed to stdout).
    """
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # When no branch filter is given, default to current branch from HEAD.
    effective_branch: str | None = branch
    if effective_branch is None:
        head_ref = (muse_dir / "HEAD").read_text().strip()
        effective_branch = head_ref.rsplit("/", 1)[-1]

    commits = await _fetch_commits(
        session,
        repo_id=repo_id,
        branch=effective_branch,
        since=since,
        until=until,
    )

    query_tokens = _tokenize(query)
    scored: list[tuple[float, MuseCliCommit]] = []
    for commit in commits:
        score = _score(query_tokens, commit.message)
        if score >= threshold:
            scored.append((score, commit))

    # Sort by score descending, then by recency (committed_at desc) for ties.
    scored.sort(key=lambda x: (x[0], x[1].committed_at.timestamp()), reverse=True)
    top = scored[:limit]

    results: list[RecallResult] = [
        RecallResult(
            rank=i + 1,
            score=round(score, 4),
            commit_id=commit.commit_id,
            date=commit.committed_at.strftime("%Y-%m-%d %H:%M:%S"),
            branch=commit.branch,
            message=commit.message,
        )
        for i, (score, commit) in enumerate(top)
    ]

    if as_json:
        typer.echo(json.dumps(results, indent=2))
    else:
        _render_results(query=query, results=results, threshold=threshold)

    return results


def _render_results(
    *,
    query: str,
    results: list[RecallResult],
    threshold: float,
) -> None:
    """Print ranked recall results in human-readable format.

    Note: similarity scores are keyword-overlap estimates, not semantic
    embeddings.  Vector search via Qdrant is a planned enhancement.
    """
    typer.echo(f'Recall: "{query}"')
    typer.echo(f"(keyword match · threshold {threshold:.2f} · "
               "vector search is a planned enhancement)")
    typer.echo("")

    if not results:
        typer.echo("  No matching commits found.")
        return

    for entry in results:
        typer.echo(
            f"  #{entry['rank']}  score={entry['score']:.4f}  "
            f"commit {entry['commit_id']}  [{entry['date']}]"
        )
        typer.echo(f"       {entry['message']}")
        typer.echo("")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def recall(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Natural-language description to search for.")],
    limit: int = typer.Option(
        5,
        "--limit",
        "-n",
        help="Maximum number of results to return.",
        min=1,
    ),
    threshold: float = typer.Option(
        0.6,
        "--threshold",
        help="Minimum similarity score (0–1) to include a commit.",
        min=0.0,
        max=1.0,
    ),
    branch: str | None = typer.Option(
        None,
        "--branch",
        help="Filter by branch name (defaults to current branch).",
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        help="Only include commits on or after this date (YYYY-MM-DD).",
    ),
    until: str | None = typer.Option(
        None,
        "--until",
        help="Only include commits on or before this date (YYYY-MM-DD).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output results as JSON.",
    ),
) -> None:
    """Search commit history by description (keyword match over messages).

    Returns the top ``--limit`` commits whose messages best match the query,
    sorted by keyword overlap score.  Commits below ``--threshold`` are
    excluded.

    Note:
        Full semantic vector search via Qdrant is a planned enhancement
        (see muse context).  Until then, scoring is based on keyword overlap
        between the query and commit messages.
    """
    # Validate date args first — fail fast before touching the filesystem.
    since_dt: datetime | None = None
    until_dt: datetime | None = None

    if since:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            typer.echo(f"❌ --since: invalid date format '{since}' — expected YYYY-MM-DD")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    if until:
        try:
            until_dt = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            typer.echo(f"❌ --until: invalid date format '{until}' — expected YYYY-MM-DD")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _recall_async(
                root=root,
                session=session,
                query=query,
                limit=limit,
                threshold=threshold,
                branch=branch,
                since=since_dt,
                until=until_dt,
                as_json=as_json,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse recall failed: {exc}")
        logger.error("❌ muse recall error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
