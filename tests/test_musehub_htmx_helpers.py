"""Tests for maestro.api.routes.musehub.htmx_helpers.

Covers HX-Request detection, HX-Boosted detection, fragment/full routing,
HX-Trigger header emission, and HX-Redirect response generation.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.testclient import TestClient

from maestro.api.routes.musehub.htmx_helpers import (
    htmx_fragment_or_full,
    htmx_redirect,
    htmx_trigger,
    is_htmx,
    is_htmx_boosted,
)


def _make_request(headers: dict[str, str] | None = None) -> MagicMock:
    """Return a mock FastAPI Request with the given headers."""
    req = MagicMock()
    req.headers = Headers(headers=headers or {})
    return req


def _make_templates(rendered_name: list[str]) -> MagicMock:
    """Return a mock Jinja2Templates that records the template name used."""

    templates = MagicMock()

    def fake_response(request: object, name: str, ctx: object) -> Response:
        rendered_name.append(name)
        return Response(content=f"<rendered:{name}>", media_type="text/html")

    templates.TemplateResponse = fake_response
    return templates


# ---------------------------------------------------------------------------
# is_htmx
# ---------------------------------------------------------------------------


def test_is_htmx_returns_true_with_header() -> None:
    req = _make_request({"HX-Request": "true"})
    assert is_htmx(req) is True


def test_is_htmx_returns_false_without_header() -> None:
    req = _make_request()
    assert is_htmx(req) is False


def test_is_htmx_returns_false_wrong_value() -> None:
    req = _make_request({"HX-Request": "false"})
    assert is_htmx(req) is False


def test_is_htmx_returns_false_on_capitalised_value() -> None:
    """Header value comparison is case-sensitive; 'True' ≠ 'true'."""
    req = _make_request({"HX-Request": "True"})
    assert is_htmx(req) is False


# ---------------------------------------------------------------------------
# is_htmx_boosted
# ---------------------------------------------------------------------------


def test_is_htmx_boosted_with_header() -> None:
    req = _make_request({"HX-Boosted": "true"})
    assert is_htmx_boosted(req) is True


def test_is_htmx_boosted_without_header() -> None:
    req = _make_request()
    assert is_htmx_boosted(req) is False


# ---------------------------------------------------------------------------
# htmx_fragment_or_full
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_htmx_fragment_or_full_returns_fragment_on_htmx_request() -> None:
    rendered: list[str] = []
    req = _make_request({"HX-Request": "true"})
    templates = _make_templates(rendered)
    ctx: dict[str, object] = {}

    await htmx_fragment_or_full(
        req, templates, ctx,
        full_template="pages/full.html",
        fragment_template="fragments/part.html",
    )

    assert rendered == ["fragments/part.html"]


@pytest.mark.anyio
async def test_htmx_fragment_or_full_returns_full_on_direct_request() -> None:
    rendered: list[str] = []
    req = _make_request()  # no HX-Request header
    templates = _make_templates(rendered)
    ctx: dict[str, object] = {}

    await htmx_fragment_or_full(
        req, templates, ctx,
        full_template="pages/full.html",
        fragment_template="fragments/part.html",
    )

    assert rendered == ["pages/full.html"]


@pytest.mark.anyio
async def test_htmx_fragment_or_full_returns_full_when_no_fragment_template() -> None:
    """Even an HTMX request must get the full page when no fragment_template is given."""
    rendered: list[str] = []
    req = _make_request({"HX-Request": "true"})
    templates = _make_templates(rendered)
    ctx: dict[str, object] = {}

    await htmx_fragment_or_full(
        req, templates, ctx,
        full_template="pages/full.html",
        fragment_template=None,
    )

    assert rendered == ["pages/full.html"]


# ---------------------------------------------------------------------------
# htmx_trigger
# ---------------------------------------------------------------------------


def test_htmx_trigger_sets_header_with_detail() -> None:
    response = Response(content="ok")
    htmx_trigger(response, "toast", {"message": "Issue closed", "type": "success"})

    raw = response.headers["HX-Trigger"]
    parsed = json.loads(raw)
    assert parsed == {"toast": {"message": "Issue closed", "type": "success"}}


def test_htmx_trigger_sets_header_without_detail() -> None:
    response = Response(content="ok")
    htmx_trigger(response, "refresh")

    raw = response.headers["HX-Trigger"]
    parsed = json.loads(raw)
    assert parsed == {"refresh": True}


def test_htmx_trigger_sets_header_with_none_detail() -> None:
    response = Response(content="ok")
    htmx_trigger(response, "ping", None)

    raw = response.headers["HX-Trigger"]
    parsed = json.loads(raw)
    assert parsed == {"ping": True}


# ---------------------------------------------------------------------------
# htmx_redirect
# ---------------------------------------------------------------------------


def test_htmx_redirect_sets_hx_redirect_header() -> None:
    response = htmx_redirect("/musehub/ui/owner/repo")

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/musehub/ui/owner/repo"
