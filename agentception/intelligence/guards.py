"""Stale-claim detection and related pipeline guard functions.

A "stale claim" is an issue that carries the ``agent:wip`` label but has no
corresponding worktree on the local filesystem.  This indicates that an agent
was killed or crashed before removing its label, leaving the issue permanently
locked out of the scheduling pool.

Usage::

    from agentception.intelligence.guards import detect_stale_claims, StaleClaim

    claims = await detect_stale_claims(wip_issues, worktrees_dir)
    for claim in claims:
        print(f"#{claim.issue_number}: {claim.issue_title} — {claim.worktree_path}")
"""
from __future__ import annotations

import logging
from pathlib import Path

from agentception.models import StaleClaim

logger = logging.getLogger(__name__)


async def detect_stale_claims(
    wip_issues: list[dict[str, object]],
    worktrees_dir: Path,
) -> list[StaleClaim]:
    """Detect issues with ``agent:wip`` label but no corresponding worktree.

    For each open issue labelled ``agent:wip``, computes the expected worktree
    path ``worktrees_dir / f"issue-{number}"``.  When that path does not exist
    on the filesystem, the issue is classified as a stale claim.

    Parameters
    ----------
    wip_issues:
        List of issue dicts as returned by
        :func:`~agentception.readers.github.get_wip_issues`.
        Each dict must contain at minimum ``number`` (int) and ``title`` (str).
    worktrees_dir:
        Root directory where worktrees are created, e.g.
        ``~/.cursor/worktrees/maestro``.

    Returns
    -------
    list[StaleClaim]
        One entry per stale issue, sorted ascending by issue number.
        Empty list when all wip issues have live worktrees.
    """
    claims: list[StaleClaim] = []

    for issue in wip_issues:
        num = issue.get("number")
        title = issue.get("title", "")
        if not isinstance(num, int):
            logger.warning("⚠️  Skipping wip issue with non-int number: %r", num)
            continue
        if not isinstance(title, str):
            title = str(title)

        expected_path = worktrees_dir / f"issue-{num}"
        if not expected_path.exists():
            logger.debug(
                "⚠️  Stale claim detected: issue #%d has no worktree at %s",
                num,
                expected_path,
            )
            claims.append(
                StaleClaim(
                    issue_number=num,
                    issue_title=title,
                    worktree_path=str(expected_path),
                )
            )

    claims.sort(key=lambda c: c.issue_number)
    return claims
