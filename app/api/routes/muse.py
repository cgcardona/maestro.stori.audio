"""Muse VCS routes — commit graph, checkout, merge, HEAD management.

Production endpoints that expose Muse's version-control primitives to
the Stori DAW.  These are the HTTP surface for the history engine built
in Phases 5–13.

Endpoint summary:
  POST /muse/variations     — persist a variation directly
  POST /muse/head           — set HEAD pointer
  GET  /muse/log            — commit DAG (MuseLogGraph)
  POST /muse/checkout       — checkout to a variation (time travel)
  POST /muse/merge          — three-way merge of two variations
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_valid_token
from app.core.state_store import get_or_create_store
from app.core.tracing import create_trace_context
from app.db import get_db
from app.models.variation import (
    MidiNoteSnapshot,
    NoteChange as DomainNoteChange,
    Phrase as DomainPhrase,
    Variation as DomainVariation,
)
from app.services import muse_repository
from app.services.muse_history_controller import (
    CheckoutBlockedError,
    MergeConflictError,
    checkout_to_variation,
    merge_variations,
)
from app.services.muse_log_graph import build_muse_log_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/muse", tags=["muse"])


# ── Request / Response models ─────────────────────────────────────────────


class SaveVariationRequest(BaseModel):
    project_id: str
    variation_id: str
    intent: str
    conversation_id: str = "default"
    parent_variation_id: str | None = None
    parent2_variation_id: str | None = None
    phrases: list[dict[str, Any]] = Field(default_factory=list)
    affected_tracks: list[str] = Field(default_factory=list)
    affected_regions: list[str] = Field(default_factory=list)
    beat_range: tuple[float, float] = (0.0, 8.0)


class SetHeadRequest(BaseModel):
    variation_id: str


class CheckoutRequest(BaseModel):
    project_id: str
    target_variation_id: str
    conversation_id: str = "default"
    force: bool = False


class MergeRequest(BaseModel):
    project_id: str
    left_id: str
    right_id: str
    conversation_id: str = "default"
    force: bool = False


# ── POST /muse/variations ────────────────────────────────────────────────


@router.post("/variations", dependencies=[Depends(require_valid_token)])
async def save_variation(
    req: SaveVariationRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Persist a variation directly into Muse history.

    Accepts a complete variation payload (phrases, note changes,
    controller changes) and writes it to the variations table.
    """
    domain_phrases: list[DomainPhrase] = []
    for p in req.phrases:
        note_changes: list[DomainNoteChange] = []
        for nc in p.get("note_changes", []):
            note_changes.append(DomainNoteChange(
                note_id=nc["note_id"],
                change_type=nc["change_type"],
                before=MidiNoteSnapshot(**nc["before"]) if nc.get("before") else None,
                after=MidiNoteSnapshot(**nc["after"]) if nc.get("after") else None,
            ))
        domain_phrases.append(DomainPhrase(
            phrase_id=p["phrase_id"],
            track_id=p["track_id"],
            region_id=p["region_id"],
            start_beat=p.get("start_beat", 0.0),
            end_beat=p.get("end_beat", 8.0),
            label=p.get("label", "Muse"),
            note_changes=note_changes,
            controller_changes=p.get("controller_changes", []),
            tags=p.get("tags", []),
        ))

    variation = DomainVariation(
        variation_id=req.variation_id,
        intent=req.intent,
        ai_explanation=None,
        affected_tracks=req.affected_tracks,
        affected_regions=req.affected_regions,
        beat_range=req.beat_range,
        phrases=domain_phrases,
    )

    region_metadata: dict[str, dict[str, Any]] = {}
    for dp in domain_phrases:
        region_metadata[dp.region_id] = {
            "startBeat": dp.start_beat,
            "durationBeats": dp.end_beat - dp.start_beat,
            "name": dp.region_id,
        }

    await muse_repository.save_variation(
        db,
        variation,
        project_id=req.project_id,
        base_state_id="muse",
        conversation_id=req.conversation_id,
        region_metadata=region_metadata,
        status="committed",
        parent_variation_id=req.parent_variation_id,
        parent2_variation_id=req.parent2_variation_id,
    )
    await db.commit()

    logger.info("✅ Variation saved via route: %s", req.variation_id[:8])
    return {"variation_id": req.variation_id}


# ── POST /muse/head ──────────────────────────────────────────────────────


@router.post("/head", dependencies=[Depends(require_valid_token)])
async def set_head(
    req: SetHeadRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Set the HEAD pointer for a project to a specific variation."""
    await muse_repository.set_head(db, req.variation_id)
    await db.commit()
    return {"head": req.variation_id}


# ── GET /muse/log ────────────────────────────────────────────────────────


@router.get("/log", dependencies=[Depends(require_valid_token)])
async def get_log(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the full commit DAG for a project as ``MuseLogGraph`` JSON."""
    graph = await build_muse_log_graph(db, project_id)
    return graph.to_dict()


# ── POST /muse/checkout ──────────────────────────────────────────────────


@router.post("/checkout", dependencies=[Depends(require_valid_token)])
async def checkout(
    req: CheckoutRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Checkout to a specific variation — musical ``git checkout``.

    Reconstructs the target state, generates a checkout plan, executes
    it against StateStore, and moves HEAD.  Returns execution summary
    and SSE-compatible events.

    Returns 409 if the working tree has uncommitted drift and
    ``force`` is not set.
    """
    store = get_or_create_store(req.conversation_id, req.project_id)
    trace = create_trace_context()

    try:
        summary = await checkout_to_variation(
            session=db,
            project_id=req.project_id,
            target_variation_id=req.target_variation_id,
            store=store,
            trace=trace,
            force=req.force,
        )
        await db.commit()
        return {
            "project_id": summary.project_id,
            "from": summary.from_variation_id,
            "to": summary.to_variation_id,
            "executed": summary.execution.executed,
            "failed": summary.execution.failed,
            "plan_hash": summary.execution.plan_hash,
            "head_moved": summary.head_moved,
            "events": list(summary.execution.events),
        }
    except CheckoutBlockedError as e:
        raise HTTPException(status_code=409, detail={
            "error": "checkout_blocked",
            "severity": e.severity.value,
            "total_changes": e.total_changes,
        })
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── POST /muse/merge ─────────────────────────────────────────────────────


@router.post("/merge", dependencies=[Depends(require_valid_token)])
async def merge(
    req: MergeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Three-way merge of two variations — musical ``git merge``.

    Computes the merge base, builds a three-way diff, and if
    conflict-free, applies the merged state via checkout execution.
    Creates a merge commit with two parents.

    Returns 409 with conflict details if the merge cannot auto-resolve.
    """
    store = get_or_create_store(req.conversation_id, req.project_id)
    trace = create_trace_context()

    try:
        summary = await merge_variations(
            session=db,
            project_id=req.project_id,
            left_id=req.left_id,
            right_id=req.right_id,
            store=store,
            trace=trace,
            force=req.force,
        )
        await db.commit()
        return {
            "project_id": summary.project_id,
            "merge_variation_id": summary.merge_variation_id,
            "left_id": summary.left_id,
            "right_id": summary.right_id,
            "executed": summary.execution.executed,
            "failed": summary.execution.failed,
            "head_moved": summary.head_moved,
        }
    except MergeConflictError as e:
        raise HTTPException(status_code=409, detail={
            "error": "merge_conflict",
            "conflicts": [
                {"region_id": c.region_id, "type": c.type, "description": c.description}
                for c in e.conflicts
            ],
        })
