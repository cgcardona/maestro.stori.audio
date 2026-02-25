"""Pydantic models for the Maestro Default UI endpoints.

Serves prompt inspiration cards, placeholder strings, and budget status —
all consumed by the macOS client's creative launchpad view.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.base import CamelModel


# ---------------------------------------------------------------------------
# Prompt inspiration cards (GET /maestro/prompts, GET /maestro/prompts/{id})
# ---------------------------------------------------------------------------

class PromptItem(CamelModel):
    """One curated STORI PROMPT example returned in the inspiration carousel."""

    id: str
    title: str = Field(description="Human label, e.g. 'Lo-fi boom bap · Cm · 75 BPM'")
    preview: str = Field(description="First 3–4 YAML lines visible in the card")
    full_prompt: str = Field(description="Complete STORI PROMPT YAML — injected verbatim into the input on tap")


class PromptsResponse(CamelModel):
    """Response for GET /maestro/prompts — 4 randomly sampled items."""

    prompts: list[PromptItem]


# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------

class PlaceholdersResponse(CamelModel):
    """Rotating placeholder strings for the hero prompt input."""

    placeholders: list[str]


# ---------------------------------------------------------------------------
# Budget status
# ---------------------------------------------------------------------------

BudgetState = Literal["normal", "low", "critical", "exhausted"]


class BudgetStatusResponse(CamelModel):
    """Focused budget/fuel status for the Creative Fuel UI."""

    remaining: float
    total: float
    state: BudgetState
    sessions_used: int
