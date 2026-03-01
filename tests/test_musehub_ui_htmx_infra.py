"""Tests for HTMX infrastructure — static assets, base.html wiring, and helpers.

Verifies that:
- htmx.min.js and alpinejs.min.js are present in the static directory.
- base.html includes the correct <script> tags and hx-boost attribute.
- musehub.js contains the HTMX JWT auth bridge and after-swap hook.
- The is_htmx() / is_htmx_boosted() helpers return the correct values.
- The static files are reachable via the /musehub/static/ HTTP endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

STATIC_DIR = Path(__file__).parent.parent / "maestro" / "templates" / "musehub" / "static"
BASE_HTML = Path(__file__).parent.parent / "maestro" / "templates" / "musehub" / "base.html"
MUSEHUB_JS = STATIC_DIR / "musehub.js"


# ── Static asset presence ────────────────────────────────────────────────────

def test_htmx_min_js_static_file_exists() -> None:
    """htmx.min.js must be present in the MuseHub static directory."""
    assert (STATIC_DIR / "htmx.min.js").exists(), "htmx.min.js not found in static dir"


def test_alpinejs_min_js_static_file_exists() -> None:
    """alpinejs.min.js must be present in the MuseHub static directory."""
    assert (STATIC_DIR / "alpinejs.min.js").exists(), "alpinejs.min.js not found in static dir"


# ── base.html wiring ─────────────────────────────────────────────────────────

def test_base_html_includes_htmx_script() -> None:
    """base.html must reference htmx.min.js so HTMX is available on every page."""
    content = BASE_HTML.read_text()
    assert "htmx.min.js" in content, "htmx.min.js script tag missing from base.html"


def test_base_html_includes_alpinejs_script() -> None:
    """base.html must reference alpinejs.min.js so Alpine.js is available on every page."""
    content = BASE_HTML.read_text()
    assert "alpinejs.min.js" in content, "alpinejs.min.js script tag missing from base.html"


def test_base_html_hx_boost_on_container() -> None:
    """The main container div must carry hx-boost so navigation links get SPA-feel transitions."""
    content = BASE_HTML.read_text()
    assert 'hx-boost="true"' in content, 'hx-boost="true" missing from container div in base.html'


def test_base_html_htmx_loading_indicator() -> None:
    """base.html must include the #htmx-loading progress bar element."""
    content = BASE_HTML.read_text()
    assert 'id="htmx-loading"' in content, "#htmx-loading element missing from base.html"


# ── musehub.js HTMX hooks ────────────────────────────────────────────────────

def test_musehub_js_has_htmx_config_request_bridge() -> None:
    """musehub.js must register an htmx:configRequest listener to inject the Bearer token."""
    content = MUSEHUB_JS.read_text()
    assert "htmx:configRequest" in content, "htmx:configRequest listener missing from musehub.js"


def test_musehub_js_has_htmx_after_swap_hook() -> None:
    """musehub.js must register an htmx:afterSwap listener to re-run initRepoNav after fragments swap."""
    content = MUSEHUB_JS.read_text()
    assert "htmx:afterSwap" in content, "htmx:afterSwap listener missing from musehub.js"


# ── is_htmx() / is_htmx_boosted() helpers ───────────────────────────────────

def test_is_htmx_helper_true() -> None:
    """is_htmx() must return True when the HX-Request: true header is present."""
    from unittest.mock import MagicMock

    from maestro.api.routes.musehub.htmx_helpers import is_htmx

    request = MagicMock()
    request.headers = {"HX-Request": "true"}
    assert is_htmx(request) is True


def test_is_htmx_helper_false() -> None:
    """is_htmx() must return False when the HX-Request header is absent."""
    from unittest.mock import MagicMock

    from maestro.api.routes.musehub.htmx_helpers import is_htmx

    request = MagicMock()
    request.headers = {}
    assert is_htmx(request) is False


def test_is_htmx_boosted_helper_true() -> None:
    """is_htmx_boosted() must return True when HX-Boosted: true is present."""
    from unittest.mock import MagicMock

    from maestro.api.routes.musehub.htmx_helpers import is_htmx_boosted

    request = MagicMock()
    request.headers = {"HX-Boosted": "true"}
    assert is_htmx_boosted(request) is True


def test_is_htmx_boosted_helper_false() -> None:
    """is_htmx_boosted() must return False when the HX-Boosted header is absent."""
    from unittest.mock import MagicMock

    from maestro.api.routes.musehub.htmx_helpers import is_htmx_boosted

    request = MagicMock()
    request.headers = {}
    assert is_htmx_boosted(request) is False


# ── HTTP endpoint reachability ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_htmx_min_js_served_over_http(async_client: AsyncClient) -> None:
    """GET /musehub/static/htmx.min.js must return 200."""
    resp = await async_client.get("/musehub/static/htmx.min.js")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"


@pytest.mark.anyio
async def test_alpinejs_min_js_served_over_http(async_client: AsyncClient) -> None:
    """GET /musehub/static/alpinejs.min.js must return 200."""
    resp = await async_client.get("/musehub/static/alpinejs.min.js")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
