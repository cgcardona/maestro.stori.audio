"""Content negotiation helper for MuseHub dual-format endpoints.

Every MuseHub URL can serve two audiences from the same path:
- HTML to browsers (default, ``Accept: text/html``)
- JSON to agents/scripts (``Accept: application/json`` or ``?format=json``)

This module provides two helpers:

``negotiate_response()`` — inspects the ``Accept`` header and an optional
``?format`` query parameter, then dispatches to the correct serialiser.

``htmx_fragment_or_full()`` — for SSR pages migrated to HTMX: inspects the
``HX-Request`` header to decide whether to return just the inner fragment
(for HTMX partial-page swaps) or the complete page template (for full
navigations).

Design rationale:
- One URL, two audiences — agents get structured JSON, humans get rich HTML.
- No separate ``/api/v1/...`` endpoint needed; one handler serves both.
- ``?format=json`` as a fallback for clients that cannot set ``Accept`` headers
  (e.g. browser ``<a>`` links, ``curl`` without ``-H``).
- JSON keys use camelCase via Pydantic ``by_alias=True``, matching the existing
  ``/api/v1/musehub/...`` convention so agents have a uniform contract.
- ``htmx_fragment_or_full`` enables progressive-enhancement SSR: the same
  endpoint serves a full page for direct navigation and a fragment for HTMX
  swaps, eliminating the need for separate ``/fragment`` routes.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.responses import Response

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


def _is_htmx_request(request: Request) -> bool:
    """Return True when the caller is an HTMX partial-page request.

    HTMX sets ``HX-Request: true`` on every XHR it issues.  We use this to
    decide whether to return just the inner fragment (no ``<html>`` wrapper)
    or the complete page template for direct navigation.
    """
    return request.headers.get("hx-request", "").lower() == "true"


async def negotiate_response(
    *,
    request: Request,
    template_name: str,
    context: dict[str, Any],
    templates: Jinja2Templates,
    json_data: BaseModel | None = None,
    format_param: str | None = None,
) -> Response:
    """Return an HTML or JSON response based on the caller's preference.

    Route handlers should call this instead of constructing responses directly.
    The handler prepares:
    - ``context``   — Jinja2 template variables for the HTML path.
    - ``json_data`` — Pydantic model for the JSON path (camelCase serialised).

    When ``json_data`` is ``None`` and JSON is requested, ``context`` is
    serialised as-is.  This is a fallback for pages that have no structured
    backend data; prefer providing a Pydantic model whenever possible.

    Args:
        request: The incoming FastAPI request (needed for template rendering).
        template_name: Jinja2 template path relative to the templates dir.
        context: Template context dict (also used as fallback JSON payload).
        templates: The ``Jinja2Templates`` instance from the route module.
        json_data: Optional Pydantic model to serialise for the JSON path.
        format_param: Value of the ``?format`` query parameter, or ``None``.

    Returns:
        ``JSONResponse`` with camelCase keys, or ``TemplateResponse`` for HTML.
    """
    if _wants_json(request, format_param):
        if json_data is not None:
            payload: dict[str, Any] = json_data.model_dump(by_alias=True, mode="json")
        else:
            payload = {k: v for k, v in context.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
        logger.debug("✅ negotiate_response: JSON path — %s", template_name)
        return JSONResponse(content=payload)

    logger.debug("✅ negotiate_response: HTML path — %s", template_name)
    return templates.TemplateResponse(request, template_name, context)


async def htmx_fragment_or_full(
    request: Request,
    templates: Jinja2Templates,
    context: dict[str, Any],
    *,
    full_template: str,
    fragment_template: str,
    json_data: BaseModel | None = None,
    format_param: str | None = None,
) -> Response:
    """Return a JSON, HTMX fragment, or full-page response based on request type.

    Decision order (first match wins):
    1. ``?format=json`` or ``Accept: application/json`` → JSON (camelCase, same
       contract as ``negotiate_response``).
    2. ``HX-Request: true`` → fragment template only (no ``<html>`` wrapper).
    3. Default → full page template including base layout.

    Used by SSR pages that support HTMX partial-page updates and JSON access.
    Both HTML paths share the same ``context`` — the fragment template is
    designed to render the inner content that the full template wraps.

    Args:
        request: Incoming FastAPI request.
        templates: ``Jinja2Templates`` instance from the calling module.
        context: Shared template context dict (same for both HTML paths).
        full_template: Path (relative to templates dir) of the full-page
            template that extends ``musehub/base.html``.
        fragment_template: Path of the fragment-only template that does NOT
            extend base (no ``<html>`` wrapper).
        json_data: Optional Pydantic model to serialise for the JSON path.
            When absent and JSON is requested, ``context`` is serialised.
        format_param: Value of the ``?format`` query parameter, or ``None``.

    Returns:
        ``JSONResponse``, or ``TemplateResponse`` for the fragment or full page.
    """
    if _wants_json(request, format_param):
        if json_data is not None:
            payload: dict[str, Any] = json_data.model_dump(by_alias=True, mode="json")
        else:
            payload = {
                k: v
                for k, v in context.items()
                if isinstance(v, (str, int, float, bool, list, dict, type(None)))
            }
        logger.debug("✅ htmx_fragment_or_full: JSON path — %s", full_template)
        return JSONResponse(content=payload)

    is_htmx = _is_htmx_request(request)
    template_name = fragment_template if is_htmx else full_template
    logger.debug(
        "✅ htmx_fragment_or_full: %s path — %s",
        "fragment" if is_htmx else "full",
        template_name,
    )
    return templates.TemplateResponse(request, template_name, context)
