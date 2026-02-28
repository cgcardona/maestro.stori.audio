"""muse ask — natural language query over Muse musical history.

Searches commit messages for keywords extracted from the user's question
and returns matching commits in a structured answer.  This is a stub
implementation: keyword matching over commit messages.  Full LLM-powered
answer generation is a planned enhancement.

Usage::

    muse ask "what tempo changes did I make last week?"
    muse ask "boom bap sessions" --branch feature/hip-hop --cite
    muse ask "piano intro" --since 2026-01-01 --until 2026-02-01 --json

``--cite`` appends the full commit ID to each matching commit entry.
``--json`` emits a machine-readable JSON response instead of plain text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
from datetime import date, datetime, timezone
from typing import Annotated, Optional

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

app = typer.Typer()

_MAX_COMMITS = 10_000


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class AnswerResult:
    """Structured result returned by ``_ask_async`` for testability."""

    def __init__(
        self,
        question: str,
        total_searched: int,
        matches: list[MuseCliCommit],
        cite: bool,
    ) -> None:
        self.question = question
        self.total_searched = total_searched
        self.matches = matches
        self.cite = cite

    def to_plain(self) -> str:
        """Format as human-readable plain text."""
        lines: list[str] = [
            f"Based on Muse history ({self.total_searched} commits searched):",
            f"Commits matching your query: {len(self.matches)} found",
        ]
        if self.matches:
            lines.append("")
            for commit in self.matches:
                ts = commit.committed_at.strftime("%Y-%m-%d %H:%M")
                if self.cite:
                    lines.append(f"  [{commit.commit_id}] {ts}  {commit.message}")
                else:
                    lines.append(f"  [{commit.commit_id[:8]}] {ts}  {commit.message}")
        else:
            lines.append("  (no matching commits)")
        lines.append("")
        lines.append(
            "Note: Full LLM-powered answer generation is a planned enhancement."
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        """Format as JSON."""
        payload: dict[str, object] = {
            "question": self.question,
            "total_searched": self.total_searched,
            "matches": [
                {
                    "commit_id": c.commit_id if self.cite else c.commit_id[:8],
                    "branch": c.branch,
                    "message": c.message,
                    "committed_at": c.committed_at.isoformat(),
                }
                for c in self.matches
            ],
            "note": "Full LLM-powered answer generation is a planned enhancement.",
        }
        return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


def _keywords(question: str) -> list[str]:
    """Extract non-trivial lowercase tokens from the question string.

    Strips punctuation and common stop-words so the keyword match focuses
    on meaningful terms from the user's question.
    """
    stop = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "i", "my", "me", "we", "our", "you", "your", "he", "she", "it",
        "they", "their", "them", "what", "when", "where", "who", "which",
        "how", "why", "in", "on", "at", "to", "of", "for", "and", "or",
        "but", "not", "with", "from", "by", "about", "into", "through",
        "did", "make", "made", "last", "any", "all", "that", "this",
    }
    tokens = re.split(r"[\s\W]+", question.lower())
    return [t for t in tokens if t and t not in stop and len(t) > 1]


async def _ask_async(
    *,
    question: str,
    root: pathlib.Path,
    session: AsyncSession,
    branch: str | None,
    since: date | None,
    until: date | None,
    cite: bool,
) -> AnswerResult:
    """Core ask logic — fully injectable for tests.

    Loads commits from the DB, applies optional filters (branch, date
    range), and performs keyword search over commit messages.  Returns
    an :class:`AnswerResult` that can be rendered as plain text or JSON.
    """
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # Determine effective branch filter: explicit flag → HEAD branch → all.
    effective_branch: str | None = branch
    if effective_branch is None:
        head_ref_text = (muse_dir / "HEAD").read_text().strip()
        effective_branch = head_ref_text.rsplit("/", 1)[-1]

    stmt = (
        select(MuseCliCommit)
        .where(MuseCliCommit.repo_id == repo_id)
        .order_by(MuseCliCommit.committed_at.desc())
        .limit(_MAX_COMMITS)
    )
    if effective_branch:
        stmt = stmt.where(MuseCliCommit.branch == effective_branch)
    if since is not None:
        since_dt = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
        stmt = stmt.where(MuseCliCommit.committed_at >= since_dt)
    if until is not None:
        # inclusive: treat until as end-of-day
        until_dt = datetime(
            until.year, until.month, until.day, 23, 59, 59, tzinfo=timezone.utc
        )
        stmt = stmt.where(MuseCliCommit.committed_at <= until_dt)

    result = await session.execute(stmt)
    all_commits: list[MuseCliCommit] = list(result.scalars().all())

    keywords = _keywords(question)
    if keywords:
        matches = [
            c for c in all_commits
            if any(kw in c.message.lower() for kw in keywords)
        ]
    else:
        # Empty query → return all commits (the question had no useful tokens).
        matches = list(all_commits)

    return AnswerResult(
        question=question,
        total_searched=len(all_commits),
        matches=matches,
        cite=cite,
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def ask(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument(help="Natural language question about your musical history.")],
    branch: Annotated[Optional[str], typer.Option("--branch", help="Restrict search to this branch name.")] = None,
    since: Annotated[Optional[datetime], typer.Option("--since", formats=["%Y-%m-%d"], help="Only include commits on or after this date (YYYY-MM-DD).")] = None,
    until: Annotated[Optional[datetime], typer.Option("--until", formats=["%Y-%m-%d"], help="Only include commits on or before this date (YYYY-MM-DD).")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
    cite: Annotated[bool, typer.Option("--cite", help="Show full commit IDs in the answer.")] = False,
) -> None:
    """Query your Muse musical history in natural language."""
    root = require_repo()

    since_date: date | None = since.date() if since is not None else None
    until_date: date | None = until.date() if until is not None else None

    async def _run() -> None:
        async with open_session() as session:
            result = await _ask_async(
                question=question,
                root=root,
                session=session,
                branch=branch,
                since=since_date,
                until=until_date,
                cite=cite,
            )
            if output_json:
                typer.echo(result.to_json())
            else:
                typer.echo(result.to_plain())

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse ask failed: {exc}")
        logger.error("❌ muse ask error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
