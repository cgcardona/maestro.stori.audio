"""
Stori Composer API - Composition Endpoints

Cursor-of-DAWs Architecture:
1. Intent Router classifies prompts → SSEState + tool allowlist
2. LLM fallback for unrecognized intents
3. StateStore for persistent, versioned state with transactions
4. Tool validation with schema + entity reference checking + suggestions
5. Streaming plan execution with incremental feedback
6. Request tracing with correlation IDs

Key safety features:
- Generator tools (Tier 1) are NEVER callable by LLM directly
- Server-side entity ID management (no LLM fabrication)
- Transaction semantics for atomic plan execution
- force_stop_after prevents over-completion
- Tool allowlist + argument validation enforced server-side
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.models.requests import ComposeRequest
from app.core.compose_handlers import UsageTracker, orchestrate
from app.core.intent import get_intent_result_with_llm, SSEState
from app.core.llm_client import LLMClient
from app.core.planner import preview_plan
from app.core.sse_utils import sse_event
from app.auth.dependencies import require_valid_token
from app.db import get_db
from app.services.budget import (
    check_budget,
    deduct_budget,
    calculate_cost_cents,
    get_model_or_default,
    InsufficientBudgetError,
    BudgetError,
)

router = APIRouter()
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/validate-token")
async def validate_token(
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """Validate access token and return budget info."""
    exp_timestamp = token_claims.get("exp", 0)
    expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)

    response = {
        "valid": True,
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": max(0, exp_timestamp - int(datetime.now(timezone.utc).timestamp())),
    }

    user_id = token_claims.get("sub")
    if user_id:
        try:
            from sqlalchemy import select
            from app.db.models import User

            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if user:
                response["budget_remaining"] = user.budget_remaining
                response["budget_limit"] = user.budget_limit
        except Exception as e:
            logger.warning(f"Could not fetch budget: {e}")

    return response


@router.post("/compose/stream")
@limiter.limit("20/minute")
async def stream_compose(
    request: Request,
    compose_request: ComposeRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Streaming composition endpoint (SSE).

    Uses Cursor-of-DAWs architecture with:
    - Intent classification with LLM fallback
    - Server-side StateStore with transactions
    - Tool validation + entity suggestions
    - Request tracing with correlation IDs
    """
    user_id = token_claims.get("sub")
    budget_remaining = None

    if user_id:
        try:
            user = await check_budget(db, user_id)
            budget_remaining = user.budget_remaining
        except InsufficientBudgetError as e:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "Insufficient budget",
                    "budget_remaining": e.budget_remaining,
                }
            )
        except BudgetError:
            pass

    selected_model = get_model_or_default(compose_request.model)
    usage_tracker = UsageTracker()

    # Load conversation history if conversation_id is provided
    conversation_history: list[dict[str, Any]] = []
    if compose_request.conversation_id:
        try:
            from app.db.models import ConversationMessage
            from sqlalchemy import select

            stmt = select(ConversationMessage).where(
                ConversationMessage.conversation_id == compose_request.conversation_id
            ).order_by(ConversationMessage.timestamp)

            result = await db.execute(stmt)
            messages = result.scalars().all()

            # Convert to chat format
            for msg in messages:
                conversation_history.append({
                    "role": msg.role,
                    "content": msg.content
                })

            logger.info(f"Loaded {len(conversation_history)} messages for conversation {compose_request.conversation_id}")
        except Exception as e:
            logger.warning(f"Failed to load conversation history: {e}")

    async def stream_with_budget():
        nonlocal budget_remaining

        try:
            async for event in orchestrate(
                compose_request.prompt,
                compose_request.project,
                model=selected_model,
                usage_tracker=usage_tracker,
                conversation_id=compose_request.conversation_id,
                user_id=user_id,
                conversation_history=conversation_history,
                execution_mode=compose_request.execution_mode,
            ):
                yield event

            # Deduct budget
            if user_id and (usage_tracker.prompt_tokens > 0 or usage_tracker.completion_tokens > 0):
                try:
                    cost_cents = calculate_cost_cents(
                        usage_tracker.prompt_tokens,
                        usage_tracker.completion_tokens,
                        selected_model,
                    )

                    user, _ = await deduct_budget(
                        db, user_id, cost_cents,
                        compose_request.prompt, selected_model,
                        usage_tracker.prompt_tokens, usage_tracker.completion_tokens,
                        store_prompt=compose_request.store_prompt,
                    )
                    await db.commit()
                    budget_remaining = user.budget_remaining

                    yield await sse_event({
                        "type": "budget_update",
                        "budget_remaining": budget_remaining,
                        "cost": cost_cents / 100.0,
                    })
                except Exception as e:
                    logger.error(f"Budget deduction failed: {e}")

        except Exception as e:
            logger.exception(f"Stream error: {e}")
            yield await sse_event({"type": "error", "message": str(e)})

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

    if budget_remaining is not None:
        headers["X-Budget-Remaining"] = str(budget_remaining)

    return StreamingResponse(
        stream_with_budget(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/compose/preview")
@limiter.limit("30/minute")
async def preview_compose(
    request: Request,
    compose_request: ComposeRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Preview a composition plan without executing.

    Returns the plan that would be generated for user review.
    """
    selected_model = get_model_or_default(compose_request.model)
    llm = LLMClient(model=selected_model)

    try:
        route = await get_intent_result_with_llm(
            compose_request.prompt,
            compose_request.project,
            llm
        )

        if route.sse_state != SSEState.COMPOSING:
            return {
                "preview_available": False,
                "reason": f"Preview only available for COMPOSING mode (got {route.sse_state.value})",
                "intent": route.intent.value,
                "sse_state": route.sse_state.value,
            }

        preview_result = await preview_plan(
            compose_request.prompt,
            compose_request.project or {},
            route,
            llm
        )

        return {
            "preview_available": True,
            "preview": preview_result,
            "intent": route.intent.value,
            "sse_state": route.sse_state.value,
        }

    finally:
        await llm.close()
