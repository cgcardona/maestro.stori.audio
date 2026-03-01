"""HTMX request detection helpers for MuseHub route handlers.

These thin wrappers read HTMX-specific request headers so route handlers can
return either a full-page response (normal browser navigation) or an HTML
fragment (HTMX partial update) without duplicating header-inspection logic.

Usage in a route handler::

    from maestro.api.routes.musehub.htmx_helpers import is_htmx

    @router.get("/musehub/ui/...")
    async def my_view(request: Request) -> HTMLResponse:
        if is_htmx(request):
            return fragment_response(...)
        return full_page_response(...)
"""

from __future__ import annotations

from fastapi import Request


def is_htmx(request: Request) -> bool:
    """Return True when the request was initiated by HTMX (HX-Request: true header present)."""
    return request.headers.get("HX-Request") == "true"


def is_htmx_boosted(request: Request) -> bool:
    """Return True when the request came from an hx-boost link (HX-Boosted: true header present)."""
    return request.headers.get("HX-Boosted") == "true"
