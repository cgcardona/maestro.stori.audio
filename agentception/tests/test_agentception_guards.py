"""Tests for agentception/intelligence/guards.py (AC-403).

Coverage:
- detect_out_of_order_prs() returns empty list when all PRs are in order
- detect_out_of_order_prs() returns PRViolation when phase label mismatches
- detect_out_of_order_prs() skips PRs with no 'Closes #N' reference
- close_pr() is called via the /api/intelligence/pr-violations/{n}/close route

Run targeted:
    pytest agentception/tests/test_agentception_guards.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agentception.intelligence.guards import PRViolation, detect_out_of_order_prs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pr(
    number: int,
    title: str = "feat: something",
    body: str = "",
    labels: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Build a minimal PR dict matching the shape returned by get_open_prs_with_body."""
    return {
        "number": number,
        "title": title,
        "headRefName": f"feat/issue-{number}",
        "labels": labels or [],
        "body": body,
    }


def _make_issue(number: int, label: str) -> dict[str, object]:
    """Build a minimal issue dict matching the shape returned by get_issue."""
    return {
        "number": number,
        "state": "OPEN",
        "title": f"Issue #{number}",
        "labels": [label],
    }


# ---------------------------------------------------------------------------
# test_detect_no_violations_when_all_in_order
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_detect_no_violations_when_all_in_order() -> None:
    """All PRs link to issues in the active phase — no violations returned."""
    active = "agentception/4-intelligence"
    prs = [
        _make_pr(100, body="Closes #200"),
        _make_pr(101, body="Closes #201"),
    ]
    issue_200 = _make_issue(200, active)
    issue_201 = _make_issue(201, active)

    def _fake_get_issue(number: int) -> dict[str, object]:
        return {200: issue_200, 201: issue_201}[number]

    with (
        patch(
            "agentception.intelligence.guards.get_active_label",
            new_callable=AsyncMock,
            return_value=active,
        ),
        patch(
            "agentception.intelligence.guards.get_open_prs_with_body",
            new_callable=AsyncMock,
            return_value=prs,
        ),
        patch(
            "agentception.intelligence.guards.get_issue",
            side_effect=_fake_get_issue,
        ),
    ):
        violations = await detect_out_of_order_prs()

    assert violations == [], f"Expected no violations, got: {violations}"


# ---------------------------------------------------------------------------
# test_detect_violation_wrong_phase_label
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_detect_violation_wrong_phase_label() -> None:
    """A PR linking to an issue from a past phase produces a PRViolation."""
    active = "agentception/4-intelligence"
    old_label = "agentception/0-scaffold"
    prs = [
        _make_pr(77, title="feat: old work", body="Closes #608"),
    ]
    issue_608 = _make_issue(608, old_label)

    with (
        patch(
            "agentception.intelligence.guards.get_active_label",
            new_callable=AsyncMock,
            return_value=active,
        ),
        patch(
            "agentception.intelligence.guards.get_open_prs_with_body",
            new_callable=AsyncMock,
            return_value=prs,
        ),
        patch(
            "agentception.intelligence.guards.get_issue",
            new_callable=AsyncMock,
            return_value=issue_608,
        ),
    ):
        violations = await detect_out_of_order_prs()

    assert len(violations) == 1
    v = violations[0]
    assert isinstance(v, PRViolation)
    assert v.pr_number == 77
    assert v.pr_title == "feat: old work"
    assert v.expected_label == active
    assert v.actual_label == old_label
    assert v.linked_issue == 608


# ---------------------------------------------------------------------------
# test_detect_no_linked_issue_skips
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_detect_no_linked_issue_skips() -> None:
    """PRs without a 'Closes #N' reference are silently skipped."""
    active = "agentception/4-intelligence"
    prs = [
        _make_pr(50, body="This PR has no closes reference."),
        _make_pr(51, body=""),  # empty body
    ]

    with (
        patch(
            "agentception.intelligence.guards.get_active_label",
            new_callable=AsyncMock,
            return_value=active,
        ),
        patch(
            "agentception.intelligence.guards.get_open_prs_with_body",
            new_callable=AsyncMock,
            return_value=prs,
        ),
        patch(
            "agentception.intelligence.guards.get_issue",
            new_callable=AsyncMock,
        ) as mock_get_issue,
    ):
        violations = await detect_out_of_order_prs()

    # get_issue should never be called since no PR has a Closes reference.
    mock_get_issue.assert_not_called()
    assert violations == []


# ---------------------------------------------------------------------------
# test_close_violating_pr_calls_gh
# ---------------------------------------------------------------------------


def test_close_violating_pr_calls_gh() -> None:
    """The close endpoint calls close_pr() with the correct PR number and message."""
    from fastapi.testclient import TestClient

    from agentception.app import app

    with TestClient(app) as client:
        with patch(
            "agentception.routes.api.close_pr",
            new_callable=AsyncMock,
        ) as mock_close:
            response = client.post("/api/intelligence/pr-violations/42/close")

    assert response.status_code == 200
    assert response.json() == {"closed": 42}
    mock_close.assert_awaited_once_with(
        42,
        "Closed by AgentCeption: out-of-order PR violation.",
    )
