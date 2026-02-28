"""Muse Hub agent context aggregation service.

This is the canonical read-path for AI composition agents.  ``build_agent_context``
aggregates musical state, commit history, analysis highlights, open PRs, and open
issues for a given repo ref into a single ``AgentContextResponse``.

Design notes
------------
- **Read-only**: this service never writes to the DB.
- **Deterministic**: for the same repo_id + resolved ref, the output is always
  identical, making it safe to cache.
- **Depth-aware**: ``brief`` returns minimal data for tight context windows;
  ``standard`` returns a full briefing; ``verbose`` adds all bodies/history.
- **Analysis stubs**: per-dimension analysis (key, groove, harmony) is currently
  None — these require Storpheus MIDI integration. The schema is fully defined so
  agents can handle None gracefully today and receive populated values once that
  integration lands.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db_models
from maestro.models.musehub_context import (
    ActivePRContext,
    AgentContextResponse,
    AnalysisSummaryContext,
    ContextDepth,
    HistoryEntryContext,
    MusicalStateContext,
    OpenIssueContext,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Depth configuration
# ---------------------------------------------------------------------------

_HISTORY_LIMIT: dict[str, int] = {
    ContextDepth.brief: 3,
    ContextDepth.standard: 10,
    ContextDepth.verbose: 50,
}

_INCLUDE_PR_BODY: dict[str, bool] = {
    ContextDepth.brief: False,
    ContextDepth.standard: True,
    ContextDepth.verbose: True,
}

_INCLUDE_ISSUE_BODY: dict[str, bool] = {
    ContextDepth.brief: False,
    ContextDepth.standard: False,
    ContextDepth.verbose: True,
}

_MUSIC_FILE_EXTENSIONS = frozenset(
    {".mid", ".midi", ".mp3", ".wav", ".aiff", ".aif", ".flac"}
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_iso(dt: datetime) -> str:
    """Return a UTC ISO-8601 string from a datetime (naive or aware)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _extract_tracks_from_snapshot(snapshot: db_models.MusehubObject | None) -> list[str]:
    """Not applicable in MuseHub context — snapshots are binary objects.

    Track names in the MuseHub context come from commit message heuristics and
    branch-level metadata.  This stub returns an empty list until commit-level
    manifest tracking is added to MusehubCommit.
    """
    return []


async def _resolve_ref_to_commit(
    session: AsyncSession,
    repo_id: str,
    ref: str,
) -> db_models.MusehubCommit | None:
    """Resolve a ref (branch name or commit ID) to a MusehubCommit row.

    Resolution order:
    1. If ``ref`` matches a branch name → return its head commit.
    2. If ``ref`` matches a commit ID (exact) → return that commit.
    3. Return None if neither matches.
    """
    # Try branch lookup first (most common case)
    branch_stmt = select(db_models.MusehubBranch).where(
        db_models.MusehubBranch.repo_id == repo_id,
        db_models.MusehubBranch.name == ref,
    )
    branch = (await session.execute(branch_stmt)).scalars().first()
    if branch is not None and branch.head_commit_id is not None:
        commit = await session.get(db_models.MusehubCommit, branch.head_commit_id)
        return commit

    # Fall back to direct commit ID lookup
    commit_stmt = select(db_models.MusehubCommit).where(
        db_models.MusehubCommit.repo_id == repo_id,
        db_models.MusehubCommit.commit_id == ref,
    )
    return (await session.execute(commit_stmt)).scalars().first()


async def _get_latest_commit(
    session: AsyncSession,
    repo_id: str,
) -> db_models.MusehubCommit | None:
    """Return the most-recent commit for any branch in the repo, or None."""
    stmt = (
        select(db_models.MusehubCommit)
        .where(db_models.MusehubCommit.repo_id == repo_id)
        .order_by(desc(db_models.MusehubCommit.timestamp))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _build_history(
    session: AsyncSession,
    repo_id: str,
    head_commit: db_models.MusehubCommit,
    limit: int,
) -> list[HistoryEntryContext]:
    """Return up to *limit* recent commits for the repo (newest-first).

    The head commit itself is excluded — it is surfaced as the current ref.
    We query by repo and timestamp rather than walking parent links, because
    MusehubCommit parent_ids are a JSONB list and graph traversal would
    require N+1 queries.  Timestamp ordering is an approximation; in practice
    it matches the commit graph order for sequential workflows.
    """
    stmt = (
        select(db_models.MusehubCommit)
        .where(
            db_models.MusehubCommit.repo_id == repo_id,
            db_models.MusehubCommit.commit_id != head_commit.commit_id,
        )
        .order_by(desc(db_models.MusehubCommit.timestamp))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        HistoryEntryContext(
            commit_id=row.commit_id,
            message=row.message,
            author=row.author,
            timestamp=_utc_iso(row.timestamp),
            active_tracks=[],
        )
        for row in rows
    ]


async def _get_open_prs(
    session: AsyncSession,
    repo_id: str,
    include_body: bool,
) -> list[ActivePRContext]:
    """Return all open pull requests for the repo."""
    stmt = (
        select(db_models.MusehubPullRequest)
        .where(
            db_models.MusehubPullRequest.repo_id == repo_id,
            db_models.MusehubPullRequest.state == "open",
        )
        .order_by(db_models.MusehubPullRequest.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        ActivePRContext(
            pr_id=row.pr_id,
            title=row.title,
            from_branch=row.from_branch,
            to_branch=row.to_branch,
            state=row.state,
            body=row.body if include_body else "",
        )
        for row in rows
    ]


async def _get_open_issues(
    session: AsyncSession,
    repo_id: str,
    include_body: bool,
) -> list[OpenIssueContext]:
    """Return all open issues for the repo, ordered by number."""
    stmt = (
        select(db_models.MusehubIssue)
        .where(
            db_models.MusehubIssue.repo_id == repo_id,
            db_models.MusehubIssue.state == "open",
        )
        .order_by(db_models.MusehubIssue.number)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        OpenIssueContext(
            issue_id=row.issue_id,
            number=row.number,
            title=row.title,
            labels=list(row.labels or []),
            body=row.body if include_body else "",
        )
        for row in rows
    ]


def _generate_suggestions(
    musical_state: MusicalStateContext,
    open_issues: list[OpenIssueContext],
    active_prs: list[ActivePRContext],
    depth: ContextDepth,
) -> list[str]:
    """Generate heuristic composition suggestions based on current context.

    This is a deterministic, rule-based function until LLM-powered suggestions
    are integrated. Suggestions are derived from:
    - Missing musical dimensions (no tempo, no key, etc.)
    - Open issues that describe compositional problems
    - Open PRs that are waiting for review

    At ``brief`` depth, only 1–2 suggestions are returned.
    """
    suggestions: list[str] = []

    if musical_state.tempo_bpm is None:
        suggestions.append(
            "Set a project tempo: no BPM detected. Run `muse tempo set <bpm>` to anchor the grid."
        )
    if musical_state.key is None:
        suggestions.append(
            "Declare a key center: no key detected. Run `muse key set <key>` to enable harmonic analysis."
        )
    if not musical_state.active_tracks:
        suggestions.append(
            "Add tracks: no audio or MIDI files found in the latest commit. Push a commit with track files."
        )
    if open_issues:
        issue = open_issues[0]
        suggestions.append(
            f"Address open issue #{issue.number}: '{issue.title}'. "
            "This may describe a compositional problem to fix before the next section."
        )
    if active_prs:
        pr = active_prs[0]
        suggestions.append(
            f"Review PR '{pr.title}' ({pr.from_branch} → {pr.to_branch}). "
            "Merge or close it before branching for the next section."
        )

    if depth == ContextDepth.brief:
        return suggestions[:2]
    if depth == ContextDepth.standard:
        return suggestions[:4]
    return suggestions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_agent_context(
    session: AsyncSession,
    *,
    repo_id: str,
    ref: str = "HEAD",
    depth: ContextDepth = ContextDepth.standard,
) -> AgentContextResponse | None:
    """Build a complete agent context document for a MuseHub repo at a given ref.

    Returns None if the repo does not exist or has no commits.

    Args:
        session:  Open async DB session. Read-only — no writes performed.
        repo_id:  The MuseHub repo UUID.
        ref:      Branch name or commit ID. Defaults to HEAD (latest commit).
        depth:    Controls how much data is returned:
                  - ``brief``    — minimal context (~2 K tokens)
                  - ``standard`` — full briefing (~8 K tokens)
                  - ``verbose``  — uncapped (all history, full bodies)

    Returns:
        ``AgentContextResponse`` if repo + ref are valid, ``None`` if repo
        is not found or has no commits. The caller should surface None as HTTP 404.
    """
    repo = await session.get(db_models.MusehubRepo, repo_id)
    if repo is None:
        return None

    # Resolve ref → commit
    if ref == "HEAD":
        head_commit = await _get_latest_commit(session, repo_id)
    else:
        head_commit = await _resolve_ref_to_commit(session, repo_id, ref)

    if head_commit is None:
        logger.warning("⚠️ No commit found for repo %s ref %s", repo_id, ref)
        return None

    resolved_ref = ref if ref != "HEAD" else head_commit.branch

    history_limit = _HISTORY_LIMIT[depth]
    include_pr_body = _INCLUDE_PR_BODY[depth]
    include_issue_body = _INCLUDE_ISSUE_BODY[depth]

    # Gather all sections concurrently would require asyncio.gather; we keep
    # sequential awaits for readability — this is a read-heavy, low-latency path.
    history = await _build_history(session, repo_id, head_commit, history_limit)
    active_prs = await _get_open_prs(session, repo_id, include_pr_body)
    open_issues = await _get_open_issues(session, repo_id, include_issue_body)

    musical_state = MusicalStateContext(active_tracks=[])

    analysis = AnalysisSummaryContext()

    suggestions = _generate_suggestions(musical_state, open_issues, active_prs, depth)

    logger.info(
        "✅ Agent context built for repo %s ref %s (depth=%s, history=%d, prs=%d, issues=%d)",
        repo_id,
        resolved_ref,
        depth,
        len(history),
        len(active_prs),
        len(open_issues),
    )

    return AgentContextResponse(
        repo_id=repo_id,
        ref=resolved_ref,
        depth=depth,
        musical_state=musical_state,
        history=history,
        analysis=analysis,
        active_prs=active_prs,
        open_issues=open_issues,
        suggestions=suggestions,
    )
