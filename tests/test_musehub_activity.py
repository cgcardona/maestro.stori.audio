"""Tests for the MuseHub activity feed — issue #219.

Covers:
- GET /musehub/repos/{repo_id}/activity returns empty feed on new repo
- record_event appends events visible via list_events
- event_type filter works correctly
- pagination works correctly (page, page_size)
- GET /musehub/ui/{owner}/{repo_slug}/activity returns 200 HTML
- 404 for unknown repo on UI route
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo
from maestro.services import musehub_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_repo(db: AsyncSession, owner: str = "alice", slug: str = "beats") -> MusehubRepo:
    repo = MusehubRepo(
        name="beats",
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-001",
    )
    db.add(repo)
    await db.flush()
    return repo


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_events_empty_on_new_repo(db_session: AsyncSession) -> None:
    """list_events returns an empty feed for a fresh repo."""
    repo = await _create_repo(db_session)
    result = await musehub_events.list_events(db_session, repo.repo_id)
    assert result.events == []
    assert result.total == 0
    assert result.page == 1


@pytest.mark.anyio
async def test_record_event_appears_in_feed(db_session: AsyncSession) -> None:
    """record_event persists an event that is returned by list_events."""
    repo = await _create_repo(db_session)
    await musehub_events.record_event(
        db_session,
        repo_id=repo.repo_id,
        event_type="commit_pushed",
        actor="alice",
        description="Add groove baseline",
        metadata={"sha": "abc123", "message": "Add groove baseline"},
    )
    await db_session.commit()

    result = await musehub_events.list_events(db_session, repo.repo_id)
    assert result.total == 1
    assert len(result.events) == 1
    ev = result.events[0]
    assert ev.event_type == "commit_pushed"
    assert ev.actor == "alice"
    assert ev.description == "Add groove baseline"
    assert ev.metadata["sha"] == "abc123"


@pytest.mark.anyio
async def test_event_type_filter_isolates_events(db_session: AsyncSession) -> None:
    """event_type filter returns only events matching that type."""
    repo = await _create_repo(db_session)
    await musehub_events.record_event(
        db_session, repo_id=repo.repo_id, event_type="commit_pushed",
        actor="alice", description="push",
    )
    await musehub_events.record_event(
        db_session, repo_id=repo.repo_id, event_type="issue_opened",
        actor="bob", description="open issue",
    )
    await db_session.commit()

    commits_result = await musehub_events.list_events(
        db_session, repo.repo_id, event_type="commit_pushed"
    )
    assert commits_result.total == 1
    assert commits_result.events[0].event_type == "commit_pushed"

    issues_result = await musehub_events.list_events(
        db_session, repo.repo_id, event_type="issue_opened"
    )
    assert issues_result.total == 1
    assert issues_result.events[0].event_type == "issue_opened"


@pytest.mark.anyio
async def test_list_events_pagination(db_session: AsyncSession) -> None:
    """list_events paginates correctly — page 1 and page 2 return disjoint events."""
    repo = await _create_repo(db_session)
    for i in range(5):
        await musehub_events.record_event(
            db_session,
            repo_id=repo.repo_id,
            event_type="commit_pushed",
            actor="alice",
            description=f"commit {i}",
        )
    await db_session.commit()

    page1 = await musehub_events.list_events(db_session, repo.repo_id, page=1, page_size=3)
    page2 = await musehub_events.list_events(db_session, repo.repo_id, page=2, page_size=3)

    assert page1.total == 5
    assert len(page1.events) == 3
    assert len(page2.events) == 2

    # Pages must be disjoint
    ids1 = {e.event_id for e in page1.events}
    ids2 = {e.event_id for e in page2.events}
    assert ids1.isdisjoint(ids2)


@pytest.mark.anyio
async def test_events_ordered_newest_first(db_session: AsyncSession) -> None:
    """Events are returned newest-first."""
    repo = await _create_repo(db_session)
    for i in range(3):
        await musehub_events.record_event(
            db_session,
            repo_id=repo.repo_id,
            event_type="commit_pushed",
            actor="alice",
            description=f"commit {i}",
        )
    await db_session.commit()

    result = await musehub_events.list_events(db_session, repo.repo_id)
    timestamps = [e.created_at for e in result.events]
    assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# HTTP API tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_activity_empty_public_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{repo_id}/activity returns empty feed for new public repo."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "activity-test", "owner": "testuser", "visibility": "public"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    repo_id = response.json()["repoId"]

    activity_response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/activity",
    )
    assert activity_response.status_code == 200
    body = activity_response.json()
    assert body["events"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["pageSize"] == 30


@pytest.mark.anyio
async def test_get_activity_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{repo_id}/activity returns 404 for unknown repo."""
    response = await client.get("/api/v1/musehub/repos/nonexistent-id/activity")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_activity_event_type_filter_via_api(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """event_type query param filters events correctly via the HTTP API."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "filter-test", "owner": "testuser2", "visibility": "public"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    repo_id = response.json()["repoId"]

    # Seed events directly via service
    await musehub_events.record_event(
        db_session, repo_id=repo_id, event_type="commit_pushed",
        actor="testuser2", description="first commit",
    )
    await musehub_events.record_event(
        db_session, repo_id=repo_id, event_type="issue_opened",
        actor="testuser2", description="open issue",
    )
    await db_session.commit()

    r = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/activity?event_type=commit_pushed",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["events"][0]["eventType"] == "commit_pushed"
    assert body["eventTypeFilter"] == "commit_pushed"


# ---------------------------------------------------------------------------
# UI page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_activity_ui_page_returns_html(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/ui/{owner}/{slug}/activity returns 200 HTML."""
    await client.post(
        "/api/v1/musehub/repos",
        json={"name": "ui-activity", "owner": "uiuser", "visibility": "public"},
        headers=auth_headers,
    )
    response = await client.get("/musehub/ui/uiuser/ui-activity/activity")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert b"Activity" in response.content


@pytest.mark.anyio
async def test_activity_ui_page_unknown_repo_404(client: AsyncClient) -> None:
    """GET /musehub/ui/{owner}/{slug}/activity returns 404 for unknown repo."""
    response = await client.get("/musehub/ui/nobody/no-repo/activity")
    assert response.status_code == 404
