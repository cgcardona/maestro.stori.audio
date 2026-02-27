"""
Maestro API — Composition Endpoints

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
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models.base import CamelModel

from app.config import settings
from app.contracts.project_types import ProjectContext
from app.contracts.json_types import ToolCallPreviewDict
from app.contracts.pydantic_types import PydanticJson, wrap_dict
from app.models.requests import MaestroRequest
from app.core.maestro_handlers import UsageTracker, orchestrate
from app.core.sanitize import normalise_user_input
from app.contracts.llm_types import (
    AssistantMessage,
    ChatMessage,
    SystemMessage,
    UserMessage,
)
from app.core.composition_limiter import (
    get_composition_limiter,
    CompositionLimitExceeded,
)
from app.core.intent import get_intent_result_with_llm, SSEState
from app.core.llm_client import LLMClient
from app.core.planner import preview_plan
from app.core.stream_utils import SSESequencer
from app.protocol.emitter import ProtocolSerializationError, emit
from app.protocol.events import CompleteEvent, ErrorEvent
from app.auth.dependencies import TokenClaims, require_valid_token
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


# ── Response models ───────────────────────────────────────────────────────


class ValidateTokenResponse(CamelModel):
    """JWT validation result returned by ``GET /validate-token``.

    Confirms that the bearer token is valid and not expired.  When the token's
    ``sub`` claim resolves to a known user, budget fields are also populated
    so the DAW can display a live credit balance without a separate request.

    Wire format: camelCase (via ``CamelModel``).

    Attributes:
        valid: Always ``True`` — the endpoint raises ``401`` rather than
            returning ``False`` for invalid tokens.
        expires_at: ISO-8601 UTC timestamp at which the token expires
            (e.g. ``"2026-03-01T12:00:00+00:00"``).
        expires_in_seconds: Seconds remaining until token expiry, clamped to
            ``0`` if the token is already past its ``exp`` claim.
        budget_remaining: Credit balance remaining for this user (in cents),
            or ``None`` if the user record could not be fetched.
        budget_limit: Maximum credit balance for this user (in cents), or
            ``None`` if the user record could not be fetched.
    """

    valid: bool = Field(
        description=(
            "Always True — the endpoint raises 401 rather than returning False "
            "for invalid tokens."
        )
    )
    expires_at: str = Field(
        description="ISO-8601 UTC timestamp at which the token expires."
    )
    expires_in_seconds: int = Field(
        description="Seconds remaining until token expiry, clamped to 0 if already past exp."
    )
    budget_remaining: float | None = Field(
        default=None,
        description=(
            "Credit balance remaining for this user (in cents), "
            "or None if the user record could not be fetched."
        ),
    )
    budget_limit: float | None = Field(
        default=None,
        description=(
            "Maximum credit balance for this user (in cents), "
            "or None if the user record could not be fetched."
        ),
    )


class ToolCallWire(CamelModel):
    """Single tool-call step in a plan preview — wire shape for JSON serialisation.

    Uses ``PydanticJson`` for ``params`` so Pydantic can serialise arbitrary
    JSON without recursion from the raw ``dict[str, JSONValue]`` type alias.
    """

    name: str
    params: dict[str, PydanticJson] = Field(default_factory=dict)


class PlanPreviewResponse(CamelModel):
    """Execution plan produced by ``POST /maestro/preview`` (without executing).

    Populated from ``PlanPreview`` (a ``TypedDict``) returned by
    ``preview_plan()``.  All fields are optional because the planner may
    produce an empty or invalid plan when the intent is unrecognised.

    Wire format: camelCase (via ``CamelModel``).

    Attributes:
        valid: ``True`` if the plan passed validation; ``False`` if it has
            structural errors (e.g. references unknown entities).
        total_steps: Total number of tool-call steps in the plan.
        generations: Number of generator tool calls (Tier 1 — audio
            generation) in the plan.
        edits: Number of editor tool calls (Tier 2 — MIDI / structure edits)
            in the plan.
        tool_calls: Ordered list of tool-call descriptors as raw dicts,
            each with keys ``name``, ``arguments``, and optional metadata.
        notes: Human-readable annotations produced by the planner (e.g.
            ``"Detected 4/4 time signature from prompt"``).
        errors: Validation errors that make the plan invalid or unexecutable.
        warnings: Non-fatal warnings (e.g. ``"Region ID not found in project"``).
    """

    valid: bool | None = Field(
        default=None,
        description="True if the plan passed validation; False if it has structural errors.",
    )
    total_steps: int | None = Field(
        default=None,
        description="Total number of tool-call steps in the plan.",
    )
    generations: int | None = Field(
        default=None,
        description="Number of generator tool calls (Tier 1 — audio generation) in the plan.",
    )
    edits: int | None = Field(
        default=None,
        description="Number of editor tool calls (Tier 2 — MIDI / structure edits) in the plan.",
    )
    tool_calls: list[ToolCallWire] = Field(
        default_factory=list,
        description=(
            "Ordered list of tool-call descriptors, "
            "each with 'name' and 'params'."
        ),
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Human-readable annotations produced by the planner.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Validation errors that make the plan invalid or unexecutable.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings (e.g. 'Region ID not found in project').",
    )


class PreviewMaestroResponse(CamelModel):
    """Response from ``POST /maestro/preview``.

    Top-level envelope for a plan preview.  When ``preview_available`` is
    ``True``, the ``preview`` field contains the full ``PlanPreviewResponse``.
    When ``False``, ``reason`` explains why a preview could not be produced
    (e.g. the prompt was classified as REASONING rather than COMPOSING).

    Wire format: camelCase (via ``CamelModel``).

    Attributes:
        preview_available: ``True`` if a plan was generated and is included in
            ``preview``; ``False`` if the prompt's intent does not support
            previews (i.e. is not ``COMPOSING``).
        intent: The classified intent value for the prompt (e.g.
            ``"COMPOSING"``, ``"REASONING"``, ``"EDITING"``).
        sse_state: The SSE state string corresponding to the intent (e.g.
            ``"composing"``, ``"reasoning"``).  Used by the DAW to display the
            correct UI mode.
        reason: Human-readable explanation of why ``preview_available`` is
            ``False`` (e.g. ``"Preview only available for COMPOSING mode"``).
            ``None`` when ``preview_available`` is ``True``.
        preview: The generated execution plan, or ``None`` if
            ``preview_available`` is ``False``.
    """

    preview_available: bool = Field(
        description=(
            "True if a plan was generated and is included in 'preview'; "
            "False if the prompt's intent does not support previews."
        )
    )
    intent: str = Field(
        description="Classified intent value for the prompt (e.g. 'COMPOSING', 'REASONING')."
    )
    sse_state: str = Field(
        description=(
            "SSE state string corresponding to the intent "
            "(e.g. 'composing', 'reasoning'). Used by the DAW to display the correct UI mode."
        )
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Human-readable explanation of why preview_available is False. "
            "None when preview_available is True."
        ),
    )
    preview: PlanPreviewResponse | None = Field(
        default=None,
        description="The generated execution plan, or None if preview_available is False.",
    )


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/validate-token", response_model_by_alias=True)
async def validate_token(
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> ValidateTokenResponse:
    """Validate access token and return budget info."""
    exp_timestamp = token_claims.get("exp", 0)
    expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)

    budget_remaining: float | None = None
    budget_limit: float | None = None

    user_id = token_claims.get("sub")
    if user_id:
        try:
            from sqlalchemy import select
            from app.db.models import User

            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if user:
                budget_remaining = user.budget_remaining
                budget_limit = user.budget_limit
        except Exception as e:
            logger.warning(f"Could not fetch budget: {e}")

    return ValidateTokenResponse(
        valid=True,
        expires_at=expires_at.isoformat(),
        expires_in_seconds=max(0, exp_timestamp - int(datetime.now(timezone.utc).timestamp())),
        budget_remaining=budget_remaining,
        budget_limit=budget_limit,
    )


@router.post("/maestro/stream")
@limiter.limit("20/minute")
async def stream_maestro(
    request: Request,
    maestro_request: MaestroRequest,
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
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
    conversation_history: list[ChatMessage] = []
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
                role = msg.role
                content = msg.content or ""
                if role == "user":
                    conversation_history.append(UserMessage(role="user", content=content))
                elif role == "assistant":
                    conversation_history.append(AssistantMessage(role="assistant", content=content))
                elif role == "system":
                    conversation_history.append(SystemMessage(role="system", content=content))

        except Exception as e:
            logger.warning(f"Failed to load conversation history: {e}")

    async def stream_with_budget() -> AsyncIterator[str]:
        sequencer = SSESequencer()
        import time as _time
        _stream_start = _time.monotonic()

        logger.info(
            f"🔌 SSE stream opened: model={selected_model}, "
            f"prompt_len={len(safe_prompt)}"
        )

        try:
            async with get_composition_limiter().acquire(user_id):
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
                    _is_terminal = '"type": "complete"' in event or '"type":"complete"' in event
                    if not _is_terminal and await request.is_disconnected():
                        _elapsed = _time.monotonic() - _stream_start
                        logger.warning(
                            f"⚠️ SSE client disconnected after {_elapsed:.1f}s, "
                            f"{sequencer.count} events sent — aborting stream"
                        )
                        return
                    yield sequencer(event)

                _elapsed = _time.monotonic() - _stream_start
                logger.info(
                    f"✅ SSE stream completed: {sequencer.count} events in {_elapsed:.1f}s"
                )

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

        except CompositionLimitExceeded as e:
            logger.warning(f"⚠️ {e}")
            yield sequencer(emit(ErrorEvent(message=str(e))))
            yield sequencer(emit(CompleteEvent(
                success=False,
                error=f"Too many concurrent compositions (limit: {e.limit})",
                trace_id="composition-limit",
            )))
        except ProtocolSerializationError as e:
            _elapsed = _time.monotonic() - _stream_start
            logger.error(
                f"❌ Protocol serialization failure after {_elapsed:.1f}s, "
                f"{sequencer.count} events: {e}"
            )
            yield sequencer(emit(ErrorEvent(message="Protocol serialization failure")))
            yield sequencer(emit(CompleteEvent(
                success=False,
                error=str(e),
                trace_id="protocol-error",
            )))
        except Exception as e:
            _elapsed = _time.monotonic() - _stream_start
            logger.exception(
                f"❌ SSE stream error after {_elapsed:.1f}s, {sequencer.count} events: {e}"
            )
            yield sequencer(emit(ErrorEvent(message=str(e))))

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


@router.post("/maestro/preview", response_model_by_alias=True)
@limiter.limit("30/minute")
async def preview_maestro(
    request: Request,
    maestro_request: MaestroRequest,
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> PreviewMaestroResponse:
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
            return PreviewMaestroResponse(
                preview_available=False,
                reason=f"Preview only available for COMPOSING mode (got {route.sse_state.value})",
                intent=route.intent.value,
                sse_state=route.sse_state.value,
            )

        preview_result = await preview_plan(
            safe_prompt,
            maestro_request.project or ProjectContext(),
            route,
            llm
        )

        return PreviewMaestroResponse(
            preview_available=True,
            intent=route.intent.value,
            sse_state=route.sse_state.value,
            preview=PlanPreviewResponse(
                valid=preview_result.get("valid"),
                total_steps=preview_result.get("total_steps"),
                generations=preview_result.get("generations"),
                edits=preview_result.get("edits"),
                tool_calls=[
                    ToolCallWire(name=tc["name"], params=wrap_dict(tc["params"]))
                    for tc in preview_result.get("tool_calls", [])
                ],
                notes=preview_result.get("notes", []),
                errors=preview_result.get("errors", []),
                warnings=preview_result.get("warnings", []),
            ),
        )

    finally:
        await llm.close()
