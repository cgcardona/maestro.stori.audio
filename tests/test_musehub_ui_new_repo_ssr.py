"""SSR tests for the Muse Hub new repo creation wizard (issue #562).

Validates that the wizard form is rendered server-side via Jinja2 — license
options, form inputs, and HTMX attributes appear in the raw HTML response
without requiring JavaScript execution.  Also validates that the availability
check endpoint returns an HTML fragment when called by HTMX.

Covers:
- test_new_repo_page_renders_license_options_server_side — license <option> in HTML
- test_new_repo_page_has_hx_get_on_name_input — name input has hx-get attribute
- test_new_repo_page_has_visibility_inputs — Public/Private radio inputs in HTML
- test_new_repo_page_form_renders_without_js — form element in SSR HTML
- test_new_repo_page_has_hx_indicator_on_name_input — hx-indicator attribute present
- test_new_repo_page_has_name_check_indicator_span — indicator span exists in HTML
- test_new_repo_name_check_htmx_returns_available_html — HTMX → HTML span "Available"
- test_new_repo_name_check_htmx_returns_taken_html — HTMX → HTML span "taken"
- test_new_repo_name_check_json_path_unchanged — no HX-Request → JSON response
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _seed_repo(
    db: AsyncSession,
    owner: str = "existingowner",
    slug: str = "taken-repo",
) -> None:
    """Seed a public repo so the slug appears as taken in check requests."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="seed-uid",
    )
    db.add(repo)
    await db.commit()


# ---------------------------------------------------------------------------
# Tests — SSR form verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_new_repo_page_renders_license_options_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """License <option> elements are server-rendered in the HTML response.

    Confirms the license dropdown is Jinja2-rendered from the ``licenses``
    context variable, not built by client-side JavaScript.
    """
    response = await client.get("/musehub/ui/new")
    assert response.status_code == 200
    body = response.text
    # CC BY license option must appear as a real <option> tag in the raw HTML
    assert "<option" in body
    assert "CC BY" in body


@pytest.mark.anyio
async def test_new_repo_page_has_hx_get_on_name_input(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The repository name input has an hx-get attribute pointing to /new/check.

    This drives the live HTMX availability check without client JS polling.
    """
    response = await client.get("/musehub/ui/new")
    assert response.status_code == 200
    body = response.text
    assert "hx-get" in body
    assert "/musehub/ui/new/check" in body


@pytest.mark.anyio
async def test_new_repo_page_has_visibility_inputs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Public and Private visibility radio inputs are in the server-rendered HTML."""
    response = await client.get("/musehub/ui/new")
    assert response.status_code == 200
    body = response.text
    assert 'value="public"' in body
    assert 'value="private"' in body


@pytest.mark.anyio
async def test_new_repo_page_form_renders_without_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A <form> element is present in the SSR HTML — no JS required to show it.

    The old JS-shell pattern rendered the form via innerHTML inside load().
    SSR means the form tag appears in the raw server response.
    """
    response = await client.get("/musehub/ui/new")
    assert response.status_code == 200
    assert "<form" in response.text


@pytest.mark.anyio
async def test_new_repo_page_has_hx_indicator_on_name_input(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The name input has hx-indicator pointing at #name-check-indicator.

    This gives users a loading spinner while the debounced availability check
    request is in-flight (issue #704).
    """
    response = await client.get("/musehub/ui/new")
    assert response.status_code == 200
    body = response.text
    assert 'hx-indicator="#name-check-indicator"' in body


@pytest.mark.anyio
async def test_new_repo_page_has_name_check_indicator_span(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A span with id=name-check-indicator and class=htmx-indicator is rendered.

    HTMX toggles opacity on this element while the availability check is
    in-flight, giving the user visual feedback without any custom JavaScript
    (issue #704).
    """
    response = await client.get("/musehub/ui/new")
    assert response.status_code == 200
    body = response.text
    assert 'id="name-check-indicator"' in body
    assert 'class="htmx-indicator"' in body


# ---------------------------------------------------------------------------
# Tests — /new/check availability endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_new_repo_name_check_htmx_returns_available_html(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /new/check with HX-Request header returns an HTML availability span.

    The span is swapped into #name-check by HTMX — no JS needed.
    """
    response = await client.get(
        "/musehub/ui/new/check",
        params={"owner": "newowner", "slug": "unique-name-xyz-123"},
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
    """GET /new/check for an existing slug returns a "taken" HTML span."""
    await _seed_repo(db_session, owner="existingowner", slug="taken-repo")
    response = await client.get(
        "/musehub/ui/new/check",
        params={"owner": "existingowner", "slug": "taken-repo"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "<span" in body
    assert "taken" in body.lower() or "✗" in body


@pytest.mark.anyio
async def test_new_repo_name_check_json_path_unchanged(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /new/check without HX-Request header returns JSON — backward-compat path."""
    response = await client.get(
        "/musehub/ui/new/check",
        params={"owner": "anyowner", "slug": "any-slug-999"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert "available" in data
    assert isinstance(data["available"], bool)
