"""Tests for Muse Hub milestones UI endpoints (issue #425).

Covers GET /musehub/ui/{owner}/{repo_slug}/milestones:
- test_milestones_list_page_returns_200          — page renders without auth
- test_milestones_list_no_auth_required          — no JWT needed for HTML shell
- test_milestones_list_has_progress_bar_js       — progress bar rendering present
- test_milestones_list_has_state_tabs_js         — open/closed/all state tabs present
- test_milestones_list_has_sort_controls_js      — due_on/title/completeness sort buttons
- test_milestones_list_json_response             — ?format=json returns MilestoneListResponse
- test_milestones_list_json_has_milestones_key   — JSON contains milestones array
- test_milestones_list_unknown_repo_404          — unknown owner/slug → 404
- test_milestones_list_shows_base_url_not_repo_id — base_url uses owner/slug pattern

Covers GET /musehub/ui/{owner}/{repo_slug}/milestones/{number}:
- test_milestone_detail_page_returns_200         — page renders without auth
- test_milestone_detail_no_auth_required         — no JWT needed for HTML shell
- test_milestone_detail_has_progress_bar         — progress bar JS present
- test_milestone_detail_has_linked_issues_js     — issue list rendering JS present
- test_milestone_detail_has_state_filter_tabs    — open/closed/all issue filter tabs
- test_milestone_detail_json_response            — ?format=json returns composite response
- test_milestone_detail_json_has_linked_issues   — JSON contains linked_issues key
- test_milestone_detail_unknown_number_404       — non-existent milestone number → 404
- test_milestone_detail_unknown_repo_404         — unknown owner/slug → 404
- test_milestone_detail_json_issue_counts        — JSON open_issues/closed_issues counts correct
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import (
    MusehubIssue,
    MusehubMilestone,
    MusehubRepo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession, owner: str = "artist", slug: str = "album-one") -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-test-artist",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_milestone(
    db: AsyncSession,
    repo_id: str,
    *,
    number: int = 1,
    title: str = "Album v1.0",
    description: str = "First release milestone",
    state: str = "open",
) -> MusehubMilestone:
    """Seed a milestone and return it."""
    ms = MusehubMilestone(
        repo_id=repo_id,
        number=number,
        title=title,
        description=description,
        state=state,
        author="artist",
    )
    db.add(ms)
    await db.commit()
    await db.refresh(ms)
    return ms


async def _make_issue(
    db: AsyncSession,
    repo_id: str,
    *,
    number: int = 1,
    title: str = "Bass mix is too loud",
    state: str = "open",
    milestone_id: str | None = None,
) -> MusehubIssue:
    """Seed an issue and return it."""
    issue = MusehubIssue(
        repo_id=repo_id,
        number=number,
        title=title,
        body="Description of the issue.",
        state=state,
        labels=["mix"],
        author="artist",
        milestone_id=milestone_id,
    )
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return issue


# ---------------------------------------------------------------------------
# Milestones list page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_milestones_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/milestones returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/artist/album-one/milestones")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_milestones_list_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestones list page is accessible without a JWT token."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/artist/album-one/milestones")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_milestones_list_has_progress_bar_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Page HTML contains progress bar rendering logic."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/artist/album-one/milestones")
    assert response.status_code == 200
    assert "progress-bar" in response.text


@pytest.mark.anyio
async def test_milestones_list_has_state_tabs_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestones list page has open/closed/all state filter tabs."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/artist/album-one/milestones")
    assert response.status_code == 200
    body = response.text
    assert "state-tabs" in body or "state-tab" in body
    assert "open" in body
    assert "closed" in body


@pytest.mark.anyio
async def test_milestones_list_has_sort_controls_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestones list page exposes due_on / title / completeness sort controls."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/artist/album-one/milestones")
    assert response.status_code == 200
    body = response.text
    assert "due_on" in body or "completeness" in body


@pytest.mark.anyio
async def test_milestones_list_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json returns JSON with HTTP 200."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, title="Mix Revision 2")
    response = await client.get("/musehub/ui/artist/album-one/milestones?format=json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_milestones_list_json_has_milestones_key(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response contains a milestones array."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, title="Album v1.0")
    response = await client.get("/musehub/ui/artist/album-one/milestones?format=json&state=all")
    assert response.status_code == 200
    data = response.json()
    assert "milestones" in data
    assert isinstance(data["milestones"], list)
    assert len(data["milestones"]) >= 1


@pytest.mark.anyio
async def test_milestones_list_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug returns 404."""
    response = await client.get("/musehub/ui/nobody/nonexistent/milestones")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_milestones_list_shows_base_url_not_repo_id(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML page uses owner/slug base_url pattern, not raw repo_id UUID."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/artist/album-one/milestones")
    assert response.status_code == 200
    assert "/musehub/ui/artist/album-one" in response.text


# ---------------------------------------------------------------------------
# Milestone detail page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_milestone_detail_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/milestones/{number} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1)
    response = await client.get("/musehub/ui/artist/album-one/milestones/1")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_milestone_detail_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestone detail page is accessible without a JWT token."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1)
    response = await client.get("/musehub/ui/artist/album-one/milestones/1")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_milestone_detail_has_progress_bar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestone detail page contains progress bar element."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1)
    response = await client.get("/musehub/ui/artist/album-one/milestones/1")
    assert response.status_code == 200
    assert "progress-bar" in response.text


@pytest.mark.anyio
async def test_milestone_detail_has_linked_issues_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestone detail page has JavaScript to render linked issues."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1)
    response = await client.get("/musehub/ui/artist/album-one/milestones/1")
    assert response.status_code == 200
    body = response.text
    assert "issue-rows" in body or "renderIssueRows" in body


@pytest.mark.anyio
async def test_milestone_detail_has_state_filter_tabs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestone detail page has open/closed/all tabs to filter linked issues."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1)
    response = await client.get("/musehub/ui/artist/album-one/milestones/1")
    assert response.status_code == 200
    body = response.text
    assert "state-tabs" in body or "state-tab" in body


@pytest.mark.anyio
async def test_milestone_detail_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json returns JSON with HTTP 200."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1)
    response = await client.get("/musehub/ui/artist/album-one/milestones/1?format=json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_milestone_detail_json_has_linked_issues(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response includes the milestone data and linkedIssues."""
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, number=1, title="Album v1.0")
    await _make_issue(db_session, repo_id, number=1, milestone_id=str(ms.milestone_id))
    response = await client.get("/musehub/ui/artist/album-one/milestones/1?format=json")
    assert response.status_code == 200
    data = response.json()
    assert "title" in data
    assert data["title"] == "Album v1.0"
    assert "linkedIssues" in data
    assert "issues" in data["linkedIssues"]


@pytest.mark.anyio
async def test_milestone_detail_unknown_number_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Non-existent milestone number returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/artist/album-one/milestones/999")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_milestone_detail_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug returns 404 for detail page."""
    response = await client.get("/musehub/ui/nobody/nonexistent/milestones/1")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_milestone_detail_json_issue_counts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response has correct open_issues and closed_issues counts."""
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, number=1)
    ms_id = str(ms.milestone_id)
    # 2 open + 1 closed
    await _make_issue(db_session, repo_id, number=1, state="open", milestone_id=ms_id)
    await _make_issue(db_session, repo_id, number=2, state="open", milestone_id=ms_id)
    await _make_issue(db_session, repo_id, number=3, state="closed", milestone_id=ms_id)
    response = await client.get("/musehub/ui/artist/album-one/milestones/1?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data.get("openIssues") == 2
    assert data.get("closedIssues") == 1
