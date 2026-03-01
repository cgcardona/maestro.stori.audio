"""SSR tests for Muse Hub new repo wizard — issue #562.

Verifies that the creation wizard renders license options server-side and
that the name availability check endpoint returns HTMX-aware HTML fragments
when requested via HTMX, and JSON otherwise.

Tests:
- test_new_repo_page_renders_license_options_server_side
- test_new_repo_page_has_hx_get_on_name_input
- test_new_repo_name_check_htmx_returns_available_html
- test_new_repo_name_check_htmx_returns_taken_html
- test_new_repo_name_check_json_path_unchanged
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_repo(
    db: AsyncSession,
    *,
    owner: str = "new-repo-artist",
    slug: str = "existing-album",
) -> None:
    """Seed a public repo used to verify name-taken responses."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-new-repo-artist",
    )
    db.add(repo)
    await db.commit()


# ---------------------------------------------------------------------------
# Full-page GET /musehub/ui/new — SSR assertions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_new_repo_page_renders_license_options_server_side(
    client: AsyncClient,
) -> None:
    """License dropdown options are rendered in HTML by the server, not JavaScript.

    The old implementation injected license data via a JS const and built the
    <select> client-side.  After SSR migration, the server must emit <option>
    elements directly so the page works without JS.
    """
    response = await client.get("/musehub/ui/new")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # At least one server-rendered <option> must appear in the HTML
    assert "<option" in response.text
    # The CC0 license option must be present as a static HTML element
    assert "CC0" in response.text


@pytest.mark.anyio
async def test_new_repo_page_has_hx_get_on_name_input(
    client: AsyncClient,
) -> None:
    """The name input must carry an hx-get attribute pointing to /new/check.

    HTMX triggers the availability check on the name field without any custom
    JavaScript — the attribute on the input element is the contract.
    """
    response = await client.get("/musehub/ui/new")
    assert response.status_code == 200
    assert 'hx-get="/musehub/ui/new/check"' in response.text
    assert 'hx-target="#name-check"' in response.text


# ---------------------------------------------------------------------------
# GET /musehub/ui/new/check — content negotiation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_new_repo_name_check_htmx_returns_available_html(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTMX request for an available name returns an HTML span, not JSON.

    The span must contain a human-readable "Available" indicator so HTMX can
    swap it directly into the DOM without any client-side processing.
    """
    response = await client.get(
        "/musehub/ui/new/check?owner=new-repo-artist&slug=unique123",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<span" in response.text
    assert "Available" in response.text


@pytest.mark.anyio
async def test_new_repo_name_check_htmx_returns_taken_html(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTMX request for a taken name returns an HTML span indicating it is taken."""
    await _seed_repo(db_session, owner="new-repo-artist", slug="existing-album")
    response = await client.get(
        "/musehub/ui/new/check?owner=new-repo-artist&slug=existing-album",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<span" in response.text
    assert "taken" in response.text.lower()


@pytest.mark.anyio
async def test_new_repo_name_check_json_path_unchanged(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Non-HTMX request returns JSON {\"available\": bool} — existing contract preserved.

    Scripts and agents that call /new/check without the HX-Request header must
    continue to receive JSON so the endpoint remains backwards-compatible.
    """
    # Available name → JSON true
    response = await client.get(
        "/musehub/ui/new/check?owner=new-repo-artist&slug=brand-new-slug",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True

    # Taken name → JSON false
    await _seed_repo(db_session, owner="new-repo-artist", slug="taken-slug")
    response = await client.get(
        "/musehub/ui/new/check?owner=new-repo-artist&slug=taken-slug",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False
