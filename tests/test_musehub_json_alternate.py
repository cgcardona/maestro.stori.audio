"""Tests for MuseHub JSON alternate content negotiation.

Verifies:
- Accept: application/json returns JSONResponse with data/meta envelope
- Accept: text/html (or no header) returns the HTML path
- Bot User-Agents receive X-MuseHub-JSON-Available header
- Non-bot User-Agents do NOT receive X-MuseHub-JSON-Available header
- Helper function behaviour in isolation
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.responses import Response

from maestro.api.routes.musehub.json_alternate import (
    add_json_available_header,
    is_bot_user_agent,
    json_or_html,
)


# ---------------------------------------------------------------------------
# Minimal test app that exercises json_or_html via a real ASGI route
# ---------------------------------------------------------------------------

_app = FastAPI()


@_app.get("/test-page")
async def _test_page(request: Request) -> Response:
    """Minimal route exercising json_or_html."""
    ctx = {"title": "Test", "value": 42}
    return json_or_html(
        request,
        lambda: HTMLResponse(content="<html>test</html>"),
        ctx,
    )


@_app.get("/bot-header-test")
async def _bot_header_test(request: Request) -> Response:
    """Route that exercises add_json_available_header."""
    response = HTMLResponse(content="<html>ok</html>")
    return add_json_available_header(response, request)


_client = TestClient(_app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# json_or_html — content negotiation
# ---------------------------------------------------------------------------


class TestJsonOrHtml:
    """json_or_html dispatches based on Accept header."""

    def test_accept_json_returns_json_response(self) -> None:
        resp = _client.get("/test-page", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        assert "data" in body
        assert "meta" in body

    def test_json_data_envelope_contains_context(self) -> None:
        resp = _client.get("/test-page", headers={"Accept": "application/json"})
        data = resp.json()["data"]
        assert data["title"] == "Test"
        assert data["value"] == 42

    def test_json_meta_contains_url(self) -> None:
        resp = _client.get("/test-page", headers={"Accept": "application/json"})
        meta = resp.json()["meta"]
        assert "url" in meta
        assert "test-page" in meta["url"]

    def test_accept_html_returns_html_response(self) -> None:
        resp = _client.get("/test-page", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert b"<html>" in resp.content

    def test_no_accept_header_returns_html(self) -> None:
        resp = _client.get("/test-page")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    def test_accept_star_returns_html(self) -> None:
        resp = _client.get("/test-page", headers={"Accept": "*/*"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    def test_accept_json_with_quality_returns_json(self) -> None:
        resp = _client.get(
            "/test-page",
            headers={"Accept": "application/json;q=0.9, text/html"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------------------
# is_bot_user_agent — User-Agent detection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_is_bot_ua_detects_bot_keyword() -> None:
    """is_bot_user_agent returns True for 'bot' User-Agents."""
    from starlette.testclient import TestClient as _STC

    app = FastAPI()

    @app.get("/ua")
    async def _ua(request: Request) -> Response:
        result = "bot" if is_bot_user_agent(request) else "human"
        return HTMLResponse(content=result)

    client = _STC(app)
    resp = client.get("/ua", headers={"User-Agent": "Googlebot/2.1"})
    assert resp.text == "bot"


@pytest.mark.anyio
async def test_is_bot_ua_detects_claude() -> None:
    app = FastAPI()

    @app.get("/ua")
    async def _ua(request: Request) -> Response:
        result = "bot" if is_bot_user_agent(request) else "human"
        return HTMLResponse(content=result)

    client = TestClient(app)
    resp = client.get("/ua", headers={"User-Agent": "claude-agent/1.0"})
    assert resp.text == "bot"


@pytest.mark.anyio
async def test_is_bot_ua_detects_gpt() -> None:
    app = FastAPI()

    @app.get("/ua")
    async def _ua(request: Request) -> Response:
        result = "bot" if is_bot_user_agent(request) else "human"
        return HTMLResponse(content=result)

    client = TestClient(app)
    resp = client.get("/ua", headers={"User-Agent": "OpenAI-GPT/4"})
    assert resp.text == "bot"


@pytest.mark.anyio
async def test_is_bot_ua_detects_cursor() -> None:
    app = FastAPI()

    @app.get("/ua")
    async def _ua(request: Request) -> Response:
        result = "bot" if is_bot_user_agent(request) else "human"
        return HTMLResponse(content=result)

    client = TestClient(app)
    resp = client.get("/ua", headers={"User-Agent": "Cursor/0.42"})
    assert resp.text == "bot"


@pytest.mark.anyio
async def test_is_bot_ua_returns_false_for_browser() -> None:
    app = FastAPI()

    @app.get("/ua")
    async def _ua(request: Request) -> Response:
        result = "bot" if is_bot_user_agent(request) else "human"
        return HTMLResponse(content=result)

    client = TestClient(app)
    resp = client.get(
        "/ua",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14) AppleWebKit/537.36"
        },
    )
    assert resp.text == "human"


# ---------------------------------------------------------------------------
# add_json_available_header
# ---------------------------------------------------------------------------


class TestAddJsonAvailableHeader:
    """add_json_available_header attaches header only for bot UAs."""

    def test_bot_ua_receives_header(self) -> None:
        resp = _client.get(
            "/bot-header-test", headers={"User-Agent": "claude-agent/1.0"}
        )
        assert resp.headers.get("x-musehub-json-available") == "true"

    def test_browser_ua_does_not_receive_header(self) -> None:
        resp = _client.get(
            "/bot-header-test",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14) AppleWebKit/537.36"
            },
        )
        assert "x-musehub-json-available" not in resp.headers

    def test_no_ua_does_not_receive_header(self) -> None:
        resp = _client.get("/bot-header-test")
        assert "x-musehub-json-available" not in resp.headers

    def test_agent_ua_receives_header(self) -> None:
        resp = _client.get(
            "/bot-header-test", headers={"User-Agent": "my-agent/2.0"}
        )
        assert resp.headers.get("x-musehub-json-available") == "true"


# ---------------------------------------------------------------------------
# Unit tests for is_bot_user_agent in isolation
# ---------------------------------------------------------------------------


class TestIsBotUserAgentUnit:
    """Pure unit tests for the bot UA detection regex."""

    def _make_request(self, ua: str) -> Request:
        """Build a minimal Starlette Request with the given User-Agent."""
        from starlette.datastructures import Headers
        from starlette.types import Scope

        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": Headers(headers={"user-agent": ua}).raw,
        }
        return Request(scope)

    def test_bot_keyword_case_insensitive(self) -> None:
        assert is_bot_user_agent(self._make_request("Moz-Bot/1.0")) is True
        assert is_bot_user_agent(self._make_request("MOZ-BOT/1.0")) is True

    def test_agent_keyword(self) -> None:
        assert is_bot_user_agent(self._make_request("my-agent/1.0")) is True

    def test_claude_keyword(self) -> None:
        assert is_bot_user_agent(self._make_request("claude-code")) is True

    def test_gpt_keyword(self) -> None:
        assert is_bot_user_agent(self._make_request("gpt4-client")) is True

    def test_cursor_keyword(self) -> None:
        assert is_bot_user_agent(self._make_request("Cursor/0.42")) is True

    def test_empty_ua(self) -> None:
        assert is_bot_user_agent(self._make_request("")) is False

    def test_regular_browser(self) -> None:
        assert (
            is_bot_user_agent(
                self._make_request(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
                )
            )
            is False
        )
