"""
Static seed data for the Maestro Default UI endpoints.

Content is returned verbatim by the API. When a CMS or per-user
personalisation is added, these become the fallback defaults.
"""

from app.data.maestro_ui.prompt_pool import PLACEHOLDERS, PROMPT_POOL, ALL_PROMPT_IDS
from app.data.maestro_ui.templates import TEMPLATES, ALL_TEMPLATE_IDS

__all__ = [
    "PLACEHOLDERS",
    "PROMPT_POOL",
    "ALL_PROMPT_IDS",
    "TEMPLATES",
    "ALL_TEMPLATE_IDS",
]
