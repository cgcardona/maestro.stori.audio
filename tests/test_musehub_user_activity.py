"""Tests for GET /musehub/users/{username}/activity — issue #418.

Covers:
- 404 for unknown username
- Empty feed for a user with no events
- Events from public repos appear in the feed
- Events from private repos are hidden from unauthenticated callers
- Events from private repos are visible to the repo owner
- type filter works correctly (push, pull_request, issue, release)
- type filter for types with no DB equivalent (star, fork) returns empty feed
- Cursor pagination (before_id) returns the next page correctly
- limit parameter is respected (default 30, max 100)
- next_cursor is None when there are no more events
- type_filter is echoed back in the response
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubProfile, MusehubRepo
from maestro.services import musehub_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_profile(
    db: AsyncSession,
    user_id: str = "uid-act-001",
    username: str = "actuser",
) -> MusehubProfile:
    profile = MusehubProfile(user_id=user_id, username=username, pinned_repo_ids=[])
    db.add(profile)
    await db.flush()
    return profile


async def _create_repo(
    db: AsyncSession,
    owner: str = "actuser",
    slug: str = "jazzrepo",
    owner_user_id: str = "uid-act-001",
    visibility: str = "public",
) -> MusehubRepo:
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id=owner_user_id,
    )
    db.add(repo)
    await db.flush()
    return repo


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_user_activity_empty_for_no_events(db_session: AsyncSession) -> None:
    """list_user_activity returns an empty feed when the user has no events."""
    await _create_profile(db_session)
    result = await musehub_events.list_user_activity(db_session, "actuser")
    assert result.events == []
    assert result.next_cursor is None


@pytest.mark.anyio
async def test_list_user_activity_public_repo_events_visible(
    db_session: AsyncSession,
) -> None:
    """Events from public repos appear in the activity feed."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session, visibility="public")
    await musehub_events.record_event(
        db_session,
        repo_id=repo.repo_id,
        event_type="commit_pushed",
        actor="actuser",
        description="Add groove baseline",
        metadata={"sha": "abc123"},
    )
    await db_session.commit()

    result = await musehub_events.list_user_activity(db_session, "actuser")
    assert len(result.events) == 1
    ev = result.events[0]
    assert ev.actor == "actuser"
    assert ev.type == "push"
    assert ev.repo == "actuser/jazzrepo"
    assert ev.payload["sha"] == "abc123"


@pytest.mark.anyio
async def test_list_user_activity_private_repo_hidden_from_unauthenticated(
    db_session: AsyncSession,
) -> None:
    """Events from private repos are not returned when caller_user_id is None."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session, slug="privrepo", visibility="private")
    await musehub_events.record_event(
        db_session,
        repo_id=repo.repo_id,
        event_type="commit_pushed",
        actor="actuser",
        description="Secret work",
    )
    await db_session.commit()

    result = await musehub_events.list_user_activity(
        db_session, "actuser", caller_user_id=None
    )
    assert result.events == []


@pytest.mark.anyio
async def test_list_user_activity_private_repo_visible_to_owner(
    db_session: AsyncSession,
) -> None:
    """Events from private repos are visible when caller_user_id matches the repo owner."""
    await _create_profile(db_session)
    repo = await _create_repo(
        db_session, slug="privrepo2", visibility="private", owner_user_id="uid-act-001"
    )
    await musehub_events.record_event(
        db_session,
        repo_id=repo.repo_id,
        event_type="commit_pushed",
        actor="actuser",
        description="Owner can see private work",
    )
    await db_session.commit()

    result = await musehub_events.list_user_activity(
        db_session, "actuser", caller_user_id="uid-act-001"
    )
    assert len(result.events) == 1


@pytest.mark.anyio
async def test_list_user_activity_type_filter_push(db_session: AsyncSession) -> None:
    """type_filter='push' returns only push-type events (commit_pushed)."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session)
    await musehub_events.record_event(
        db_session, repo_id=repo.repo_id, event_type="commit_pushed",
        actor="actuser", description="pushed",
    )
    await musehub_events.record_event(
        db_session, repo_id=repo.repo_id, event_type="issue_opened",
        actor="actuser", description="issue",
    )
    await db_session.commit()

    result = await musehub_events.list_user_activity(
        db_session, "actuser", type_filter="push"
    )
    assert len(result.events) == 1
    assert result.events[0].type == "push"
    assert result.type_filter == "push"


@pytest.mark.anyio
async def test_list_user_activity_type_filter_pull_request(
    db_session: AsyncSession,
) -> None:
    """type_filter='pull_request' returns pr_opened, pr_merged, pr_closed events."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session)
    await musehub_events.record_event(
        db_session, repo_id=repo.repo_id, event_type="pr_opened",
        actor="actuser", description="opened PR",
    )
    await musehub_events.record_event(
        db_session, repo_id=repo.repo_id, event_type="pr_merged",
        actor="actuser", description="merged PR",
    )
    await musehub_events.record_event(
        db_session, repo_id=repo.repo_id, event_type="commit_pushed",
        actor="actuser", description="push noise",
    )
    await db_session.commit()

    result = await musehub_events.list_user_activity(
        db_session, "actuser", type_filter="pull_request"
    )
    assert len(result.events) == 2
    for ev in result.events:
        assert ev.type == "pull_request"


@pytest.mark.anyio
async def test_list_user_activity_type_filter_star_returns_empty(
    db_session: AsyncSession,
) -> None:
    """type_filter='star' returns empty feed (no star events in DB yet)."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session)
    await musehub_events.record_event(
        db_session, repo_id=repo.repo_id, event_type="commit_pushed",
        actor="actuser", description="push",
    )
    await db_session.commit()

    result = await musehub_events.list_user_activity(
        db_session, "actuser", type_filter="star"
    )
    assert result.events == []
    assert result.type_filter == "star"


@pytest.mark.anyio
async def test_list_user_activity_limit_respected(db_session: AsyncSession) -> None:
    """limit parameter caps the number of returned events."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session)
    for i in range(5):
        await musehub_events.record_event(
            db_session, repo_id=repo.repo_id, event_type="commit_pushed",
            actor="actuser", description=f"commit {i}",
        )
    await db_session.commit()

    result = await musehub_events.list_user_activity(
        db_session, "actuser", limit=3
    )
    assert len(result.events) == 3


@pytest.mark.anyio
async def test_list_user_activity_cursor_pagination(db_session: AsyncSession) -> None:
    """before_id cursor returns the next page of events disjoint from the first page."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session)
    for i in range(5):
        await musehub_events.record_event(
            db_session, repo_id=repo.repo_id, event_type="commit_pushed",
            actor="actuser", description=f"commit {i}",
        )
    await db_session.commit()

    page1 = await musehub_events.list_user_activity(db_session, "actuser", limit=3)
    assert len(page1.events) == 3
    assert page1.next_cursor is not None

    page2 = await musehub_events.list_user_activity(
        db_session, "actuser", limit=3, before_id=page1.next_cursor
    )
    assert len(page2.events) == 2

    ids1 = {e.id for e in page1.events}
    ids2 = {e.id for e in page2.events}
    assert ids1.isdisjoint(ids2)


@pytest.mark.anyio
async def test_list_user_activity_next_cursor_none_on_last_page(
    db_session: AsyncSession,
) -> None:
    """next_cursor is None when all events fit within the limit."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session)
    for i in range(2):
        await musehub_events.record_event(
            db_session, repo_id=repo.repo_id, event_type="commit_pushed",
            actor="actuser", description=f"commit {i}",
        )
    await db_session.commit()

    result = await musehub_events.list_user_activity(
        db_session, "actuser", limit=10
    )
    assert len(result.events) == 2
    assert result.next_cursor is None


@pytest.mark.anyio
async def test_list_user_activity_events_newest_first(db_session: AsyncSession) -> None:
    """User activity events are ordered newest-first."""
    await _create_profile(db_session)
    repo = await _create_repo(db_session)
    for i in range(3):
        await musehub_events.record_event(
            db_session, repo_id=repo.repo_id, event_type="commit_pushed",
            actor="actuser", description=f"commit {i}",
        )
    await db_session.commit()

    result = await musehub_events.list_user_activity(db_session, "actuser")
    timestamps = [e.created_at for e in result.events]
    assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# HTTP API tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_user_activity_404_unknown_username(client: AsyncClient) -> None:
    """GET /musehub/users/{username}/activity returns 404 for unknown username."""
    response = await client.get("/api/v1/musehub/users/nobody-xyz/activity")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_user_activity_empty_feed(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET activity returns empty feed for a user with no events."""
    await client.post(
        "/api/v1/musehub/users",
        json={"username": "acttest1"},
        headers=auth_headers,
    )
    response = await client.get("/api/v1/musehub/users/acttest1/activity")
    assert response.status_code == 200
    body = response.json()
    assert body["events"] == []
    assert body["nextCursor"] is None


@pytest.mark.anyio
async def test_get_user_activity_returns_public_events(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET activity returns events from public repos."""
    await client.post(
        "/api/v1/musehub/users",
        json={"username": "acttest2"},
        headers=auth_headers,
    )
    repo_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "act-repo", "owner": "acttest2", "visibility": "public"},
        headers=auth_headers,
    )
    assert repo_resp.status_code == 201
    repo_id = repo_resp.json()["repoId"]

    await musehub_events.record_event(
        db_session, repo_id=repo_id, event_type="commit_pushed",
        actor="acttest2", description="Hello world",
    )
    await db_session.commit()

    response = await client.get("/api/v1/musehub/users/acttest2/activity")
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    ev = body["events"][0]
    assert ev["type"] == "push"
    assert ev["actor"] == "acttest2"
    assert "acttest2/" in ev["repo"]


@pytest.mark.anyio
async def test_get_user_activity_type_filter_via_api(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """?type=issue filter returns only issue-type events."""
    await client.post(
        "/api/v1/musehub/users",
        json={"username": "acttest3"},
        headers=auth_headers,
    )
    repo_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "act-repo3", "owner": "acttest3", "visibility": "public"},
        headers=auth_headers,
    )
    assert repo_resp.status_code == 201
    repo_id = repo_resp.json()["repoId"]

    await musehub_events.record_event(
        db_session, repo_id=repo_id, event_type="commit_pushed",
        actor="acttest3", description="push",
    )
    await musehub_events.record_event(
        db_session, repo_id=repo_id, event_type="issue_opened",
        actor="acttest3", description="opened issue",
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/musehub/users/acttest3/activity?type=issue"
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["type"] == "issue"
    assert body["typeFilter"] == "issue"


@pytest.mark.anyio
async def test_get_user_activity_invalid_type_returns_422(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """?type=invalid returns 422 (query param validation)."""
    await client.post(
        "/api/v1/musehub/users",
        json={"username": "acttest4"},
        headers=auth_headers,
    )
    response = await client.get(
        "/api/v1/musehub/users/acttest4/activity?type=invalid"
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_get_user_activity_limit_param(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """?limit=2 returns at most 2 events."""
    await client.post(
        "/api/v1/musehub/users",
        json={"username": "acttest5"},
        headers=auth_headers,
    )
    repo_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "act-repo5", "owner": "acttest5", "visibility": "public"},
        headers=auth_headers,
    )
    assert repo_resp.status_code == 201
    repo_id = repo_resp.json()["repoId"]

    for i in range(5):
        await musehub_events.record_event(
            db_session, repo_id=repo_id, event_type="commit_pushed",
            actor="acttest5", description=f"commit {i}",
        )
    await db_session.commit()

    response = await client.get(
        "/api/v1/musehub/users/acttest5/activity?limit=2"
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 2
    assert body["nextCursor"] is not None
