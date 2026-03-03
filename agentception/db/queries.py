"""Read-only query helpers for AgentCeption's Postgres data store.

All functions return plain dicts / lists so callers (routes, poller) have
zero dependency on SQLAlchemy internals.  Swallows DB errors and returns
empty results so a database outage degrades gracefully to in-memory state.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select, text

from agentception.db.engine import get_session
from agentception.db.models import (
    ACAgentMessage,
    ACAgentRun,
    ACIssue,
    ACPipelineSnapshot,
    ACPullRequest,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Board issues — replaces live gh CLI call in the overview sidebar
# ---------------------------------------------------------------------------


async def get_board_issues(
    repo: str,
    label: str | None = None,
    include_claimed: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return open issues from ``ac_issues``, optionally filtered by phase label.

    Returns dicts shaped like the ``gh`` CLI JSON output so existing templates
    work without changes.  Falls back to ``[]`` on any DB error.
    """
    try:
        async with get_session() as session:
            stmt = (
                select(ACIssue)
                .where(ACIssue.repo == repo, ACIssue.state == "open")
                .order_by(ACIssue.github_number.desc())
                .limit(limit)
            )
            if label:
                # Filter to issues whose labels_json contains the phase label.
                # Using a text fragment is simpler than a JSON operator for
                # cross-dialect compatibility (works on Postgres and SQLite).
                stmt = stmt.where(ACIssue.labels_json.contains(label))
            result = await session.execute(stmt)
            rows = result.scalars().all()

        issues: list[dict[str, Any]] = []
        for row in rows:
            labels = json.loads(row.labels_json)
            is_claimed = "agent:wip" in labels
            if not include_claimed and is_claimed:
                continue
            issues.append(
                {
                    "number": row.github_number,
                    "title": row.title,
                    "state": row.state,
                    "labels": [{"name": n} for n in labels],
                    "claimed": is_claimed,
                    "phase_label": row.phase_label,
                    "last_synced_at": row.last_synced_at.isoformat(),
                }
            )
        return issues
    except Exception as exc:
        logger.warning("⚠️  get_board_issues DB query failed (non-fatal): %s", exc)
        return []


async def get_board_counts(
    repo: str,
    label: str | None = None,
) -> dict[str, int]:
    """Return unclaimed/claimed/total counts for the active phase board."""
    try:
        async with get_session() as session:
            stmt = select(ACIssue).where(
                ACIssue.repo == repo, ACIssue.state == "open"
            )
            if label:
                stmt = stmt.where(ACIssue.labels_json.contains(label))
            result = await session.execute(stmt)
            rows = result.scalars().all()

        total = len(rows)
        claimed = sum(
            1 for r in rows if "agent:wip" in json.loads(r.labels_json)
        )
        return {"total": total, "claimed": claimed, "unclaimed": total - claimed}
    except Exception as exc:
        logger.warning("⚠️  get_board_counts DB query failed (non-fatal): %s", exc)
        return {"total": 0, "claimed": 0, "unclaimed": 0}


# ---------------------------------------------------------------------------
# Pipeline trend — replaces ephemeral in-memory data on the telemetry page
# ---------------------------------------------------------------------------


async def get_pipeline_trend(
    hours: int = 24,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return recent pipeline snapshots for trend charts.

    Each dict has: ``polled_at`` (ISO string), ``active_label``,
    ``issues_open``, ``prs_open``, ``agents_active``, ``alert_count``.
    """
    try:
        async with get_session() as session:
            stmt = (
                select(ACPipelineSnapshot)
                .order_by(ACPipelineSnapshot.polled_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return [
            {
                "polled_at": row.polled_at.isoformat(),
                "active_label": row.active_label,
                "issues_open": row.issues_open,
                "prs_open": row.prs_open,
                "agents_active": row.agents_active,
                "alert_count": len(json.loads(row.alerts_json)),
            }
            for row in reversed(rows)  # chronological order for charts
        ]
    except Exception as exc:
        logger.warning("⚠️  get_pipeline_trend DB query failed (non-fatal): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Agent run history — enriches the agents list / detail pages
# ---------------------------------------------------------------------------


async def get_agent_run_history(
    limit: int = 100,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent agent runs from ``ac_agent_runs``, newest first."""
    try:
        async with get_session() as session:
            stmt = (
                select(ACAgentRun)
                .order_by(ACAgentRun.spawned_at.desc())
                .limit(limit)
            )
            if status:
                stmt = stmt.where(ACAgentRun.status == status)
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return [
            {
                "id": row.id,
                "wave_id": row.wave_id,
                "issue_number": row.issue_number,
                "pr_number": row.pr_number,
                "branch": row.branch,
                "worktree_path": row.worktree_path,
                "role": row.role,
                "status": row.status,
                "attempt_number": row.attempt_number,
                "spawn_mode": row.spawn_mode,
                "batch_id": row.batch_id,
                "spawned_at": row.spawned_at.isoformat(),
                "last_activity_at": (
                    row.last_activity_at.isoformat() if row.last_activity_at else None
                ),
                "completed_at": (
                    row.completed_at.isoformat() if row.completed_at else None
                ),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("⚠️  get_agent_run_history DB query failed (non-fatal): %s", exc)
        return []


async def get_agent_run_detail(
    run_id: str,
) -> dict[str, Any] | None:
    """Return a single agent run with its transcript messages.

    ``run_id`` is the worktree basename (e.g. ``issue-732``), which is the
    primary key stored in ``ac_agent_runs``.
    """
    try:
        async with get_session() as session:
            run_result = await session.execute(
                select(ACAgentRun).where(ACAgentRun.id == run_id)
            )
            run = run_result.scalar_one_or_none()
            if run is None:
                return None

            msg_result = await session.execute(
                select(ACAgentMessage)
                .where(ACAgentMessage.agent_run_id == run_id)
                .order_by(ACAgentMessage.sequence_index)
            )
            messages = msg_result.scalars().all()

        return {
            "id": run.id,
            "issue_number": run.issue_number,
            "pr_number": run.pr_number,
            "branch": run.branch,
            "role": run.role,
            "status": run.status,
            "spawned_at": run.spawned_at.isoformat(),
            "last_activity_at": (
                run.last_activity_at.isoformat() if run.last_activity_at else None
            ),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_name": m.tool_name,
                    "sequence_index": m.sequence_index,
                    "recorded_at": m.recorded_at.isoformat(),
                }
                for m in messages
            ],
        }
    except Exception as exc:
        logger.warning("⚠️  get_agent_run_detail DB query failed (non-fatal): %s", exc)
        return None


# ---------------------------------------------------------------------------
# Open PRs — replaces gh CLI PR reads
# ---------------------------------------------------------------------------


async def get_open_prs_db(repo: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return open PRs from ``ac_pull_requests``."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ACPullRequest)
                .where(ACPullRequest.repo == repo, ACPullRequest.state == "open")
                .order_by(ACPullRequest.github_number.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
        return [
            {
                "number": row.github_number,
                "title": row.title,
                "state": row.state,
                "headRefName": row.head_ref,
                "labels": [{"name": n} for n in json.loads(row.labels_json)],
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("⚠️  get_open_prs_db DB query failed (non-fatal): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Issue detail — single issue with linked PR and agent runs
# ---------------------------------------------------------------------------


async def get_issue_detail(
    repo: str,
    number: int,
) -> dict[str, Any] | None:
    """Return full detail for a single issue from ``ac_issues``.

    Includes linked PR (via ``closes_issue_number``) and all agent runs
    that worked on this issue.  Returns ``None`` when the issue is not in DB.
    """
    try:
        async with get_session() as session:
            issue_result = await session.execute(
                select(ACIssue).where(
                    ACIssue.repo == repo,
                    ACIssue.github_number == number,
                )
            )
            issue = issue_result.scalar_one_or_none()
            if issue is None:
                return None

            pr_result = await session.execute(
                select(ACPullRequest).where(
                    ACPullRequest.repo == repo,
                    ACPullRequest.closes_issue_number == number,
                )
            )
            linked_prs = pr_result.scalars().all()

            runs_result = await session.execute(
                select(ACAgentRun)
                .where(ACAgentRun.issue_number == number)
                .order_by(ACAgentRun.spawned_at.desc())
                .limit(20)
            )
            runs = runs_result.scalars().all()

        labels = json.loads(issue.labels_json)
        return {
            "number": issue.github_number,
            "title": issue.title,
            "body": issue.body or "",
            "state": issue.state,
            "labels": labels,
            "phase_label": issue.phase_label,
            "claimed": "agent:wip" in labels,
            "first_seen_at": issue.first_seen_at.isoformat(),
            "last_synced_at": issue.last_synced_at.isoformat(),
            "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
            "linked_prs": [
                {
                    "number": pr.github_number,
                    "title": pr.title,
                    "state": pr.state,
                    "head_ref": pr.head_ref,
                    "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
                }
                for pr in linked_prs
            ],
            "agent_runs": [
                {
                    "id": r.id,
                    "role": r.role,
                    "status": r.status,
                    "branch": r.branch,
                    "pr_number": r.pr_number,
                    "spawned_at": r.spawned_at.isoformat(),
                    "last_activity_at": r.last_activity_at.isoformat() if r.last_activity_at else None,
                }
                for r in runs
            ],
        }
    except Exception as exc:
        logger.warning("⚠️  get_issue_detail DB query failed (non-fatal): %s", exc)
        return None


async def get_all_issues(
    repo: str,
    state: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return issues from ``ac_issues``, optionally filtered by state."""
    try:
        async with get_session() as session:
            stmt = (
                select(ACIssue)
                .where(ACIssue.repo == repo)
                .order_by(ACIssue.github_number.desc())
                .limit(limit)
            )
            if state:
                stmt = stmt.where(ACIssue.state == state)
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [
            {
                "number": row.github_number,
                "title": row.title,
                "state": row.state,
                "labels": json.loads(row.labels_json),
                "phase_label": row.phase_label,
                "closed_at": row.closed_at.isoformat() if row.closed_at else None,
                "last_synced_at": row.last_synced_at.isoformat(),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("⚠️  get_all_issues DB query failed (non-fatal): %s", exc)
        return []


# ---------------------------------------------------------------------------
# PR detail — single PR with CI checks and agent runs
# ---------------------------------------------------------------------------


async def get_pr_detail(
    repo: str,
    number: int,
) -> dict[str, Any] | None:
    """Return full detail for a single PR from ``ac_pull_requests``.

    Includes linked issue and agent runs that worked on this PR.
    Returns ``None`` when the PR is not in DB.
    """
    try:
        async with get_session() as session:
            pr_result = await session.execute(
                select(ACPullRequest).where(
                    ACPullRequest.repo == repo,
                    ACPullRequest.github_number == number,
                )
            )
            pr = pr_result.scalar_one_or_none()
            if pr is None:
                return None

            issue: ACIssue | None = None
            if pr.closes_issue_number is not None:
                issue_result = await session.execute(
                    select(ACIssue).where(
                        ACIssue.repo == repo,
                        ACIssue.github_number == pr.closes_issue_number,
                    )
                )
                issue = issue_result.scalar_one_or_none()

            runs_result = await session.execute(
                select(ACAgentRun)
                .where(ACAgentRun.pr_number == number)
                .order_by(ACAgentRun.spawned_at.desc())
                .limit(20)
            )
            runs = runs_result.scalars().all()

        labels = json.loads(pr.labels_json)
        return {
            "number": pr.github_number,
            "title": pr.title,
            "state": pr.state,
            "head_ref": pr.head_ref,
            "labels": labels,
            "closes_issue_number": pr.closes_issue_number,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "first_seen_at": pr.first_seen_at.isoformat(),
            "last_synced_at": pr.last_synced_at.isoformat(),
            "linked_issue": {
                "number": issue.github_number,
                "title": issue.title,
                "state": issue.state,
            } if issue else None,
            "agent_runs": [
                {
                    "id": r.id,
                    "role": r.role,
                    "status": r.status,
                    "branch": r.branch,
                    "issue_number": r.issue_number,
                    "spawned_at": r.spawned_at.isoformat(),
                    "last_activity_at": r.last_activity_at.isoformat() if r.last_activity_at else None,
                }
                for r in runs
            ],
        }
    except Exception as exc:
        logger.warning("⚠️  get_pr_detail DB query failed (non-fatal): %s", exc)
        return None


async def get_all_prs(
    repo: str,
    state: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return PRs from ``ac_pull_requests``, optionally filtered by state."""
    try:
        async with get_session() as session:
            stmt = (
                select(ACPullRequest)
                .where(ACPullRequest.repo == repo)
                .order_by(ACPullRequest.github_number.desc())
                .limit(limit)
            )
            if state:
                stmt = stmt.where(ACPullRequest.state == state)
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [
            {
                "number": row.github_number,
                "title": row.title,
                "state": row.state,
                "head_ref": row.head_ref,
                "labels": json.loads(row.labels_json),
                "closes_issue_number": row.closes_issue_number,
                "merged_at": row.merged_at.isoformat() if row.merged_at else None,
                "last_synced_at": row.last_synced_at.isoformat(),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("⚠️  get_all_prs DB query failed (non-fatal): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Counts for SSE expansion
# ---------------------------------------------------------------------------


async def get_closed_issues_count(repo: str, hours: int = 24) -> int:
    """Count issues closed within the last *hours* using the actual ``closed_at`` timestamp."""
    try:
        import datetime
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        async with get_session() as session:
            result = await session.execute(
                text(
                    "SELECT COUNT(*) FROM ac_issues "
                    "WHERE repo = :repo AND state = 'closed' AND closed_at >= :cutoff"
                ).bindparams(repo=repo, cutoff=cutoff)
            )
            row = result.one()
        return int(row[0])
    except Exception as exc:
        logger.warning("⚠️  get_closed_issues_count failed (non-fatal): %s", exc)
        return 0


async def get_merged_prs_count(repo: str, hours: int = 24) -> int:
    """Count PRs merged within the last *hours* using the actual ``merged_at`` timestamp."""
    try:
        import datetime
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        async with get_session() as session:
            result = await session.execute(
                text(
                    "SELECT COUNT(*) FROM ac_pull_requests "
                    "WHERE repo = :repo AND state = 'merged' AND merged_at >= :cutoff"
                ).bindparams(repo=repo, cutoff=cutoff)
            )
            row = result.one()
        return int(row[0])
    except Exception as exc:
        logger.warning("⚠️  get_merged_prs_count failed (non-fatal): %s", exc)
        return 0
