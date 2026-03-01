"""Tests for HTMX fragment response helpers in maestro/api/routes/musehub/htmx_helpers.py.

Verifies that is_htmx(), is_htmx_boosted(), htmx_fragment_or_full(), htmx_trigger(),
and htmx_redirect() behave correctly for all expected request shapes.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
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


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Construct a minimal Starlette Request with the given headers."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


def _make_templates(rendered_name: str | None = None) -> MagicMock:
    """Return a mock Jinja2Templates that records which template was rendered."""
    templates = MagicMock()
    response = MagicMock(spec=Response)

    def template_response(request: Request, name: str, ctx: dict[str, object]) -> MagicMock:
        response.template_name = name
        return response

    templates.TemplateResponse.side_effect = template_response
    return templates


# ---------------------------------------------------------------------------
# is_htmx
# ---------------------------------------------------------------------------


def test_is_htmx_returns_true_with_header() -> None:
    request = _make_request({"HX-Request": "true"})
    assert is_htmx(request) is True


def test_is_htmx_returns_false_without_header() -> None:
    request = _make_request()
    assert is_htmx(request) is False


def test_is_htmx_returns_false_wrong_value() -> None:
    request = _make_request({"HX-Request": "false"})
    assert is_htmx(request) is False


def test_is_htmx_returns_false_on_arbitrary_value() -> None:
    request = _make_request({"HX-Request": "1"})
    assert is_htmx(request) is False


# ---------------------------------------------------------------------------
# is_htmx_boosted
# ---------------------------------------------------------------------------


def test_is_htmx_boosted_with_header() -> None:
    request = _make_request({"HX-Boosted": "true"})
    assert is_htmx_boosted(request) is True


def test_is_htmx_boosted_returns_false_without_header() -> None:
    request = _make_request()
    assert is_htmx_boosted(request) is False


# ---------------------------------------------------------------------------
# htmx_fragment_or_full
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_htmx_fragment_or_full_returns_fragment_on_htmx_request() -> None:
    request = _make_request({"HX-Request": "true"})
    templates = _make_templates()
    ctx: dict[str, object] = {"key": "value"}

    response = await htmx_fragment_or_full(
        request,
        templates,
        ctx,
        full_template="musehub/pages/full.html",
        fragment_template="musehub/fragments/partial.html",
    )

    templates.TemplateResponse.assert_called_once_with(
        request, "musehub/fragments/partial.html", ctx
    )


@pytest.mark.anyio
async def test_htmx_fragment_or_full_returns_full_on_direct_request() -> None:
    request = _make_request()
    templates = _make_templates()
    ctx: dict[str, object] = {"key": "value"}

    response = await htmx_fragment_or_full(
        request,
        templates,
        ctx,
        full_template="musehub/pages/full.html",
        fragment_template="musehub/fragments/partial.html",
    )

    templates.TemplateResponse.assert_called_once_with(
        request, "musehub/pages/full.html", ctx
    )


@pytest.mark.anyio
async def test_htmx_fragment_or_full_returns_full_when_no_fragment_template() -> None:
    """HTMX request without a fragment_template must still return the full page."""
    request = _make_request({"HX-Request": "true"})
    templates = _make_templates()
    ctx: dict[str, object] = {}

    response = await htmx_fragment_or_full(
        request,
        templates,
        ctx,
        full_template="musehub/pages/full.html",
        fragment_template=None,
    )

    templates.TemplateResponse.assert_called_once_with(
        request, "musehub/pages/full.html", ctx
    )


# ---------------------------------------------------------------------------
# htmx_trigger
# ---------------------------------------------------------------------------


def test_htmx_trigger_sets_header_with_detail() -> None:
    response = Response(status_code=200)
    htmx_trigger(response, "toast", {"message": "Issue closed", "type": "success"})

    raw = response.headers["HX-Trigger"]
    payload = json.loads(raw)
    assert payload == {"toast": {"message": "Issue closed", "type": "success"}}


def test_htmx_trigger_sets_header_without_detail() -> None:
    response = Response(status_code=200)
    htmx_trigger(response, "refresh")

    raw = response.headers["HX-Trigger"]
    payload = json.loads(raw)
    assert payload == {"refresh": True}


def test_htmx_trigger_overwrites_existing_header() -> None:
    response = Response(status_code=200)
    htmx_trigger(response, "first", {"x": 1})
    htmx_trigger(response, "second", {"y": 2})

    raw = response.headers["HX-Trigger"]
    payload = json.loads(raw)
    assert payload == {"second": {"y": 2}}


# ---------------------------------------------------------------------------
# htmx_redirect
# ---------------------------------------------------------------------------


def test_htmx_redirect_sets_hx_redirect_header() -> None:
    response = htmx_redirect("/musehub/ui/some/path")

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/musehub/ui/some/path"


def test_htmx_redirect_absolute_url() -> None:
    url = "https://example.com/path"
    response = htmx_redirect(url)

    assert response.headers["HX-Redirect"] == url
