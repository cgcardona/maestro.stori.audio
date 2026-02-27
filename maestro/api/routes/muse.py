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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    JSONValue,
    PitchBendDict,
    RegionMetadataWire,
    jfloat,
    jint,
)
from maestro.contracts.pydantic_types import PydanticJson, wrap_dict, unwrap_dict
from maestro.auth.dependencies import require_valid_token
from maestro.core.state_store import get_or_create_store
from maestro.core.tracing import create_trace_context
from maestro.db import get_db
from maestro.models.variation import (
    ChangeType,
    MidiNoteSnapshot,
    NoteChange as DomainNoteChange,
    Phrase as DomainPhrase,
    Variation as DomainVariation,
)
from maestro.services import muse_repository
from maestro.services.muse_history_controller import (
    CheckoutBlockedError,
    MergeConflictError,
    checkout_to_variation,
    merge_variations,
)
from maestro.services.muse_log_graph import MuseLogGraphResponse, build_muse_log_graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/muse", tags=["muse"])


def _parse_change_type(raw: str) -> ChangeType:
    """Narrow a wire-format string to the ChangeType literal."""
    if raw == "added":
        return "added"
    if raw == "removed":
        return "removed"
    return "modified"


# ── Request models ────────────────────────────────────────────────────────


class SaveVariationRequest(BaseModel):
    project_id: str
    variation_id: str
    intent: str
    conversation_id: str = "default"
    parent_variation_id: str | None = None
    parent2_variation_id: str | None = None
    phrases: list[dict[str, PydanticJson]] = Field(default_factory=list)
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


# ── Response models ───────────────────────────────────────────────────────


class SaveVariationResponse(BaseModel):
    """Confirmation that a variation was persisted to Muse history.

    Returned by ``POST /muse/variations`` after the variation record has been
    written to the database and the transaction committed.

    Attributes:
        variation_id: UUID of the variation that was saved.  Echoes back the
            ID supplied in the request so the caller can correlate the response
            without re-reading the request body.
    """

    variation_id: str = Field(
        description="UUID of the variation that was saved."
    )


class SetHeadResponse(BaseModel):
    """Confirmation that the HEAD pointer was moved.

    Returned by ``POST /muse/head`` after the HEAD record has been updated and
    the transaction committed.

    Attributes:
        head: UUID of the variation that is now HEAD.  Echoes back the ID
            supplied in the request.
    """

    head: str = Field(
        description="UUID of the variation that is now HEAD."
    )


class CheckoutExecutionStats(BaseModel):
    """Execution statistics for a single plan-execution pass.

    Shared by both ``CheckoutResponse`` and ``MergeResponse`` because both
    operations run a checkout plan against the ``StateStore`` at the end.

    Attributes:
        executed: Number of tool-call steps that were executed successfully
            during this checkout pass.
        failed: Number of tool-call steps that failed during this checkout
            pass.  A non-zero value indicates a partial checkout — the DAW
            state may be inconsistent.
        plan_hash: SHA-256 content hash of the serialised checkout plan (hex
            string).  Identical hashes guarantee identical execution plans;
            useful for deduplication and idempotency checks.
        events: Ordered list of SSE event payloads that were emitted during
            execution.  Each element is a raw ``dict[str, object]`` matching
            the wire format of the corresponding ``MaestroEvent`` subclass.
            Included so callers can replay or inspect the execution trace
            without re-running the checkout.
    """

    executed: int = Field(
        description="Number of tool-call steps executed successfully during this checkout pass."
    )
    failed: int = Field(
        description=(
            "Number of tool-call steps that failed. "
            "Non-zero indicates a partial checkout — DAW state may be inconsistent."
        )
    )
    plan_hash: str = Field(
        description=(
            "SHA-256 content hash of the serialised checkout plan (hex string). "
            "Identical hashes guarantee identical execution plans."
        )
    )
    events: list[dict[str, PydanticJson]] = Field(
        description=(
            "Ordered list of SSE event payloads emitted during execution. "
            "Each element is a raw dict matching the wire format of a MaestroEvent subclass."
        )
    )


class CheckoutResponse(BaseModel):
    """Full summary of a checkout operation — the musical equivalent of ``git checkout``.

    Returned by ``POST /muse/checkout`` after the target variation has been
    reconstructed, its checkout plan executed against ``StateStore``, and HEAD
    moved.  Returns 409 instead if the working tree is dirty and ``force`` is
    not set.

    Attributes:
        project_id: UUID of the project on which the checkout was performed.
        from_variation_id: UUID of the variation that was HEAD before checkout,
            or ``None`` if the project had no HEAD (first checkout).
        to_variation_id: UUID of the variation that is now HEAD after checkout.
        execution: Plan-execution statistics and event trace for this checkout
            pass (see ``CheckoutExecutionStats``).
        head_moved: ``True`` if the HEAD pointer was successfully updated to
            ``to_variation_id``.  ``False`` would indicate an unexpected
            no-op (e.g. already at target), though in practice the endpoint
            raises on failure rather than returning ``False``.
    """

    project_id: str = Field(
        description="UUID of the project on which the checkout was performed."
    )
    from_variation_id: str | None = Field(
        description=(
            "UUID of the variation that was HEAD before checkout, "
            "or None if the project had no HEAD (first checkout)."
        )
    )
    to_variation_id: str = Field(
        description="UUID of the variation that is now HEAD after checkout."
    )
    execution: CheckoutExecutionStats = Field(
        description="Plan-execution statistics and event trace for this checkout pass."
    )
    head_moved: bool = Field(
        description="True if the HEAD pointer was successfully updated to to_variation_id."
    )


class MergeResponse(BaseModel):
    """Full summary of a three-way merge — the musical equivalent of ``git merge``.

    Returned by ``POST /muse/merge`` after the merge base is computed, the
    three-way diff is applied, the merged state is checked out via plan
    execution, and a merge commit with two parents is created.  Returns 409
    instead if the merge has unresolvable conflicts.

    Attributes:
        project_id: UUID of the project on which the merge was performed.
        merge_variation_id: UUID of the new merge commit (two parents:
            ``left_id`` and ``right_id``).
        left_id: UUID of the left (first) variation passed to the merge.
        right_id: UUID of the right (second) variation passed to the merge.
        execution: Plan-execution statistics and event trace for the checkout
            pass that applied the merged state (see ``CheckoutExecutionStats``).
        head_moved: ``True`` if HEAD was moved to ``merge_variation_id`` after
            the merge commit was created.
    """

    project_id: str = Field(
        description="UUID of the project on which the merge was performed."
    )
    merge_variation_id: str = Field(
        description=(
            "UUID of the new merge commit with two parents: left_id and right_id."
        )
    )
    left_id: str = Field(
        description="UUID of the left (first) variation passed to the merge."
    )
    right_id: str = Field(
        description="UUID of the right (second) variation passed to the merge."
    )
    execution: CheckoutExecutionStats = Field(
        description=(
            "Plan-execution statistics and event trace for the checkout pass "
            "that applied the merged state."
        )
    )
    head_moved: bool = Field(
        description="True if HEAD was moved to merge_variation_id after the merge commit was created."
    )


# ── POST /muse/variations ────────────────────────────────────────────────


@router.post("/variations", dependencies=[Depends(require_valid_token)])
async def save_variation(
    req: SaveVariationRequest,
    db: AsyncSession = Depends(get_db),
) -> SaveVariationResponse:
    """Persist a variation directly into Muse history.

    Accepts a complete variation payload (phrases, note changes,
    controller changes) and writes it to the variations table.
    """
    domain_phrases: list[DomainPhrase] = []
    for p_raw in req.phrases:
        p = unwrap_dict(p_raw)  # dict[str, JSONValue] — known phrase shape
        note_changes: list[DomainNoteChange] = []
        _raw_nc: JSONValue = p.get("note_changes", [])
        for nc in (_raw_nc if isinstance(_raw_nc, list) else []):
            if not isinstance(nc, dict):
                continue
            _nc_before = nc.get("before")
            _nc_after = nc.get("after")
            note_changes.append(DomainNoteChange(
                note_id=str(nc.get("note_id", "")),
                change_type=_parse_change_type(str(nc.get("change_type", ""))),
                before=MidiNoteSnapshot.model_validate(_nc_before) if isinstance(_nc_before, dict) else None,
                after=MidiNoteSnapshot.model_validate(_nc_after) if isinstance(_nc_after, dict) else None,
            ))
        _raw_cc_events: JSONValue = p.get("cc_events", [])
        _cc_events: list[CCEventDict] = [
            CCEventDict(cc=jint(e.get("cc", 0)), beat=jfloat(e.get("beat", 0.0)), value=jint(e.get("value", 0)))
            for e in (_raw_cc_events if isinstance(_raw_cc_events, list) else [])
            if isinstance(e, dict)
        ]
        _raw_pb: JSONValue = p.get("pitch_bends", [])
        _pitch_bends: list[PitchBendDict] = [
            PitchBendDict(beat=jfloat(e.get("beat", 0.0)), value=jint(e.get("value", 0)))
            for e in (_raw_pb if isinstance(_raw_pb, list) else [])
            if isinstance(e, dict)
        ]
        _raw_at: JSONValue = p.get("aftertouch", [])
        _aftertouch: list[AftertouchDict] = []
        for at_raw in (_raw_at if isinstance(_raw_at, list) else []):
            if not isinstance(at_raw, dict):
                continue
            at_ev: AftertouchDict = {
                "beat": jfloat(at_raw.get("beat", 0.0)),
                "value": jint(at_raw.get("value", 0)),
            }
            if "pitch" in at_raw:
                at_ev["pitch"] = jint(at_raw["pitch"])
            _aftertouch.append(at_ev)
        _raw_tags: JSONValue = p.get("tags", [])
        _tags: list[str] = [t for t in _raw_tags if isinstance(t, str)] if isinstance(_raw_tags, list) else []
        _sb = p.get("start_beat", 0.0)
        _eb = p.get("end_beat", 8.0)
        domain_phrases.append(DomainPhrase(
            phrase_id=str(p.get("phrase_id", "")),
            track_id=str(p.get("track_id", "")),
            region_id=str(p.get("region_id", "")),
            start_beat=float(_sb) if isinstance(_sb, (int, float)) else 0.0,
            end_beat=float(_eb) if isinstance(_eb, (int, float)) else 8.0,
            label=str(p.get("label", "Muse")),
            note_changes=note_changes,
            cc_events=_cc_events,
            pitch_bends=_pitch_bends,
            aftertouch=_aftertouch,
            tags=_tags,
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

    region_metadata: dict[str, RegionMetadataWire] = {}
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
    return SaveVariationResponse(variation_id=req.variation_id)


# ── POST /muse/head ──────────────────────────────────────────────────────


@router.post("/head", dependencies=[Depends(require_valid_token)])
async def set_head(
    req: SetHeadRequest,
    db: AsyncSession = Depends(get_db),
) -> SetHeadResponse:
    """Set the HEAD pointer for a project to a specific variation."""
    await muse_repository.set_head(db, req.variation_id)
    await db.commit()
    return SetHeadResponse(head=req.variation_id)


# ── GET /muse/log ────────────────────────────────────────────────────────


@router.get("/log", dependencies=[Depends(require_valid_token)])
async def get_log(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> MuseLogGraphResponse:
    """Return the full commit DAG for a project as ``MuseLogGraphResponse``."""
    graph = await build_muse_log_graph(db, project_id)
    return graph.to_response()


# ── POST /muse/checkout ──────────────────────────────────────────────────


@router.post("/checkout", dependencies=[Depends(require_valid_token)])
async def checkout(
    req: CheckoutRequest,
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    """Checkout to a specific variation — musical ``git checkout``.

    Reconstructs the target state, generates a checkout plan, executes
    it against StateStore, and moves HEAD.

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
        return CheckoutResponse(
            project_id=summary.project_id,
            from_variation_id=summary.from_variation_id,
            to_variation_id=summary.to_variation_id,
            execution=CheckoutExecutionStats(
                executed=summary.execution.executed,
                failed=summary.execution.failed,
                plan_hash=summary.execution.plan_hash,
                events=[wrap_dict(e) for e in summary.execution.events],
            ),
            head_moved=summary.head_moved,
        )
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
) -> MergeResponse:
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
        return MergeResponse(
            project_id=summary.project_id,
            merge_variation_id=summary.merge_variation_id,
            left_id=summary.left_id,
            right_id=summary.right_id,
            execution=CheckoutExecutionStats(
                executed=summary.execution.executed,
                failed=summary.execution.failed,
                plan_hash=summary.execution.plan_hash,
                events=[wrap_dict(e) for e in summary.execution.events],
            ),
            head_moved=summary.head_moved,
        )
    except MergeConflictError as e:
        raise HTTPException(status_code=409, detail={
            "error": "merge_conflict",
            "conflicts": [
                {"region_id": c.region_id, "type": c.type, "description": c.description}
                for c in e.conflicts
            ],
        })
