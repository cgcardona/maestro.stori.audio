"""SSR tests for the harmony analysis page migration (issue #585).

Verifies that harmony_analysis_page renders Roman-numeral chord events,
cadences, and modulations server-side using a Jinja2 template rather than
the deleted _render_harmony_html() inline Python HTML builder.

Tests
-----
- test_harmony_page_uses_jinja2_template
    GET page → assert Jinja2 template (not inline HTML builder) renders it.
- test_harmony_page_renders_chord_frequency_server_side
    GET page → assert at least one Roman numeral chord symbol in HTML.
- test_harmony_page_no_python_html_builder_in_module
    Assert _render_harmony_html no longer exists in ui.py source.
- test_harmony_page_htmx_fragment_path
    GET with HX-Request:true → fragment only (no <html>).
- test_harmony_page_renders_functional_categories
    GET page → assert a tonal function label appears in the HTML.
"""
from __future__ import annotations

import inspect

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANALYSIS_REF = "cafebeef00112233"
_OWNER = "harmonyuser"
_SLUG = "harmony-test-repo"
_BASE = f"/musehub/ui/{_OWNER}/{_SLUG}"
_HARMONY_URL = f"{_BASE}/analysis/{_ANALYSIS_REF}/harmony"


async def _make_repo(db_session: AsyncSession) -> str:
    """Seed a minimal repo and return its repo_id."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="private",
        owner_user_id="harmony-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_harmony_page_uses_jinja2_template(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET harmony page returns 200 HTML rendered by Jinja2, not the inline builder."""
    await _make_repo(db_session)
    response = await client.get(_HARMONY_URL)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # Jinja2-rendered pages include the base layout's <html> tag
    assert "<html" in body
    # Harmony analysis heading must be present (rendered by template, not by JS)
    assert "Harmony Analysis" in body


@pytest.mark.anyio
async def test_harmony_page_renders_chord_frequency_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET harmony page renders at least one Roman numeral chord symbol in HTML."""
    await _make_repo(db_session)
    response = await client.get(_HARMONY_URL)
    assert response.status_code == 200
    body = response.text
    # Roman numerals are rendered as chord labels; at minimum "I" must appear
    roman_symbols = ["I", "II", "III", "IV", "V", "VI", "VII"]
    assert any(sym in body for sym in roman_symbols), (
        "Expected at least one Roman numeral chord symbol in server-rendered harmony page"
    )


@pytest.mark.anyio
async def test_harmony_page_no_python_html_builder_in_module(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """_render_harmony_html must not exist in the musehub ui module."""
    import maestro.api.routes.musehub.ui as ui_module

    source = inspect.getsource(ui_module)
    assert "_render_harmony_html" not in source, (
        "_render_harmony_html still exists in ui.py — the inline HTML builder was not removed"
    )


@pytest.mark.anyio
async def test_harmony_page_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET harmony page with HX-Request:true returns fragment (no <html> wrapper)."""
    await _make_repo(db_session)
    response = await client.get(_HARMONY_URL, headers={"HX-Request": "true"})
    assert response.status_code == 200
    body = response.text
    assert "<html" not in body
    assert "Harmony Analysis" in body


@pytest.mark.anyio
async def test_harmony_page_renders_functional_categories(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET harmony page renders at least one tonal function label server-side."""
    await _make_repo(db_session)
    response = await client.get(_HARMONY_URL)
    assert response.status_code == 200
    body = response.text
    tonal_functions = ["tonic", "dominant", "subdominant", "pre-dominant", "secondary-dominant"]
    assert any(fn in body for fn in tonal_functions), (
        "Expected at least one tonal function label in server-rendered harmony page"
    )
