"""SSR tests for the Muse Hub harmony analysis page (issue #585).

Verifies that :func:`~maestro.api.routes.musehub.ui.harmony_analysis_page`
renders data server-side via a Jinja2 template — not via the former inline
Python HTML builder.

All assertions are made against the raw HTML returned by the server, without
running JavaScript.  The page uses
:func:`~maestro.services.musehub_analysis.compute_harmony_analysis` which
returns deterministic stub data seeded by the ``ref`` hash, so no musical data
needs to be inserted into the database.

Covers:
- test_harmony_page_uses_jinja2_template
- test_harmony_page_renders_key_server_side
- test_harmony_page_no_python_html_builder_in_module
- test_harmony_page_htmx_fragment_path
- test_harmony_page_renders_roman_numerals_server_side
- test_harmony_page_renders_cadences_server_side
- test_harmony_page_unknown_repo_returns_404
"""
from __future__ import annotations

import inspect

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.api.routes.musehub import ui as ui_module
from maestro.db.musehub_models import MusehubRepo
from maestro.services import musehub_analysis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db: AsyncSession,
    owner: str = "harmony_artist",
    slug: str = "harmony-album",
) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-harmony-artist",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_harmony_page_uses_jinja2_template(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Harmony page is rendered by Jinja2 — not an inline HTML string.

    Asserts that the response is valid HTML from the base template (contains
    the Muse Hub branding and breadcrumb structure) rather than the former
    bare HTMLResponse built by Python f-string concatenation.
    """
    repo_id = await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/harmony_artist/harmony-album/analysis/abc12345/harmony"
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Base template landmarks confirm Jinja2 rendered the page
    assert "Muse Hub" in response.text
    assert "harmony_artist" in response.text
    assert "harmony-album" in response.text


@pytest.mark.anyio
async def test_harmony_page_renders_key_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Detected key label appears in the HTML without JavaScript execution.

    compute_harmony_analysis returns deterministic data for any ref — the key
    label (e.g. "C major") must be present in the raw server response.
    """
    repo_id = await _make_repo(db_session)
    ref = "abc12345"
    harmony = musehub_analysis.compute_harmony_analysis(repo_id=repo_id, ref=ref)

    response = await client.get(
        f"/musehub/ui/harmony_artist/harmony-album/analysis/{ref}/harmony"
    )
    assert response.status_code == 200
    # The server-computed key label must appear verbatim in the SSR output
    assert harmony.key in response.text


@pytest.mark.anyio
async def test_harmony_page_no_python_html_builder_in_module(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The _render_harmony_html inline HTML builder must not exist in ui.py.

    Confirms that the migration removed the Python-based HTML builder entirely.
    Neither the function definition nor any call site should remain.
    """
    source = inspect.getsource(ui_module)
    assert "_render_harmony_html" not in source
    # The old approach built an inline 'html = f"""..."""' block within the handler
    assert "HTMLResponse(content=html)" not in source or "harmony" not in source


@pytest.mark.anyio
async def test_harmony_page_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTMX request (HX-Request: true) receives only the fragment — no <html> tag.

    When HTMX sends a partial-page request for the harmony view, the server
    returns only the inner content fragment, not the full base.html shell.
    """
    repo_id = await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/harmony_artist/harmony-album/analysis/abc12345/harmony",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Fragment must NOT contain the full-page HTML wrapper
    assert "<html" not in response.text
    assert "<!DOCTYPE" not in response.text
    # But it must contain the harmony content
    assert "harmony" in response.text.lower()


@pytest.mark.anyio
async def test_harmony_page_renders_roman_numerals_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Roman Numeral Analysis section and table headers appear in the HTML.

    The Roman numeral table (Beat / Chord / Root / Quality / Function columns)
    is rendered server-side — no JavaScript required.
    """
    repo_id = await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/harmony_artist/harmony-album/analysis/abc12345/harmony"
    )
    assert response.status_code == 200
    assert "Roman Numeral Analysis" in response.text
    # Table column headers from the Jinja2 template
    assert "Beat" in response.text
    assert "Chord" in response.text
    assert "Function" in response.text


@pytest.mark.anyio
async def test_harmony_page_renders_cadences_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Cadences section is rendered server-side with cadence type and beat.

    compute_harmony_analysis always returns at least one cadence; the cadence
    type (e.g. 'authentic') and beat position must appear in the raw HTML.
    """
    repo_id = await _make_repo(db_session)
    ref = "abc12345"
    harmony = musehub_analysis.compute_harmony_analysis(repo_id=repo_id, ref=ref)

    response = await client.get(
        f"/musehub/ui/harmony_artist/harmony-album/analysis/{ref}/harmony"
    )
    assert response.status_code == 200
    assert "Cadences" in response.text
    # At least one cadence type must appear server-rendered
    assert harmony.cadences, "Test expects at least one cadence in stub data"
    assert harmony.cadences[0].type in response.text


@pytest.mark.anyio
async def test_harmony_page_unknown_repo_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Non-existent repo slug returns HTTP 404 (resolved via DB lookup)."""
    response = await client.get(
        "/musehub/ui/nobody/no-such-repo/analysis/abc12345/harmony"
    )
    assert response.status_code == 404
