"""
User management endpoints.

Handles user registration, profile info, budget tracking, and model listing.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field

from maestro.models.base import CamelModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.config import settings, APPROVED_MODELS, ALLOWED_MODEL_IDS
from maestro.core.llm_client import LLMClient
from maestro.db import get_db, User, UsageLog, AccessToken
from maestro.auth.dependencies import require_valid_token
from maestro.auth.tokens import TokenClaims
from maestro.services.token_service import (
    revoke_token,
    revoke_all_user_tokens,
    get_user_active_tokens,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models

class UserRegisterRequest(CamelModel):
    """Request to register a new user (device)."""
    user_id: str = Field(
        ...,
        description="Device UUID from the app (generated once per install, stored in UserDefaults). Same identifier sent as X-Device-ID on asset requests.",
        examples=["550e8400-e29b-41d4-a716-446655440000"]
    )


class UserResponse(CamelModel):
    """User profile response."""
    user_id: str
    budget_remaining: float = Field(description="Remaining budget in dollars")
    budget_limit: float = Field(description="Total budget limit in dollars")
    usage_count: int | None = Field(default=None, description="Number of requests made")
    created_at: str | None = Field(default=None, description="Account creation timestamp")


class ModelInfo(CamelModel):
    """Information about an available model."""
    id: str
    name: str
    cost_per_1m_input: float = Field(description="Cost per 1M input tokens in dollars")
    cost_per_1m_output: float = Field(description="Cost per 1M output tokens in dollars")
    supports_reasoning: bool = Field(
        description="Whether the model supports extended chain-of-thought reasoning"
    )


class ModelsResponse(CamelModel):
    """Available models response."""
    models: list[ModelInfo]
    default_model: str


class BudgetUpdateRequest(CamelModel):
    """Request to update user budget (admin only)."""
    budget_cents: int = Field(
        ...,
        ge=0,
        description="New budget in cents"
    )


# Endpoints

@router.post("/users/register", response_model=UserResponse, response_model_by_alias=True)
async def register_user(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Register a new user with the default budget.

    Single-identifier architecture: user_id is the app's device UUID (generated once
    per install). No JWT required. Creates or returns user with id = device UUID.
    Issue access codes with --user-id <this same UUID> so JWT sub = device UUID.
    """
    # Validate UUID format
    try:
        uuid.UUID(request.user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id format. Must be a valid UUID."
        )
    
    # Check if user already exists
    result = await db.execute(
        select(User).where(User.id == request.user_id)
    )
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        logger.info(f"User {request.user_id[:8]}... already registered")
        # Get usage count
        count_result = await db.execute(
            select(func.count(UsageLog.id)).where(UsageLog.user_id == request.user_id)
        )
        usage_count = count_result.scalar() or 0
        
        return UserResponse(
            user_id=existing_user.id,
            budget_remaining=existing_user.budget_remaining,
            budget_limit=existing_user.budget_limit,
            usage_count=usage_count,
            created_at=existing_user.created_at.isoformat(),
        )
    
    # Create new user with default budget
    new_user = User(
        id=request.user_id,
        budget_cents=settings.default_budget_cents,
        budget_limit_cents=settings.default_budget_cents,
    )
    db.add(new_user)
    await db.flush()  # Get the created_at timestamp
    await db.commit()  # Commit the transaction to persist the user
    
    logger.info(f"Registered new user {request.user_id[:8]}... with ${settings.default_budget_cents / 100:.2f} budget")
    
    return UserResponse(
        user_id=new_user.id,
        budget_remaining=new_user.budget_remaining,
        budget_limit=new_user.budget_limit,
        usage_count=0,
        created_at=new_user.created_at.isoformat(),
    )


@router.get("/users/me", response_model=UserResponse, response_model_by_alias=True)
async def get_current_user(
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Get the current user's profile and budget info.
    
    Requires a valid access token with a user_id (sub) claim.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID. Please use a token generated with --user-id."
        )
    
    # Get user
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please register first."
        )
    
    # Get usage count
    count_result = await db.execute(
        select(func.count(UsageLog.id)).where(UsageLog.user_id == user_id)
    )
    usage_count = count_result.scalar() or 0
    
    return UserResponse(
        user_id=user.id,
        budget_remaining=user.budget_remaining,
        budget_limit=user.budget_limit,
        usage_count=usage_count,
        created_at=user.created_at.isoformat(),
    )


@router.get("/models", response_model=ModelsResponse, response_model_by_alias=True)
async def list_models() -> ModelsResponse:
    """
    list available models with pricing information.

    Returns only models in ALLOWED_MODEL_IDS, sorted cheapest-first.
    Falls back to all Claude models in APPROVED_MODELS if the allowlist
    produces no results (e.g. OpenRouter slug changed before config update).
    No authentication required.
    """
    allowed = {mid for mid in ALLOWED_MODEL_IDS if mid in APPROVED_MODELS}

    if not allowed:
        logger.warning(
            "ALLOWED_MODEL_IDS produced no matches in APPROVED_MODELS — "
            "falling back to all Claude models. Update ALLOWED_MODEL_IDS in config.py."
        )
        allowed = {mid for mid in APPROVED_MODELS if "claude" in mid}

    models = [
        ModelInfo(
            id=model_id,
            name=str(APPROVED_MODELS[model_id]["name"]),
            cost_per_1m_input=float(APPROVED_MODELS[model_id]["input_cost"]),
            cost_per_1m_output=float(APPROVED_MODELS[model_id]["output_cost"]),
            supports_reasoning=model_id in LLMClient.REASONING_MODELS,
        )
        for model_id in allowed
    ]

    models.sort(key=lambda m: m.cost_per_1m_input)

    default_model = models[0].id if models else settings.llm_model

    return ModelsResponse(
        models=models,
        default_model=default_model,
    )


@router.post("/users/{user_id}/budget", response_model=UserResponse, response_model_by_alias=True)
async def update_user_budget(
    user_id: str,
    request: BudgetUpdateRequest,
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Update a user's budget (admin endpoint).
    
    Requires an admin token (JWT with role=admin claim).
    Generate admin tokens with: python scripts/generate_access_code.py --admin --user-id UUID
    """
    # Check for admin role in token
    if token_claims.get("role") != "admin":
        logger.warning(f"Non-admin user attempted to update budget for {user_id[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required. Use an admin token to modify user budgets."
        )
    # Validate UUID format
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id format. Must be a valid UUID."
        )
    
    # Get user
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found."
        )
    
    # Update budget
    user.budget_cents = request.budget_cents
    user.budget_limit_cents = max(user.budget_limit_cents, request.budget_cents)
    
    logger.info(f"Updated budget for user {user_id[:8]}... to ${request.budget_cents / 100:.2f}")
    
    # Get usage count
    count_result = await db.execute(
        select(func.count(UsageLog.id)).where(UsageLog.user_id == user_id)
    )
    usage_count = count_result.scalar() or 0
    
    return UserResponse(
        user_id=user.id,
        budget_remaining=user.budget_remaining,
        budget_limit=user.budget_limit,
        usage_count=usage_count,
        created_at=user.created_at.isoformat(),
    )


# =============================================================================
# Token Revocation Endpoints
# =============================================================================

class TokenInfo(CamelModel):
    """Information about an access token."""
    id: str
    expires_at: str
    revoked: bool
    created_at: str


class TokenListResponse(CamelModel):
    """list of active tokens."""
    tokens: list[TokenInfo]
    count: int


class RevokeResponse(CamelModel):
    """Response from token revocation."""
    success: bool
    message: str
    revoked_count: int = 0


@router.get("/users/me/tokens", response_model=TokenListResponse, response_model_by_alias=True)
async def list_my_tokens(
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> TokenListResponse:
    """
    list all active tokens for the current user.
    
    Requires a valid access token.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    tokens = await get_user_active_tokens(db, user_id)
    
    return TokenListResponse(
        tokens=[
            TokenInfo(
                id=t.id,
                expires_at=t.expires_at.isoformat(),
                revoked=t.revoked,
                created_at=t.created_at.isoformat(),
            )
            for t in tokens
        ],
        count=len(tokens),
    )


@router.post("/users/me/tokens/revoke-all", response_model=RevokeResponse, response_model_by_alias=True)
async def revoke_my_tokens(
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> RevokeResponse:
    """
    Revoke all tokens for the current user.
    
    Useful if a token may have been compromised.
    Note: The current token being used will also be revoked.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token does not contain user ID."
        )
    
    count = await revoke_all_user_tokens(db, user_id)
    
    return RevokeResponse(
        success=True,
        message=f"Revoked {count} tokens. You will need to obtain a new token.",
        revoked_count=count,
    )


@router.post("/users/{user_id}/tokens/revoke-all", response_model=RevokeResponse, response_model_by_alias=True)
async def admin_revoke_user_tokens(
    user_id: str,
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> RevokeResponse:
    """
    Revoke all tokens for a specific user (admin endpoint).
    
    Requires an admin token.
    """
    # Check for admin role
    if token_claims.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required."
        )
    
    # Validate UUID format
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id format."
        )
    
    count = await revoke_all_user_tokens(db, user_id)
    
    return RevokeResponse(
        success=True,
        message=f"Revoked {count} tokens for user {user_id[:8]}...",
        revoked_count=count,
    )
