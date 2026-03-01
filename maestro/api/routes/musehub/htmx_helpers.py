"""HTMX request detection and fragment response helpers for MuseHub route handlers.

These thin utilities read HTMX-specific request headers so handlers can
return either a full-page response or a partial fragment without duplicating
header-inspection logic across every route.

Usage pattern for migrated route handlers::

    return await htmx_fragment_or_full(
        request, templates, ctx,
        full_template="musehub/pages/issue_list.html",
        fragment_template="musehub/fragments/issue_rows.html",
    )

Priority order when a request arrives:
1. HTMX partial request (HX-Request: true) + fragment_template → return fragment
2. No HTMX header, or no fragment_template provided → return full page
"""
from __future__ import annotations

import json

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response


def is_htmx(request: Request) -> bool:
    """Return True when the request was initiated by HTMX (HX-Request header present)."""
    return request.headers.get("HX-Request") == "true"


def is_htmx_boosted(request: Request) -> bool:
    """Return True when the request came from an hx-boost link (HX-Boosted header present)."""
    return request.headers.get("HX-Boosted") == "true"


async def htmx_fragment_or_full(
    request: Request,
    templates: Jinja2Templates,
    ctx: dict[str, object],
    full_template: str,
    fragment_template: str | None = None,
) -> Response:
    """Return a fragment for HTMX requests, full page for direct navigation.

    When HTMX sends ``HX-Request: true`` and a ``fragment_template`` is provided,
    the response contains only the fragment — no ``<html>``, ``<head>``, or nav.
    Direct browser navigation (bookmark, refresh, first load) always receives
    the complete page that extends ``base.html``.

    Args:
        request: The incoming FastAPI request.
        templates: The ``Jinja2Templates`` instance from the route module.
        ctx: Template context dict shared by both full and fragment templates.
        full_template: Jinja2 path to the full-page template (extends base.html).
        fragment_template: Jinja2 path to the bare fragment template (no extends).
            When ``None``, always returns the full page regardless of request type.

    Returns:
        ``TemplateResponse`` using either ``fragment_template`` or ``full_template``.
    """
    if is_htmx(request) and fragment_template is not None:
        return templates.TemplateResponse(request, fragment_template, ctx)
    return templates.TemplateResponse(request, full_template, ctx)


def htmx_trigger(response: Response, event: str, detail: dict[str, object] | None = None) -> None:
    """Set ``HX-Trigger`` header to fire a client-side event after the swap.

    Use for toast notifications, badge refreshes, and other side effects that
    should happen after HTMX swaps the response into the DOM::

        htmx_trigger(response, "toast", {"message": "Issue closed", "type": "success"})

    Args:
        response: The Starlette/FastAPI response object to mutate.
        event: The client-side event name HTMX will dispatch.
        detail: Optional payload attached to the event. When ``None``, the
            event fires with ``True`` as its value (a simple signal).
    """
    payload: dict[str, object] = {event: detail} if detail is not None else {event: True}
    response.headers["HX-Trigger"] = json.dumps(payload)


def htmx_redirect(url: str) -> Response:
    """Return an ``HX-Redirect`` response that redirects HTMX without a full page reload.

    The browser URL bar updates and HTMX fetches the target page, but the
    navigation happens client-side rather than issuing a 302 that the browser
    follows before HTMX can intercept.

    Args:
        url: The target URL the client should navigate to.

    Returns:
        A 200 response with the ``HX-Redirect`` header set; HTMX performs the redirect.
    """
    r = Response(status_code=200)
    r.headers["HX-Redirect"] = url
    return r
