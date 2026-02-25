"""POST /variation/commit — apply accepted phrases from persistent store."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.requests import CommitVariationRequest
from app.models.variation import (
    MidiNoteSnapshot,
    NoteChange,
    Phrase,
    Variation,
    CommitVariationResponse,
    UpdatedRegionPayload,
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
from app.services import muse_repository
from app.variation.core.state_machine import can_commit
from app.variation.storage.variation_store import get_variation_store, VariationRecord
from app.api.routes.variation._state import limiter


def _record_to_variation(record: VariationRecord) -> Variation:
    """Convert an in-memory VariationRecord to a domain Variation.

    Used by tests that create VariationRecords directly.
    """
    phrases: list[Phrase] = []
    for pr in record.phrases:
        diff = pr.diff_json or {}
        note_changes: list[NoteChange] = []
        for nc_dict in diff.get("noteChanges", []):
            before_raw = nc_dict.get("before")
            after_raw = nc_dict.get("after")
            note_changes.append(NoteChange(
                note_id=nc_dict.get("noteId", ""),
                change_type=nc_dict.get("changeType", "added"),
                before=MidiNoteSnapshot.model_validate(before_raw) if before_raw else None,
                after=MidiNoteSnapshot.model_validate(after_raw) if after_raw else None,
            ))
        phrases.append(Phrase(
            phrase_id=pr.phrase_id,
            track_id=pr.track_id,
            region_id=pr.region_id,
            start_beat=pr.beat_start,
            end_beat=pr.beat_end,
            label=pr.label,
            note_changes=note_changes,
            controller_changes=diff.get("controllerChanges", []),
            explanation=pr.ai_explanation,
            tags=pr.tags or [],
        ))

    beat_starts = [p.start_beat for p in phrases] if phrases else [0.0]
    beat_ends = [p.end_beat for p in phrases] if phrases else [0.0]

    return Variation(
        variation_id=record.variation_id,
        intent=record.intent,
        ai_explanation=record.ai_explanation,
        affected_tracks=record.affected_tracks or [],
        affected_regions=record.affected_regions or [],
        beat_range=(min(beat_starts), max(beat_ends)),
        phrases=phrases,
    )

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/variation/commit", response_model=CommitVariationResponse, response_model_by_alias=True)
@limiter.limit("30/minute")
async def commit_variation(
    request: Request,
    commit_request: CommitVariationRequest,
    token_claims: dict[str, Any] = Depends(require_valid_token),
    db: AsyncSession = Depends(get_db),
) -> CommitVariationResponse:
    """
    Commit accepted phrases from a variation.

    Primary lookup: Postgres (via muse_repository).
    Fallback: in-memory VariationStore (for variations created before persistence).

    1. Load variation from DB (or in-memory fallback)
    2. Validate status == READY (or ready)
    3. Validate base_state_id matches
    4. Apply accepted phrases in sequence order (adds + removals)
    5. Mark COMMITTED in DB + in-memory store
    """
    user_id = token_claims.get("sub")
    trace = create_trace_context(
        conversation_id=commit_request.project_id,
        user_id=user_id,
    )

    try:
        with trace_span(trace, "commit_variation", {"phrase_count": len(commit_request.accepted_phrase_ids)}):

            # ── 1. Load variation (DB primary, in-memory fallback) ────────
            from_db = True
            db_status = await muse_repository.get_status(db, commit_request.variation_id)
            variation = await muse_repository.load_variation(db, commit_request.variation_id)

            if variation is None:
                from_db = False
                vstore = get_variation_store()
                mem_record = vstore.get(commit_request.variation_id)
                if mem_record is None:
                    raise HTTPException(status_code=404, detail={
                        "error": "Variation not found",
                        "variationId": commit_request.variation_id,
                    })
                variation = _record_to_variation(mem_record)
                db_status = mem_record.status.value if hasattr(mem_record.status, "value") else str(mem_record.status)

            # ── 2. Validate status ───────────────────────────────────────
            if db_status in ("committed", "COMMITTED"):
                raise HTTPException(status_code=409, detail={
                    "error": "Already committed",
                    "message": f"Variation {commit_request.variation_id} is already committed",
                })

            if db_status not in ("ready", "READY"):
                raise HTTPException(status_code=409, detail={
                    "error": "Invalid state for commit",
                    "message": (
                        f"Cannot commit variation in state '{db_status}'. "
                        f"Commit is only allowed from READY state."
                    ),
                    "currentStatus": db_status,
                })

            # ── 3. Validate base_state_id ────────────────────────────────
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

            if from_db:
                db_base_state = await muse_repository.get_base_state_id(
                    db, commit_request.variation_id,
                )
                if db_base_state != commit_request.base_state_id:
                    raise HTTPException(status_code=409, detail={
                        "error": "Baseline mismatch",
                        "message": (
                            f"Variation was proposed against state_id={db_base_state}, "
                            f"but commit requests state_id={commit_request.base_state_id}"
                        ),
                    })

            # ── 4. Validate phrase IDs ───────────────────────────────────
            if from_db:
                available_ids = await muse_repository.get_phrase_ids(
                    db, commit_request.variation_id,
                )
            else:
                available_ids = [p.phrase_id for p in variation.phrases]
            available_set = set(available_ids)
            invalid_ids = [
                pid for pid in commit_request.accepted_phrase_ids
                if pid not in available_set
            ]
            if invalid_ids:
                raise HTTPException(status_code=400, detail={
                    "error": "Invalid phrase IDs",
                    "message": f"Phrases not found: {invalid_ids[:3]}{'...' if len(invalid_ids) > 3 else ''}",
                })

            # ── 5. Collect region metadata ───────────────────────────────
            commit_region_meta: dict[str, dict[str, Any]] = {}
            for phrase in variation.phrases:
                if phrase.region_id not in commit_region_meta:
                    entity = project_store.registry.get_region(phrase.region_id)
                    if entity and entity.metadata:
                        commit_region_meta[phrase.region_id] = {
                            **entity.metadata,
                            "name": entity.name,
                        }

            if not commit_region_meta and from_db:
                commit_region_meta = await muse_repository.get_region_metadata(
                    db, commit_request.variation_id,
                )

            # ── 5b. Drift safety check ────────────────────────────────
            try:
                from app.services.muse_replay import reconstruct_head_snapshot
                from app.services.muse_drift import compute_drift_report, CommitConflictPayload

                head_snap = await reconstruct_head_snapshot(db, commit_request.project_id)
                if head_snap is not None:
                    from app.core.executor.snapshots import capture_base_snapshot
                    working = capture_base_snapshot(project_store)
                    drift = compute_drift_report(
                        project_id=commit_request.project_id,
                        head_variation_id=head_snap.variation_id,
                        head_snapshot_notes=head_snap.notes,
                        working_snapshot_notes=working.notes,
                        track_regions=head_snap.track_regions,
                        head_cc=head_snap.cc,
                        working_cc=working.cc,
                        head_pb=head_snap.pitch_bends,
                        working_pb=working.pitch_bends,
                        head_at=head_snap.aftertouch,
                        working_at=working.aftertouch,
                    )
                    if drift.requires_user_action() and not commit_request.force:
                        import dataclasses
                        conflict = CommitConflictPayload.from_drift_report(drift)
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "error": "WORKING_TREE_DIRTY",
                                "message": (
                                    f"Working tree has diverged from HEAD ({drift.severity.value}). "
                                    f"{drift.total_changes} change(s) across "
                                    f"{len(drift.changed_regions) + len(drift.added_regions) + len(drift.deleted_regions)} region(s). "
                                    f"Use force=true to bypass."
                                ),
                                "drift": dataclasses.asdict(conflict),
                            },
                        )
                    if not drift.is_clean:
                        logger.warning(
                            "⚠️ Drift detected (force=True): %s (%d changes)",
                            drift.severity.value,
                            drift.total_changes,
                        )
            except HTTPException:
                raise
            except Exception:
                logger.debug("Drift safety check skipped", exc_info=True)

            # ── 6. Apply variation phrases ───────────────────────────────
            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=commit_request.accepted_phrase_ids,
                project_state={},
                store=project_store,
                region_metadata=commit_region_meta,
            )

            if not result.success:
                raise HTTPException(status_code=500, detail={
                    "error": "Application failed",
                    "message": result.error or "Unknown error",
                })

            # ── 7. Mark committed in DB + in-memory, set HEAD ────────────
            new_state_id = project_store.get_state_id()

            if from_db:
                await muse_repository.mark_committed(db, commit_request.variation_id)
                await muse_repository.set_head(
                    db,
                    commit_request.variation_id,
                    commit_state_id=new_state_id,
                )

            vstore = get_variation_store()
            mem_record = vstore.get(commit_request.variation_id)
            if mem_record is not None:
                from app.variation.core.state_machine import VariationStatus, InvalidTransitionError
                try:
                    mem_record.transition_to(VariationStatus.COMMITTED)
                except InvalidTransitionError:
                    pass

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

            # ── 8. Build response ────────────────────────────────────────
            db_region_meta: dict[str, dict[str, Any]] = {}
            if from_db:
                db_region_meta = await muse_repository.get_region_metadata(
                    db, commit_request.variation_id,
                )

            updated_region_payloads: list[UpdatedRegionPayload] = []

            if result.updated_regions:
                for ur in result.updated_regions:
                    rid = ur["region_id"]
                    pr_meta = db_region_meta.get(rid, {})
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
                for phrase in variation.phrases:
                    if phrase.phrase_id not in commit_request.accepted_phrase_ids:
                        continue
                    pr_meta = db_region_meta.get(phrase.region_id, {})
                    notes = [
                        MidiNoteSnapshot.from_note_dict(nc.after.model_dump())
                        for nc in phrase.note_changes
                        if nc.change_type in ("added", "modified") and nc.after
                    ]
                    if notes:
                        updated_region_payloads.append(UpdatedRegionPayload(
                            region_id=phrase.region_id,
                            track_id=phrase.track_id,
                            notes=notes,
                            start_beat=pr_meta.get("start_beat"),
                            duration_beats=pr_meta.get("duration_beats"),
                            name=pr_meta.get("name"),
                        ))

            return CommitVariationResponse(
                project_id=commit_request.project_id,
                new_state_id=new_state_id,
                applied_phrase_ids=result.applied_phrase_ids,
                undo_label=f"Accept Variation: {variation.intent[:50]}",
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
