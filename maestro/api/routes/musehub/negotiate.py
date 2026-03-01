"""Content negotiation helper for MuseHub dual-format endpoints.

Every MuseHub URL can serve two audiences from the same path:
- HTML to browsers (default, ``Accept: text/html``)
- JSON to agents/scripts (``Accept: application/json`` or ``?format=json``)
- HTMX fragment to HTMX requests (``HX-Request: true``)

This module provides ``negotiate_response()`` — a single function that route
handlers call after preparing both a Pydantic data model and a Jinja2 template
context.  The function inspects headers and an optional ``?format`` query
parameter, then dispatches to the correct serialiser.

Priority order (first match wins):
1. ``HX-Request: true`` + ``fragment_template`` provided → return bare fragment
2. ``?format=json`` or ``Accept: application/json`` → return JSON
3. Default → return full HTML page

Design rationale:
- One URL, three audiences — HTMX gets fragments, agents get JSON, humans get HTML.
- No separate ``/api/v1/...`` endpoint needed; one handler serves all.
- ``?format=json`` as a fallback for clients that cannot set ``Accept`` headers
  (e.g. browser ``<a>`` links, ``curl`` without ``-H``).
- JSON keys use camelCase via Pydantic ``by_alias=True``, matching the existing
  ``/api/v1/musehub/...`` convention so agents have a uniform contract.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.responses import Response

from maestro.api.routes.musehub.htmx_helpers import is_htmx

logger = logging.getLogger(__name__)


def _wants_json(request: Request, format_param: str | None) -> bool:
    """Return True when the caller prefers a JSON response.

    Decision order (first match wins):
    1. ``?format=json`` query param — explicit override for any client.
    2. ``Accept: application/json`` header — standard HTTP content negotiation.
    3. Default → False (HTML).
    """
    if format_param == "json":
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept


async def negotiate_response(
    *,
    request: Request,
    template_name: str,
    context: dict[str, Any],
    templates: Jinja2Templates,
    json_data: BaseModel | None = None,
    format_param: str | None = None,
    fragment_template: str | None = None,
) -> Response:
    """Return an HTML, fragment, or JSON response based on the caller's preference.

    Route handlers should call this instead of constructing responses directly.
    The handler prepares:
    - ``context``           — Jinja2 template variables for both HTML paths.
    - ``json_data``         — Pydantic model for the JSON path (camelCase serialised).
    - ``fragment_template`` — Bare fragment template for HTMX partial updates.

    Priority order (first match wins):
    1. HTMX request (``HX-Request: true``) + ``fragment_template`` provided
       → returns bare fragment (no ``<html>``, ``<head>``, or nav).
    2. JSON requested (``?format=json`` or ``Accept: application/json``)
       → returns ``JSONResponse`` with camelCase keys.
    3. Default → returns full ``TemplateResponse`` using ``template_name``.

    When ``json_data`` is ``None`` and JSON is requested, ``context`` is
    serialised as-is.  This is a fallback for pages that have no structured
    backend data; prefer providing a Pydantic model whenever possible.

    Args:
        request: The incoming FastAPI request (needed for template rendering).
        template_name: Jinja2 template path for the full-page HTML response.
        context: Template context dict (also used as fallback JSON payload).
        templates: The ``Jinja2Templates`` instance from the route module.
        json_data: Optional Pydantic model to serialise for the JSON path.
        format_param: Value of the ``?format`` query parameter, or ``None``.
        fragment_template: Optional Jinja2 path to a bare fragment template
            (no ``{% extends %}``). When provided, HTMX requests receive this
            fragment instead of the full page.

    Returns:
        ``TemplateResponse`` (fragment or full page), or ``JSONResponse``.
    """
    if is_htmx(request) and fragment_template is not None:
        logger.debug("✅ negotiate_response: HTMX fragment path — %s", fragment_template)
        return templates.TemplateResponse(request, fragment_template, context)

    if _wants_json(request, format_param):
        if json_data is not None:
            payload: dict[str, Any] = json_data.model_dump(by_alias=True, mode="json")
        else:
            payload = {k: v for k, v in context.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
        logger.debug("✅ negotiate_response: JSON path — %s", template_name)
        return JSONResponse(content=payload)

    logger.debug("✅ negotiate_response: HTML path — %s", template_name)
    return templates.TemplateResponse(request, template_name, context)
