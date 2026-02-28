"""Tests for Muse Hub web UI endpoints.

Covers the minimum acceptance criteria from issue #43 and #239:
- test_ui_repo_page_returns_200        — GET /musehub/ui/{repo_id} returns HTML
- test_ui_commit_page_shows_artifact_links — commit page HTML mentions img/download
- test_ui_pr_list_page_returns_200     — PR list page renders without error
- test_ui_issue_list_page_returns_200  — Issue list page renders without error
- test_sessions_page_renders           — GET /musehub/ui/{repo_id}/sessions returns 200
- test_sessions_newest_first           — JSON endpoint orders sessions newest-first
- test_sessions_json_response          — JSON returns session list with metadata

UI routes require no JWT auth (they return HTML shells whose JS handles auth).
The HTML content tests assert structural markers present in every rendered page.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo, MusehubSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db_session: AsyncSession) -> str:
    """Seed a minimal repo and return its repo_id."""
    repo = MusehubRepo(
        name="test-beats",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


# ---------------------------------------------------------------------------
# UI route tests (no auth required — routes return HTML)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ui_repo_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id} returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # Verify shared chrome is present
    assert "Muse Hub" in body
    assert repo_id[:8] in body
    # Verify page-specific JS is injected
    assert "branch-sel" in body or "All branches" in body


@pytest.mark.anyio
async def test_ui_commit_page_shows_artifact_links(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/commits/{commit_id} returns HTML with img/download markers."""
    repo_id = await _make_repo(db_session)
    commit_id = "abc1234567890abcdef1234567890abcdef12345678"
    response = await client.get(f"/musehub/ui/{repo_id}/commits/{commit_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # The JS function that renders artifacts must be in the page
    assert "artifactHtml" in body
    # Inline img pattern for .webp artifacts
    assert "<img" in body
    # Download pattern for .mid and other binary artifacts
    assert "Download" in body
    # Audio player pattern
    assert "<audio" in body


@pytest.mark.anyio
async def test_ui_pr_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/pulls returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/pulls")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Pull Requests" in body
    assert "Muse Hub" in body
    # State filter select element must be present in the JS
    assert "state" in body


@pytest.mark.anyio
async def test_ui_issue_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/issues returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/issues")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Issues" in body
    assert "Muse Hub" in body


@pytest.mark.anyio
async def test_ui_pr_detail_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/pulls/{pr_id} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    pr_id = "some-pr-uuid-1234"
    response = await client.get(f"/musehub/ui/{repo_id}/pulls/{pr_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Merge pull request" in body


@pytest.mark.anyio
async def test_ui_issue_detail_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/issues/{number} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/issues/1")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Close issue" in body


@pytest.mark.anyio
async def test_ui_repo_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """UI routes must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}")
    # Must NOT return 401 — HTML shell has no auth requirement
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_ui_pages_include_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Every UI page embeds the JWT token input form so unauthenticated visitors can sign in."""
    repo_id = await _make_repo(db_session)
    for path in [
        f"/musehub/ui/{repo_id}",
        f"/musehub/ui/{repo_id}/pulls",
        f"/musehub/ui/{repo_id}/issues",
    ]:
        response = await client.get(path)
        assert response.status_code == 200
        body = response.text
        # The JS setToken / getToken helpers must be present
        assert "localStorage" in body
        assert "musehub_token" in body


# ---------------------------------------------------------------------------
# Object listing endpoint tests (JSON, authed)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_objects_returns_empty_for_new_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/objects returns empty list for new repo."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/objects",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["objects"] == []


@pytest.mark.anyio
async def test_list_objects_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/objects returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/objects")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_list_objects_404_for_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/objects returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/objects",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_object_content_404_for_unknown_object(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/objects/{unknown}/content returns 404."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/objects/sha256:notexist/content",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Session log — UI page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sessions_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/sessions returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/sessions")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Sessions" in body
    assert "Muse Hub" in body
    # The live indicator CSS must be present
    assert "session-live" in body
    # Breadcrumb back link must render the repo ID prefix
    assert repo_id[:8] in body


@pytest.mark.anyio
async def test_sessions_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sessions UI page must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/sessions")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_sessions_page_includes_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sessions page must embed the JWT token form so unauthenticated visitors can sign in."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/sessions")
    assert response.status_code == 200
    body = response.text
    assert "localStorage" in body
    assert "musehub_token" in body


# ---------------------------------------------------------------------------
# Session log — JSON API tests
# ---------------------------------------------------------------------------


async def _make_session(
    db_session: AsyncSession,
    repo_id: str,
    *,
    started_offset_seconds: int = 0,
    is_active: bool = False,
    intent: str = "jazz composition",
    participants: list[str] | None = None,
) -> str:
    """Seed a MusehubSession and return its session_id."""
    start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    from datetime import timedelta

    started_at = start + timedelta(seconds=started_offset_seconds)
    ended_at = None if is_active else started_at + timedelta(hours=1)
    row = MusehubSession(
        repo_id=repo_id,
        started_at=started_at,
        ended_at=ended_at,
        participants=participants or ["producer-a"],
        intent=intent,
        location="Studio A",
        is_active=is_active,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return str(row.session_id)


@pytest.mark.anyio
async def test_sessions_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions returns session list with metadata."""
    repo_id = await _make_repo(db_session)
    session_id = await _make_session(db_session, repo_id, intent="jazz solo")

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert "total" in data
    assert data["total"] == 1
    sess = data["sessions"][0]
    assert sess["sessionId"] == session_id
    assert sess["intent"] == "jazz solo"
    assert sess["location"] == "Studio A"
    assert sess["isActive"] is False
    assert sess["durationSeconds"] == pytest.approx(3600.0)


@pytest.mark.anyio
async def test_sessions_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Sessions are returned newest-first (active sessions appear before ended sessions)."""
    repo_id = await _make_repo(db_session)
    # older ended session
    await _make_session(db_session, repo_id, started_offset_seconds=0, intent="older")
    # newer ended session
    await _make_session(db_session, repo_id, started_offset_seconds=3600, intent="newer")
    # active session (should surface first regardless of time)
    await _make_session(
        db_session, repo_id, started_offset_seconds=100, is_active=True, intent="live"
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 3
    # Active session must come first
    assert sessions[0]["isActive"] is True
    assert sessions[0]["intent"] == "live"
    # Then newest ended session
    assert sessions[1]["intent"] == "newer"
    assert sessions[2]["intent"] == "older"


@pytest.mark.anyio
async def test_sessions_empty_for_new_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions returns empty list for new repo."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sessions"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_sessions_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/sessions")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_sessions_404_for_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/sessions returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_create_session_returns_201(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/repos/{repo_id}/sessions creates a session and returns 201."""
    repo_id = await _make_repo(db_session)
    payload = {
        "participants": ["producer-a", "collab-b"],
        "intent": "house beat experiment",
        "location": "Remote – Berlin",
        "isActive": True,
    }
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["isActive"] is True
    assert data["intent"] == "house beat experiment"
    assert data["location"] == "Remote \u2013 Berlin"
    assert data["participants"] == ["producer-a", "collab-b"]
    assert "sessionId" in data


@pytest.mark.anyio
async def test_stop_session_marks_ended(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/repos/{repo_id}/sessions/{session_id}/stop closes a live session."""
    repo_id = await _make_repo(db_session)
    session_id = await _make_session(db_session, repo_id, is_active=True)

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/sessions/{session_id}/stop",
        json={},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["isActive"] is False
    assert data["endedAt"] is not None
    assert data["durationSeconds"] is not None


@pytest.mark.anyio
async def test_active_session_has_null_duration(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Active sessions must have durationSeconds=null (session still in progress)."""
    repo_id = await _make_repo(db_session)
    await _make_session(db_session, repo_id, is_active=True)

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    sess = response.json()["sessions"][0]
    assert sess["isActive"] is True
    assert sess["durationSeconds"] is None
