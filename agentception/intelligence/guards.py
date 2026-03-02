"""Out-of-order PR guard for the AgentCeption intelligence layer.

Detects open PRs whose linked issue belongs to a phase label that no longer
matches the currently active pipeline phase. Surfaces violations so operators
can close stale PRs before they create merge conflicts or pollute the dev
branch with work from the wrong batch.

Depends on: #616 (GitHub reader layer must be present).

Public API:
- ``PRViolation``               — typed result for a single ordering violation
- ``detect_out_of_order_prs()`` — main detection coroutine; called by poller
                                   and the /api/intelligence/pr-violations route
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel

from agentception.readers.github import get_active_label, get_issue, get_open_prs_with_body

logger = logging.getLogger(__name__)

# Matches "Closes #NNN", "Close #NNN", "closes #NNN" in PR bodies.
_CLOSES_PATTERN: re.Pattern[str] = re.compile(r"[Cc]loses?\s+#(\d+)")


class PRViolation(BaseModel):
    """A PR whose linked issue belongs to a pipeline phase that is no longer active.

    ``expected_label`` is the currently active agentception/* label.
    ``actual_label``   is the label carried by the issue the PR closes.
    ``linked_issue``   is the issue number parsed from 'Closes #N' in the PR body.
    """

    pr_number: int
    pr_title: str
    expected_label: str
    actual_label: str
    linked_issue: int | None


def _parse_closes(body: str) -> int | None:
    """Return the first issue number from a 'Closes #NNN' reference, or None.

    Only the first reference is returned — a PR closing multiple issues is
    rare in this codebase, and the first reference is always the primary one.
    """
    match = _CLOSES_PATTERN.search(body)
    return int(match.group(1)) if match else None


async def _get_issue_ac_label(issue_number: int) -> str | None:
    """Return the first ``agentception/*`` label of an issue, or None.

    Returns None when the issue cannot be fetched (e.g. deleted or private)
    or when it carries no agentception/* label.
    """
    try:
        issue = await get_issue(issue_number)
    except RuntimeError:
        logger.warning("⚠️  Could not fetch issue #%d for label check", issue_number)
        return None

    labels = issue.get("labels", [])
    if isinstance(labels, list):
        for label in labels:
            if isinstance(label, str) and label.startswith("agentception/"):
                return label
    return None


async def detect_out_of_order_prs() -> list[PRViolation]:
    """Detect open PRs whose linked issue belongs to a non-active pipeline phase.

    Algorithm per PR:
    1. Parse ``Closes #NNN`` from the PR body.
    2. Fetch that issue's ``agentception/*`` label from GitHub.
    3. Compare against the currently active label (lowest-numbered open phase).
    4. Mismatch → append a ``PRViolation``.

    PRs with no ``Closes #N`` reference or whose linked issue carries no
    ``agentception/*`` label are silently skipped — they cannot be classified.

    Returns an empty list when there is no active label or no violations.
    """
    active_label = await get_active_label()
    if not active_label:
        logger.debug("🔍 No active label — skipping out-of-order PR check")
        return []

    open_prs = await get_open_prs_with_body()
    violations: list[PRViolation] = []

    for pr in open_prs:
        pr_number = pr.get("number")
        if not isinstance(pr_number, int):
            continue

        pr_title = pr.get("title", "")
        if not isinstance(pr_title, str):
            pr_title = str(pr_title)

        body = pr.get("body", "")
        if not isinstance(body, str):
            body = ""

        linked_issue = _parse_closes(body)
        if linked_issue is None:
            logger.debug("🔍 PR #%d has no 'Closes #N' — skipping", pr_number)
            continue

        actual_label = await _get_issue_ac_label(linked_issue)
        if actual_label is None:
            continue

        if actual_label != active_label:
            logger.warning(
                "⚠️  PR #%d links issue #%d with label %r (active=%r)",
                pr_number,
                linked_issue,
                actual_label,
                active_label,
            )
            violations.append(
                PRViolation(
                    pr_number=pr_number,
                    pr_title=pr_title,
                    expected_label=active_label,
                    actual_label=actual_label,
                    linked_issue=linked_issue,
                )
            )

    return violations
