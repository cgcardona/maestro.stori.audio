"""Tests for the HTMX + Alpine.js infrastructure layer (issue #552).

Verifies that:
- Static JS assets (htmx.min.js, alpinejs.min.js) are served correctly.
- base.html includes the correct <script> tags for HTMX and Alpine.js.
- The main container in base.html carries hx-boost="true".
- musehub.js contains the HTMX JWT auth bridge and after-swap hook.
- The htmx_helpers module correctly identifies HTMX requests.

Covers:
- test_htmx_min_js_static_file_exists
- test_alpinejs_min_js_static_file_exists
- test_base_html_includes_htmx_script
- test_base_html_includes_alpinejs_script
- test_base_html_hx_boost_on_container
- test_musehub_js_has_htmx_config_request_bridge
- test_musehub_js_has_htmx_after_swap_hook
- test_is_htmx_helper_true
- test_is_htmx_helper_false
- test_is_htmx_boosted_helper_true
- test_is_htmx_boosted_helper_false
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient

from maestro.api.routes.musehub.htmx_helpers import is_htmx, is_htmx_boosted
from maestro.main import app


# ---------------------------------------------------------------------------
# Static asset serving
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_htmx_min_js_static_file_exists(client: AsyncClient) -> None:
    """GET /musehub/static/htmx.min.js returns 200 with JavaScript content."""
    response = await client.get("/musehub/static/htmx.min.js")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_alpinejs_min_js_static_file_exists(client: AsyncClient) -> None:
    """GET /musehub/static/alpinejs.min.js returns 200 with JavaScript content."""
    response = await client.get("/musehub/static/alpinejs.min.js")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# base.html template assertions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_base_html_includes_htmx_script(client: AsyncClient) -> None:
    """base.html <head> contains a <script> tag loading htmx.min.js."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    assert "htmx.min.js" in response.text


@pytest.mark.anyio
async def test_base_html_includes_alpinejs_script(client: AsyncClient) -> None:
    """base.html <head> contains a <script> tag loading alpinejs.min.js."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    assert "alpinejs.min.js" in response.text


@pytest.mark.anyio
async def test_base_html_hx_boost_on_container(client: AsyncClient) -> None:
    """The main .container div in base.html carries hx-boost='true'."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    assert 'hx-boost="true"' in response.text


# ---------------------------------------------------------------------------
# musehub.js content assertions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_musehub_js_has_htmx_config_request_bridge(client: AsyncClient) -> None:
    """musehub.js contains the HTMX configRequest event listener that injects the JWT."""
    response = await client.get("/musehub/static/musehub.js")
    assert response.status_code == 200
    assert "htmx:configRequest" in response.text


@pytest.mark.anyio
async def test_musehub_js_has_htmx_after_swap_hook(client: AsyncClient) -> None:
    """musehub.js contains the htmx:afterSwap event listener for re-running initRepoNav."""
    response = await client.get("/musehub/static/musehub.js")
    assert response.status_code == 200
    assert "htmx:afterSwap" in response.text


# ---------------------------------------------------------------------------
# htmx_helpers unit tests — use Starlette's sync TestClient for header injection
# ---------------------------------------------------------------------------


def _make_request(headers: dict[str, str]) -> object:
    """Return a minimal Request-like object for unit testing helpers."""
    from starlette.requests import Request
    from starlette.datastructures import Headers

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_is_htmx_helper_true() -> None:
    """is_htmx() returns True when HX-Request: true header is present."""
    req = _make_request({"HX-Request": "true"})
    assert is_htmx(req) is True  # type: ignore[arg-type]


def test_is_htmx_helper_false() -> None:
    """is_htmx() returns False when HX-Request header is absent."""
    req = _make_request({})
    assert is_htmx(req) is False  # type: ignore[arg-type]


def test_is_htmx_boosted_helper_true() -> None:
    """is_htmx_boosted() returns True when HX-Boosted: true header is present."""
    req = _make_request({"HX-Boosted": "true"})
    assert is_htmx_boosted(req) is True  # type: ignore[arg-type]


def test_is_htmx_boosted_helper_false() -> None:
    """is_htmx_boosted() returns False when HX-Boosted header is absent."""
    req = _make_request({})
    assert is_htmx_boosted(req) is False  # type: ignore[arg-type]
