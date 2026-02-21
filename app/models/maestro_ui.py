"""Pydantic models for the Maestro Default UI endpoints.

Serves prompt chips, template cards, placeholder strings, individual prompt
templates, and budget status — all consumed by the macOS client's creative
launchpad view.
"""

from typing import Literal

from pydantic import Field

from app.models.base import CamelModel


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

class PromptSection(CamelModel):
    """One accordion section inside a prompt card or template (STORI PROMPT SPEC v2)."""

    heading: str
    content: str


# ---------------------------------------------------------------------------
# Chips
# ---------------------------------------------------------------------------

class PromptChip(CamelModel):
    """Quick-start genre chip for the flow grid."""

    id: str
    title: str
    icon: str = Field(description="SF Symbol name")
    prompt_template_id: str = Field(alias="promptTemplateID")
    full_prompt: str


class ChipsResponse(CamelModel):
    """Response for GET /maestro/prompts/chips."""

    chips: list[PromptChip]


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

class PromptCard(CamelModel):
    """Advanced structured template card for the horizontal carousel."""

    id: str
    title: str
    description: str
    preview_tags: list[str] = Field(max_length=3)
    template_id: str = Field(alias="templateID")
    sections: list[PromptSection]


class CardsResponse(CamelModel):
    """Response for GET /maestro/prompts/cards."""

    cards: list[PromptCard]


# ---------------------------------------------------------------------------
# Prompt template (single lookup)
# ---------------------------------------------------------------------------

class PromptTemplate(CamelModel):
    """Fully expanded prompt template returned by GET /maestro/prompts/{template_id}."""

    id: str
    title: str
    full_prompt: str
    sections: list[PromptSection]


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
