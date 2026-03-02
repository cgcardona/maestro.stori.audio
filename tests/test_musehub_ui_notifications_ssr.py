"""SSR-specific tests for the MuseHub notification inbox (ui_notifications.py).

Covers the SSR migration at GET /musehub/ui/notifications:

- test_notifications_page_unauthenticated_renders_login_prompt — login prompt rendered without token
- test_notifications_page_renders_notification_server_side — authenticated GET includes notif body
- test_notifications_filter_type_narrows_results — ?type_filter=issue returns only issue notifs
- test_notifications_unread_only_filter — ?unread_only=true returns only unread notifs
- test_notifications_htmx_request_returns_fragment — HX-Request: true → fragment only (no <html>)
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
    actor: str = "test-actor",
    repo_id: str | None = None,
) -> MusehubNotification:
    return MusehubNotification(
        notif_id=str(uuid.uuid4()),
        recipient_id=recipient_id,
        event_type=event_type,
        repo_id=repo_id or str(uuid.uuid4()),
        actor=actor,
        payload={"description": f"did {event_type}"},
        is_read=is_read,
        created_at=datetime.now(tz=timezone.utc),
    )


async def _seed(db: AsyncSession, *notifs: MusehubNotification) -> None:
    for n in notifs:
        db.add(n)
    await db.commit()


# ---------------------------------------------------------------------------
# SSR — unauthenticated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_page_unauthenticated_renders_login_prompt(
    client: AsyncClient,
) -> None:
    """GET without token renders SSR login prompt (no data fetch, no JS shell)."""
    resp = await client.get(_UI_PATH)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Sign in to see notifications" in resp.text
    # Must NOT render the filter form or notification rows — those are auth-gated
    assert "notification-rows" not in resp.text
    assert "hx-get" not in resp.text


# ---------------------------------------------------------------------------
# SSR — authenticated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_page_renders_notification_server_side(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Authenticated GET renders a seeded notification body in the HTML response."""
    await _seed(db_session, _make_notif(_TEST_USER_ID, actor="alice", event_type="mention"))

    resp = await client.get(_UI_PATH, headers=auth_headers)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # The actor name and event type must appear in the SSR output
    assert "alice" in resp.text
    assert "mention" in resp.text


@pytest.mark.anyio
async def test_notifications_filter_type_narrows_results(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?type_filter=issue renders only issue-type notifications in the HTML."""
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, event_type="issue_opened", actor="bob"),
        _make_notif(_TEST_USER_ID, event_type="fork", actor="carol"),
    )

    resp = await client.get(
        _UI_PATH, params={"type_filter": "issue_opened"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert "bob" in resp.text
    assert "carol" not in resp.text


@pytest.mark.anyio
async def test_notifications_unread_only_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?unread_only=true renders only unread notifications."""
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, is_read=False, actor="alice-unread"),
        _make_notif(_TEST_USER_ID, is_read=True, actor="bob-already-read"),
    )

    resp = await client.get(
        _UI_PATH, params={"unread_only": "true"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert "alice-unread" in resp.text
    assert "bob-already-read" not in resp.text


@pytest.mark.anyio
async def test_notifications_htmx_request_returns_fragment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """HX-Request: true causes the handler to return only the fragment, not the full page."""
    await _seed(db_session, _make_notif(_TEST_USER_ID, actor="frag-actor"))

    htmx_headers = {**auth_headers, "HX-Request": "true"}
    resp = await client.get(_UI_PATH, headers=htmx_headers)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # Fragment must NOT include full-page chrome
    assert "<html" not in resp.text
    assert "<head" not in resp.text
    # But must include notification content
    assert "frag-actor" in resp.text
