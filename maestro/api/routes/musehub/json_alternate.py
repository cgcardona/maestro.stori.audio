"""JSON alternate response helpers for MuseHub HTML page handlers.

Every MuseHub HTML page is a shell that loads its data client-side via JS.
This module adds machine-readable JSON access to those same URLs so AI agents
and bots can consume the same endpoints without a browser.

Two audiences, one URL:
- Browsers get the rich HTML shell (default).
- Agents send ``Accept: application/json`` and get structured JSON.

Bot detection adds ``X-MuseHub-JSON-Available`` to responses for known bot
User-Agents so they know content negotiation is supported.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Matches bot/AI agent User-Agent strings (case-insensitive).
_BOT_UA_PATTERN = re.compile(r"bot|agent|claude|gpt|cursor", re.IGNORECASE)


def is_bot_user_agent(request: Request) -> bool:
    """Return True when the request originates from a known bot or AI agent.

    Checks the User-Agent header for patterns common to crawlers and AI agents.
    Used to conditionally add ``X-MuseHub-JSON-Available`` so bots discover
    that content negotiation is supported without setting Accept explicitly.
    """
    ua = request.headers.get("user-agent", "")
    return bool(_BOT_UA_PATTERN.search(ua))


def json_or_html(
    request: Request,
    template_fn: Callable[[], Response],
    json_data: dict[str, Any],
) -> Response:
    """Return JSON or HTML based on the caller's Accept header.

    Inspects ``Accept: application/json`` and dispatches accordingly:
    - JSON callers receive ``{"data": <json_data>, "meta": {"url": <url>}}``.
    - All other callers receive the HTML shell from ``template_fn()``.

    The ``meta.url`` field lets agents reconstruct canonical URLs from
    relative links without additional parsing.

    Args:
        request: The incoming FastAPI request.
        template_fn: Zero-argument callable that renders and returns the HTML
            response.  Called lazily — only when the caller wants HTML.
        json_data: Serialisable dict to embed under the ``data`` key.

    Returns:
        ``JSONResponse`` for JSON callers; ``template_fn()`` result otherwise.
    """
    if request.headers.get("accept", "").startswith("application/json"):
        logger.debug("✅ json_or_html: JSON path — %s", str(request.url))
        return JSONResponse(
            content={
                "data": json_data,
                "meta": {"url": str(request.url)},
            }
        )
    logger.debug("✅ json_or_html: HTML path — %s", str(request.url))
    return add_json_available_header(template_fn(), request)


def add_json_available_header(response: Response, request: Request) -> Response:
    """Attach ``X-MuseHub-JSON-Available: true`` to bot responses.

    Agents that do not yet set ``Accept: application/json`` can detect support
    from this header and switch on their next request.  The header is only
    added for known bot User-Agents to avoid polluting browser network traces.

    Args:
        response: The response to annotate (mutated in-place).
        request: The originating request used for User-Agent inspection.

    Returns:
        The same response object (mutated).
    """
    if is_bot_user_agent(request):
        response.headers["X-MuseHub-JSON-Available"] = "true"
    return response
