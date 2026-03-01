"""Routes sub-package for AgentCeption.

Split into two routers:
- ``api``:  JSON endpoints consumed by HTMX and future clients.
- ``ui``:   HTML endpoints rendered via Jinja2 templates.

Both are registered in ``app.py`` via ``app.include_router()``.
"""
from __future__ import annotations
