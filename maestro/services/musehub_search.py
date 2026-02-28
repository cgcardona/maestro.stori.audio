"""Muse Hub in-repo search service.

Provides four search modes over a repo's commit history, all operating on the
shared ``muse_cli_commits`` table and scoped to a single ``repo_id``.

Modes and their underlying algorithms:
- ``property``  — musical property filter (delegates to :mod:`maestro.services.muse_find`)
- ``ask``       — natural-language query; keyword extraction + overlap scoring
- ``keyword``   — raw keyword/phrase overlap (normalised overlap coefficient)
- ``pattern``   — substring pattern match against message and branch name

All four modes return :class:`~maestro.models.musehub.SearchResponse` so the
UI can render results with a single shared commit-row template regardless of mode.

Date-range filtering (``since`` / ``until``) is applied at the SQL layer for
efficiency before any Python-level scoring.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.models.musehub import SearchCommitMatch, SearchResponse
from maestro.muse_cli.models import MuseCliCommit
from maestro.services import muse_find

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")

# Stop-words stripped during NL ask-mode keyword extraction.
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "i", "my", "me", "we", "our", "you", "your", "he", "she", "it",
    "they", "their", "them", "what", "when", "where", "who", "which",
    "how", "why", "in", "on", "at", "to", "of", "for", "and", "or",
    "but", "not", "with", "from", "by", "about", "into", "through",
    "did", "make", "made", "last", "any", "all", "that", "this",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Return a set of lowercase word tokens from *text*."""
    return {m.group().lower() for m in _TOKEN_RE.finditer(text)}


def _overlap_score(query_tokens: set[str], message: str) -> float:
    """Normalised overlap coefficient: |Q ∩ M| / |Q|.

    Returns 1.0 when every query token appears in the message, 0.0 when
    none do.  Returns 0.0 for an empty query set to avoid division by zero.
    """
    if not query_tokens:
        return 0.0
    message_tokens = _tokenize(message)
    return len(query_tokens & message_tokens) / len(query_tokens)


async def _fetch_candidates(
    session: AsyncSession,
    *,
    repo_id: str,
    since: datetime | None,
    until: datetime | None,
    cap: int = 5000,
) -> tuple[list[MuseCliCommit], int]:
    """Fetch candidate commits from DB with optional date range filter.

    Returns ``(rows, total_scanned)`` where ``total_scanned`` is the raw DB count
    before any Python-level filtering.  We over-fetch (up to ``cap``) and let
    callers apply their own ranking/limit so the SQL stays simple and fast.
    """
    stmt = select(MuseCliCommit).where(MuseCliCommit.repo_id == repo_id)

    conditions = []
    if since is not None:
        conditions.append(MuseCliCommit.committed_at >= since)
    if until is not None:
        conditions.append(MuseCliCommit.committed_at <= until)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    stmt = stmt.order_by(MuseCliCommit.committed_at.desc()).limit(cap)

    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return rows, len(rows)


def _commit_to_match(
    commit: MuseCliCommit,
    *,
    score: float = 1.0,
    match_source: str = "message",
) -> SearchCommitMatch:
    """Convert a DB row to the wire-format :class:`SearchCommitMatch`."""
    return SearchCommitMatch(
        commit_id=commit.commit_id,
        branch=commit.branch,
        message=commit.message,
        author=commit.author,
        timestamp=commit.committed_at,
        score=round(score, 4),
        match_source=match_source,
    )


# ---------------------------------------------------------------------------
# Search modes
# ---------------------------------------------------------------------------


async def search_by_property(
    session: AsyncSession,
    *,
    repo_id: str,
    harmony: str | None = None,
    rhythm: str | None = None,
    melody: str | None = None,
    structure: str | None = None,
    dynamic: str | None = None,
    emotion: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> SearchResponse:
    """Musical property filter — delegates to :func:`muse_find.search_commits`.

    Each non-None property is ANDed together; accepts both plain-text and
    ``key=low-high`` range expressions (e.g. ``tempo=120-130``).

    Args:
        session: Async SQLAlchemy session.
        repo_id: Repo to search.
        harmony: Harmony property filter string.
        rhythm: Rhythm property filter string.
        melody: Melody property filter string.
        structure: Structure property filter string.
        dynamic: Dynamics property filter string.
        emotion: Emotion property filter string.
        since: Earliest committed_at to include (inclusive).
        until: Latest committed_at to include (inclusive).
        limit: Maximum results to return.

    Returns:
        :class:`~maestro.models.musehub.SearchResponse` with mode="property".
    """
    query = muse_find.MuseFindQuery(
        harmony=harmony,
        rhythm=rhythm,
        melody=melody,
        structure=structure,
        dynamic=dynamic,
        emotion=emotion,
        since=since,
        until=until,
        limit=limit,
    )
    results = await muse_find.search_commits(session, repo_id, query)

    # Build a human-readable summary of the active filters for the query echo.
    active_filters = {
        k: v for k, v in {
            "harmony": harmony,
            "rhythm": rhythm,
            "melody": melody,
            "structure": structure,
            "dynamic": dynamic,
            "emotion": emotion,
        }.items() if v is not None
    }
    query_echo = " AND ".join(f"{k}={v}" for k, v in active_filters.items()) or "(all commits)"

    matches = [
        SearchCommitMatch(
            commit_id=m.commit_id,
            branch=m.branch,
            message=m.message,
            author=m.author,
            timestamp=m.committed_at,
            score=1.0,
            match_source="property",
        )
        for m in results.matches
    ]

    logger.info("✅ musehub search property: %d matches (repo=%s)", len(matches), repo_id[:8])
    return SearchResponse(
        mode="property",
        query=query_echo,
        matches=matches,
        total_scanned=results.total_scanned,
        limit=limit,
    )


async def search_by_ask(
    session: AsyncSession,
    *,
    repo_id: str,
    question: str,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> SearchResponse:
    """Natural-language query — keyword extraction + overlap scoring.

    Strips stop-words from the question to produce a focused keyword set,
    then ranks commits by overlap coefficient.  Commits with zero overlap
    are excluded.  Returns at most ``limit`` results ordered by score desc.

    This is a stub implementation; LLM-powered answer generation is a planned
    enhancement that will replace the keyword scoring step.

    Args:
        session: Async SQLAlchemy session.
        repo_id: Repo to search.
        question: Natural-language question string.
        since: Earliest committed_at (inclusive).
        until: Latest committed_at (inclusive).
        limit: Maximum results to return.

    Returns:
        :class:`~maestro.models.musehub.SearchResponse` with mode="ask".
    """
    rows, total_scanned = await _fetch_candidates(
        session, repo_id=repo_id, since=since, until=until
    )

    # Extract meaningful keywords after stop-word removal.
    tokens_raw = re.split(r"[\s\W]+", question.lower())
    keywords: set[str] = {t for t in tokens_raw if t and t not in _STOP_WORDS and len(t) > 1}

    scored: list[tuple[float, MuseCliCommit]] = []
    for commit in rows:
        if keywords:
            score = _overlap_score(keywords, commit.message)
        else:
            # No useful tokens → include all commits with neutral score.
            score = 1.0
        if score > 0.0:
            scored.append((score, commit))

    scored.sort(key=lambda x: (x[0], x[1].committed_at.timestamp()), reverse=True)
    top = scored[:limit]

    matches = [_commit_to_match(c, score=s, match_source="message") for s, c in top]

    logger.info("✅ musehub search ask: %d matches (repo=%s)", len(matches), repo_id[:8])
    return SearchResponse(
        mode="ask",
        query=question,
        matches=matches,
        total_scanned=total_scanned,
        limit=limit,
    )


async def search_by_keyword(
    session: AsyncSession,
    *,
    repo_id: str,
    keyword: str,
    threshold: float = 0.0,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> SearchResponse:
    """Keyword search — overlap coefficient over commit messages.

    Tokenises both *keyword* and each commit message, then scores using the
    overlap coefficient.  Commits below *threshold* are excluded.

    Args:
        session: Async SQLAlchemy session.
        repo_id: Repo to search.
        keyword: Keyword or phrase to search for.
        threshold: Minimum overlap score [0, 1] to include a commit (default 0 = any match).
        since: Earliest committed_at (inclusive).
        until: Latest committed_at (inclusive).
        limit: Maximum results to return.

    Returns:
        :class:`~maestro.models.musehub.SearchResponse` with mode="keyword".
    """
    rows, total_scanned = await _fetch_candidates(
        session, repo_id=repo_id, since=since, until=until
    )

    query_tokens = _tokenize(keyword)
    scored: list[tuple[float, MuseCliCommit]] = []
    for commit in rows:
        score = _overlap_score(query_tokens, commit.message)
        if score >= threshold and score > 0.0:
            scored.append((score, commit))

    scored.sort(key=lambda x: (x[0], x[1].committed_at.timestamp()), reverse=True)
    top = scored[:limit]

    matches = [_commit_to_match(c, score=s, match_source="message") for s, c in top]

    logger.info("✅ musehub search keyword: %d matches (repo=%s)", len(matches), repo_id[:8])
    return SearchResponse(
        mode="keyword",
        query=keyword,
        matches=matches,
        total_scanned=total_scanned,
        limit=limit,
    )


async def search_by_pattern(
    session: AsyncSession,
    *,
    repo_id: str,
    pattern: str,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> SearchResponse:
    """Pattern search — case-insensitive substring match against message and branch.

    Matches commits where *pattern* appears anywhere in the commit message or
    the branch name.  Prioritises message matches over branch-name matches in
    the result ordering.

    Args:
        session: Async SQLAlchemy session.
        repo_id: Repo to search.
        pattern: Substring pattern to search for.
        since: Earliest committed_at (inclusive).
        until: Latest committed_at (inclusive).
        limit: Maximum results to return.

    Returns:
        :class:`~maestro.models.musehub.SearchResponse` with mode="pattern".
    """
    rows, total_scanned = await _fetch_candidates(
        session, repo_id=repo_id, since=since, until=until
    )

    pat = pattern.lower()
    message_matches: list[SearchCommitMatch] = []
    branch_matches: list[SearchCommitMatch] = []

    for commit in rows:
        if pat in commit.message.lower():
            message_matches.append(_commit_to_match(commit, match_source="message"))
        elif pat in commit.branch.lower():
            branch_matches.append(_commit_to_match(commit, match_source="branch"))

    # Message matches come first, then branch matches.
    all_matches = (message_matches + branch_matches)[:limit]

    logger.info("✅ musehub search pattern: %d matches (repo=%s)", len(all_matches), repo_id[:8])
    return SearchResponse(
        mode="pattern",
        query=pattern,
        matches=all_matches,
        total_scanned=total_scanned,
        limit=limit,
    )
