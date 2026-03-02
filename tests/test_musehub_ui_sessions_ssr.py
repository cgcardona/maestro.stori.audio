"""SSR tests for the Muse Hub sessions list and session detail pages (issue #573).

Verifies that both ``GET /musehub/ui/{owner}/{repo_slug}/sessions`` and
``GET /musehub/ui/{owner}/{repo_slug}/sessions/{session_id}`` render session
data server-side rather than relying on client-side JavaScript fetches.

Tests:
- test_sessions_list_renders_session_name_server_side
  — Seed a session, GET page, assert session_id present in HTML without JS
- test_sessions_list_active_badge_present
  — Active session → badge with "live" in HTML
- test_sessions_list_htmx_fragment_path
  — GET with HX-Request: true → fragment only (no <html>)
- test_sessions_list_empty_state_when_no_sessions
  — No sessions → empty state rendered server-side
- test_session_detail_renders_session_id
  — GET detail page, assert session metadata in HTML
- test_session_detail_renders_participants
  — Seed participant, assert user_id in HTML
- test_session_detail_unknown_id_404
  — Non-existent session_id → 404
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo, MusehubSession

_OWNER = "composer"
_SLUG = "symphony-no-9"
_USER_ID = "550e8400-e29b-41d4-a716-446655440000"  # matches test_user fixture


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession) -> str:
    """Seed a repo and return its repo_id string."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="public",
        owner_user_id=_USER_ID,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_session(
    db: AsyncSession,
    repo_id: str,
    *,
    is_active: bool = False,
    participants: list[str] | None = None,
    intent: str = "Record the final movement",
    location: str = "Studio A",
    notes: str = "",
    commits: list[str] | None = None,
) -> MusehubSession:
    """Seed a recording session and return the ORM row."""
    session_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    ended_at = None if is_active else started_at
    row = MusehubSession(
        session_id=session_id,
        repo_id=repo_id,
        started_at=started_at,
        ended_at=ended_at,
        participants=participants or [],
        intent=intent,
        location=location,
        notes=notes,
        commits=commits or [],
        is_active=is_active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Sessions list SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sessions_list_renders_session_name_server_side(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Session ID appears in the HTML response without a JS round-trip.

    The handler queries the DB during the request and inlines the session
    identifier into the Jinja2 template so browsers receive a complete page
    on first load.
    """
    repo_id = await _make_repo(db_session)
    row = await _make_session(db_session, repo_id, intent="Compose bridge section")
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/sessions", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert row.session_id[:8] in body
    assert "session-row" in body


@pytest.mark.anyio
async def test_sessions_list_active_badge_present(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Active session renders a live badge in the server-rendered HTML."""
    repo_id = await _make_repo(db_session)
    await _make_session(db_session, repo_id, is_active=True)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/sessions", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "live" in body
    assert "badge-active" in body


@pytest.mark.anyio
async def test_sessions_list_htmx_fragment_path(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """GET with HX-Request: true returns rows fragment, not the full page.

    When HTMX issues a partial swap request the response must NOT contain
    the full page chrome and MUST contain the session row markup.
    """
    repo_id = await _make_repo(db_session)
    row = await _make_session(db_session, repo_id)
    htmx_headers = {**auth_headers, "HX-Request": "true"}
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/sessions", headers=htmx_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert row.session_id[:8] in body
    assert "<!DOCTYPE html>" not in body
    assert "<html" not in body


@pytest.mark.anyio
async def test_sessions_list_empty_state_when_no_sessions(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Empty session list renders an empty-state component server-side (no JS fetch needed)."""
    await _make_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/sessions", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert '<div class="session-row' not in body
    assert "empty-state" in body or "No sessions yet" in body


# ---------------------------------------------------------------------------
# Session detail SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_detail_renders_session_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Session detail page renders the session ID and metadata server-side."""
    repo_id = await _make_repo(db_session)
    row = await _make_session(
        db_session, repo_id, intent="Lay down the horn section", location="Studio B"
    )
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/sessions/{row.session_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.text
    assert row.session_id[:8] in body
    assert "Studio B" in body


@pytest.mark.anyio
async def test_session_detail_renders_participants(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Participant user IDs appear in the session detail HTML response."""
    repo_id = await _make_repo(db_session)
    row = await _make_session(
        db_session, repo_id, participants=["alice", "bob"]
    )
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/sessions/{row.session_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.text
    assert "alice" in body
    assert "bob" in body


@pytest.mark.anyio
async def test_session_detail_unknown_id_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Non-existent session_id returns HTTP 404."""
    await _make_repo(db_session)
    fake_id = str(uuid.uuid4())
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/sessions/{fake_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 404
