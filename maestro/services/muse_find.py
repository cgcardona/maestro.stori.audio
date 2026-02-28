"""Muse Find — search commit history by musical properties.

This is the musical equivalent of ``git log --grep``, extended with
domain-specific filters for harmony, rhythm, melody, structure, dynamics,
and emotion.  All filters combine with AND logic: a commit must satisfy
every non-None criterion to appear in results.

Query DSL
---------
Each property filter accepts a free-text query string matched
case-insensitively against the commit message.  Two syntaxes:

**Equality match** (default)::

    --harmony "key=Eb"   → substring match for "key=Eb" in message

**Numeric range** (``key=low-high``)::

    --rhythm "tempo=120-130"  → extract tempo=<N> from message,
                                 check 120 <= N <= 130

Range syntax is triggered when the value portion of ``key=value``
contains exactly one hyphen separating two non-negative numbers.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class MuseFindQuery:
    """All search criteria for a ``muse find`` invocation.

    Every field is optional.  Non-None fields are ANDed together.
    ``limit`` caps the result set (default 20).
    """

    harmony: str | None = None
    rhythm: str | None = None
    melody: str | None = None
    structure: str | None = None
    dynamic: str | None = None
    emotion: str | None = None
    section: str | None = None
    track: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = _DEFAULT_LIMIT


@dataclass(frozen=True)
class MuseFindCommitResult:
    """A single commit that matched the search criteria."""

    commit_id: str
    branch: str
    message: str
    author: str
    committed_at: datetime
    parent_commit_id: str | None
    snapshot_id: str


@dataclass(frozen=True)
class MuseFindResults:
    """Container returned by :func:`search_commits`.

    ``matches`` is newest-first, capped at ``query.limit``.
    ``total_scanned`` is the number of DB rows examined before limit was applied.
    """

    matches: tuple[MuseFindCommitResult, ...]
    total_scanned: int
    query: MuseFindQuery


_RANGE_RE = re.compile(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$")
_KEY_VALUE_RE = re.compile(r"^([^=]+)=(.+)$")


def _parse_property_filter(query_str: str) -> tuple[str, float, float] | None:
    """Parse ``key=low-high`` range syntax.

    Returns ``(key, low, high)`` when matched, or ``None`` for plain text.

    Examples::

        "tempo=120-130"  -> ("tempo", 120.0, 130.0)
        "key=Eb"         -> None
    """
    m = _KEY_VALUE_RE.match(query_str)
    if m is None:
        return None
    key = m.group(1).strip()
    value = m.group(2).strip()
    rm = _RANGE_RE.match(value)
    if rm is None:
        return None
    return (key, float(rm.group(1)), float(rm.group(2)))


def _extract_numeric_value(message: str, key: str) -> float | None:
    """Extract the numeric value for *key* from a commit message.

    Matches patterns like ``key=<number>`` and returns the first as float.

    Examples::

        "tempo=125 bpm" -> key="tempo" -> 125.0
        "swing=0.72"    -> key="swing" -> 0.72
    """
    pattern = re.compile(
        r"\b" + re.escape(key) + r"\s*=\s*(\d+(?:\.\d+)?)\b",
        re.IGNORECASE,
    )
    m = pattern.search(message)
    if m is None:
        return None
    return float(m.group(1))


def _matches_property(message: str, query_str: str) -> bool:
    """Return True when *message* satisfies *query_str*.

    Handles both plain text (case-insensitive substring) and range matching.
    """
    parsed = _parse_property_filter(query_str)
    if parsed is not None:
        key, low, high = parsed
        value = _extract_numeric_value(message, key)
        if value is None:
            return False
        return low <= value <= high
    return query_str.lower() in message.lower()


async def search_commits(
    session: AsyncSession,
    repo_id: str,
    query: MuseFindQuery,
) -> MuseFindResults:
    """Search commit history for commits matching all criteria in *query*.

    Strategy:
    1. Build a SQL query applying date range and plain text filters at DB layer.
    2. Load candidate rows ordered newest-first.
    3. Apply Python-level range filtering for numeric range expressions.
    4. Collect up to ``query.limit`` results.

    This function is read-only.

    Args:
        session: Async SQLAlchemy session.
        repo_id: Repository to scope the search to.
        query: Search criteria.

    Returns:
        :class:`MuseFindResults` with matching commits and diagnostics.
    """
    stmt = select(MuseCliCommit).where(MuseCliCommit.repo_id == repo_id)

    date_conditions = []
    if query.since is not None:
        date_conditions.append(MuseCliCommit.committed_at >= query.since)
    if query.until is not None:
        date_conditions.append(MuseCliCommit.committed_at <= query.until)
    if date_conditions:
        stmt = stmt.where(and_(*date_conditions))

    # Push plain-text (non-range) filters to SQL for efficiency.
    # Range queries require Python-level numeric extraction (applied below).
    all_terms: list[str | None] = [
        query.harmony,
        query.rhythm,
        query.melody,
        query.structure,
        query.dynamic,
        query.emotion,
        query.section,
        query.track,
    ]
    for term in all_terms:
        if term is not None and _parse_property_filter(term) is None:
            stmt = stmt.where(MuseCliCommit.message.ilike(f"%{term}%"))

    stmt = stmt.order_by(MuseCliCommit.committed_at.desc())

    result = await session.execute(stmt)
    rows: list[MuseCliCommit] = list(result.scalars().all())
    total_scanned = len(rows)

    # Python-level range filtering for numeric range expressions.
    range_filters: list[str] = [
        term
        for term in all_terms
        if term is not None and _parse_property_filter(term) is not None
    ]

    matches: list[MuseFindCommitResult] = []
    for row in rows:
        if len(matches) >= query.limit:
            break
        if all(_matches_property(row.message, f) for f in range_filters):
            matches.append(
                MuseFindCommitResult(
                    commit_id=row.commit_id,
                    branch=row.branch,
                    message=row.message,
                    author=row.author,
                    committed_at=row.committed_at,
                    parent_commit_id=row.parent_commit_id,
                    snapshot_id=row.snapshot_id,
                )
            )

    logger.info(
        "✅ muse find: %d match(es) from %d scanned (repo=%s)",
        len(matches),
        total_scanned,
        repo_id[:8],
    )
    return MuseFindResults(
        matches=tuple(matches),
        total_scanned=total_scanned,
        query=query,
    )
