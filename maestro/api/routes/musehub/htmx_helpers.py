"""HTMX request detection helpers for MuseHub route handlers.

These thin utilities read HTMX-specific request headers so handlers can
return either a full-page response or a partial fragment without duplicating
header-inspection logic across every route.
"""

from __future__ import annotations

from fastapi import Request


def is_htmx(request: Request) -> bool:
    """Return True when the request was initiated by HTMX (HX-Request header present)."""
    return request.headers.get("HX-Request") == "true"


def is_htmx_boosted(request: Request) -> bool:
    """Return True when the request came from an hx-boost link (HX-Boosted header present)."""
    return request.headers.get("HX-Boosted") == "true"
