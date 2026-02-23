"""POST /conversations/{id}/messages — stream AI response, save to DB."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.auth.dependencies import require_valid_token
from app.services import conversations as conv_service
from app.services.conversations import get_optimized_context
from app.services.budget import (
    check_budget,
    deduct_budget,
    calculate_cost_cents,
    InsufficientBudgetError,
)
from app.models.requests import MaestroRequest
from app.api.routes.maestro import orchestrate, UsageTracker
from app.core.sse_utils import sse_event
from app.protocol.validation import ProtocolGuard
from app.protocol.emitter import ProtocolSerializationError

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/conversations/{conversation_id}/messages")
async def add_message_to_conversation(
    conversation_id: str,
    maestro_request: MaestroRequest,
    request: Request,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Add a message to a conversation and generate AI response.

    1. Saves the user's message
    2. Streams the AI response (SSE)
    3. Saves the assistant's message with tokens/cost
    4. Auto-generates title if still "New Conversation"
    5. Deducts budget from user

    Streams in the same format as /api/v1/maestro/stream.
    """
    user_id = token_claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token does not contain user ID.")

    conversation = await conv_service.get_conversation(db=db, conversation_id=conversation_id, user_id=user_id)

    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    try:
        await check_budget(db, user_id)
    except InsufficientBudgetError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": str(e),
                "budgetRemaining": e.budget_remaining,
            },
        )

    user_message_content = maestro_request.prompt if maestro_request.store_prompt else "[content not stored]"
    user_message = await conv_service.add_message(
        db=db,
        conversation_id=conversation_id,
        role="user",
        content=user_message_content,
    )
    await db.commit()

    async def stream_with_save():
        usage_tracker = UsageTracker()
        _guard = ProtocolGuard()
        assistant_content_parts: list[str] = []
        tool_calls_made: list[dict] = []
        sse_events_captured: list[dict] = []
        tool_actions: dict[str, Any] = {}
        assistant_message_id: Optional[str] = None

        try:
            conversation_history: list[dict[str, Any]] = []
            if conversation.messages:
                previous_messages = [m for m in conversation.messages if m.id != user_message.id]
                conversation_history, _ = await get_optimized_context(
                    previous_messages,
                    max_messages=20,
                    include_entity_summary=True,
                )
            async for event in orchestrate(
                prompt=maestro_request.prompt,
                project_context=maestro_request.project,
                model=maestro_request.model,
                usage_tracker=usage_tracker,
                conversation_history=conversation_history,
                is_cancelled=request.is_disconnected,
                quality_preset=maestro_request.quality_preset,
            ):
                if event.startswith("data: "):
                    event_data = json.loads(event[6:])
                    event_type = event_data.get("type")
                    _guard.check_event(event_type or "unknown", event_data)

                    sse_events_captured.append({
                        "type": event_type,
                        "data": event_data,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

                    if event_type == "content":
                        assistant_content_parts.append(event_data.get("content", ""))

                    if event_type == "toolCall":
                        arguments = event_data.get("params", {})
                        tool_call_id = event_data.get("id", "")
                        sanitized_id = (
                            re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)
                            if tool_call_id
                            else f"call_{uuid.uuid4().hex[:12]}"
                        )
                        tool_calls_made.append({
                            "id": sanitized_id,
                            "type": "function",
                            "name": event_data.get("name"),
                            "arguments": arguments,
                        })

                    if event_type == "toolStart":
                        tool_name = event_data.get("name")
                        tool_actions[tool_name] = {
                            "action_type": "tool_execution",
                            "description": f"Execute {tool_name}",
                            "tool_name": tool_name,
                            "params": event_data.get("params", {}),
                            "start_time": datetime.now(timezone.utc).isoformat(),
                            "success": None,
                        }
                    elif event_type == "toolComplete":
                        tool_name = event_data.get("name")
                        if tool_name in tool_actions:
                            tool_actions[tool_name].update({
                                "success": event_data.get("success", True),
                                "result": event_data.get("result"),
                                "end_time": datetime.now(timezone.utc).isoformat(),
                            })
                    elif event_type == "toolError":
                        tool_name = event_data.get("name")
                        if tool_name in tool_actions:
                            tool_actions[tool_name].update({
                                "success": False,
                                "error_message": event_data.get("error"),
                                "end_time": datetime.now(timezone.utc).isoformat(),
                            })

                    yield event

            total_tokens = usage_tracker.prompt_tokens + usage_tracker.completion_tokens
            cost_cents = calculate_cost_cents(
                model=maestro_request.model or settings.llm_model,
                prompt_tokens=usage_tracker.prompt_tokens,
                completion_tokens=usage_tracker.completion_tokens,
            )

            assistant_message = await conv_service.add_message(
                db=db,
                conversation_id=conversation_id,
                role="assistant",
                content="".join(assistant_content_parts),
                model_used=maestro_request.model or settings.llm_model,
                tokens_used={
                    "prompt": usage_tracker.last_input_tokens,
                    "completion": usage_tracker.completion_tokens,
                },
                cost_cents=cost_cents,
                tool_calls=tool_calls_made,
                sse_events=sse_events_captured,
            )
            assistant_message_id = assistant_message.id

            for tool_name, action_data in tool_actions.items():
                success = action_data.get("success", False)
                await conv_service.add_action(
                    db=db,
                    message_id=assistant_message_id,
                    action_type=action_data["action_type"],
                    description=action_data["description"],
                    success=success if success is not None else False,
                    error_message=action_data.get("error_message"),
                    extra_metadata={
                        "tool_name": tool_name,
                        "params": action_data.get("params"),
                        "result": action_data.get("result"),
                        "start_time": action_data.get("start_time"),
                        "end_time": action_data.get("end_time"),
                    },
                )

            await deduct_budget(
                db=db,
                user_id=user_id,
                cost_cents=cost_cents,
                model=maestro_request.model or settings.llm_model,
                prompt_tokens=usage_tracker.prompt_tokens,
                completion_tokens=usage_tracker.completion_tokens,
                prompt=user_message_content if maestro_request.store_prompt else None,
            )

            if conversation.title == "New Conversation" and maestro_request.prompt:
                new_title = conv_service.generate_title_from_prompt(maestro_request.prompt)
                await conv_service.update_conversation_title(
                    db=db,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    title=new_title,
                )

            await db.commit()

        except ProtocolSerializationError as e:
            logger.error(f"❌ Protocol serialization failure in conversation stream: {e}")
            await db.rollback()
            yield await sse_event({"type": "error", "message": "Protocol serialization failure"})
            yield await sse_event({
                "type": "complete",
                "success": False,
                "error": str(e),
                "traceId": "protocol-error",
            })
        except Exception as e:
            logger.error(f"Error in conversation message stream: {e}", exc_info=True)
            await db.rollback()
            yield await sse_event({"type": "error", "message": str(e)})

    return StreamingResponse(
        stream_with_save(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
