"""
Stori Composer API - Variation Endpoints (Muse Specification Compliant)

This module implements the Muse/Variation specification endpoints:
- POST /variation/propose - Create a variation proposal from Muse
- GET /variation/stream - Stream variation phrases via SSE
- POST /variation/commit - Apply accepted phrases to canonical state
- POST /variation/discard - Discard a variation without applying

Key principles:
1. Variations are **ephemeral** - not persisted on backend
2. Frontend maintains variation state and sends it back on commit
3. Streaming-first UX - phrases are emitted incrementally
4. Optimistic concurrency via base_state_id
5. Non-destructive - changes are reviewable before application
"""

import json
import logging
import time
import uuid
from typing import AsyncIterator, Optional, Any

from fastapi import APIRouter, HTTPException, Depends, Request
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
    ProposeVariationResponse,
    CommitVariationResponse,
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
    deduct_budget,
    calculate_cost_cents,
    get_model_or_default,
    InsufficientBudgetError,
    BudgetError,
)

router = APIRouter()
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


async def sse_event(data: dict[str, Any]) -> str:
    """Format data as an SSE event."""
    return f"data: {json.dumps(data)}\n\n"


@router.post("/variation/propose", response_model=ProposeVariationResponse)
@limiter.limit("20/minute")
async def propose_variation(
    request: Request,
    propose_request: ProposeVariationRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Propose a variation (Muse Specification endpoint).
    
    Creates a variation proposal and returns metadata immediately.
    If streaming is requested, client should connect to stream_url
    to receive phrases incrementally.
    
    Flow:
    1. Validate project state matches base_state_id
    2. Run intent classification + planning
    3. Execute in variation mode (no mutation)
    4. Return variation_id + stream_url
    
    Args:
        propose_request: Variation proposal parameters from spec
        
    Returns:
        ProposeVariationResponse with variation_id and stream_url
    """
    user_id = token_claims.get("sub")
    
    # Budget check
    if user_id:
        try:
            await check_budget(db, user_id)
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
    
    trace = create_trace_context(
        conversation_id=propose_request.project_id,
        user_id=user_id,
    )
    
    try:
        with trace_span(trace, "propose_variation"):
            # Get or create StateStore for this project
            store = get_or_create_store(
                conversation_id=propose_request.project_id,
                project_id=propose_request.project_id,
            )
            
            # Optimistic concurrency check
            if not store.check_state_id(propose_request.base_state_id):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "State conflict",
                        "message": (
                            f"Project state has changed. "
                            f"Expected state_id={propose_request.base_state_id}, "
                            f"but current is {store.get_state_id()}"
                        ),
                        "current_state_id": store.get_state_id(),
                    }
                )
            
            # Generate variation_id
            variation_id = str(uuid.uuid4())
            
            # For MVP, we compute the variation synchronously
            # Future: Support async streaming via GET /variation/stream
            selected_model = get_model_or_default(propose_request.model)
            llm = LLMClient(model=selected_model)
            
            try:
                # Get intent classification
                route = await get_intent_result_with_llm(
                    propose_request.intent,
                    {},  # project_context - we'll use StateStore
                    llm
                )
                
                # Only COMPOSING intents can generate variations
                if route.sse_state != SSEState.COMPOSING:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "Invalid intent",
                            "message": f"Variations only supported for COMPOSING mode (got {route.sse_state.value})",
                        }
                    )
                
                # Run the planner to get tool calls
                output = await run_pipeline(
                    propose_request.intent,
                    {},  # project_context
                    llm
                )
                
                if not output.plan or not output.plan.tool_calls:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "No plan generated",
                            "message": "Could not generate a plan - please be more specific",
                        }
                    )
                
                # For now, return immediate response with variation computed
                # Future: Make this truly async with streaming
                logger.info(
                    f"[{trace.trace_id[:8]}] Variation {variation_id[:8]} proposed: "
                    f"{len(output.plan.tool_calls)} tool calls"
                )
                
                return ProposeVariationResponse(
                    variation_id=variation_id,
                    project_id=propose_request.project_id,
                    base_state_id=propose_request.base_state_id,
                    intent=propose_request.intent,
                    ai_explanation=output.plan.llm_response_text,
                    stream_url=f"/variation/stream?variation_id={variation_id}",
                )
                
            finally:
                await llm.close()
                
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[{trace.trace_id[:8]}] Propose variation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal error",
                "message": str(e),
                "trace_id": trace.trace_id,
            }
        )
    finally:
        clear_trace_context()


@router.get("/variation/stream")
async def stream_variation(
    variation_id: str,
    token_claims: dict = Depends(require_valid_token),
):
    """
    Stream variation phrases via SSE (Muse Specification endpoint).
    
    Emits:
    - meta: Overall variation summary + counts
    - phrase: Individual musical phrase (can be multiple)
    - progress: Optional progress updates
    - done: Completion signal
    - error: Terminal error
    
    Note: For MVP, variations are computed synchronously in /variation/propose.
    This endpoint is a placeholder for future streaming support.
    """
    async def stream():
        yield await sse_event({
            "type": "error",
            "message": "Streaming not yet implemented - use /variation/propose for synchronous generation"
        })
    
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/variation/commit", response_model=CommitVariationResponse)
@limiter.limit("30/minute")
async def commit_variation(
    request: Request,
    commit_request: CommitVariationRequest,
    token_claims: dict = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Commit (accept) selected phrases from a variation (Muse Specification endpoint).
    
    This is the "Accept Variation" phase - only accepted phrases are applied to canonical state.
    Creates a single undo boundary for all applied changes.
    
    Flow:
    1. Validate base_state_id (optimistic concurrency)
    2. Reconstruct Variation from variation_data
    3. Apply accepted phrases via StateStore transaction
    4. Return new_state_id + updated regions
    
    Args:
        commit_request: Commit parameters from spec
        
    Returns:
        CommitVariationResponse with new_state_id and updated regions
    """
    user_id = token_claims.get("sub")
    trace = create_trace_context(
        conversation_id=commit_request.project_id,
        user_id=user_id,
    )
    
    try:
        with trace_span(trace, "commit_variation", {"phrase_count": len(commit_request.accepted_phrase_ids)}):
            # Get StateStore for this project
            store = get_or_create_store(
                conversation_id=commit_request.project_id,
                project_id=commit_request.project_id,
            )
            
            # Optimistic concurrency check
            if not store.check_state_id(commit_request.base_state_id):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "State conflict",
                        "message": (
                            f"Project state has changed since variation was proposed. "
                            f"Expected state_id={commit_request.base_state_id}, "
                            f"but current is {store.get_state_id()}"
                        ),
                        "current_state_id": store.get_state_id(),
                    }
                )
            
            # Reconstruct Variation from provided data
            try:
                variation = Variation.model_validate(commit_request.variation_data)
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Invalid variation_data",
                        "message": str(e),
                    }
                )
            
            # Validate variation_id matches
            if variation.variation_id != commit_request.variation_id:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Variation ID mismatch",
                        "message": "variation_id in request does not match variation_data",
                    }
                )
            
            # Validate all requested phrases exist
            available_phrase_ids = {p.phrase_id for p in variation.phrases}
            invalid_phrases = [p for p in commit_request.accepted_phrase_ids if p not in available_phrase_ids]
            if invalid_phrases:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Invalid phrase IDs",
                        "message": f"Phrases not found: {invalid_phrases[:3]}{'...' if len(invalid_phrases) > 3 else ''}",
                    }
                )
            
            # Apply the accepted phrases
            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=commit_request.accepted_phrase_ids,
                project_state={},  # StateStore is source of truth
                conversation_id=commit_request.project_id,
            )
            
            if not result.success:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "Application failed",
                        "message": result.error or "Unknown error during application",
                    }
                )
            
            # Get new state ID after commit
            new_state_id = store.get_state_id()
            
            # Generate undo label
            undo_label = f"Accept Variation: {variation.intent[:50]}"
            
            logger.info(
                f"[{trace.trace_id[:8]}] Committed variation {commit_request.variation_id[:8]}: "
                f"{len(result.applied_phrase_ids)} phrases, "
                f"+{result.notes_added} -{result.notes_removed} ~{result.notes_modified}"
            )
            
            return CommitVariationResponse(
                project_id=commit_request.project_id,
                new_state_id=new_state_id,
                applied_phrase_ids=result.applied_phrase_ids,
                undo_label=undo_label,
                updated_regions=[],  # TODO: Extract from result (backlog)
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[{trace.trace_id[:8]}] Commit variation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal error",
                "message": str(e),
                "trace_id": trace.trace_id,
            }
        )
    finally:
        clear_trace_context()


@router.post("/variation/discard")
@limiter.limit("30/minute")
async def discard_variation(
    request: Request,
    discard_request: DiscardVariationRequest,
    token_claims: dict = Depends(require_valid_token),
):
    """
    Discard a variation without applying (Muse Specification endpoint).
    
    Since variations are stateless and ephemeral on the backend,
    this is effectively a no-op. However, we return success to
    confirm the client's intent to discard.
    
    Args:
        discard_request: Discard parameters from spec
        
    Returns:
        Simple success response
    """
    logger.info(
        f"Variation {discard_request.variation_id[:8]} discarded "
        f"for project {discard_request.project_id[:8]}"
    )
    
    return {"ok": True}
