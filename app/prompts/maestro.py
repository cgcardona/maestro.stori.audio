"""Maestro Prompt — the canonical structured prompt dialect.

``MaestroPrompt`` is the only structured prompt type accepted by Maestro
today.  It subclasses ``StructuredPrompt`` with ``prompt_kind = "maestro"``
to provide a clear, named type for the platform's prompt language.

Future prompt dialects (if any) would subclass ``StructuredPrompt`` with
their own ``prompt_kind`` value; the parser union would expand accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.prompts.base import StructuredPrompt


@dataclass
class MaestroPrompt(StructuredPrompt):
    """Canonical Maestro structured prompt.

    Currently identical to ``StructuredPrompt``.  Exists as a named type so
    downstream code can ``isinstance``-check for the Maestro dialect and to
    provide a clean seam for future dialect-specific invariants.
    """

    prompt_kind: str = "maestro"
