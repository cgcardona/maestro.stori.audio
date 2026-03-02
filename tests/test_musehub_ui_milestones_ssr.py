"""SSR tests for Muse Hub milestones UI pages — issue #558.

Validates that milestone data is rendered server-side into HTML (not deferred
to client JS) and that HTMX fragment requests return bare HTML without the
full page shell.

Covers GET /musehub/ui/{owner}/{repo_slug}/milestones:
- test_milestones_list_renders_title_server_side      — milestone title in HTML
- test_milestones_list_progress_bar_has_correct_width — width:75% for 3/4 closed
- test_milestones_list_htmx_state_switch_returns_fragment — HX-Request → bare fragment

Covers GET /musehub/ui/{owner}/{repo_slug}/milestones/{number}:
- test_milestone_detail_renders_milestone_title        — title in HTML server-side
- test_milestone_detail_shows_linked_issues            — issue title in HTML
- test_milestone_detail_issue_state_filter_open        — ?state=closed shows only closed
- test_milestone_detail_unknown_number_404             — unknown number → 404
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
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession, owner: str = "artist", slug: str = "ssr-album") -> str:
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
    description: str = "SSR milestone description",
    state: str = "open",
) -> MusehubMilestone:
    """Seed a milestone and return the ORM object."""
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
    title: str = "Linked issue",
    state: str = "open",
    milestone_id: str | None = None,
) -> MusehubIssue:
    """Seed an issue and return the ORM object."""
    issue = MusehubIssue(
        repo_id=repo_id,
        number=number,
        title=title,
        body="Issue body.",
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
# Milestones list SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_milestones_list_renders_title_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestone title is rendered into the HTML response server-side (not via JS)."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, title="Album Release Milestone")
    response = await client.get(
        "/musehub/ui/artist/ssr-album/milestones?state=all"
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Title must appear in the HTML without requiring client-side JS execution.
    assert "Album Release Milestone" in response.text


@pytest.mark.anyio
async def test_milestones_list_progress_bar_has_correct_width(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Progress bar fill width reflects closed/total ratio (3 closed, 1 open → 75%)."""
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, title="Progress Test")
    mid = str(ms.milestone_id)

    # Seed 3 closed + 1 open issue linked to the milestone.
    await _make_issue(db_session, repo_id, number=1, state="closed", milestone_id=mid)
    await _make_issue(db_session, repo_id, number=2, state="closed", milestone_id=mid)
    await _make_issue(db_session, repo_id, number=3, state="closed", milestone_id=mid)
    await _make_issue(db_session, repo_id, number=4, state="open", milestone_id=mid)

    response = await client.get("/musehub/ui/artist/ssr-album/milestones?state=all")
    assert response.status_code == 200
    # Fragment renders the inline style; int(75.0) == 75 → "width:75%"
    assert "width:75%" in response.text


@pytest.mark.anyio
async def test_milestones_list_htmx_state_switch_returns_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true returns a bare HTML fragment without the full page shell."""
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, state="closed", title="Closed Milestone")
    _ = ms  # milestone exists so the closed tab has content

    response = await client.get(
        "/musehub/ui/artist/ssr-album/milestones?state=closed",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Fragment must NOT contain the full HTML shell.
    assert "<html" not in response.text
    assert "<head" not in response.text
    # Fragment should contain milestone content or empty-state markup.
    assert "milestone" in response.text.lower() or "No milestones" in response.text


# ---------------------------------------------------------------------------
# Milestone detail SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_milestone_detail_renders_milestone_title(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Milestone title is in the response HTML server-side (not behind JS)."""
    repo_id = await _make_repo(db_session)
    await _make_milestone(db_session, repo_id, number=1, title="SSR Detail Title")
    response = await client.get("/musehub/ui/artist/ssr-album/milestones/1")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "SSR Detail Title" in response.text


@pytest.mark.anyio
async def test_milestone_detail_shows_linked_issues(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issues assigned to the milestone appear in the HTML without JS execution."""
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, number=1, title="Linked Issues Test")
    await _make_issue(
        db_session,
        repo_id,
        number=1,
        title="Bass groove needs more swing",
        milestone_id=str(ms.milestone_id),
    )
    response = await client.get("/musehub/ui/artist/ssr-album/milestones/1")
    assert response.status_code == 200
    assert "Bass groove needs more swing" in response.text


@pytest.mark.anyio
async def test_milestone_detail_issue_state_filter_closed(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?state=closed shows only closed linked issues."""
    repo_id = await _make_repo(db_session)
    ms = await _make_milestone(db_session, repo_id, number=1, title="Filter Test")
    mid = str(ms.milestone_id)
    await _make_issue(
        db_session, repo_id, number=1, title="Open issue title", state="open", milestone_id=mid
    )
    await _make_issue(
        db_session, repo_id, number=2, title="Closed issue title", state="closed", milestone_id=mid
    )
    response = await client.get("/musehub/ui/artist/ssr-album/milestones/1?state=closed")
    assert response.status_code == 200
    body = response.text
    assert "Closed issue title" in body
    assert "Open issue title" not in body


@pytest.mark.anyio
async def test_milestone_detail_unknown_number_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Non-existent milestone number returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/artist/ssr-album/milestones/9999")
    assert response.status_code == 404
