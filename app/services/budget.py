"""
Budget management service.

Handles budget checking, cost calculation, and usage logging.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, APPROVED_MODELS
from app.db.models import User, UsageLog

logger = logging.getLogger(__name__)


class BudgetError(Exception):
    """Raised when budget-related operations fail."""
    pass


class InsufficientBudgetError(BudgetError):
    """Raised when user has insufficient budget."""
    def __init__(self, budget_remaining: float, estimated_cost: float = 0):
        self.budget_remaining = budget_remaining
        self.estimated_cost = estimated_cost
        super().__init__(
            f"Insufficient budget. Remaining: ${budget_remaining:.4f}"
        )


def calculate_cost_cents(
    prompt_tokens: int,
    completion_tokens: int,
    model: str,
) -> int:
    """
    Calculate the cost of a request in cents.
    
    Args:
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        model: Model identifier
        
    Returns:
        Cost in cents (integer)
    """
    model_info = APPROVED_MODELS.get(model)
    if not model_info:
        # Use default model pricing if unknown
        model_info = APPROVED_MODELS.get(settings.llm_model, {
            "input_cost": 3.0,  # Default to Claude pricing
            "output_cost": 15.0,
        })
    
    # Costs are per 1M tokens, convert to per-token cost
    input_cost_per_token = float(model_info.get("input_cost", 0) or 0) / 1_000_000
    output_cost_per_token = float(model_info.get("output_cost", 0) or 0) / 1_000_000
    
    # Calculate total cost in dollars
    total_cost = (
        prompt_tokens * input_cost_per_token +
        completion_tokens * output_cost_per_token
    )
    
    # Convert to cents (round up to ensure we don't undercharge)
    cost_cents = int(total_cost * 100) + (1 if total_cost * 100 % 1 > 0 else 0)
    
    return max(cost_cents, 1)  # Minimum 1 cent per request


async def check_budget(
    db: AsyncSession,
    user_id: str,
    minimum_cents: int = 1,
) -> User:
    """
    Check if user has sufficient budget.
    
    Args:
        db: Database session
        user_id: User UUID
        minimum_cents: Minimum required budget in cents
        
    Returns:
        User object if budget is sufficient
        
    Raises:
        BudgetError: If user not found
        InsufficientBudgetError: If budget is exhausted
    """
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise BudgetError(f"User {user_id} not found")
    
    if user.budget_cents < minimum_cents:
        raise InsufficientBudgetError(
            budget_remaining=user.budget_remaining,
        )
    
    return user


async def deduct_budget(
    db: AsyncSession,
    user_id: str,
    cost_cents: int,
    prompt: str | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    store_prompt: bool = True,
) -> tuple[User, UsageLog]:
    """
    Deduct cost from user budget and log the usage.
    
    Args:
        db: Database session
        user_id: User UUID
        cost_cents: Cost in cents to deduct
        prompt: User's prompt (None if opted out)
        model: Model used
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        store_prompt: Whether to store the prompt
        
    Returns:
        tuple of (updated User, UsageLog)
        
    Raises:
        BudgetError: If user not found
        InsufficientBudgetError: If budget would go negative
    """
    # Get user with lock for update
    result = await db.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise BudgetError(f"User {user_id} not found")
    
    # Check if deduction would result in negative balance
    if user.budget_cents < cost_cents:
        raise InsufficientBudgetError(
            budget_remaining=user.budget_cents / 100.0,
            estimated_cost=cost_cents / 100.0,
        )
    
    # Deduct from budget
    user.budget_cents -= cost_cents
    
    # Create usage log
    usage_log = UsageLog(
        user_id=user_id,
        prompt=prompt if store_prompt else None,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_cents=cost_cents,
    )
    db.add(usage_log)
    
    logger.info(
        f"Charged user {user_id[:8]}... ${cost_cents / 100:.4f} for {model}. "
        f"Remaining: ${user.budget_cents / 100:.2f}"
    )
    
    return user, usage_log


async def get_user_budget(
    db: AsyncSession,
    user_id: str,
) -> float | None:
    """
    Get user's remaining budget in dollars.
    
    Args:
        db: Database session
        user_id: User UUID
        
    Returns:
        Budget in dollars, or None if user not found
    """
    result = await db.execute(
        select(User.budget_cents).where(User.id == user_id)
    )
    budget_cents = result.scalar_one_or_none()
    
    if budget_cents is None:
        return None
    
    return budget_cents / 100.0


def validate_model(model: str) -> str:
    """
    Validate and normalize a model identifier.
    
    Args:
        model: Model identifier
        
    Returns:
        Validated model identifier
        
    Raises:
        ValueError: If model is not in approved list
    """
    if model not in APPROVED_MODELS:
        available = ", ".join(sorted(APPROVED_MODELS.keys()))
        raise ValueError(
            f"Model '{model}' is not available. "
            f"Approved models: {available}"
        )
    return model


def get_model_or_default(model: str | None) -> str:
    """
    Get the model to use, falling back to default if not specified or invalid.
    
    Args:
        model: Requested model or None
        
    Returns:
        Model identifier to use
    """
    if model is None:
        return settings.llm_model
    
    if model not in APPROVED_MODELS:
        logger.warning(f"Invalid model '{model}', using default {settings.llm_model}")
        return settings.llm_model
    
    return model


# =============================================================================
# Optimistic Budget Reservation
# =============================================================================

# Estimated costs per model (in cents) - conservative estimates
ESTIMATED_COSTS = {
    "anthropic/claude-3.5-sonnet": 25,  # ~$0.25 per typical request
    "anthropic/claude-3-5-sonnet-20241022": 25,
    "openai/gpt-4o": 20,
    "openai/gpt-4o-mini": 5,
    "openai/o1-preview": 50,
    "openai/o1-mini": 10,
}

DEFAULT_ESTIMATED_COST = 25  # Default estimate in cents


class BudgetReservation:
    """
    Represents a budget reservation that can be released or consumed.
    
    Usage:
        async with reserve_budget(db, user_id, model) as reservation:
            # Do LLM work
            actual_cost = calculate_cost(...)
            reservation.set_actual_cost(actual_cost)
        # Reservation automatically released/consumed on exit
    """
    
    def __init__(
        self,
        db: AsyncSession,
        user_id: str,
        reserved_cents: int,
        model: str,
    ):
        self.db = db
        self.user_id = user_id
        self.reserved_cents = reserved_cents
        self.actual_cost_cents: int | None = None
        self.model = model
        self._released = False
    
    def set_actual_cost(self, cost_cents: int) -> None:
        """set the actual cost after LLM call completes."""
        self.actual_cost_cents = cost_cents
    
    async def release(self) -> None:
        """Release unused reservation back to budget."""
        if self._released:
            return
        
        if self.actual_cost_cents is None:
            # Request failed or was cancelled - release full reservation
            await _release_reservation(self.db, self.user_id, self.reserved_cents)
            logger.info(f"💸 Released full reservation ${self.reserved_cents/100:.4f} for user {self.user_id[:8]}")
        elif self.actual_cost_cents < self.reserved_cents:
            # Release unused portion
            unused = self.reserved_cents - self.actual_cost_cents
            await _release_reservation(self.db, self.user_id, unused)
            logger.info(f"💸 Released unused ${unused/100:.4f} (reserved: ${self.reserved_cents/100:.4f}, actual: ${self.actual_cost_cents/100:.4f})")
        
        self._released = True
    
    async def consume(self, prompt_tokens: int, completion_tokens: int, prompt: str | None = None, store_prompt: bool = True) -> None:
        """Consume the reservation and log usage."""
        if self._released:
            raise BudgetError("Cannot consume already released reservation")
        
        if self.actual_cost_cents is None:
            raise BudgetError("Must set actual cost before consuming")
        
        # Log usage (budget already deducted during reservation)
        usage_log = UsageLog(
            user_id=self.user_id,
            prompt=prompt if store_prompt else None,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cents=self.actual_cost_cents,
        )
        self.db.add(usage_log)
        
        # Release unused portion
        if self.actual_cost_cents < self.reserved_cents:
            unused = self.reserved_cents - self.actual_cost_cents
            await _release_reservation(self.db, self.user_id, unused)
        
        self._released = True
        logger.info(f"✅ Consumed ${self.actual_cost_cents/100:.4f} for user {self.user_id[:8]}")


async def reserve_budget(
    db: AsyncSession,
    user_id: str,
    model: str,
    estimated_cost_cents: int | None = None,
) -> BudgetReservation:
    """
    Optimistically reserve budget before making an LLM call.
    
    This prevents concurrent requests from exceeding the budget.
    
    Args:
        db: Database session
        user_id: User UUID
        model: Model identifier (for cost estimation)
        estimated_cost_cents: Optional override for estimated cost
        
    Returns:
        BudgetReservation that must be released or consumed
        
    Raises:
        BudgetError: If user not found
        InsufficientBudgetError: If estimated cost exceeds budget
    """
    # Get estimated cost
    if estimated_cost_cents is None:
        estimated_cost_cents = ESTIMATED_COSTS.get(model, DEFAULT_ESTIMATED_COST)
    
    # Lock user row and check budget
    result = await db.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise BudgetError(f"User {user_id} not found")
    
    if user.budget_cents < estimated_cost_cents:
        raise InsufficientBudgetError(
            budget_remaining=user.budget_remaining,
            estimated_cost=estimated_cost_cents / 100.0,
        )
    
    # Reserve the estimated cost
    user.budget_cents -= estimated_cost_cents
    await db.flush()
    
    logger.info(f"🔒 Reserved ${estimated_cost_cents/100:.4f} for user {user_id[:8]}")
    
    return BudgetReservation(
        db=db,
        user_id=user_id,
        reserved_cents=estimated_cost_cents,
        model=model,
    )


async def _release_reservation(
    db: AsyncSession,
    user_id: str,
    release_cents: int,
) -> None:
    """Release reservation back to user budget."""
    result = await db.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = result.scalar_one_or_none()
    
    if user:
        user.budget_cents += release_cents
        await db.flush()


def estimate_request_cost(model: str, is_composing: bool = False) -> int:
    """
    Estimate the cost of a request.
    
    Args:
        model: Model identifier
        is_composing: Whether this is a composing request (typically more expensive)
        
    Returns:
        Estimated cost in cents
    """
    base_cost = ESTIMATED_COSTS.get(model, DEFAULT_ESTIMATED_COST)
    
    if is_composing:
        # Composing requests involve planner + generators, roughly 2x cost
        return base_cost * 2
    
    return base_cost
