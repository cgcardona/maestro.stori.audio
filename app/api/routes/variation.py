"""
Stori Maestro API — Variation Endpoints (v1 Canonical)

Implements the Muse/Variation protocol as a first-class backend subsystem:
- POST /variation/propose  — create record, launch background generation
- GET  /variation/stream   — real SSE stream with envelopes + replay
- GET  /variation/{id}     — poll status + phrases (reconnect support)
- POST /variation/commit   — apply accepted phrases from store
- POST /variation/discard  — cancel generation, transition to DISCARDED

Key principles (v1 canonical):
1. Variations are persisted in VariationStore (backend owns lifecycle)
2. State machine enforces CREATED→STREAMING→READY→COMMITTED|DISCARDED|FAILED
3. All SSE events use transport-agnostic EventEnvelope with strict sequencing
4. No mutation of canonical state during proposal
5. base_state_id validated at both propose and commit
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.models.requests import (
    ProposeVariationRequest,
    CommitVariationRequest,
    DiscardVariationRequest,
)
from app.models.variation import (
    Variation,
    Phrase,
    MidiNoteSnapshot,
    ProposeVariationResponse,
    CommitVariationResponse,
    UpdatedRegionPayload,
)
from app.core.llm_client import LLMClient
from app.core.intent import get_intent_result_with_llm, SSEState
from app.core.pipeline import run_pipeline
from app.core.executor import execute_plan_variation, apply_variation_phrases
from app.core.state_store import get_or_create_store
from app.core.tracing import (
    create_trace_context,
    clear_trace_context,
    trace_span,
)
from app.auth.dependencies import require_valid_token
from app.db import get_db
from app.services.budget import (
    check_budget,
    get_model_or_default,
    InsufficientBudgetError,
    BudgetError,
)
from app.variation.core.state_machine import (
    VariationStatus,
    InvalidTransitionError,
    can_commit,
    can_discard,
    is_terminal,
)
from app.variation.core.event_envelope import (
    EventEnvelope,
    build_meta_envelope,
    build_phrase_envelope,
    build_done_envelope,
    build_error_envelope,
)
from app.variation.storage.variation_store import (
    VariationRecord,
    PhraseRecord,
    get_variation_store,
)
from app.variation.streaming.stream_router import publish_event, close_variation_stream
from app.variation.streaming.sse_broadcaster import get_sse_broadcaster

router = APIRouter()
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

# Background generation tasks keyed by variation_id (for cancellation)
_generation_tasks: dict[str, asyncio.Task] = {}


# =============================================================================
# POST /variation/propose
# =============================================================================

@router.post("/variation/propose", response_model=ProposeVariationResponse, response_model_by_alias=True)
@limiter.limit("20/minute")
async def propose_variation(
    request: Request,
    propose_request: ProposeVariationRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
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
                "error": "Insufficient budget",
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

            logger.info(
                "Variation proposed",
                extra={
                    "variation_id": variation_id,
                    "project_id": propose_request.project_id,
                    "base_state_id": propose_request.base_state_id,
                    "intent": propose_request.intent[:80],
                },
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


# =============================================================================
# GET /variation/stream — real SSE with envelopes + replay
# =============================================================================

@router.get("/variation/stream")
async def stream_variation(
    variation_id: str,
    from_sequence: int = Query(default=0, ge=0, description="Resume from sequence"),
    token_claims: dict = Depends(require_valid_token),
):
    """
    Stream variation events via SSE with transport-agnostic envelopes.

    Emits EventEnvelope objects as SSE events:
      event: meta|phrase|done|error
      data: {type, sequence, variation_id, project_id, base_state_id, payload, timestamp_ms}

    Supports late-join replay via ?from_sequence=N.
    """
    vstore = get_variation_store()
    record = vstore.get(variation_id)
    if record is None:
        raise HTTPException(status_code=404, detail={
            "error": "Variation not found",
            "variationId": variation_id,
        })

    if is_terminal(record.status):
        # Stream stored history for completed variations
        async def replay_stream():
            broadcaster = get_sse_broadcaster()
            for envelope in broadcaster.get_history(variation_id, from_sequence):
                yield envelope.to_sse()

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    broadcaster = get_sse_broadcaster()
    queue = broadcaster.subscribe(variation_id, from_sequence=from_sequence)

    async def live_stream():
        try:
            while True:
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    continue

                if envelope is None:
                    break
                yield envelope.to_sse()

                if envelope.type == "done":
                    break
        finally:
            broadcaster.unsubscribe(variation_id, queue)

    return StreamingResponse(
        live_stream(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


# =============================================================================
# GET /variation/{variation_id} — poll status + phrases
# =============================================================================

@router.get("/variation/{variation_id}")
async def get_variation(
    variation_id: str,
    token_claims: dict = Depends(require_valid_token),
):
    """
    Poll variation status and phrases (for reconnect / non-streaming clients).

    Returns current status, phrases generated so far, and last sequence number.
    """
    vstore = get_variation_store()
    record = vstore.get(variation_id)
    if record is None:
        raise HTTPException(status_code=404, detail={
            "error": "Variation not found",
            "variationId": variation_id,
        })

    phrases_data = []
    for p in sorted(record.phrases, key=lambda pr: pr.sequence):
        phrases_data.append({
            "phraseId": p.phrase_id,
            "sequence": p.sequence,
            "trackId": p.track_id,
            "regionId": p.region_id,
            "beatStart": p.beat_start,
            "beatEnd": p.beat_end,
            "label": p.label,
            "tags": p.tags,
            "aiExplanation": p.ai_explanation,
            "diff": p.diff_json,
        })

    return {
        "variationId": record.variation_id,
        "projectId": record.project_id,
        "baseStateId": record.base_state_id,
        "intent": record.intent,
        "status": record.status.value,
        "aiExplanation": record.ai_explanation,
        "affectedTracks": record.affected_tracks,
        "affectedRegions": record.affected_regions,
        "phrases": phrases_data,
        "phraseCount": len(record.phrases),
        "lastSequence": record.last_sequence,
        "createdAt": record.created_at.isoformat(),
        "updatedAt": record.updated_at.isoformat(),
        "errorMessage": record.error_message,
    }


# =============================================================================
# POST /variation/commit
# =============================================================================

@router.post("/variation/commit", response_model=CommitVariationResponse, response_model_by_alias=True)
@limiter.limit("30/minute")
async def commit_variation(
    request: Request,
    commit_request: CommitVariationRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Commit accepted phrases from a variation (loads from VariationStore).

    1. Load variation from store (no client-provided variation_data needed)
    2. Validate status == READY
    3. Validate base_state_id matches
    4. Apply accepted phrases in sequence order (adds + removals)
    5. Transition to COMMITTED
    """
    user_id = token_claims.get("sub")
    trace = create_trace_context(
        conversation_id=commit_request.project_id,
        user_id=user_id,
    )

    try:
        with trace_span(trace, "commit_variation", {"phrase_count": len(commit_request.accepted_phrase_ids)}):
            # --- Load from VariationStore ---
            vstore = get_variation_store()
            record = vstore.get(commit_request.variation_id)

            if record is None:
                # Fall back to variation_data for backward compatibility
                if commit_request.variation_data:
                    return await _commit_from_variation_data(commit_request, trace)
                raise HTTPException(status_code=404, detail={
                    "error": "Variation not found",
                    "variationId": commit_request.variation_id,
                })

            # --- Validate status ---
            if record.status == VariationStatus.COMMITTED:
                raise HTTPException(status_code=409, detail={
                    "error": "Already committed",
                    "message": f"Variation {commit_request.variation_id} is already committed",
                })

            if not can_commit(record.status):
                raise HTTPException(status_code=409, detail={
                    "error": "Invalid state for commit",
                    "message": (
                        f"Cannot commit variation in state '{record.status.value}'. "
                        f"Commit is only allowed from READY state."
                    ),
                    "currentStatus": record.status.value,
                })

            # --- Validate baseline ---
            project_store = get_or_create_store(
                conversation_id=commit_request.project_id,
                project_id=commit_request.project_id,
            )

            if not project_store.check_state_id(commit_request.base_state_id):
                raise HTTPException(status_code=409, detail={
                    "error": "State conflict",
                    "message": (
                        f"Project state has changed since variation was proposed. "
                        f"Expected state_id={commit_request.base_state_id}, "
                        f"but current is {project_store.get_state_id()}"
                    ),
                    "currentStateId": project_store.get_state_id(),
                })

            # Verify base_state_id also matches what was recorded at creation
            if record.base_state_id != commit_request.base_state_id:
                raise HTTPException(status_code=409, detail={
                    "error": "Baseline mismatch",
                    "message": (
                        f"Variation was proposed against state_id={record.base_state_id}, "
                        f"but commit requests state_id={commit_request.base_state_id}"
                    ),
                })

            # --- Validate phrase IDs ---
            available_ids = {p.phrase_id for p in record.phrases}
            invalid_ids = [pid for pid in commit_request.accepted_phrase_ids if pid not in available_ids]
            if invalid_ids:
                raise HTTPException(status_code=400, detail={
                    "error": "Invalid phrase IDs",
                    "message": f"Phrases not found: {invalid_ids[:3]}{'...' if len(invalid_ids) > 3 else ''}",
                })

            # --- Build Variation model from store record for apply ---
            variation = _record_to_variation(record)

            # Use the conversation_id from the compose phase so apply_variation_phrases
            # operates on the same StateStore that has the generated notes. Fall back
            # to project_id only if the record pre-dates this field.
            apply_conversation_id = record.conversation_id or commit_request.project_id

            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=commit_request.accepted_phrase_ids,
                project_state={},
                conversation_id=apply_conversation_id,
            )

            if not result.success:
                try:
                    record.transition_to(VariationStatus.FAILED)
                    record.error_message = result.error
                except InvalidTransitionError:
                    pass
                raise HTTPException(status_code=500, detail={
                    "error": "Application failed",
                    "message": result.error or "Unknown error",
                })

            # --- Transition to COMMITTED ---
            record.transition_to(VariationStatus.COMMITTED)
            new_state_id = project_store.get_state_id()

            logger.info(
                "Variation committed",
                extra={
                    "variation_id": commit_request.variation_id,
                    "project_id": commit_request.project_id,
                    "phrases_applied": len(result.applied_phrase_ids),
                    "notes_added": result.notes_added,
                    "notes_removed": result.notes_removed,
                    "notes_modified": result.notes_modified,
                },
            )

            # --- Convert to typed UpdatedRegionPayload at the API boundary ---
            # result.updated_regions uses snake_case (Python-idiomatic).
            # PhraseRecords carry region position so brand-new regions can be
            # created by the frontend without a second round-trip.
            region_meta: dict[str, dict] = {}
            for pr in record.phrases:
                if pr.region_id not in region_meta:
                    region_meta[pr.region_id] = {
                        "start_beat": pr.region_start_beat,
                        "duration_beats": pr.region_duration_beats,
                        "name": pr.region_name,
                    }

            updated_region_payloads: list[UpdatedRegionPayload] = []
            for ur in result.updated_regions:
                rid = ur["region_id"]
                meta = region_meta.get(rid, {})
                updated_region_payloads.append(UpdatedRegionPayload(
                    region_id=rid,
                    track_id=ur["track_id"],
                    notes=[MidiNoteSnapshot.from_note_dict(n) for n in ur["notes"]],
                    start_beat=meta.get("start_beat"),
                    duration_beats=meta.get("duration_beats"),
                    name=meta.get("name"),
                ))

            return CommitVariationResponse(
                project_id=commit_request.project_id,
                new_state_id=new_state_id,
                applied_phrase_ids=result.applied_phrase_ids,
                undo_label=f"Accept Variation: {record.intent[:50]}",
                updated_regions=updated_region_payloads,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Commit variation failed: {e}")
        raise HTTPException(status_code=500, detail={
            "error": "Internal error",
            "message": str(e),
            "traceId": trace.trace_id,
        })
    finally:
        clear_trace_context()


# =============================================================================
# POST /variation/discard
# =============================================================================

@router.post("/variation/discard")
@limiter.limit("30/minute")
async def discard_variation(
    request: Request,
    discard_request: DiscardVariationRequest,
    token_claims: dict = Depends(require_valid_token),
):
    """
    Discard a variation — cancel generation if streaming, transition to DISCARDED.
    """
    vstore = get_variation_store()
    record = vstore.get(discard_request.variation_id)

    if record is None:
        # Stateless discard for backward compatibility — still safe
        logger.info(
            "Variation discarded (not in store)",
            extra={"variation_id": discard_request.variation_id},
        )
        return {"ok": True}

    if is_terminal(record.status):
        if record.status == VariationStatus.DISCARDED:
            return {"ok": True}
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

    # Cancel background generation if running
    was_streaming = record.status == VariationStatus.STREAMING
    task = _generation_tasks.pop(discard_request.variation_id, None)
    if task is not None and not task.done():
        task.cancel()
        logger.info(
            "Cancelled generation task",
            extra={"variation_id": discard_request.variation_id},
        )

    # Transition to DISCARDED
    record.transition_to(VariationStatus.DISCARDED)

    # Emit terminal done event so streaming clients close cleanly
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

    return {"ok": True}


# =============================================================================
# Background Generation Task
# =============================================================================

async def _run_generation(
    record: VariationRecord,
    propose_request: ProposeVariationRequest,
    project_context: dict[str, Any],
) -> None:
    """
    Background task: intent → plan → variation → emit envelopes.

    State transitions:
      CREATED → STREAMING → (emit meta/phrases/done) → READY
      CREATED → STREAMING → (error) → FAILED
    """
    variation_id = record.variation_id
    try:
        # --- CREATED → STREAMING ---
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

            # Check cancellation before heavy work
            if record.status != VariationStatus.STREAMING:
                return

            variation = await execute_plan_variation(
                tool_calls=output.plan.tool_calls,
                project_state=project_context,
                intent=propose_request.intent,
                conversation_id=propose_request.project_id,
                explanation=output.plan.llm_response_text,
            )

            if record.status != VariationStatus.STREAMING:
                return

            # Populate record metadata
            record.ai_explanation = variation.ai_explanation
            record.affected_tracks = variation.affected_tracks
            record.affected_regions = variation.affected_regions

            # --- Emit meta envelope (seq=1) ---
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

            # --- Emit phrase envelopes (seq=2..N) ---
            for phrase in variation.phrases:
                if record.status != VariationStatus.STREAMING:
                    return

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
                    "controllerChanges": phrase.controller_changes,
                }

                phrase_env = build_phrase_envelope(
                    variation_id=variation_id,
                    project_id=record.project_id,
                    base_state_id=record.base_state_id,
                    sequence=seq,
                    phrase_data=phrase_data,
                )
                await publish_event(phrase_env)

                # Store phrase in record
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
                return

            # --- Emit done envelope ---
            done_env = build_done_envelope(
                variation_id=variation_id,
                project_id=record.project_id,
                base_state_id=record.base_state_id,
                sequence=record.next_sequence(),
                status="ready",
                phrase_count=len(record.phrases),
            )
            await publish_event(done_env)

            # --- STREAMING → READY ---
            record.transition_to(VariationStatus.READY)

            logger.info(
                "Variation generation complete",
                extra={
                    "variation_id": variation_id,
                    "phrase_count": len(record.phrases),
                    "status": "ready",
                },
            )

        finally:
            await llm.close()

    except asyncio.CancelledError:
        logger.info(f"Generation cancelled for variation {variation_id[:8]}")
        if record.status == VariationStatus.STREAMING:
            try:
                record.transition_to(VariationStatus.DISCARDED)
            except InvalidTransitionError:
                pass

    except Exception as e:
        logger.exception(
            f"Generation failed for variation {variation_id[:8]}: {e}"
        )
        record.error_message = str(e)

        # Emit error + done(failed)
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


# =============================================================================
# Backward-compatible commit (client sends variation_data)
# =============================================================================

async def _commit_from_variation_data(
    commit_request: CommitVariationRequest,
    trace,
) -> CommitVariationResponse:
    """Backward-compatible commit path when variation is not in store."""
    project_store = get_or_create_store(
        conversation_id=commit_request.project_id,
        project_id=commit_request.project_id,
    )

    if not project_store.check_state_id(commit_request.base_state_id):
        raise HTTPException(status_code=409, detail={
            "error": "State conflict",
            "message": (
                f"Project state has changed. "
                f"Expected={commit_request.base_state_id}, "
                f"current={project_store.get_state_id()}"
            ),
            "currentStateId": project_store.get_state_id(),
        })

    try:
        variation = Variation.model_validate(commit_request.variation_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "error": "Invalid variation_data",
            "message": str(e),
        })

    if variation.variation_id != commit_request.variation_id:
        raise HTTPException(status_code=400, detail={
            "error": "Variation ID mismatch",
        })

    available = {p.phrase_id for p in variation.phrases}
    invalid = [pid for pid in commit_request.accepted_phrase_ids if pid not in available]
    if invalid:
        raise HTTPException(status_code=400, detail={
            "error": "Invalid phrase IDs",
            "message": f"Not found: {invalid[:3]}",
        })

    result = await apply_variation_phrases(
        variation=variation,
        accepted_phrase_ids=commit_request.accepted_phrase_ids,
        project_state={},
        conversation_id=commit_request.project_id,
    )

    if not result.success:
        raise HTTPException(status_code=500, detail={
            "error": "Application failed",
            "message": result.error or "Unknown error",
        })

    return CommitVariationResponse(
        project_id=commit_request.project_id,
        new_state_id=project_store.get_state_id(),
        applied_phrase_ids=result.applied_phrase_ids,
        undo_label=f"Accept Variation: {variation.intent[:50]}",
        updated_regions=result.updated_regions,
    )


# =============================================================================
# Helpers
# =============================================================================

def _record_to_variation(record: VariationRecord) -> Variation:
    """Convert a VariationRecord back to a Variation model for apply."""
    phrases = []
    for pr in sorted(record.phrases, key=lambda p: p.sequence):
        phrase_data = pr.diff_json
        note_changes_raw = phrase_data.get("noteChanges", [])
        from app.models.variation import NoteChange, MidiNoteSnapshot

        note_changes = []
        for nc_raw in note_changes_raw:
            note_changes.append(NoteChange.model_validate(nc_raw))

        controller_changes = phrase_data.get("controllerChanges", [])
        phrases.append(Phrase(
            phrase_id=pr.phrase_id,
            track_id=pr.track_id,
            region_id=pr.region_id,
            start_beat=pr.beat_start,
            end_beat=pr.beat_end,
            label=pr.label,
            note_changes=note_changes,
            controller_changes=controller_changes,
            explanation=pr.ai_explanation,
            tags=pr.tags,
        ))

    beat_starts = [p.start_beat for p in phrases] if phrases else [0.0]
    beat_ends = [p.end_beat for p in phrases] if phrases else [0.0]

    return Variation(
        variation_id=record.variation_id,
        intent=record.intent,
        ai_explanation=record.ai_explanation,
        affected_tracks=record.affected_tracks,
        affected_regions=record.affected_regions,
        beat_range=(min(beat_starts), max(beat_ends)),
        phrases=phrases,
    )


def _sse_headers() -> dict[str, str]:
    """Standard SSE response headers."""
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
