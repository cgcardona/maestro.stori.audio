"""Credits aggregation service for Muse Hub repos.

Aggregates contributor information from commit history — think dynamic album
liner notes that update as the composition evolves.  Every pushed commit
contributes an author name, a timestamp, and a message whose keywords are
used to infer contribution types (composer, arranger, producer, etc.).

Design decisions:
- Pure DB read — no mutations, no side effects.
- Contribution types are inferred from commit message keywords, not stored
  explicitly, so they evolve as musicians describe their work more richly.
- Sort options mirror what a label credit page would offer: by contribution
  count (most prolific first), by recency (most recently active first), and
  alphabetical (predictable scanning order).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import ContributorCredits, CreditsResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role inference keyword map
# Keys are contribution-type labels; values are substrings to search for in
# the lower-cased commit message.  Order matters: first match wins per token.
# ---------------------------------------------------------------------------

_ROLE_KEYWORDS: dict[str, list[str]] = {
    "composer": ["compos", "wrote", "writing", "melody", "theme", "motif"],
    "arranger": ["arrang", "orchestrat", "voicing", "reharmoni"],
    "producer": ["produc", "session", "master", "mix session", "track layout"],
    "performer": ["perform", "record", "played", "guitar", "piano", "bass", "drum"],
    "mixer": ["mix", "blend", "balance", "eq ", "equaliz", "compressor"],
    "editor": ["edit", "cut", "splice", "trim", "clip"],
    "lyricist": ["lyric", "word", "verse", "chorus", "hook", "lyric"],
    "sound designer": ["synth", "sound design", "patch", "preset", "timbre"],
}


def _infer_roles(message: str) -> list[str]:
    """Return contribution type labels detected from a commit message.

    Uses a simple keyword scan — sufficient for MVP.  If no keywords match,
    falls back to ``["contributor"]`` so every commit always carries a role.
    """
    lower = message.lower()
    found: list[str] = []
    for role, keywords in _ROLE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            found.append(role)
    return found if found else ["contributor"]


def _sort_contributors(
    contributors: list[ContributorCredits], sort: str
) -> list[ContributorCredits]:
    """Apply the requested sort order to the contributor list.

    Supported values:
    - ``"count"``      — most prolific contributor first (default)
    - ``"recency"``    — most recently active contributor first
    - ``"alpha"``      — alphabetical by author name
    """
    if sort == "recency":
        return sorted(contributors, key=lambda c: c.last_active, reverse=True)
    if sort == "alpha":
        return sorted(contributors, key=lambda c: c.author.lower())
    # Default: sort by session count descending, then alpha for ties
    return sorted(contributors, key=lambda c: (-c.session_count, c.author.lower()))


async def aggregate_credits(
    session: AsyncSession,
    repo_id: str,
    *,
    sort: str = "count",
) -> CreditsResponse:
    """Aggregate contributors across all commits in a repo.

    Reads every commit for the repo (no limit — credits need completeness,
    not pagination).  Groups by author string, counts sessions, infers roles
    from commit messages, and records activity timestamps.

    Args:
        session: Active async DB session.
        repo_id: Target repo ID.
        sort: Sort order for the contributor list — ``"count"`` (default),
              ``"recency"``, or ``"alpha"``.

    Returns:
        ``CreditsResponse`` with a complete contributor list and echoed sort.
    """
    stmt = (
        select(db.MusehubCommit)
        .where(db.MusehubCommit.repo_id == repo_id)
        .order_by(db.MusehubCommit.timestamp)
    )
    rows = (await session.execute(stmt)).scalars().all()

    # Per-author accumulators
    counts: dict[str, int] = defaultdict(int)
    roles_sets: dict[str, set[str]] = defaultdict(set)
    first_active: dict[str, datetime] = {}
    last_active: dict[str, datetime] = {}

    for row in rows:
        author = row.author
        counts[author] += 1
        for role in _infer_roles(row.message):
            roles_sets[author].add(role)
        ts = row.timestamp
        if author not in first_active or ts < first_active[author]:
            first_active[author] = ts
        if author not in last_active or ts > last_active[author]:
            last_active[author] = ts

    contributors = [
        ContributorCredits(
            author=author,
            session_count=counts[author],
            contribution_types=sorted(roles_sets[author]),
            first_active=first_active[author],
            last_active=last_active[author],
        )
        for author in counts
    ]

    sorted_contributors = _sort_contributors(contributors, sort)
    logger.debug(
        "✅ Credits aggregated for repo %s: %d contributor(s), sort=%s",
        repo_id,
        len(sorted_contributors),
        sort,
    )
    return CreditsResponse(
        repo_id=repo_id,
        contributors=sorted_contributors,
        sort=sort,
        total_contributors=len(sorted_contributors),
    )
