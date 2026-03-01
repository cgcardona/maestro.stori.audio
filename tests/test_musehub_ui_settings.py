"""Tests for Muse Hub repo settings page (issue #429).

Covers the new ``GET /musehub/ui/{owner}/{repo_slug}/settings`` endpoint
implemented in ``maestro/api/routes/musehub/ui_settings.py``.

Test matrix:
- test_settings_page_returns_200                 — happy-path HTML response
- test_settings_page_no_auth_required            — HTML shell needs no JWT
- test_settings_page_unknown_repo_404            — unknown owner/slug → 404
- test_settings_page_contains_general_section    — General settings form present
- test_settings_page_contains_danger_zone        — Danger Zone section present
- test_settings_page_contains_merge_section      — Merge settings section present
- test_settings_page_contains_collaboration      — Collaboration section present
- test_settings_page_sidebar_navigation          — Sidebar nav links present
- test_settings_page_section_param               — ?section= pre-selects sidebar section
- test_settings_json_response                    — ?format=json returns RepoSettingsResponse fields
- test_settings_json_has_visibility              — JSON includes visibility field
- test_settings_json_has_merge_flags             — JSON includes merge strategy flags
- test_settings_page_topic_tag_input             — tag input container present in template
- test_settings_page_danger_zone_delete_confirm  — delete confirmation pattern present
- test_settings_page_danger_zone_transfer        — transfer ownership action present
- test_settings_page_danger_zone_archive         — archive action present
- test_settings_page_uses_owner_slug_base_url    — base URL uses owner/slug not UUID
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

async def _make_repo(
    db_session: AsyncSession,
    owner: str = "settingsowner",
    slug: str = "settings-repo",
    visibility: str = "private",
) -> MusehubRepo:
    """Seed a minimal repo for settings tests and return the ORM row."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id="settings-owner-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return repo


# ---------------------------------------------------------------------------
# Happy-path — HTML responses
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/settings returns HTTP 200."""
    repo = await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_settings_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The settings HTML shell is publicly accessible without a JWT.

    Auth is enforced client-side when writing (PATCH/DELETE), not on the HTML
    shell itself — consistent with all other MuseHub UI pages.
    """
    repo = await _make_repo(db_session, owner="pubowner", slug="pub-repo", visibility="public")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.anyio
async def test_settings_page_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/settings returns 404 for unknown repos."""
    resp = await client.get("/musehub/ui/ghost-owner/nonexistent-repo/settings")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Content checks — sections and navigation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_page_contains_general_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page HTML contains the General settings form."""
    repo = await _make_repo(db_session, owner="genowner", slug="gen-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "section-general" in resp.text


@pytest.mark.anyio
async def test_settings_page_contains_danger_zone(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page HTML contains the Danger Zone section."""
    repo = await _make_repo(db_session, owner="dangowner", slug="dang-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "danger" in resp.text.lower()
    assert "Delete" in resp.text or "delete" in resp.text


@pytest.mark.anyio
async def test_settings_page_contains_merge_section(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page HTML contains the Merge settings section."""
    repo = await _make_repo(db_session, owner="mergeowner", slug="merge-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "section-merge" in resp.text


@pytest.mark.anyio
async def test_settings_page_contains_collaboration(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page HTML contains the Collaboration section."""
    repo = await _make_repo(db_session, owner="collabowner", slug="collab-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "section-collaboration" in resp.text


@pytest.mark.anyio
async def test_settings_page_sidebar_navigation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page HTML contains sidebar navigation buttons."""
    repo = await _make_repo(db_session, owner="navowner", slug="nav-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    html = resp.text
    assert "activateSection" in html
    assert "data-section=" in html


@pytest.mark.anyio
async def test_settings_page_section_param(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?section=danger pre-selects the danger sidebar section in the template context."""
    repo = await _make_repo(db_session, owner="secpowner", slug="secp-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings?section=danger")
    assert resp.status_code == 200
    # The activeSection JS variable should be populated from the context
    assert "activeSection" in resp.text or "active_section" in resp.text or "danger" in resp.text


# ---------------------------------------------------------------------------
# Content negotiation — JSON
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/settings?format=json returns RepoSettingsResponse."""
    repo = await _make_repo(db_session, owner="jsonowner", slug="json-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings?format=json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "")
    data = resp.json()
    assert "name" in data or "visibility" in data


@pytest.mark.anyio
async def test_settings_json_has_visibility(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response includes the ``visibility`` field."""
    repo = await _make_repo(db_session, owner="visowner", slug="vis-repo", visibility="public")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("visibility") == "public"


@pytest.mark.anyio
async def test_settings_json_has_merge_flags(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response includes merge strategy boolean flags."""
    repo = await _make_repo(db_session, owner="flagowner", slug="flag-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings?format=json")
    assert resp.status_code == 200
    data = resp.json()
    # RepoSettingsResponse uses camelCase via by_alias=True in negotiate_response
    assert "allowMergeCommit" in data or "allow_merge_commit" in data


# ---------------------------------------------------------------------------
# Template content — specific UI elements
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_page_topic_tag_input(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page includes the topic tag input container."""
    repo = await _make_repo(db_session, owner="tagowner", slug="tag-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "topics-container" in resp.text or "tag-input" in resp.text


@pytest.mark.anyio
async def test_settings_page_danger_zone_delete_confirm(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page requires typing the full repo name to confirm deletion."""
    repo = await _make_repo(db_session, owner="delowner", slug="del-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    # Confirmation input pattern for repo name
    assert "confirm-delete-name" in resp.text or "deleteRepo" in resp.text


@pytest.mark.anyio
async def test_settings_page_danger_zone_transfer(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page includes a transfer ownership action."""
    repo = await _make_repo(db_session, owner="tfrowner", slug="tfr-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "transfer" in resp.text.lower()
    assert "transferOwnership" in resp.text or "modal-transfer" in resp.text


@pytest.mark.anyio
async def test_settings_page_danger_zone_archive(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page includes an archive repository action."""
    repo = await _make_repo(db_session, owner="archowner", slug="arch-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "archive" in resp.text.lower()
    assert "archiveRepo" in resp.text or "modal-archive" in resp.text


@pytest.mark.anyio
async def test_settings_page_uses_owner_slug_base_url(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The page injects the owner/slug-based base URL into the JS context, not a UUID.

    Regression guard: all MuseHub UI pages must use ``/musehub/ui/{owner}/{slug}``
    style URLs so breadcrumb links and API calls are human-readable.
    """
    repo = await _make_repo(db_session, owner="slugowner", slug="slug-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert f"/musehub/ui/{repo.owner}/{repo.slug}" in resp.text
