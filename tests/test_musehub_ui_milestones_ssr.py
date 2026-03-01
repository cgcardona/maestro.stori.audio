"""SSR tests for Muse Hub milestones UI — issue #558.

Verifies that both milestone pages render data server-side in Jinja2 templates
without requiring JavaScript execution.  Tests assert on HTML content directly
returned by the server, not on JavaScript rendering logic.

Covers milestones list page (GET /musehub/ui/{owner}/{repo_slug}/milestones):
- test_milestones_list_renders_title_server_side
- test_milestones_list_progress_bar_has_correct_width
- test_milestones_list_htmx_state_switch_returns_fragment

Covers milestone detail page (GET /musehub/ui/{owner}/{repo_slug}/milestones/{number}):
- test_milestone_detail_renders_milestone_title
- test_milestone_detail_shows_linked_issues
- test_milestone_detail_issue_state_filter_closed
- test_milestone_detail_unknown_number_404
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


async def _make_repo(
    db: AsyncSession,
    owner: str = "ssr_artist",
    slug: str = "ssr-album",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-ssr-artist",
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
    title: str = "SSR Milestone",
    description: str = "A server-rendered milestone",
    state: str = "open",
) -> MusehubMilestone:
    """Seed a milestone and return the ORM instance."""
    ms = MusehubMilestone(
        repo_id=repo_id,
        number=number,
        title=title,
        description=description,
        state=state,
        author="ssr_artist",
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
    title: str = "SSR Issue",
    state: str = "open",
    milestone_id: str | None = None,
) -> MusehubIssue:
    """Seed an issue and return the ORM instance."""
    issue = MusehubIssue(
        repo_id=repo_id,
        number=number,
        title=title,
        body="Issue body for SSR testing.",
        state=state,
        labels=["test"],
        author="ssr_artist",
        milestone_id=milestone_id,
    )
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return issue


# ---------------------------------------------------------------------------
# Milestones list page — SSR assertions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_milestones_list_renders_title_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestone title is rendered in HTML by the server, not JavaScript."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, title="Album v2.0 SSR")
    response = await client.get("/musehub/ui/ssr_artist/ssr-album/milestones?state=all")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Album v2.0 SSR" in response.text


@pytest.mark.anyio
async def test_milestones_list_progress_bar_has_correct_width(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Progress bar fill width reflects closed/total ratio computed server-side.

    Seeding 3 closed + 1 open issues → 75% complete → width:75% in HTML.
    """
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, number=1, title="Progress Test")
    ms_id = str(ms.milestone_id)
    await _make_issue(db_session, repo_id, number=1, state="closed", milestone_id=ms_id)
    await _make_issue(db_session, repo_id, number=2, state="closed", milestone_id=ms_id)
    await _make_issue(db_session, repo_id, number=3, state="closed", milestone_id=ms_id)
    await _make_issue(db_session, repo_id, number=4, state="open", milestone_id=ms_id)
    response = await client.get("/musehub/ui/ssr_artist/ssr-album/milestones?state=all")
    assert response.status_code == 200
    assert "width:75%" in response.text


@pytest.mark.anyio
async def test_milestones_list_htmx_state_switch_returns_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request header causes the handler to return only the rows fragment.

    The fragment must not contain the full <html> shell — just milestone rows.
    """
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1, state="closed", title="Closed MS")
    response = await client.get(
        "/musehub/ui/ssr_artist/ssr-album/milestones?state=closed",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Fragment should not contain the full page shell
    assert "<html" not in response.text
    # Fragment should contain milestone content
    assert "Closed MS" in response.text


# ---------------------------------------------------------------------------
# Milestone detail page — SSR assertions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_milestone_detail_renders_milestone_title(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestone title is rendered in the detail page HTML by the server."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1, title="Detail SSR Milestone")
    response = await client.get("/musehub/ui/ssr_artist/ssr-album/milestones/1")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Detail SSR Milestone" in response.text


@pytest.mark.anyio
async def test_milestone_detail_shows_linked_issues(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issue titles linked to a milestone appear in the detail page HTML."""
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, number=1, title="Issue Linking Test")
    await _make_issue(
        db_session,
        repo_id,
        number=1,
        title="Verse needs more reverb SSR",
        milestone_id=str(ms.milestone_id),
    )
    response = await client.get("/musehub/ui/ssr_artist/ssr-album/milestones/1")
    assert response.status_code == 200
    assert "Verse needs more reverb SSR" in response.text


@pytest.mark.anyio
async def test_milestone_detail_issue_state_filter_closed(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?state=closed returns only closed issues in the detail page HTML."""
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, number=1, title="State Filter Test")
    ms_id = str(ms.milestone_id)
    await _make_issue(db_session, repo_id, number=1, title="Open issue SSR", state="open", milestone_id=ms_id)
    await _make_issue(db_session, repo_id, number=2, title="Closed issue SSR", state="closed", milestone_id=ms_id)
    response = await client.get("/musehub/ui/ssr_artist/ssr-album/milestones/1?state=closed")
    assert response.status_code == 200
    assert "Closed issue SSR" in response.text
    assert "Open issue SSR" not in response.text


@pytest.mark.anyio
async def test_milestone_detail_unknown_number_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Non-existent milestone number returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/ssr_artist/ssr-album/milestones/9999")
    assert response.status_code == 404
