"""Shared Jinja2 templates instance for MuseHub UI route handlers.

Route handlers that need a templates instance should import from here
rather than constructing their own, so MuseHub filters are registered
exactly once and available across all templates.

Usage::

    from maestro.api.routes.musehub._templates import templates

    # then in a handler:
    return templates.TemplateResponse(request, "musehub/pages/foo.html", ctx)
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from maestro.api.routes.musehub.jinja2_filters import register_musehub_filters

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
register_musehub_filters(templates.env)
