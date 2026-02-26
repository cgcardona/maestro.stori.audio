"""POST /variation/discard — cancel generation, transition to DISCARDED."""

from __future__ import annotations

import logging

from typing import Any

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field

from app.models.requests import DiscardVariationRequest
from app.auth.dependencies import require_valid_token
from app.variation.core.state_machine import (
    VariationStatus,
    InvalidTransitionError,
    can_discard,
    is_terminal,
)
from app.variation.core.event_envelope import build_done_envelope
from app.variation.storage.variation_store import get_variation_store
from app.variation.streaming.stream_router import publish_event, close_variation_stream
from app.api.routes.variation._state import limiter, _generation_tasks

router = APIRouter()
logger = logging.getLogger(__name__)


class DiscardVariationResponse(BaseModel):
    """Acknowledgement that a variation was discarded.

    Returned by ``POST /variation/discard`` in all non-error paths:
    - The variation was found, was in a discardable state, and was
      successfully transitioned to ``DISCARDED``.
    - The variation was not found in the store (already expired or never
      created) — treated as an implicit discard.
    - The variation was already in the ``DISCARDED`` terminal state.

    The endpoint raises ``409`` (rather than returning this entity) if the
    variation is in a non-discardable terminal state other than ``DISCARDED``,
    or if the state machine rejects the transition.

    Attributes:
        ok: Always ``True`` in the response body.  The endpoint uses HTTP
            status codes for failure signalling, so ``ok=False`` is never
            returned.
    """

    ok: bool = Field(
        description=(
            "Always True in the response body. "
            "The endpoint uses HTTP status codes for failure signalling."
        )
    )


@router.post("/variation/discard")
@limiter.limit("30/minute")
async def discard_variation(
    request: Request,
    discard_request: DiscardVariationRequest,
    token_claims: dict[str, Any] = Depends(require_valid_token),
) -> DiscardVariationResponse:
    """Discard a variation — cancel generation if streaming, transition to DISCARDED."""
    vstore = get_variation_store()
    record = vstore.get(discard_request.variation_id)

    if record is None:
        logger.info(
            "Variation discarded (not in store)",
            extra={"variation_id": discard_request.variation_id},
        )
        return DiscardVariationResponse(ok=True)

    if is_terminal(record.status):
        if record.status == VariationStatus.DISCARDED:
            return DiscardVariationResponse(ok=True)
        raise HTTPException(status_code=409, detail={
            "error": "Invalid state for discard",
            "message": f"Variation is in terminal state '{record.status.value}'",
            "currentStatus": record.status.value,
        })

    if not can_discard(record.status):
        raise HTTPException(status_code=409, detail={
            "error": "Invalid state for discard",
            "currentStatus": record.status.value,
        })

    was_streaming = record.status == VariationStatus.STREAMING
    task = _generation_tasks.pop(discard_request.variation_id, None)
    if task is not None and not task.done():
        task.cancel()
        logger.info(
            "Cancelled generation task",
            extra={"variation_id": discard_request.variation_id},
        )

    record.transition_to(VariationStatus.DISCARDED)

    if was_streaming:
        done_env = build_done_envelope(
            variation_id=record.variation_id,
            project_id=record.project_id,
            base_state_id=record.base_state_id,
            sequence=record.next_sequence(),
            status="discarded",
            phrase_count=len(record.phrases),
        )
        await publish_event(done_env)
        await close_variation_stream(record.variation_id)

    logger.info(
        "Variation discarded",
        extra={
            "variation_id": discard_request.variation_id,
            "project_id": discard_request.project_id,
            "was_streaming": was_streaming,
        },
    )

    return DiscardVariationResponse(ok=True)
