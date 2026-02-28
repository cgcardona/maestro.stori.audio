"""Tests for Muse Hub web UI endpoints.

Covers the minimum acceptance criteria from issue #43 and #240:
- test_ui_repo_page_returns_200        — GET /musehub/ui/{repo_id} returns HTML
- test_ui_commit_page_shows_artifact_links — commit page HTML mentions img/download
- test_ui_pr_list_page_returns_200     — PR list page renders without error
- test_ui_issue_list_page_returns_200  — Issue list page renders without error
- test_session_detail_renders          — session detail page returns 200 HTML
- test_session_detail_participants     — session detail page includes participant section
- test_session_detail_commits         — session detail page includes commits section
- test_session_detail_404             — unknown session returns page with error marker
- test_session_detail_json            — JSON API returns full session data

UI routes require no JWT auth (they return HTML shells whose JS handles auth).
The HTML content tests assert structural markers present in every rendered page.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubRepo, MusehubSession


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
# Session UI and API tests (issue #240)
# ---------------------------------------------------------------------------

_UTC = timezone.utc


async def _make_session(db_session: AsyncSession, repo_id: str) -> str:
    """Seed a completed session record and return its session_id."""
    session_id = "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb"
    session = MusehubSession(
        session_id=session_id,
        repo_id=repo_id,
        schema_version="1",
        started_at=datetime(2026, 1, 15, 10, 0, 0, tzinfo=_UTC),
        ended_at=datetime(2026, 1, 15, 12, 30, 0, tzinfo=_UTC),
        participants=["Alice", "Bob"],
        location="Studio A",
        intent="Record the main groove track for track 3",
        commits=["abc123def456", "789000aabbcc"],
        notes="Great session — kept the second take. Alice nailed the bass line.",
    )
    db_session.add(session)
    await db_session.commit()
    return session_id


@pytest.mark.anyio
async def test_session_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/sessions returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/sessions")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Sessions" in body
    assert "localStorage" in body


@pytest.mark.anyio
async def test_session_detail_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/sessions/{session_id} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    session_id = "some-session-uuid-1234"
    response = await client.get(f"/musehub/ui/{repo_id}/sessions/{session_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Recording Session" in body
    assert session_id[:8] in body


@pytest.mark.anyio
async def test_session_detail_participants(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page HTML includes the Participants section."""
    repo_id = await _make_repo(db_session)
    session_id = "participant-session-5678"
    response = await client.get(f"/musehub/ui/{repo_id}/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "Participants" in body


@pytest.mark.anyio
async def test_session_detail_commits(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page HTML includes the Commits section."""
    repo_id = await _make_repo(db_session)
    session_id = "commits-session-9012"
    response = await client.get(f"/musehub/ui/{repo_id}/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    assert "Commits" in body


@pytest.mark.anyio
async def test_session_detail_404_marker(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Session detail page renders a 404 error message for unknown session IDs.

    The page itself returns 200 (HTML shell) — the 404 is detected client-side
    when the JS calls the JSON API.  The page must include error-handling JS that
    checks for a 404 response and shows a user-friendly message.
    """
    repo_id = await _make_repo(db_session)
    session_id = "does-not-exist-1234"
    response = await client.get(f"/musehub/ui/{repo_id}/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    # The JS error handler must check for a 404 and render a "not found" message
    assert "Session not found" in body or "404" in body


@pytest.mark.anyio
async def test_session_detail_json(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions/{session_id} returns full session JSON."""
    repo_id = await _make_repo(db_session)
    session_id = await _make_session(db_session, repo_id)

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions/{session_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sessionId"] == session_id
    assert data["repoId"] == repo_id
    assert data["participants"] == ["Alice", "Bob"]
    assert data["location"] == "Studio A"
    assert data["intent"] == "Record the main groove track for track 3"
    assert len(data["commits"]) == 2
    assert "nailed the bass line" in data["notes"]


@pytest.mark.anyio
async def test_session_list_json_returns_sessions(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions returns list with pushed sessions."""
    repo_id = await _make_repo(db_session)
    await _make_session(db_session, repo_id)

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["participants"] == ["Alice", "Bob"]


@pytest.mark.anyio
async def test_session_list_json_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/sessions")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_session_detail_json_404_unknown_session(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/sessions/{unknown} returns 404."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions/does-not-exist",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_session_push_creates_session(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/repos/{repo_id}/sessions creates a session and returns 201."""
    repo_id = await _make_repo(db_session)
    payload = {
        "sessionId": "new-session-uuid-abcd",
        "schemaVersion": "1",
        "startedAt": "2026-02-01T09:00:00+00:00",
        "endedAt": "2026-02-01T11:30:00+00:00",
        "participants": ["Carol", "Dave"],
        "location": "Home Studio",
        "intent": "Finalize bridge section",
        "commits": [],
        "notes": "",
    }
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["sessionId"] == "new-session-uuid-abcd"
    assert data["participants"] == ["Carol", "Dave"]


@pytest.mark.anyio
async def test_session_push_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Re-pushing the same session_id updates the record rather than duplicating it."""
    repo_id = await _make_repo(db_session)
    payload = {
        "sessionId": "idempotent-session-uuid",
        "schemaVersion": "1",
        "startedAt": "2026-02-10T14:00:00+00:00",
        "endedAt": "2026-02-10T16:00:00+00:00",
        "participants": ["Eve"],
        "location": "Studio B",
        "intent": "Initial intent",
        "commits": [],
        "notes": "First push notes",
    }
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        json=payload,
        headers=auth_headers,
    )

    # Re-push with updated notes
    payload["notes"] = "Updated notes after re-push"
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["notes"] == "Updated notes after re-push"

    # Verify no duplicate was created
    list_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/sessions",
        headers=auth_headers,
    )
    assert list_resp.json()["total"] == 1


@pytest.mark.anyio
async def test_session_upsert_scoped_to_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Session upsert lookup is scoped to repo_id — a session pushed to repo A is NOT
    visible via repo B's detail endpoint, even if the IDs match."""
    repo_a = await _make_repo(db_session)
    repo_b = await _make_repo(db_session)
    payload = {
        "sessionId": "scoped-uuid-aaaa-bbbb-cccc-ddddeeeeeeee",
        "schemaVersion": "1",
        "startedAt": "2026-03-01T10:00:00+00:00",
        "endedAt": "2026-03-01T12:00:00+00:00",
        "participants": ["Frank"],
        "location": "Studio C",
        "intent": "Test repo scoping",
        "commits": [],
        "notes": "repo A only",
    }
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_a}/sessions",
        json=payload,
        headers=auth_headers,
    )
    assert resp.status_code == 201

    # The same session_id does NOT appear under repo B
    detail_b = await client.get(
        f"/api/v1/musehub/repos/{repo_b}/sessions/{payload['sessionId']}",
        headers=auth_headers,
    )
    assert detail_b.status_code == 404

    # And repo A's list is unaffected
    list_a = await client.get(
        f"/api/v1/musehub/repos/{repo_a}/sessions",
        headers=auth_headers,
    )
    assert list_a.json()["total"] == 1
    assert list_a.json()["sessions"][0]["notes"] == "repo A only"


@pytest.mark.anyio
async def test_session_repo_page_has_sessions_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The repo landing page includes a navigation link to Sessions.

    The Sessions button is rendered inside a JavaScript template literal so the
    actual href uses the JS variable ``${base}/sessions``.  We assert the text
    label and the path fragment are both present in the page source.
    """
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}")
    assert response.status_code == 200
    body = response.text
    assert "Sessions" in body
    assert "/sessions" in body



# ---------------------------------------------------------------------------
# Context viewer tests (issue #232)
# ---------------------------------------------------------------------------

_FIXED_COMMIT_ID = "aabbccdd" * 8  # 64-char hex string


async def _make_repo_with_commit(db_session: AsyncSession) -> tuple[str, str]:
    """Seed a repo with one commit and return (repo_id, commit_id)."""
    repo = MusehubRepo(
        name="jazz-context-test",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    commit = MusehubCommit(
        commit_id=_FIXED_COMMIT_ID,
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="Add bass and drums",
        author="test-musician",
        timestamp=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(commit)
    await db_session.commit()
    return repo_id, _FIXED_COMMIT_ID


@pytest.mark.anyio
async def test_context_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/context/{ref} returns 200 HTML without auth."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/context/{commit_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "What the Agent Sees" in body
    assert commit_id[:8] in body


@pytest.mark.anyio
async def test_context_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/context/{ref} returns MuseHubContextResponse."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context/{commit_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["repoId"] == repo_id
    assert body["currentBranch"] == "main"
    assert "headCommit" in body
    assert body["headCommit"]["commitId"] == commit_id
    assert body["headCommit"]["author"] == "test-musician"
    assert "musicalState" in body
    assert "history" in body
    assert "missingElements" in body
    assert "suggestions" in body


@pytest.mark.anyio
async def test_context_includes_musical_state(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Context response includes musicalState with an activeTracks field."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context/{commit_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    musical_state = response.json()["musicalState"]
    assert "activeTracks" in musical_state
    assert isinstance(musical_state["activeTracks"], list)
    # Dimensions requiring MIDI analysis are None at this stage
    assert musical_state["key"] is None
    assert musical_state["tempoBpm"] is None


@pytest.mark.anyio
async def test_context_unknown_ref_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/context/{ref} returns 404 for unknown ref."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context/deadbeef" + "0" * 56,
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_context_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/context/{ref} returns 404 for unknown repo."""
    response = await client.get(
        "/api/v1/musehub/repos/ghost-repo/context/deadbeef" + "0" * 56,
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_context_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/context/{ref} returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/context/deadbeef" + "0" * 56,
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_context_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The context UI page must be accessible without a JWT (HTML shell handles auth)."""
    repo_id, commit_id = await _make_repo_with_commit(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/context/{commit_id}")
    assert response.status_code != 401
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Embed player route tests (issue #244)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embed_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/embed/{ref} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/{repo_id}/embed/{ref}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_embed_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Embed page must be accessible without an Authorization header (public embedding)."""
    repo_id = await _make_repo(db_session)
    ref = "deadbeef1234"
    response = await client.get(f"/musehub/ui/{repo_id}/embed/{ref}")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_embed_page_x_frame_options(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Embed page must set X-Frame-Options: ALLOWALL to permit cross-origin framing."""
    repo_id = await _make_repo(db_session)
    ref = "cafebabe1234"
    response = await client.get(f"/musehub/ui/{repo_id}/embed/{ref}")
    assert response.status_code == 200
    assert response.headers.get("x-frame-options") == "ALLOWALL"


@pytest.mark.anyio
async def test_embed_page_contains_player_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Embed page HTML must contain player elements: play button, progress bar, and Muse Hub link."""
    repo_id = await _make_repo(db_session)
    ref = "feedface0123456789ab"
    response = await client.get(f"/musehub/ui/{repo_id}/embed/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "play-btn" in body
    assert "progress-bar" in body
    assert "View on Muse Hub" in body
    assert "audio" in body
    assert repo_id in body
