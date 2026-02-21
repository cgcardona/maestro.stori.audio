"""Maestro Default UI endpoints.

Serves the creative launchpad data consumed by the macOS client:
  - Rotating placeholder strings for the hero prompt input
  - 4 randomly sampled STORI PROMPT inspiration cards
  - Individual prompt template lookup
  - Focused budget / Creative Fuel status
"""

import logging
import random

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_valid_token
from app.data.maestro_ui import PLACEHOLDERS, PROMPT_POOL, TEMPLATES
from app.db import get_db, User, UsageLog
from app.models.maestro_ui import (
    BudgetState,
    BudgetStatusResponse,
    PlaceholdersResponse,
    PromptsResponse,
    PromptTemplate,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMPTS_SAMPLE_SIZE = 4


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
# 2. Prompt inspiration cards (random sample)
# ---------------------------------------------------------------------------


@router.get(
    "/maestro/prompts",
    response_model=PromptsResponse,
    response_model_by_alias=True,
)
async def get_prompts():
    """Return 4 randomly sampled STORI PROMPT inspiration cards.

    Each call returns a different random set drawn from a curated pool of
    22 full STORI PROMPTs spanning a wide sonic field. No auth required.

    Each item carries:
      - id         — unique slug
      - title      — human label (e.g. "Lo-fi boom bap · Cm · 75 BPM")
      - preview    — first 3–4 YAML lines visible in the card
      - fullPrompt — complete STORI PROMPT YAML, injected verbatim on tap
    """
    sample_size = min(_PROMPTS_SAMPLE_SIZE, len(PROMPT_POOL))
    sampled = random.sample(PROMPT_POOL, sample_size)
    return PromptsResponse(prompts=sampled)


# ---------------------------------------------------------------------------
# 3. Single template lookup
# ---------------------------------------------------------------------------


@router.get(
    "/maestro/prompts/{template_id}",
    response_model=PromptTemplate,
    response_model_by_alias=True,
)
async def get_prompt_template(template_id: str):
    """Fetch a single fully-expanded prompt template by ID.

    Template IDs: lofi_chill, dark_trap, jazz_trio, synthwave, cinematic,
    funk_groove, ambient, deep_house, full_production, beat_lab, mood_piece.
    Returns 404 if the ID is unknown.
    """
    template = TEMPLATES.get(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )
    return template


# ---------------------------------------------------------------------------
# 4. Budget / Creative Fuel status
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
