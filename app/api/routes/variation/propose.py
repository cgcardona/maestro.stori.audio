"""POST /variation/propose — create record, launch background generation."""

from __future__ import annotations

import asyncio
import logging
import uuid
from app.contracts.project_types import ProjectContext

from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.requests import ProposeVariationRequest
from app.models.variation import ProposeVariationResponse
from app.core.llm_client import LLMClient
from app.core.intent import get_intent_result_with_llm, SSEState
from app.core.pipeline import run_pipeline
from app.core.executor import execute_plan_variation
from app.core.state_store import get_or_create_store
from app.core.tracing import create_trace_context, clear_trace_context, trace_span
from app.auth.dependencies import require_valid_token
from app.auth.tokens import TokenClaims
from app.db import get_db
from app.services.budget import (
    check_budget,
    get_model_or_default,
    InsufficientBudgetError,
    BudgetError,
)
from app.variation.core.state_machine import VariationStatus, InvalidTransitionError
from app.variation.core.event_envelope import (
    build_meta_envelope,
    build_phrase_envelope,
    build_done_envelope,
    build_error_envelope,
)
from app.variation.storage.variation_store import PhraseRecord, VariationRecord, get_variation_store
from app.variation.streaming.stream_router import publish_event, close_variation_stream
from app.api.routes.variation._state import limiter, _generation_tasks

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/variation/propose", response_model=ProposeVariationResponse, response_model_by_alias=True)
@limiter.limit("20/minute")
async def propose_variation(
    request: Request,
    propose_request: ProposeVariationRequest,
    token_claims: TokenClaims = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> ProposeVariationResponse:
    """
    Propose a variation — create record, launch background generation.

    1. Validate base_state_id (optimistic concurrency)
    2. Create VariationRecord in CREATED state
    3. Launch async generation task (CREATED → STREAMING → READY)
    4. Return variation_id + stream_url immediately
    """
    user_id = token_claims.get("sub")

    if user_id:
        try:
            await check_budget(db, user_id)
        except InsufficientBudgetError as e:
            raise HTTPException(status_code=402, detail={
                "message": "Insufficient budget",
                "budgetRemaining": e.budget_remaining,
            })
        except BudgetError:
            pass

    trace = create_trace_context(
        conversation_id=propose_request.project_id,
        user_id=user_id,
    )

    try:
        with trace_span(trace, "propose_variation"):
            store = get_or_create_store(
                conversation_id=propose_request.project_id,
                project_id=propose_request.project_id,
            )

            if not store.check_state_id(propose_request.base_state_id):
                raise HTTPException(status_code=409, detail={
                    "error": "State conflict",
                    "message": (
                        f"Project state has changed. "
                        f"Expected state_id={propose_request.base_state_id}, "
                        f"but current is {store.get_state_id()}"
                    ),
                    "currentStateId": store.get_state_id(),
                })

            variation_id = str(uuid.uuid4())
            vstore = get_variation_store()
            record = vstore.create(
                project_id=propose_request.project_id,
                base_state_id=propose_request.base_state_id,
                intent=propose_request.intent,
                variation_id=variation_id,
            )

            task = asyncio.create_task(
                _run_generation(
                    record=record,
                    propose_request=propose_request,
                    project_context={},
                )
            )
            _generation_tasks[variation_id] = task
            task.add_done_callback(lambda t: _generation_tasks.pop(variation_id, None))

            return ProposeVariationResponse(
                variation_id=variation_id,
                project_id=propose_request.project_id,
                base_state_id=propose_request.base_state_id,
                intent=propose_request.intent,
                ai_explanation=None,
                stream_url=f"/api/v1/variation/stream?variation_id={variation_id}",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Propose variation failed: {e}")
        raise HTTPException(status_code=500, detail={
            "error": "Internal error",
            "message": str(e),
            "traceId": trace.trace_id,
        })
    finally:
        clear_trace_context()


async def _run_generation(
    record: VariationRecord,
    propose_request: ProposeVariationRequest,
    project_context: ProjectContext,
) -> None:
    """
    Background task: intent → plan → variation → emit envelopes.

    State transitions:
      CREATED → STREAMING → (emit meta/phrases/done) → READY
      CREATED → STREAMING → (error) → FAILED
    """
    variation_id = record.variation_id
    try:
        record.transition_to(VariationStatus.STREAMING)

        selected_model = get_model_or_default(propose_request.model)
        llm = LLMClient(model=selected_model)

        try:
            route = await get_intent_result_with_llm(
                propose_request.intent, project_context, llm
            )

            if route.sse_state not in (SSEState.COMPOSING, SSEState.EDITING):
                raise ValueError(
                    f"Variations require COMPOSING or EDITING intent (got {route.sse_state.value})"
                )

            output = await run_pipeline(propose_request.intent, project_context, llm)

            if not output.plan or not output.plan.tool_calls:
                raise ValueError("Could not generate a plan — be more specific")

            if record.status != VariationStatus.STREAMING:
                return  # defensive: cancellation may transition record

            variation = await execute_plan_variation(
                tool_calls=output.plan.tool_calls,
                project_state=project_context,
                intent=propose_request.intent,
                conversation_id=propose_request.project_id,
                explanation=output.plan.llm_response_text,
            )

            if record.status != VariationStatus.STREAMING:
                return  # type: ignore[unreachable]  # defensive: cancellation may transition record

            record.ai_explanation = variation.ai_explanation
            record.affected_tracks = variation.affected_tracks
            record.affected_regions = variation.affected_regions

            meta_env = build_meta_envelope(
                variation_id=variation_id,
                project_id=record.project_id,
                base_state_id=record.base_state_id,
                intent=record.intent,
                ai_explanation=variation.ai_explanation,
                affected_tracks=variation.affected_tracks,
                affected_regions=variation.affected_regions,
                note_counts=variation.note_counts,
                sequence=record.next_sequence(),
            )
            await publish_event(meta_env)

            for phrase in variation.phrases:
                if record.status != VariationStatus.STREAMING:
                    return  # type: ignore[unreachable]  # defensive: cancellation may transition record

                seq = record.next_sequence()
                phrase_data = {
                    "phraseId": phrase.phrase_id,
                    "trackId": phrase.track_id,
                    "regionId": phrase.region_id,
                    "startBeat": phrase.start_beat,
                    "endBeat": phrase.end_beat,
                    "label": phrase.label,
                    "tags": phrase.tags,
                    "explanation": phrase.explanation,
                    "noteChanges": [nc.model_dump(by_alias=True) for nc in phrase.note_changes],
                    "ccEvents": list(phrase.cc_events),
                    "pitchBends": list(phrase.pitch_bends),
                    "aftertouch": list(phrase.aftertouch),
                }

                phrase_env = build_phrase_envelope(
                    variation_id=variation_id,
                    project_id=record.project_id,
                    base_state_id=record.base_state_id,
                    sequence=seq,
                    phrase_data=phrase_data,
                )
                await publish_event(phrase_env)

                record.add_phrase(PhraseRecord(
                    phrase_id=phrase.phrase_id,
                    variation_id=variation_id,
                    sequence=seq,
                    track_id=phrase.track_id,
                    region_id=phrase.region_id,
                    beat_start=phrase.start_beat,
                    beat_end=phrase.end_beat,
                    label=phrase.label,
                    diff_json=phrase_data,
                    ai_explanation=phrase.explanation,
                    tags=phrase.tags,
                ))

            if record.status != VariationStatus.STREAMING:
                return  # type: ignore[unreachable]  # defensive: cancellation may transition record

            done_env = build_done_envelope(
                variation_id=variation_id,
                project_id=record.project_id,
                base_state_id=record.base_state_id,
                sequence=record.next_sequence(),
                status="ready",
                phrase_count=len(record.phrases),
            )
            await publish_event(done_env)

            record.transition_to(VariationStatus.READY)

        finally:
            await llm.close()

    except asyncio.CancelledError:
        if record.status == VariationStatus.STREAMING:
            try:
                record.transition_to(VariationStatus.DISCARDED)
            except InvalidTransitionError:
                pass

    except Exception as e:
        logger.exception(f"Generation failed for variation {variation_id[:8]}: {e}")
        record.error_message = str(e)

        try:
            error_env = build_error_envelope(
                variation_id=variation_id,
                project_id=record.project_id,
                base_state_id=record.base_state_id,
                sequence=record.next_sequence(),
                error_message=str(e),
            )
            await publish_event(error_env)

            done_env = build_done_envelope(
                variation_id=variation_id,
                project_id=record.project_id,
                base_state_id=record.base_state_id,
                sequence=record.next_sequence(),
                status="failed",
                phrase_count=len(record.phrases),
            )
            await publish_event(done_env)
        except Exception:
            logger.exception("Failed to emit error envelopes")

        try:
            record.transition_to(VariationStatus.FAILED)
        except InvalidTransitionError:
            pass

    finally:
        await close_variation_stream(variation_id)
