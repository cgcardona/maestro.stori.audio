"""
Stori Maestro API - Composition Endpoints

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

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.models.requests import MaestroRequest
from app.core.maestro_handlers import UsageTracker, orchestrate
from app.core.sanitize import normalise_user_input
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
        "expiresAt": expires_at.isoformat(),
        "expiresInSeconds": max(0, exp_timestamp - int(datetime.now(timezone.utc).timestamp())),
    }

    user_id = token_claims.get("sub")
    if user_id:
        try:
            from sqlalchemy import select
            from app.db.models import User

            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if user:
                response["budgetRemaining"] = user.budget_remaining
                response["budgetLimit"] = user.budget_limit
        except Exception as e:
            logger.warning(f"Could not fetch budget: {e}")

    return response


@router.post("/maestro/stream")
@limiter.limit("20/minute")
async def stream_maestro(
    request: Request,
    maestro_request: MaestroRequest,
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

    if user_id:
        try:
            await check_budget(db, user_id)
        except InsufficientBudgetError as e:
            raise HTTPException(
                status_code=402,
                detail={
                    "message": "Insufficient budget",
                    "budgetRemaining": e.budget_remaining,
                },
            )
        except BudgetError:
            pass

    selected_model = get_model_or_default(maestro_request.model)
    usage_tracker = UsageTracker()

    # Normalise the prompt: strip control chars, invisible Unicode, and
    # normalise line endings. Runs after Pydantic validation (which already
    # rejected null bytes and enforced max_length) so this is a belt-and-
    # suspenders pass for anything Pydantic doesn't catch.
    safe_prompt = normalise_user_input(maestro_request.prompt)

    # Load conversation history if conversation_id is provided.
    # Ownership check: join through Conversation so we only load messages
    # that belong to the authenticated user — prevents IDOR where a caller
    # could supply another user's conversation_id.
    conversation_history: list[dict[str, Any]] = []
    if maestro_request.conversation_id and user_id:
        try:
            from app.db.models import Conversation, ConversationMessage
            from sqlalchemy import select

            stmt = (
                select(ConversationMessage)
                .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
                .where(
                    ConversationMessage.conversation_id == maestro_request.conversation_id,
                    Conversation.user_id == user_id,
                )
                .order_by(ConversationMessage.timestamp)
            )

            result = await db.execute(stmt)
            messages = result.scalars().all()

            # Convert to chat format
            for msg in messages:
                conversation_history.append({
                    "role": msg.role,
                    "content": msg.content
                })

        except Exception as e:
            logger.warning(f"Failed to load conversation history: {e}")

    async def stream_with_budget():
        _seq = 0

        def _with_seq(event_str: str) -> str:
            """Inject monotonic seq counter into every SSE event."""
            nonlocal _seq
            if not event_str.startswith("data: "):
                return event_str
            _seq += 1
            try:
                data = json.loads(event_str[6:].strip())
                data["seq"] = _seq
                return f"data: {json.dumps(data)}\n\n"
            except Exception:
                return event_str

        try:
            async for event in orchestrate(
                safe_prompt,
                maestro_request.project,
                model=selected_model,
                usage_tracker=usage_tracker,
                conversation_id=maestro_request.conversation_id,
                user_id=user_id,
                conversation_history=conversation_history,
                is_cancelled=request.is_disconnected,
                quality_preset=maestro_request.quality_preset,
            ):
                yield _with_seq(event)

            # Deduct budget
            if user_id and (usage_tracker.prompt_tokens > 0 or usage_tracker.completion_tokens > 0):
                try:
                    cost_cents = calculate_cost_cents(
                        usage_tracker.prompt_tokens,
                        usage_tracker.completion_tokens,
                        selected_model,
                    )

                    await deduct_budget(
                        db, user_id, cost_cents,
                        safe_prompt, selected_model,
                        usage_tracker.prompt_tokens, usage_tracker.completion_tokens,
                        store_prompt=maestro_request.store_prompt,
                    )
                    await db.commit()
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

    return StreamingResponse(
        stream_with_budget(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/maestro/preview")
@limiter.limit("30/minute")
async def preview_maestro(
    request: Request,
    maestro_request: MaestroRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Preview a composition plan without executing.

    Returns the plan that would be generated for user review.
    """
    selected_model = get_model_or_default(maestro_request.model)
    llm = LLMClient(model=selected_model)

    safe_prompt = normalise_user_input(maestro_request.prompt)

    try:
        route = await get_intent_result_with_llm(
            safe_prompt,
            maestro_request.project,
            llm
        )

        if route.sse_state != SSEState.COMPOSING:
            return {
                "previewAvailable": False,
                "reason": f"Preview only available for COMPOSING mode (got {route.sse_state.value})",
                "intent": route.intent.value,
                "sseState": route.sse_state.value,
            }

        preview_result = await preview_plan(
            safe_prompt,
            maestro_request.project or {},
            route,
            llm
        )

        return {
            "previewAvailable": True,
            "preview": preview_result,
            "intent": route.intent.value,
            "sseState": route.sse_state.value,
        }

    finally:
        await llm.close()
