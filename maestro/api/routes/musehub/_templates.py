"""Shared Jinja2Templates instance for all MuseHub UI route modules.

Every UI module in this package (``ui.py``, ``ui_forks.py``, …) previously
created its own ``Jinja2Templates(directory=…)`` object.  That meant custom
filters had to be registered on each instance independently or, worse, would
silently be missing from some pages.

This module provides a single, pre-configured ``templates`` object that all
UI modules import.  Filters are registered exactly once at import time so
there is no risk of a page rendering without them.

Usage in UI route modules::

    from maestro.api.routes.musehub._templates import templates

    # Use exactly as before — no other changes needed.
    return await negotiate_response(request=request, templates=templates, …)
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from maestro.api.routes.musehub.jinja2_filters import register_musehub_filters

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"

#: Pre-configured Jinja2Templates with all MuseHub custom filters registered.
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
register_musehub_filters(templates.env)
