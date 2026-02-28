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

Covers issue #241 (credits page):
- test_credits_page_renders            — GET /musehub/ui/{repo_id}/credits returns 200 HTML
- test_credits_json_response           — GET /api/v1/musehub/repos/{repo_id}/credits returns JSON
- test_credits_empty_state             — empty state message when no commits exist
- test_credits_no_auth_required        — credits UI page is accessible without JWT

UI routes require no JWT auth (they return HTML shells whose JS handles auth).
The HTML content tests assert structural markers present in every rendered page.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubRepo


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
async def test_global_search_ui_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/search returns 200 HTML (no auth required — HTML shell)."""
    response = await client.get("/musehub/ui/search")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Global Search" in body
    assert "Muse Hub" in body


@pytest.mark.anyio
async def test_global_search_ui_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/search must not return 401 — it is a static HTML shell."""
    response = await client.get("/musehub/ui/search")
    assert response.status_code != 401
    assert response.status_code == 200


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
    """GET /musehub/ui/{repo_id}/credits returns 200 HTML without requiring a JWT."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/credits")
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
    response = await client.get(f"/musehub/ui/{repo_id}/graph")
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
    assert "context" in body.lower()
    assert repo_id[:8] in body


@pytest.mark.anyio
async def test_credits_page_contains_json_ld_injection(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits page embeds JSON-LD injection logic for machine-readable attribution."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/credits")
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
    response = await client.get(f"/musehub/ui/{repo_id}/credits")
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
    response = await client.get(f"/musehub/ui/{repo_id}/credits")
    assert response.status_code == 200
    body = response.text
    assert "muse session start" in body


@pytest.mark.anyio
async def test_credits_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Credits UI page must be accessible without an Authorization header (HTML shell)."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/credits")
    assert response.status_code == 200
    assert response.status_code != 401


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
async def test_graph_page_contains_dag_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page embeds the client-side DAG renderer JavaScript."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/graph")
    assert response.status_code == 200
    body = response.text
    # Key renderer identifiers must be present
    assert "renderGraph" in body
    assert "dag-viewport" in body
    assert "dag-svg" in body


@pytest.mark.anyio
async def test_graph_page_has_zoom_pan(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page includes zoom and pan JavaScript logic."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/graph")
    assert response.status_code == 200
    body = response.text
    assert "wheel" in body
    assert "mousedown" in body
    assert "applyTransform" in body


@pytest.mark.anyio
async def test_graph_page_has_popover(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page includes commit detail hover popover markup."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/graph")
    assert response.status_code == 200
    body = response.text
    assert "dag-popover" in body
    assert "pop-sha" in body
    assert "pop-msg" in body


@pytest.mark.anyio
async def test_graph_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph UI page must be accessible without an Authorization header."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/graph")
    assert response.status_code != 401
    assert response.status_code == 200


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
    response = await client.get(f"/musehub/ui/{repo_id}/context/{commit_id}")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_graph_page_includes_token_form(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph page embeds the JWT token input form so visitors can authenticate."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/graph")
    assert response.status_code == 200
    body = response.text
    assert "localStorage" in body
    assert "musehub_token" in body

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
    response = await client.get(f"/musehub/ui/{repo_id}/groove-check")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Groove Check" in body


@pytest.mark.anyio
async def test_groove_check_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check UI page must be accessible without an Authorization header (HTML shell)."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/groove-check")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_groove_check_page_contains_chart_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove check page embeds the SVG chart rendering JavaScript."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/groove-check")
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
    response = await client.get(f"/musehub/ui/{repo_id}/groove-check")
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
    response = await client.get(f"/musehub/ui/{repo_id}/groove-check")
    assert response.status_code == 200
    body = response.text
    assert "localStorage" in body
    assert "musehub_token" in body


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
    response = await client.get(f"/musehub/ui/{repo_id}")
    assert response.status_code == 200
    body = response.text
    assert "groove-check" in body
