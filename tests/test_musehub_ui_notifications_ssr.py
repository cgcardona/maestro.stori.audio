"""SSR tests for the Muse Hub notification inbox (issue #559).

Verifies that ``GET /musehub/ui/notifications`` renders notifications
server-side rather than relying on client-side JavaScript fetches.

Tests:
- test_notifications_page_unauthenticated_renders_login_prompt
  — GET without token → login prompt in the HTML response
- test_notifications_page_renders_notification_server_side
  — Seed a notification, GET with auth → actor name present in HTML
- test_notifications_filter_type_narrows_results
  — GET ?type_filter=issue seeds two types, only the matching type appears
- test_notifications_unread_only_filter
  — GET ?unread_only=true returns only unread rows in HTML
- test_notifications_htmx_request_returns_fragment
  — GET with HX-Request: true header → fragment only (no full page chrome)
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
    actor: str = "testactor",
    repo_id: str | None = None,
) -> MusehubNotification:
    """Create an unsaved MusehubNotification ORM object for seeding."""
    return MusehubNotification(
        notif_id=str(uuid.uuid4()),
        recipient_id=recipient_id,
        event_type=event_type,
        repo_id=repo_id or str(uuid.uuid4()),
        actor=actor,
        payload={"description": f"{event_type} event"},
        is_read=is_read,
        created_at=datetime.now(tz=timezone.utc),
    )


async def _seed(db: AsyncSession, *notifs: MusehubNotification) -> None:
    """Persist notification rows and flush the session."""
    for n in notifs:
        db.add(n)
    await db.commit()


# ---------------------------------------------------------------------------
# SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_page_unauthenticated_renders_login_prompt(
    client: AsyncClient,
) -> None:
    """GET without a JWT renders a login prompt, not a data-fetching JS shell.

    The SSR handler detects a missing token and returns a page that invites
    the user to sign in rather than loading a skeleton that calls back to the
    API via JavaScript.
    """
    resp = await client.get(_UI_PATH)
    assert resp.status_code == 200
    body = resp.text
    # Must mention signing in — unauthenticated prompt
    assert "Sign in" in body or "sign in" in body or "login" in body.lower() or "notifications appear" in body.lower()
    # Must NOT contain server-rendered notification rows (no data was fetched)
    assert "notification-row" not in body


@pytest.mark.anyio
async def test_notifications_page_renders_notification_server_side(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Seeded notification actor appears in the HTML without a JS round-trip.

    The SSR handler queries the DB during the request and inlines the actor
    name directly into the response HTML so the browser receives a complete
    page on the first load.
    """
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, actor="alice", event_type="mention"),
    )
    resp = await client.get(_UI_PATH, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.text
    assert "alice" in body
    assert "notification-row" in body


@pytest.mark.anyio
async def test_notifications_filter_type_narrows_results(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET ?type_filter=fork shows only fork notifications in the rendered HTML.

    The filter is applied server-side during the SSR render; the HTML must
    contain the fork notification's actor and must NOT contain the mention
    actor that was excluded by the filter.
    """
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, event_type="fork", actor="forkuser"),
        _make_notif(_TEST_USER_ID, event_type="mention", actor="mentionuser"),
    )
    resp = await client.get(
        _UI_PATH, params={"type_filter": "fork"}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "forkuser" in body
    assert "mentionuser" not in body


@pytest.mark.anyio
async def test_notifications_unread_only_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET ?unread_only=true renders only unread notification rows.

    The unread_only flag is evaluated server-side so the HTML contains exactly
    the unread notification actor and omits the read one.  Actor names are chosen
    to avoid substring collisions (e.g. "readactor" inside "unreadactor").
    """
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, is_read=False, actor="alice-unread"),
        _make_notif(_TEST_USER_ID, is_read=True, actor="bob-isread"),
    )
    resp = await client.get(
        _UI_PATH, params={"unread_only": "true"}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "alice-unread" in body
    assert "bob-isread" not in body


@pytest.mark.anyio
async def test_notifications_htmx_request_returns_fragment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET with HX-Request: true returns the rows fragment, not the full page.

    When HTMX issues a filter swap request it sends this header.  The response
    must NOT contain the full page chrome (nav bar, breadcrumb) and MUST
    contain the notification rows markup.
    """
    await _seed(
        db_session,
        _make_notif(_TEST_USER_ID, actor="htmxactor"),
    )
    htmx_headers = {**auth_headers, "HX-Request": "true"}
    resp = await client.get(_UI_PATH, headers=htmx_headers)
    assert resp.status_code == 200
    body = resp.text
    # Fragment should contain notification data
    assert "htmxactor" in body
    # Full page chrome (nav, base layout) should be absent in the fragment
    assert "<!DOCTYPE html>" not in body
    assert "<html" not in body
