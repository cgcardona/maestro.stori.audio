"""Tests for Muse Hub web UI endpoints.

Covers the minimum acceptance criteria from issue #43 and issue #232:
- test_ui_repo_page_returns_200        — GET /musehub/ui/{repo_id} returns HTML
- test_ui_commit_page_shows_artifact_links — commit page HTML mentions img/download
- test_ui_pr_list_page_returns_200     — PR list page renders without error
- test_ui_issue_list_page_returns_200  — Issue list page renders without error
- test_context_page_renders            — context viewer page returns 200 HTML
- test_context_json_response           — JSON returns MuseHubContextResponse structure
- test_context_includes_musical_state  — response includes active_tracks field
- test_context_unknown_ref_404         — nonexistent ref returns 404

Covers acceptance criteria from issue #244 (embed player):
- test_embed_page_renders              — GET /musehub/ui/{repo_id}/embed/{ref} returns 200
- test_embed_no_auth_required          — Public embed accessible without JWT
- test_embed_page_x_frame_options      — Response sets X-Frame-Options: ALLOWALL
- test_embed_page_contains_player_ui   — Player elements present in embed HTML

UI routes require no JWT auth (they return HTML shells whose JS handles auth).
The HTML content tests assert structural markers present in every rendered page.

Covers regression for PR #282 (owner/slug URL scheme):
- test_ui_nav_links_use_owner_slug_not_uuid_*  — every page handler injects
  ``const base = '/musehub/ui/{owner}/{slug}'`` not a UUID-based path.
- test_ui_unknown_owner_slug_returns_404        — bad owner/slug → 404.

Covers issue #221 (analysis dashboard):
- test_analysis_dashboard_renders               — GET /musehub/ui/{owner}/{slug}/analysis/{ref} returns 200
- test_analysis_dashboard_no_auth_required      — accessible without JWT
- test_analysis_dashboard_all_dimension_labels  — 10 dimension labels present in HTML
- test_analysis_dashboard_sparkline_logic_present — sparkline JS present
- test_analysis_dashboard_card_links_to_dimensions — /analysis/ path in page
See also test_musehub_analysis.py::test_analysis_aggregate_endpoint_returns_all_dimensions
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubProfile, MusehubRepo, MusehubSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db_session: AsyncSession) -> str:
    """Seed a minimal repo and return its repo_id."""
    repo = MusehubRepo(
        name="test-beats",
        owner="testuser",
        slug="test-beats",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


_TEST_USER_ID = "550e8400-e29b-41d4-a716-446655440000"


async def _make_profile(db_session: AsyncSession, username: str = "testmusician") -> MusehubProfile:
    """Seed a minimal profile and return it."""
    profile = MusehubProfile(
        user_id=_TEST_USER_ID,
        username=username,
        bio="Test bio",
        avatar_url=None,
        pinned_repo_ids=[],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _make_public_repo(db_session: AsyncSession) -> str:
    """Seed a public repo for the test user and return its repo_id."""
    repo = MusehubRepo(
        name="public-beats",
        owner="testuser",
        slug="public-beats",
        visibility="public",
        owner_user_id=_TEST_USER_ID,
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
    response = await client.get("/musehub/ui/testuser/test-beats")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
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
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
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
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/pulls/{pr_id}")
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
    response = await client.get("/musehub/ui/testuser/test-beats/issues/1")
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
    response = await client.get("/musehub/ui/testuser/test-beats")
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
        "/musehub/ui/testuser/test-beats",
        "/musehub/ui/testuser/test-beats/pulls",
        "/musehub/ui/testuser/test-beats/issues",
        "/musehub/ui/testuser/test-beats/releases",
    ]:
        response = await client.get(path)
        assert response.status_code == 200
        body = response.text
        # musehub.js (which contains localStorage helpers) must be loaded
        assert "musehub/static/musehub.js" in body
        assert "token-form" in body


@pytest.mark.anyio
async def test_ui_release_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/releases returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Releases" in body
    assert "Muse Hub" in body
    assert repo_id[:8] in body


@pytest.mark.anyio
async def test_ui_release_detail_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/releases/{tag} returns 200 HTML with download section."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases/v1.0")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Download" in body
    assert "v1.0" in body


@pytest.mark.anyio
async def test_ui_repo_page_shows_releases_button(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id} includes a Releases navigation button."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "releases" in body.lower()


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
# Credits UI page tests (issue #241)
# DAG graph UI page tests (issue #229)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_credits_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/credits returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Credits" in body


@pytest.mark.anyio


async def test_graph_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/graph returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "graph" in body.lower()


# ---------------------------------------------------------------------------
# Context viewer tests (issue #232)
# ---------------------------------------------------------------------------

_FIXED_COMMIT_ID = "aabbccdd" * 8  # 64-char hex string


async def _make_repo_with_commit(db_session: AsyncSession) -> tuple[str, str]:
    """Seed a repo with one commit and return (repo_id, commit_id)."""
    repo = MusehubRepo(
        name="jazz-context-test",
        owner="testuser",
        slug="jazz-context-test",
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
    response = await client.get(f"/musehub/ui/testuser/jazz-context-test/context/{commit_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "context" in body.lower()
    assert repo_id[:8] in body




@pytest.mark.anyio
async def test_credits_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/credits returns JSON with required fields."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "repoId" in body
    assert "contributors" in body
    assert "sort" in body
    assert "totalContributors" in body
    assert body["repoId"] == repo_id
    assert isinstance(body["contributors"], list)
    assert body["sort"] == "count"



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
    assert "repoId" in body

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
async def test_credits_empty_state_json(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Repo with no commits returns empty contributors list and totalContributors=0."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributors"] == []
    assert body["totalContributors"] == 0


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
    response = await client.get(f"/musehub/ui/testuser/jazz-context-test/context/{commit_id}")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/embed/{ref}")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/embed/{ref}")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/embed/{ref}")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/embed/{ref}")
    assert response.status_code == 200
    body = response.text
    assert "play-btn" in body
    assert "progress-bar" in body
    assert "View on Muse Hub" in body
    assert "audio" in body
    assert repo_id in body

# ---------------------------------------------------------------------------
# Groove check page and endpoint tests (issue #226)

# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_groove_check_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/groove-check returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Groove Check" in body


@pytest.mark.anyio
async def test_credits_page_contains_json_ld_injection(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page embeds JSON-LD injection logic for machine-readable attribution."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "application/ld+json" in body
    assert "schema.org" in body
    assert "MusicComposition" in body


@pytest.mark.anyio
async def test_credits_page_contains_sort_options(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page includes sort dropdown with count, recency, and alpha options."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "Most prolific" in body
    assert "Most recent" in body
    assert "A" in body  # "A – Z" option


@pytest.mark.anyio
async def test_credits_empty_state_message_in_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page JS includes empty-state message for repos with no sessions."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    body = response.text
    assert "muse session start" in body


@pytest.mark.anyio
async def test_credits_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page must be accessible without an Authorization header (HTML shell)."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/credits")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_groove_check_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check UI page must be accessible without an Authorization header (HTML shell)."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code != 401
    assert response.status_code == 200



# ---------------------------------------------------------------------------
# Object listing endpoint tests (JSON, authed)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_groove_check_page_contains_chart_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check page embeds the SVG chart rendering JavaScript."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code == 200
    body = response.text
    assert "renderGrooveChart" in body
    assert "grooveScore" in body
    assert "driftDelta" in body


@pytest.mark.anyio
async def test_groove_check_page_contains_status_badges(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check page HTML includes OK / WARN / FAIL status badge rendering."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code == 200
    body = response.text
    assert "statusBadge" in body
    assert "WARN" in body
    assert "FAIL" in body


@pytest.mark.anyio
async def test_groove_check_page_includes_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check page embeds the JWT token input form so visitors can authenticate."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/groove-check")
    assert response.status_code == 200
    body = response.text
    assert "token-form" in body
    assert "token-input" in body


@pytest.mark.anyio
async def test_groove_check_endpoint_returns_json(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/groove-check returns JSON with required fields."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/groove-check",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "commitRange" in body
    assert "threshold" in body
    assert "totalCommits" in body
    assert "flaggedCommits" in body
    assert "worstCommit" in body
    assert "entries" in body
    assert isinstance(body["entries"], list)


@pytest.mark.anyio
async def test_graph_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page must be accessible without an Authorization header (HTML shell)."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    assert response.status_code != 401


async def test_groove_check_endpoint_entries_have_required_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Groove check entries each contain commit, grooveScore, driftDelta, and status."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/groove-check?limit=5",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["totalCommits"] > 0
    entry = body["entries"][0]
    assert "commit" in entry
    assert "grooveScore" in entry
    assert "driftDelta" in entry
    assert "status" in entry
    assert entry["status"] in ("OK", "WARN", "FAIL")
    assert "track" in entry
    assert "midiFiles" in entry


@pytest.mark.anyio
async def test_groove_check_endpoint_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/groove-check returns 401 without auth."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/groove-check")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_groove_check_endpoint_404_for_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/groove-check returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/groove-check",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_groove_check_endpoint_respects_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Groove check endpoint returns at most ``limit`` entries."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/groove-check?limit=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["totalCommits"] <= 3
    assert len(body["entries"]) <= 3


@pytest.mark.anyio
async def test_groove_check_endpoint_custom_threshold(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Groove check endpoint accepts a custom threshold parameter."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/groove-check?threshold=0.05",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert abs(body["threshold"] - 0.05) < 1e-9


@pytest.mark.anyio
async def test_repo_page_contains_groove_check_link(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo landing page navigation includes a Groove Check link."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "groove-check" in body


# ---------------------------------------------------------------------------
# User profile page tests (issue #233 — pre-existing from dev, fixed here)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/users/{username} returns 200 HTML for a known profile."""
    await _make_profile(db_session, "rockstar")
    response = await client.get("/musehub/ui/users/rockstar")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "@rockstar" in body
    # Contribution graph JS must be present
    assert "contributionGraph" in body or "contrib-graph" in body


@pytest.mark.anyio
async def test_profile_no_auth_required_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile UI page is publicly accessible without a JWT (returns 200, not 401)."""
    await _make_profile(db_session, "public-user")
    response = await client.get("/musehub/ui/users/public-user")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_profile_unknown_user_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{unknown} returns 404 for a non-existent profile."""
    response = await client.get("/api/v1/musehub/users/does-not-exist-xyz")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_profile_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username} returns a valid JSON profile with required fields."""
    await _make_profile(db_session, "jazzmaster")
    response = await client.get("/api/v1/musehub/users/jazzmaster")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "jazzmaster"
    assert "repos" in data
    assert "contributionGraph" in data
    assert "sessionCredits" in data
    assert isinstance(data["sessionCredits"], int)
    assert isinstance(data["contributionGraph"], list)


@pytest.mark.anyio
async def test_profile_lists_repos(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/users/{username} includes public repos in the response."""
    await _make_profile(db_session, "beatmaker")
    repo_id = await _make_public_repo(db_session)
    response = await client.get("/api/v1/musehub/users/beatmaker")
    assert response.status_code == 200
    data = response.json()
    repo_ids = [r["repoId"] for r in data["repos"]]
    assert repo_id in repo_ids


@pytest.mark.anyio
async def test_profile_create_and_update(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/users creates a profile; PUT updates it."""
    # Create profile
    resp = await client.post(
        "/api/v1/musehub/users",
        json={"username": "newartist", "bio": "Initial bio"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "newartist"
    assert data["bio"] == "Initial bio"

    # Update profile
    resp2 = await client.put(
        "/api/v1/musehub/users/newartist",
        json={"bio": "Updated bio"},
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["bio"] == "Updated bio"


@pytest.mark.anyio
async def test_profile_create_duplicate_username_409(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/users returns 409 when username is already taken."""
    await _make_profile(db_session, "takenname")
    resp = await client.post(
        "/api/v1/musehub/users",
        json={"username": "takenname"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_profile_update_403_for_wrong_owner(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """PUT /api/v1/musehub/users/{username} returns 403 when caller doesn't own the profile."""
    # Create a profile owned by a DIFFERENT user
    other_profile = MusehubProfile(
        user_id="different-user-id-999",
        username="someoneelse",
        bio="not yours",
        pinned_repo_ids=[],
    )
    db_session.add(other_profile)
    await db_session.commit()

    resp = await client.put(
        "/api/v1/musehub/users/someoneelse",
        json={"bio": "hijacked"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_profile_page_unknown_user_renders_404_inline(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/users/{unknown} returns 200 HTML (JS renders 404 inline)."""
    response = await client.get("/musehub/ui/users/ghost-user-xyz")
    # The HTML shell always returns 200 — the JS fetches and handles the API 404
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_timeline_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/timeline returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "timeline" in body.lower()
    assert repo_id[:8] in body


@pytest.mark.anyio
async def test_timeline_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline UI route must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_timeline_page_contains_layer_controls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page embeds toggleable layer controls for all four layers."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    assert "Commits" in body
    assert "Emotion" in body
    assert "Sections" in body
    assert "Tracks" in body


@pytest.mark.anyio
async def test_timeline_page_contains_zoom_controls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page embeds day/week/month/all zoom buttons."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    assert "Day" in body
    assert "Week" in body
    assert "Month" in body
    assert "All" in body


@pytest.mark.anyio
async def test_timeline_page_includes_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Timeline page includes the JWT token input form."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/timeline")
    assert response.status_code == 200
    body = response.text
    assert "musehub/static/musehub.js" in body
    assert "token-form" in body


# ---------------------------------------------------------------------------
# Embed player route tests (issue #244)
# ---------------------------------------------------------------------------


_UTC = timezone.utc


@pytest.mark.anyio
async def test_graph_page_contains_dag_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page embeds the client-side DAG renderer JavaScript."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    body = response.text
    assert "renderGraph" in body
    assert "dag-viewport" in body
    assert "dag-svg" in body


@pytest.mark.anyio
async def test_session_list_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/sessions returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/sessions")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Sessions" in body
    assert "musehub/static/musehub.js" in body


@pytest.mark.anyio
async def test_session_detail_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/sessions/{session_id} returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    session_id = "some-session-uuid-1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
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
    response = await client.get(f"/musehub/ui/testuser/test-beats/sessions/{session_id}")
    assert response.status_code == 200
    body = response.text
    # The JS error handler must check for a 404 and render a "not found" message
    assert "Session not found" in body or "404" in body

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

async def test_contour_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/analysis/{ref}/contour returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/contour")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_contour_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Contour analysis page must be accessible without a JWT (HTML shell handles auth)."""
    repo_id = await _make_repo(db_session)
    ref = "deadbeef1234"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/contour")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_contour_page_contains_graph_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Contour page must contain pitch-curve graph, shape badge, and tessitura elements."""
    repo_id = await _make_repo(db_session)
    ref = "cafebabe12345678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/contour")
    assert response.status_code == 200
    body = response.text
    assert "Melodic Contour" in body
    assert "pitchCurveSvg" in body or "pitchCurve" in body
    assert "Tessitura" in body
    assert "Shape" in body
    assert "track-inp" in body
    assert repo_id in body


@pytest.mark.anyio
async def test_contour_json_response(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/contour returns ContourData.

    Verifies that the JSON response includes shape classification labels and
    the pitch_curve array that the contour page visualises.
    """
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "contour-test-repo", "owner": "testuser", "visibility": "private"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    repo_id = resp.json()["repoId"]

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/contour",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "contour"
    assert body["ref"] == "main"
    data = body["data"]
    assert "shape" in data
    assert "pitchCurve" in data
    assert "overallDirection" in data
    assert "directionChanges" in data
    assert len(data["pitchCurve"]) > 0
    assert data["shape"] in ("arch", "ascending", "descending", "flat", "wave")


@pytest.mark.anyio
async def test_tempo_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/analysis/{ref}/tempo returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    ref = "abc1234567890abcdef"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/tempo")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_tempo_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tempo analysis page must be accessible without a JWT (HTML shell handles auth)."""
    repo_id = await _make_repo(db_session)
    ref = "deadbeef5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/tempo")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_tempo_page_contains_bpm_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tempo page must contain BPM display, stability bar, and tempo-change timeline."""
    repo_id = await _make_repo(db_session)
    ref = "feedface5678"
    response = await client.get(f"/musehub/ui/testuser/test-beats/analysis/{ref}/tempo")
    assert response.status_code == 200
    body = response.text
    assert "Tempo Analysis" in body
    assert "BPM" in body
    assert "Stability" in body
    assert "tempoChangeSvg" in body or "tempoChanges" in body or "Tempo Changes" in body
    assert repo_id in body


@pytest.mark.anyio
async def test_tempo_json_response(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/tempo returns TempoData.

    Verifies that the JSON response includes BPM, stability, time feel, and
    tempo_changes history that the tempo page visualises.
    """
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "tempo-test-repo", "owner": "testuser", "visibility": "private"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    repo_id = resp.json()["repoId"]

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/tempo",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "tempo"
    assert body["ref"] == "main"
    data = body["data"]
    assert "bpm" in data
    assert "stability" in data
    assert "timeFeel" in data
    assert "tempoChanges" in data
    assert data["bpm"] > 0
    assert 0.0 <= data["stability"] <= 1.0
    assert isinstance(data["tempoChanges"], list)


# ---------------------------------------------------------------------------
# owner/slug navigation link correctness (regression for PR #282)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_repo_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo page must inject owner/slug base URL, not the internal UUID.

    Before the fix, every handler except repo_page used ``const base =
    '/musehub/ui/' + repoId``.  That produced UUID-based hrefs that 404 under
    the new /{owner}/{repo_slug} routing.  This test guards the regression.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    # JS base variable must use owner/slug, not UUID concatenation
    assert '"/musehub/ui/testuser/test-beats"' in body
    # UUID-concatenation pattern must NOT appear
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_commit_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Commit page back-to-repo link must use owner/slug, not internal UUID."""
    await _make_repo(db_session)
    commit_id = "abc1234567890123456789012345678901234567"
    response = await client.get(f"/musehub/ui/testuser/test-beats/commits/{commit_id}")
    assert response.status_code == 200
    body = response.text
    assert '"/musehub/ui/testuser/test-beats"' in body
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_graph_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page back-to-repo link must use owner/slug, not internal UUID."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/graph")
    assert response.status_code == 200
    body = response.text
    assert '"/musehub/ui/testuser/test-beats"' in body
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_pr_list_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page navigation must use owner/slug, not internal UUID."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    body = response.text
    assert '"/musehub/ui/testuser/test-beats"' in body
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_nav_links_use_owner_slug_not_uuid_releases_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Releases page navigation must use owner/slug, not internal UUID."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/releases")
    assert response.status_code == 200
    body = response.text
    assert '"/musehub/ui/testuser/test-beats"' in body
    assert "'/musehub/ui/' + repoId" not in body


@pytest.mark.anyio
async def test_ui_unknown_owner_slug_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{unknown-owner}/{unknown-slug} must return 404."""
    response = await client.get("/musehub/ui/nobody/nonexistent-repo")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Issue #199 — Design System Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_design_tokens_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/tokens.css must return 200 with CSS content-type.

    Verifies the design token file is reachable at its canonical static path.
    If this fails, every MuseHub page will render unstyled because the CSS
    custom properties (--bg-base, --color-accent, etc.) will be missing.
    """
    response = await client.get("/musehub/static/tokens.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    body = response.text
    assert "--bg-base" in body
    assert "--color-accent" in body
    assert "--dim-harmonic" in body


@pytest.mark.anyio
async def test_components_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/components.css must return 200 with CSS content.

    Verifies the component class file is reachable.  These classes (.card,
    .badge, .btn, etc.) are used on every MuseHub page.
    """
    response = await client.get("/musehub/static/components.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    body = response.text
    assert ".badge" in body
    assert ".btn" in body
    assert ".card" in body


@pytest.mark.anyio
async def test_layout_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/layout.css must return 200."""
    response = await client.get("/musehub/static/layout.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert ".container" in response.text


@pytest.mark.anyio
async def test_icons_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/icons.css must return 200."""
    response = await client.get("/musehub/static/icons.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert ".icon-mid" in response.text


@pytest.mark.anyio
async def test_music_css_served(client: AsyncClient) -> None:
    """GET /musehub/static/music.css must return 200."""
    response = await client.get("/musehub/static/music.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert ".piano-roll" in response.text


@pytest.mark.anyio
async def test_repo_page_uses_design_system(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo page HTML must reference all five design system CSS files.

    This is the regression guard for the monolithic _CSS removal.  If the
    _page() helper ever reverts to embedding CSS inline, this test will
    catch it by asserting the external link tags are present.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    body = response.text
    assert "/musehub/static/tokens.css" in body
    assert "/musehub/static/components.css" in body
    assert "/musehub/static/layout.css" in body
    assert "/musehub/static/icons.css" in body
    assert "/musehub/static/music.css" in body


@pytest.mark.anyio
async def test_responsive_meta_tag_present_repo_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo page must include a viewport meta tag for mobile responsiveness."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text


@pytest.mark.anyio
async def test_responsive_meta_tag_present_pr_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PR list page must include a viewport meta tag for mobile responsiveness."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/pulls")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text


@pytest.mark.anyio
async def test_responsive_meta_tag_present_issues_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Issues page must include a viewport meta tag for mobile responsiveness."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/issues")
    assert response.status_code == 200
    assert 'name="viewport"' in response.text


@pytest.mark.anyio
async def test_design_tokens_css_contains_dimension_colors(
    client: AsyncClient,
) -> None:
    """tokens.css must define all five musical dimension color tokens.

    These tokens are used in piano rolls, radar charts, and diff heatmaps.
    Missing tokens would break analysis page visualisations silently.
    """
    response = await client.get("/musehub/static/tokens.css")
    assert response.status_code == 200
    body = response.text
    for dim in ("harmonic", "rhythmic", "melodic", "structural", "dynamic"):
        assert f"--dim-{dim}:" in body, f"Missing dimension token --dim-{dim}"


@pytest.mark.anyio
async def test_design_tokens_css_contains_track_colors(
    client: AsyncClient,
) -> None:
    """tokens.css must define all 8 track color tokens (--track-0 through --track-7)."""
    response = await client.get("/musehub/static/tokens.css")
    assert response.status_code == 200
    body = response.text
    for i in range(8):
        assert f"--track-{i}:" in body, f"Missing track color token --track-{i}"


@pytest.mark.anyio
async def test_badge_variants_in_components_css(client: AsyncClient) -> None:
    """components.css must define all required badge variants including .badge-clean and .badge-dirty."""
    response = await client.get("/musehub/static/components.css")
    assert response.status_code == 200
    body = response.text
    for variant in ("open", "closed", "merged", "active", "clean", "dirty"):
        assert f".badge-{variant}" in body, f"Missing badge variant .badge-{variant}"


@pytest.mark.anyio
async def test_file_type_icons_in_icons_css(client: AsyncClient) -> None:
    """icons.css must define icon classes for all required file types."""
    response = await client.get("/musehub/static/icons.css")
    assert response.status_code == 200
    body = response.text
    for ext in ("mid", "mp3", "wav", "json", "webp", "xml", "abc"):
        assert f".icon-{ext}" in body, f"Missing file-type icon .icon-{ext}"


@pytest.mark.anyio
async def test_no_inline_css_on_repo_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo page must NOT embed the old monolithic CSS string inline.

    Regression test: verifies the _CSS removal was not accidentally reverted.
    The old _CSS block contained the literal string 'background: #0d1117'
    inside a <style> tag in the <head>.  After the design system migration,
    all styling comes from external files.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats")
    body = response.text
    # Find the <head> section — inline CSS should not appear there
    head_end = body.find("</head>")
    head_section = body[:head_end] if head_end != -1 else body
    # The old monolithic block started with "box-sizing: border-box"
    # If it appears inside <head>, the migration has been reverted.
    assert "box-sizing: border-box; margin: 0; padding: 0;" not in head_section


# ---------------------------------------------------------------------------
# Analysis dashboard UI tests (issue #221)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_analysis_dashboard_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref} returns 200 HTML without a JWT."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Analysis" in body
    assert "test-beats" in body


@pytest.mark.anyio
async def test_analysis_dashboard_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Analysis dashboard HTML shell must be accessible without an Authorization header."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_analysis_dashboard_all_dimension_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dashboard HTML embeds all 10 required dimension card labels in the page script.

    Regression test for issue #221: if any card label is missing the JS template
    will silently skip rendering that dimension, so agents get an incomplete picture.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    body = response.text
    for label in ("Key", "Tempo", "Meter", "Chord Map", "Dynamics",
                  "Groove", "Emotion", "Form", "Motifs", "Contour"):
        assert label in body, f"Expected dimension label {label!r} in dashboard HTML"


@pytest.mark.anyio
async def test_analysis_dashboard_sparkline_logic_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dashboard HTML includes sparkline rendering logic for velocity/pitch visualisations."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    body = response.text
    assert "sparkline" in body
    assert "velocityCurve" in body or "pitchCurve" in body


@pytest.mark.anyio
async def test_analysis_dashboard_card_links_to_dimensions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Each dimension card must link to the per-dimension analysis detail page.

    The card href is built client-side from ``base + '/analysis/' + ref + '/' + id``,
    so the JS template string must reference ``/analysis/`` as the path segment.
    """
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main")
    assert response.status_code == 200
    body = response.text
    assert "/analysis/" in body




# ---------------------------------------------------------------------------
# Motifs browser page — issue #224
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_motifs_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/motifs returns 200 HTML."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body


@pytest.mark.anyio
async def test_motifs_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs UI page must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    assert response.status_code != 401


@pytest.mark.anyio
async def test_motifs_page_contains_filter_ui(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page embeds client-side track and section filter controls."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    body = response.text
    assert "track-filter" in body
    assert "section-filter" in body


@pytest.mark.anyio
async def test_motifs_page_contains_piano_roll_renderer(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page embeds the piano roll renderer JavaScript function."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    body = response.text
    assert "pianoRollHtml" in body


@pytest.mark.anyio
async def test_motifs_page_contains_recurrence_grid(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page embeds the recurrence grid (heatmap) renderer."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    body = response.text
    assert "recurrenceGridHtml" in body


@pytest.mark.anyio
async def test_motifs_page_shows_transformation_badges(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page includes transformation badge renderer for inversion/retrograde labels."""
    repo_id = await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/analysis/main/motifs")
    assert response.status_code == 200
    body = response.text
    assert "transformationsHtml" in body
    assert "inversion" in body
