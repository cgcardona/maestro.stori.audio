"""POST /variation/commit — apply accepted phrases from store."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.requests import CommitVariationRequest
from app.models.variation import (
    Variation,
    Phrase,
    MidiNoteSnapshot,
    CommitVariationResponse,
    UpdatedRegionPayload,
    NoteChange,
)
from app.core.executor import apply_variation_phrases
from app.core.state_store import get_or_create_store
from app.core.tracing import create_trace_context, clear_trace_context, trace_span
from app.auth.dependencies import require_valid_token
from app.db import get_db
from app.services.budget import (
    check_budget,
    InsufficientBudgetError,
    BudgetError,
)
from app.variation.core.state_machine import (
    VariationStatus,
    InvalidTransitionError,
    can_commit,
)
from app.variation.storage.variation_store import VariationRecord, get_variation_store
from app.api.routes.variation._state import limiter

router = APIRouter()
logger = logging.getLogger(__name__)


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

    1. Load variation from store
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
            vstore = get_variation_store()
            record = vstore.get(commit_request.variation_id)

            if record is None:
                raise HTTPException(status_code=404, detail={
                    "error": "Variation not found",
                    "variationId": commit_request.variation_id,
                })

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

            if record.base_state_id != commit_request.base_state_id:
                raise HTTPException(status_code=409, detail={
                    "error": "Baseline mismatch",
                    "message": (
                        f"Variation was proposed against state_id={record.base_state_id}, "
                        f"but commit requests state_id={commit_request.base_state_id}"
                    ),
                })

            available_ids = {p.phrase_id for p in record.phrases}
            invalid_ids = [pid for pid in commit_request.accepted_phrase_ids if pid not in available_ids]
            if invalid_ids:
                raise HTTPException(status_code=400, detail={
                    "error": "Invalid phrase IDs",
                    "message": f"Phrases not found: {invalid_ids[:3]}{'...' if len(invalid_ids) > 3 else ''}",
                })

            variation = _record_to_variation(record)

            commit_region_meta: dict[str, dict] = {}
            for pr in record.phrases:
                if pr.region_id not in commit_region_meta:
                    entity = project_store.registry.get_region(pr.region_id)
                    if entity and entity.metadata:
                        commit_region_meta[pr.region_id] = {
                            **entity.metadata,
                            "name": entity.name,
                        }
                    else:
                        commit_region_meta[pr.region_id] = {
                            "startBeat": pr.region_start_beat,
                            "durationBeats": pr.region_duration_beats,
                            "name": pr.region_name,
                        }

            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=commit_request.accepted_phrase_ids,
                project_state={},
                store=project_store,
                region_metadata=commit_region_meta,
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

            region_meta: dict[str, dict] = {}
            for pr in record.phrases:
                if pr.region_id not in region_meta:
                    region_meta[pr.region_id] = {
                        "start_beat": pr.region_start_beat,
                        "duration_beats": pr.region_duration_beats,
                        "name": pr.region_name,
                    }

            updated_region_payloads: list[UpdatedRegionPayload] = []

            if result.updated_regions:
                for ur in result.updated_regions:
                    rid = ur["region_id"]
                    pr_meta = region_meta.get(rid, {})
                    updated_region_payloads.append(UpdatedRegionPayload(
                        region_id=rid,
                        track_id=ur["track_id"],
                        notes=[MidiNoteSnapshot.from_note_dict(n) for n in ur.get("notes", [])],
                        cc_events=ur.get("cc_events", []),
                        pitch_bends=ur.get("pitch_bends", []),
                        aftertouch=ur.get("aftertouch", []),
                        start_beat=ur.get("start_beat") or pr_meta.get("start_beat"),
                        duration_beats=ur.get("duration_beats") or pr_meta.get("duration_beats"),
                        name=ur.get("name") or pr_meta.get("name"),
                    ))
            else:
                for pr in sorted(record.phrases, key=lambda p: p.sequence):
                    if pr.phrase_id not in commit_request.accepted_phrase_ids:
                        continue
                    pr_meta = region_meta.get(pr.region_id, {})
                    nc_raw = pr.diff_json.get("noteChanges", [])
                    notes: list[MidiNoteSnapshot] = []
                    for nc in nc_raw:
                        after = nc.get("after")
                        if after and nc.get("changeType") in ("added", "modified"):
                            notes.append(MidiNoteSnapshot.model_validate(after))
                    if notes:
                        updated_region_payloads.append(UpdatedRegionPayload(
                            region_id=pr.region_id,
                            track_id=pr.track_id,
                            notes=notes,
                            start_beat=pr_meta.get("start_beat"),
                            duration_beats=pr_meta.get("duration_beats"),
                            name=pr_meta.get("name"),
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


def _record_to_variation(record: VariationRecord) -> Variation:
    """Convert a VariationRecord back to a Variation model for apply."""
    phrases = []
    for pr in sorted(record.phrases, key=lambda p: p.sequence):
        phrase_data = pr.diff_json
        note_changes = [
            NoteChange.model_validate(nc_raw)
            for nc_raw in phrase_data.get("noteChanges", [])
        ]
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
