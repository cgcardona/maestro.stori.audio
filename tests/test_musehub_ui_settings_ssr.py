"""SSR-specific tests for the Muse Hub repo settings page.

Verifies that the settings page uses server-side rendering (Jinja2 templates)
rather than a client-side JS shell. The handler passes ``RepoSettingsResponse``
into the template context so field values are embedded in the HTML at
render-time — no client-side fetch required to display the form.

Test matrix:
- test_settings_page_renders_repo_name_server_side — GET page, assert repo name in form value
- test_settings_page_general_form_has_hx_patch — general form has ``hx-patch`` attribute
- test_settings_page_danger_zone_has_hx_delete — delete form has ``hx-delete``
- test_settings_page_section_nav_present — section nav links present
- test_settings_unknown_repo_404 — unknown slug → 404
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
    owner: str = "ssrowner",
    slug: str = "ssr-settings-repo",
    visibility: str = "public",
    name: str | None = None,
) -> MusehubRepo:
    """Seed a minimal repo for SSR settings tests and return the ORM row."""
    repo_name = name or slug
    repo = MusehubRepo(
        name=repo_name,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id="ssr-settings-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return repo


# ---------------------------------------------------------------------------
# SSR: repo name embedded in form value at render-time
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_page_renders_repo_name_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET settings page embeds the repo name directly into the HTML form value.

    With SSR, the template renders ``value="{{ s.name }}"`` so the repo name
    is present in the raw HTML response without any client-side fetch.
    """
    repo = await _make_repo(
        db_session, owner="ssrname", slug="my-ssr-repo", name="my-ssr-repo"
    )
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "my-ssr-repo" in resp.text


# ---------------------------------------------------------------------------
# HTMX attributes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_page_general_form_has_hx_patch(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """General settings form uses ``hx-patch`` for HTMX section-save."""
    repo = await _make_repo(db_session, owner="htmxpatch", slug="htmx-patch-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "hx-patch" in resp.text


@pytest.mark.anyio
async def test_settings_page_danger_zone_has_hx_delete(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Danger Zone delete form uses ``hx-delete`` for HTMX repo deletion."""
    repo = await _make_repo(db_session, owner="htmxdel", slug="htmx-delete-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    assert "hx-delete" in resp.text


# ---------------------------------------------------------------------------
# Section navigation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_page_section_nav_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Settings page includes Alpine.js-powered section navigation links.

    The nav uses ``x-on:click.prevent`` to switch sections client-side without
    a server round-trip, and ``:class`` binding to highlight the active link.
    """
    repo = await _make_repo(db_session, owner="secnav", slug="sec-nav-repo")
    resp = await client.get(f"/musehub/ui/{repo.owner}/{repo.slug}/settings")
    assert resp.status_code == 200
    html = resp.text
    assert "settings-nav-link" in html
    # Alpine.js section switching
    assert "x-data" in html
    assert "x-show" in html
    # All four sections present
    assert "section-general" in html
    assert "section-merge" in html
    assert "section-collaboration" in html
    assert "section-danger" in html


# ---------------------------------------------------------------------------
# 404 for unknown repo
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_settings_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET settings for an unknown repo/slug returns 404."""
    resp = await client.get("/musehub/ui/nobody/nonexistent-repo-ssr/settings")
    assert resp.status_code == 404
