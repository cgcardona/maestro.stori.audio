"""HTMX helper utilities for MuseHub SSR routes.

Provides thin utilities that read HTMX-specific request headers, plus
``htmx_fragment_or_full`` — a single function that route handlers call after
building a template context to return either an HTMX fragment or a full page.

Design rationale: centralise HTMX header detection so handlers stay thin —
they build context, then call these helpers rather than inspecting headers.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

logger = logging.getLogger(__name__)


def is_htmx(request: Request) -> bool:
    """Return True when the request was initiated by HTMX (HX-Request header present)."""
    return request.headers.get("HX-Request") == "true"


def is_htmx_boosted(request: Request) -> bool:
    """Return True when the request came from an hx-boost link (HX-Boosted header present)."""
    return request.headers.get("HX-Boosted") == "true"


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
    template = fragment_template if is_htmx(request) else full_template
    logger.debug("🔀 htmx_fragment_or_full → %s (htmx=%s)", template, is_htmx(request))
    return templates.TemplateResponse(request, template, context)
