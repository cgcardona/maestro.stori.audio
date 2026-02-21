"""Maestro Default UI endpoints.

Serves the creative launchpad data consumed by the macOS client:
  - Rotating placeholder strings for the hero prompt input
  - Quick-start genre chips (flow grid)
  - Advanced structured template cards (horizontal carousel)
  - Individual prompt template lookup
  - Focused budget / Creative Fuel status
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_valid_token
from app.data.maestro_ui import CARDS, CHIPS, PLACEHOLDERS, TEMPLATES
from app.db import get_db, User, UsageLog
from app.models.maestro_ui import (
    BudgetState,
    BudgetStatusResponse,
    CardsResponse,
    ChipsResponse,
    PlaceholdersResponse,
    PromptTemplate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_budget_state(remaining: float) -> BudgetState:
    """Compute the fuel state from the remaining budget.

    Thresholds are authoritative and must match the frontend derivation:
        remaining <= 0      → "exhausted"
        remaining <  0.25   → "critical"
        remaining <  1.0    → "low"
        else                → "normal"
    """
    if remaining <= 0:
        return "exhausted"
    if remaining < 0.25:
        return "critical"
    if remaining < 1.0:
        return "low"
    return "normal"


# ---------------------------------------------------------------------------
# 1. Placeholders (public, cacheable)
# ---------------------------------------------------------------------------


@router.get(
    "/maestro/ui/placeholders",
    response_model=PlaceholdersResponse,
    response_model_by_alias=True,
)
async def get_placeholders():
    """Rotating placeholder strings for the hero prompt input field.

    Returns at least 3 strings; the client cycles through them every 4 seconds.
    No auth required.
    """
    return PlaceholdersResponse(placeholders=PLACEHOLDERS)


# ---------------------------------------------------------------------------
# 2. Chips
# ---------------------------------------------------------------------------


@router.get(
    "/maestro/prompts/chips",
    response_model=ChipsResponse,
    response_model_by_alias=True,
)
async def get_prompt_chips():
    """Quick-start genre chips for the flow grid.

    No auth required. Each chip contains a fullPrompt string that is injected
    directly into the hero prompt input when the user taps the chip.
    """
    return ChipsResponse(chips=CHIPS)


# ---------------------------------------------------------------------------
# 3. Cards
# ---------------------------------------------------------------------------


@router.get(
    "/maestro/prompts/cards",
    response_model=CardsResponse,
    response_model_by_alias=True,
)
async def get_prompt_cards():
    """Advanced structured template cards for the horizontal carousel.

    No auth required. Each card contains 5 sections following the
    STORI PROMPT SPEC v2 format.
    """
    return CardsResponse(cards=CARDS)


# ---------------------------------------------------------------------------
# 4. Single template lookup
# ---------------------------------------------------------------------------


@router.get(
    "/maestro/prompts/{template_id}",
    response_model=PromptTemplate,
    response_model_by_alias=True,
)
async def get_prompt_template(template_id: str):
    """Fetch a single fully-expanded prompt template by ID.

    Template IDs are the same slugs used in chips (promptTemplateID) and cards
    (templateID): lofi_chill, dark_trap, jazz_trio, synthwave, cinematic,
    funk_groove, ambient, deep_house, full_production, beat_lab, mood_piece.
    """
    template = TEMPLATES.get(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )
    return template


# ---------------------------------------------------------------------------
# 5. Budget / Creative Fuel status
# ---------------------------------------------------------------------------


@router.get(
    "/maestro/budget/status",
    response_model=BudgetStatusResponse,
    response_model_by_alias=True,
)
async def get_budget_status(
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """Focused budget status for the Creative Fuel UI.

    Returns remaining/total budget, the derived fuel state, and the number of
    compose sessions used this billing period.  Auth required.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID.",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please register first.",
        )

    count_result = await db.execute(
        select(func.count(UsageLog.id)).where(UsageLog.user_id == user_id)
    )
    sessions_used: int = count_result.scalar() or 0

    return BudgetStatusResponse(
        remaining=user.budget_remaining,
        total=user.budget_limit,
        state=_derive_budget_state(user.budget_remaining),
        sessions_used=sessions_used,
    )
