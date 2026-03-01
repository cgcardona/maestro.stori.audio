"""Unit tests for the MuseHub content negotiation helper.

Covers issue #200 — negotiate_response() dispatches HTML vs JSON based on
Accept header and ?format query param.

Covers issue #554 — negotiate_response() dispatches HTMX fragment when
HX-Request header is present and fragment_template is provided.

Tests:
- test_negotiate_wants_json_format_param         — ?format=json → JSON path
- test_negotiate_wants_json_accept_header        — Accept: application/json → JSON path
- test_negotiate_wants_html_by_default           — no header/param → HTML path
- test_negotiate_wants_html_text_html_header     — Accept: text/html → HTML path
- test_negotiate_json_uses_pydantic_by_alias     — camelCase keys in JSON output
- test_negotiate_json_fallback_to_context        — no json_data → context dict as JSON
- test_negotiate_accept_partial_match            — mixed Accept header containing json
- test_negotiate_htmx_fragment_path              — HX-Request + fragment_template → fragment
- test_negotiate_htmx_no_fragment_template       — HX-Request but no fragment_template → HTML
- test_negotiate_htmx_fragment_beats_json        — HX-Request takes priority over JSON Accept
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import JSONResponse
from starlette.responses import Response

from maestro.api.routes.musehub.negotiate import _wants_json, negotiate_response
from maestro.models.base import CamelModel


# ---------------------------------------------------------------------------
# _wants_json unit tests (synchronous helper — no I/O)
# ---------------------------------------------------------------------------


def _make_request(accept: str = "", format_param: str | None = None) -> Any:
    """Build a minimal mock Request with the given Accept header."""
    req = MagicMock()
    req.headers = {"accept": accept} if accept else {}
    return req


def test_negotiate_wants_json_format_param() -> None:
    """?format=json forces JSON regardless of Accept header."""
    req = _make_request(accept="text/html")
    assert _wants_json(req, format_param="json") is True


def test_negotiate_wants_json_accept_header() -> None:
    """Accept: application/json triggers JSON path."""
    req = _make_request(accept="application/json")
    assert _wants_json(req, format_param=None) is True


def test_negotiate_wants_html_by_default() -> None:
    """No Accept header and no format param → HTML (default)."""
    req = _make_request()
    assert _wants_json(req, format_param=None) is False


def test_negotiate_wants_html_text_html_header() -> None:
    """Explicit Accept: text/html → HTML path."""
    req = _make_request(accept="text/html,application/xhtml+xml")
    assert _wants_json(req, format_param=None) is False


def test_negotiate_accept_partial_match() -> None:
    """Mixed Accept containing application/json → JSON path."""
    req = _make_request(accept="text/html, application/json;q=0.9")
    assert _wants_json(req, format_param=None) is True


def test_negotiate_format_param_not_json_means_html() -> None:
    """?format=html (or any non-json value) → HTML path."""
    req = _make_request(accept="")
    assert _wants_json(req, format_param="html") is False


# ---------------------------------------------------------------------------
# negotiate_response async tests (full response construction)
# ---------------------------------------------------------------------------


class _SampleModel(CamelModel):
    """Minimal CamelModel for testing camelCase serialisation via by_alias=True."""

    repo_id: str
    star_count: int


@pytest.mark.anyio
async def test_negotiate_json_uses_pydantic_by_alias() -> None:
    """JSON path serialises Pydantic model with camelCase keys (by_alias=True)."""
    req = _make_request(accept="application/json")
    templates = MagicMock()

    model = _SampleModel(repo_id="abc-123", star_count=42)
    resp = await negotiate_response(
        request=req,
        template_name="musehub/pages/repo.html",
        context={"repo_id": "abc-123"},
        templates=templates,
        json_data=model,
        format_param=None,
    )
    assert isinstance(resp, JSONResponse)
    import json
    body_bytes = bytes(resp.body) if isinstance(resp.body, memoryview) else resp.body
    payload = json.loads(body_bytes)
    assert "repoId" in payload, f"Expected camelCase 'repoId', got keys: {list(payload)}"
    assert "starCount" in payload, f"Expected camelCase 'starCount', got keys: {list(payload)}"
    assert payload["repoId"] == "abc-123"
    assert payload["starCount"] == 42
    templates.TemplateResponse.assert_not_called()


@pytest.mark.anyio
async def test_negotiate_json_fallback_to_context() -> None:
    """When json_data is None, JSON path returns serialisable context values."""
    req = _make_request(accept="application/json")
    templates = MagicMock()

    resp = await negotiate_response(
        request=req,
        template_name="musehub/pages/repo.html",
        context={"owner": "alice", "repo_slug": "my-beats", "count": 3},
        templates=templates,
        json_data=None,
        format_param=None,
    )
    assert isinstance(resp, JSONResponse)
    import json
    body_bytes = bytes(resp.body) if isinstance(resp.body, memoryview) else resp.body
    payload = json.loads(body_bytes)
    assert payload["owner"] == "alice"
    assert payload["repo_slug"] == "my-beats"
    assert payload["count"] == 3


@pytest.mark.anyio
async def test_negotiate_html_path_calls_template_response() -> None:
    """HTML path delegates to templates.TemplateResponse."""
    req = _make_request(accept="text/html")
    mock_template_resp = MagicMock()
    templates = MagicMock()
    templates.TemplateResponse.return_value = mock_template_resp

    resp = await negotiate_response(
        request=req,
        template_name="musehub/pages/repo.html",
        context={"owner": "alice"},
        templates=templates,
        json_data=None,
        format_param=None,
    )
    templates.TemplateResponse.assert_called_once_with(req, "musehub/pages/repo.html", {"owner": "alice"})
    assert resp is mock_template_resp


@pytest.mark.anyio
async def test_negotiate_format_param_overrides_html_accept() -> None:
    """?format=json forces JSON even when Accept: text/html."""
    req = _make_request(accept="text/html")
    templates = MagicMock()

    model = _SampleModel(repo_id="xyz", star_count=0)
    resp = await negotiate_response(
        request=req,
        template_name="musehub/pages/repo.html",
        context={},
        templates=templates,
        json_data=model,
        format_param="json",
    )
    assert isinstance(resp, JSONResponse)
    templates.TemplateResponse.assert_not_called()


# ---------------------------------------------------------------------------
# negotiate_response — HTMX fragment path (added in issue #554)
# ---------------------------------------------------------------------------


def _make_htmx_request(accept: str = "") -> Any:
    """Build a minimal mock Request with the HX-Request: true header set."""
    req = MagicMock()
    headers: dict[str, str] = {"HX-Request": "true"}
    if accept:
        headers["accept"] = accept
    req.headers = headers
    return req


@pytest.mark.anyio
async def test_negotiate_htmx_fragment_path() -> None:
    """HTMX request + fragment_template → TemplateResponse with fragment template."""
    req = _make_htmx_request()
    mock_fragment_resp = MagicMock()
    templates = MagicMock()
    templates.TemplateResponse.return_value = mock_fragment_resp

    resp = await negotiate_response(
        request=req,
        template_name="musehub/pages/repo.html",
        context={"owner": "alice"},
        templates=templates,
        fragment_template="musehub/fragments/repo_card.html",
    )

    templates.TemplateResponse.assert_called_once_with(
        req, "musehub/fragments/repo_card.html", {"owner": "alice"}
    )
    assert resp is mock_fragment_resp


@pytest.mark.anyio
async def test_negotiate_htmx_no_fragment_template() -> None:
    """HTMX request with no fragment_template falls through to full HTML page."""
    req = _make_htmx_request()
    mock_full_resp = MagicMock()
    templates = MagicMock()
    templates.TemplateResponse.return_value = mock_full_resp

    resp = await negotiate_response(
        request=req,
        template_name="musehub/pages/repo.html",
        context={"owner": "alice"},
        templates=templates,
        fragment_template=None,
    )

    templates.TemplateResponse.assert_called_once_with(
        req, "musehub/pages/repo.html", {"owner": "alice"}
    )
    assert resp is mock_full_resp


@pytest.mark.anyio
async def test_negotiate_htmx_fragment_beats_json() -> None:
    """HTMX fragment path takes priority over Accept: application/json."""
    req = _make_htmx_request(accept="application/json")
    mock_fragment_resp = MagicMock()
    templates = MagicMock()
    templates.TemplateResponse.return_value = mock_fragment_resp

    model = _SampleModel(repo_id="xyz", star_count=7)
    resp = await negotiate_response(
        request=req,
        template_name="musehub/pages/repo.html",
        context={"owner": "alice"},
        templates=templates,
        json_data=model,
        fragment_template="musehub/fragments/repo_card.html",
    )

    templates.TemplateResponse.assert_called_once_with(
        req, "musehub/fragments/repo_card.html", {"owner": "alice"}
    )
    assert not isinstance(resp, JSONResponse)
