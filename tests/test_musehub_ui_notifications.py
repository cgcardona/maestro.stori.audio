"""Tests for the Muse Hub notification inbox UI page (ui_notifications.py).

Covers issue #428 — GET /musehub/ui/notifications:

HTML page (unauthenticated shell):
- test_notifications_page_returns_200_html         — page renders without auth
- test_notifications_page_contains_load_function   — loadNotifications() JS present
- test_notifications_page_contains_filter_controls — type filter and unread-only controls
- test_notifications_page_contains_mark_all_read   — markAllRead() JS function present
- test_notifications_page_contains_mark_one_read   — markOneRead() JS function present
- test_notifications_page_contains_pagination      — renderPagination() JS function present

JSON alternate (authenticated):
- test_notifications_json_requires_auth            — JSON path returns 401 without token
- test_notifications_json_returns_empty_inbox      — authenticated user with no notifs
- test_notifications_json_pagination               — per_page / page respected
- test_notifications_json_type_filter_mention      — type=mention filters by event_type
- test_notifications_json_type_filter_watch        — type=watch filters by event_type
- test_notifications_json_type_filter_fork         — type=fork filters by event_type
- test_notifications_json_unread_only              — unread_only=true excludes read items
- test_notifications_json_mark_one_read_reflected  — read status respected in JSON response
- test_notifications_json_unread_count_global      — unread_count not scoped by type filter
- test_notifications_json_accept_header            — Accept: application/json triggers JSON path
- test_notifications_json_pagination_metadata      — total / total_pages / page in response
- test_notifications_json_empty_state_structure    — empty inbox returns valid schema
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubNotification

_TEST_USER_ID = "550e8400-e29b-41d4-a716-446655440000"
_UI_PATH = "/musehub/ui/notifications"


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _make_notif(
    recipient_id: str,
    event_type: str = "mention",
    is_read: bool = False,
    repo_id: str | None = None,
) -> MusehubNotification:
    return MusehubNotification(
        notif_id=str(uuid.uuid4()),
        recipient_id=recipient_id,
        event_type=event_type,
        repo_id=repo_id or str(uuid.uuid4()),
        actor="some-actor",
        payload={"ref": "main"},
        is_read=is_read,
        created_at=datetime.now(tz=timezone.utc),
    )


async def _seed(db: AsyncSession, *notifs: MusehubNotification) -> None:
    for n in notifs:
        db.add(n)
    await db.commit()


# ---------------------------------------------------------------------------
# HTML page (unauthenticated shell)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_page_returns_200_html(client: AsyncClient) -> None:
    """GET /musehub/ui/notifications returns 200 HTML without auth."""
    resp = await client.get(_UI_PATH)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_notifications_page_contains_load_function(client: AsyncClient) -> None:
    """The HTML shell embeds the loadNotifications() JavaScript function."""
    resp = await client.get(_UI_PATH)
    assert resp.status_code == 200
    assert "loadNotifications" in resp.text


@pytest.mark.anyio
async def test_notifications_page_contains_filter_controls(client: AsyncClient) -> None:
    """The page includes type filter select and unread-only checkbox JS."""
    resp = await client.get(_UI_PATH)
    assert resp.status_code == 200
    assert "type-filter" in resp.text
    assert "unread-filter" in resp.text


@pytest.mark.anyio
async def test_notifications_page_contains_mark_all_read(client: AsyncClient) -> None:
    """The page includes the markAllRead() JS function."""
    resp = await client.get(_UI_PATH)
    assert resp.status_code == 200
    assert "markAllRead" in resp.text


@pytest.mark.anyio
async def test_notifications_page_contains_mark_one_read(client: AsyncClient) -> None:
    """The page includes the markOneRead() JS function."""
    resp = await client.get(_UI_PATH)
    assert resp.status_code == 200
    assert "markOneRead" in resp.text


@pytest.mark.anyio
async def test_notifications_page_contains_pagination(client: AsyncClient) -> None:
    """The page includes renderPagination() for multi-page navigation."""
    resp = await client.get(_UI_PATH)
    assert resp.status_code == 200
    assert "renderPagination" in resp.text


# ---------------------------------------------------------------------------
# JSON alternate — unauthenticated guard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_json_requires_auth(client: AsyncClient) -> None:
    """JSON path returns 401 when no bearer token is provided."""
    resp = await client.get(_UI_PATH, params={"format": "json"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_notifications_json_requires_auth_accept_header(
    client: AsyncClient,
) -> None:
    """JSON path via Accept header also returns 401 without auth."""
    resp = await client.get(
        _UI_PATH, headers={"Accept": "application/json"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# JSON alternate — authenticated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_json_returns_empty_inbox(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Authenticated user with no notifications gets empty inbox."""
    resp = await client.get(
        _UI_PATH, params={"format": "json"}, headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["notifications"] == []
    assert data["total"] == 0
    assert data["unreadCount"] == 0


@pytest.mark.anyio
async def test_notifications_json_empty_state_structure(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Empty inbox JSON response has all required schema fields."""
    resp = await client.get(
        _UI_PATH, params={"format": "json"}, headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    required_fields = {
        "notifications", "total", "page", "perPage",
        "totalPages", "unreadCount", "typeFilter", "unreadOnly",
    }
    assert required_fields <= set(data.keys()), f"Missing fields: {required_fields - set(data.keys())}"


@pytest.mark.anyio
async def test_notifications_json_pagination(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """per_page and page query params are respected; page 2 returns correct slice."""
    await _seed(db_session, *[_make_notif(_TEST_USER_ID) for _ in range(5)])

    resp = await client.get(
        _UI_PATH,
        params={"format": "json", "per_page": 2, "page": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 2
    assert data["perPage"] == 2
    assert len(data["notifications"]) == 2
    assert data["total"] == 5
    assert data["totalPages"] == 3


@pytest.mark.anyio
async def test_notifications_json_pagination_metadata(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """total / total_pages / page metadata is correct on a single-page inbox."""
    await _seed(db_session, _make_notif(_TEST_USER_ID))

    resp = await client.get(
        _UI_PATH, params={"format": "json"}, headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["totalPages"] == 1
    assert data["page"] == 1


@pytest.mark.anyio
async def test_notifications_json_type_filter_mention(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """type=mention returns only mention events."""
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, event_type="mention"),
        _make_notif(_TEST_USER_ID, event_type="fork"),
    )
    resp = await client.get(
        _UI_PATH,
        params={"format": "json", "type": "mention"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert all(n["eventType"] == "mention" for n in data["notifications"])
    assert data["typeFilter"] == "mention"


@pytest.mark.anyio
async def test_notifications_json_type_filter_watch(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """type=watch returns only watch events."""
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, event_type="watch"),
        _make_notif(_TEST_USER_ID, event_type="comment"),
    )
    resp = await client.get(
        _UI_PATH,
        params={"format": "json", "type": "watch"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert all(n["eventType"] == "watch" for n in data["notifications"])


@pytest.mark.anyio
async def test_notifications_json_type_filter_fork(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """type=fork returns only fork events."""
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, event_type="fork"),
        _make_notif(_TEST_USER_ID, event_type="mention"),
    )
    resp = await client.get(
        _UI_PATH,
        params={"format": "json", "type": "fork"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert all(n["eventType"] == "fork" for n in data["notifications"])


@pytest.mark.anyio
async def test_notifications_json_unread_only(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """unread_only=true excludes already-read notifications."""
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, is_read=False),
        _make_notif(_TEST_USER_ID, is_read=True),
        _make_notif(_TEST_USER_ID, is_read=False),
    )
    resp = await client.get(
        _UI_PATH,
        params={"format": "json", "unread_only": "true"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(not n["isRead"] for n in data["notifications"])
    assert data["unreadOnly"] is True


@pytest.mark.anyio
async def test_notifications_json_mark_one_read_reflected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Notification seeded as is_read=True appears with isRead=true in JSON."""
    await _seed(db_session, _make_notif(_TEST_USER_ID, is_read=True))

    resp = await client.get(
        _UI_PATH, params={"format": "json"}, headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["notifications"]) == 1
    assert data["notifications"][0]["isRead"] is True


@pytest.mark.anyio
async def test_notifications_json_unread_count_global(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """unread_count is the global count, not scoped by the active type filter."""
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, event_type="mention", is_read=False),
        _make_notif(_TEST_USER_ID, event_type="fork", is_read=False),
    )
    # Filter to mention only — but unread_count must reflect BOTH unread items.
    resp = await client.get(
        _UI_PATH,
        params={"format": "json", "type": "mention"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["unreadCount"] == 2


@pytest.mark.anyio
async def test_notifications_json_accept_header(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Accept: application/json triggers the JSON response path."""
    headers = {**auth_headers, "Accept": "application/json"}
    resp = await client.get(_UI_PATH, headers=headers)
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    assert "notifications" in data


@pytest.mark.anyio
async def test_notifications_json_only_own_notifications(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Notifications for other users are not returned in the inbox."""
    other_user_id = str(uuid.uuid4())
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID),
        _make_notif(other_user_id),   # belongs to a different user
    )
    resp = await client.get(
        _UI_PATH, params={"format": "json"}, headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
