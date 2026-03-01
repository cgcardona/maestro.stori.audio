"""HTMX helper utilities for MuseHub SSR routes.

Provides ``htmx_fragment_or_full`` — a single function that route handlers
call after building a template context.  The function detects whether the
caller is an HTMX partial-update request (via the ``HX-Request`` header) and
returns the appropriate template: the fragment for HTMX swaps, the full page
otherwise.

This avoids duplicating the detection logic across every route handler and
keeps the route handlers thin — they build context, then call this function.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

logger = logging.getLogger(__name__)


async def htmx_fragment_or_full(
    request: Request,
    templates: Jinja2Templates,
    context: dict[str, Any],
    *,
    full_template: str,
    fragment_template: str,
) -> Response:
    """Return the fragment template for HTMX requests, the full page otherwise.

    HTMX sends ``HX-Request: true`` on every partial-update fetch.  When this
    header is present we render only the fragment — typically the ``<div>`` that
    HTMX swaps into the page.  When it is absent (full navigation or bookmark
    open) we render the complete page, which includes the fragment inline so
    the initial paint shows real data without a second round-trip.

    Args:
        request:           FastAPI/Starlette ``Request`` object.
        templates:         Jinja2Templates instance configured for the app.
        context:           Template context dict (passed as-is to both templates).
        full_template:     Template path for the full page
                           (e.g. ``musehub/pages/notifications.html``).
        fragment_template: Template path for the HTMX swap target
                           (e.g. ``musehub/fragments/notification_rows.html``).

    Returns:
        A Starlette ``TemplateResponse`` — either the full page or the fragment.
    """
    is_htmx = request.headers.get("HX-Request") == "true"
    template = fragment_template if is_htmx else full_template
    logger.debug("🔀 htmx_fragment_or_full → %s (htmx=%s)", template, is_htmx)
    return templates.TemplateResponse(request, template, context)
