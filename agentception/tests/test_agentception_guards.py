"""Tests for agentception/intelligence/guards.py (AC-404).

Coverage:
- detect_stale_claims() flags issues with agent:wip but no worktree
- detect_stale_claims() ignores issues whose worktree exists
- clear_stale_claim endpoint removes the agent:wip label
- stale claims from guards propagate into PipelineState.alerts via detect_alerts()

Run targeted:
    pytest agentception/tests/test_agentception_guards.py -v
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agentception.app import app
from agentception.intelligence.guards import detect_stale_claims
from agentception.models import StaleClaim, TaskFile
from agentception.poller import GitHubBoard, detect_alerts


# ---------------------------------------------------------------------------
# detect_stale_claims() — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_detect_stale_claim_missing_worktree(tmp_path: Path) -> None:
    """detect_stale_claims() should flag an issue whose expected worktree is absent."""
    wip_issues = [{"number": 100, "title": "Fix the thing"}]
    # tmp_path/issue-100 does NOT exist → stale claim expected
    claims = await detect_stale_claims(wip_issues, tmp_path)

    assert len(claims) == 1
    assert claims[0].issue_number == 100
    assert claims[0].issue_title == "Fix the thing"
    assert claims[0].worktree_path == str(tmp_path / "issue-100")


@pytest.mark.anyio
async def test_detect_no_stale_when_worktree_exists(tmp_path: Path) -> None:
    """detect_stale_claims() must not flag issues whose worktree directory exists."""
    # Create the worktree directory so the issue is considered live.
    (tmp_path / "issue-200").mkdir()

    wip_issues = [{"number": 200, "title": "Already working"}]
    claims = await detect_stale_claims(wip_issues, tmp_path)

    assert claims == []


@pytest.mark.anyio
async def test_detect_stale_claims_returns_sorted(tmp_path: Path) -> None:
    """detect_stale_claims() returns results sorted ascending by issue number."""
    # Neither worktree exists — both should be stale.
    wip_issues = [
        {"number": 300, "title": "Third"},
        {"number": 100, "title": "First"},
        {"number": 200, "title": "Second"},
    ]
    claims = await detect_stale_claims(wip_issues, tmp_path)

    assert [c.issue_number for c in claims] == [100, 200, 300]


@pytest.mark.anyio
async def test_detect_stale_claims_skips_non_int_number(tmp_path: Path) -> None:
    """detect_stale_claims() should skip issues where number is not an int."""
    wip_issues: list[dict[str, object]] = [{"number": "not-a-number", "title": "Bad issue"}]
    claims = await detect_stale_claims(wip_issues, tmp_path)

    assert claims == []


# ---------------------------------------------------------------------------
# detect_alerts() integration — stale claims appear in alerts
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stale_claim_shows_in_alerts(tmp_path: Path) -> None:
    """Stale claims from guards.detect_stale_claims() must appear in PipelineState alerts.

    Verifies the full path: detect_alerts() calls detect_stale_claims() and
    converts each StaleClaim into a human-readable string in the alerts list.
    The structured stale_claims list is also returned alongside alerts.
    """
    board = GitHubBoard(
        active_label="agentception/4-intelligence",
        open_issues=[],
        open_prs=[],
        wip_issues=[{"number": 42, "title": "Stale issue"}],
    )
    # No worktrees — issue 42 has no live worktree → stale claim expected.
    worktrees: list[TaskFile] = []

    with patch("agentception.poller.settings") as poller_mock:
        poller_mock.worktrees_dir = tmp_path
        alerts, stale_claims = await detect_alerts(worktrees, board)

    assert any("Stale claim on #42" in a for a in alerts), (
        f"Expected 'Stale claim on #42' in alerts, got: {alerts}"
    )
    assert len(stale_claims) == 1
    assert stale_claims[0].issue_number == 42
    assert stale_claims[0].issue_title == "Stale issue"


# ---------------------------------------------------------------------------
# clear_stale_claim endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_clear_stale_claim_removes_label() -> None:
    """POST /api/intelligence/stale-claims/{n}/clear should call clear_wip_label."""
    with patch(
        "agentception.routes.intelligence.clear_wip_label",
        new_callable=AsyncMock,
    ) as mock_clear:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/intelligence/stale-claims/99/clear")

    assert response.status_code == 200
    assert response.json() == {"cleared": 99}
    mock_clear.assert_awaited_once_with(99)


@pytest.mark.anyio
async def test_clear_stale_claim_returns_500_on_gh_failure() -> None:
    """POST /api/intelligence/stale-claims/{n}/clear should return 500 when gh fails."""
    with patch(
        "agentception.routes.intelligence.clear_wip_label",
        new_callable=AsyncMock,
        side_effect=RuntimeError("gh auth error"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/intelligence/stale-claims/55/clear")

    assert response.status_code == 500
    assert "gh auth error" in response.json()["detail"]
