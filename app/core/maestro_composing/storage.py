"""Variation storage helper — persist Variation to VariationStore + Postgres."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.contracts.json_types import RegionMetadataWire
    from app.contracts.project_types import ProjectContext
    from app.models.variation import Variation

logger = logging.getLogger(__name__)


async def _store_variation(
    variation: Variation,
    project_context: ProjectContext,
    base_state_id: str,
    conversation_id: str,
    region_metadata: dict[str, RegionMetadataWire],
) -> None:
    """Persist a Variation to both in-memory VariationStore and Postgres.

    Called from the maestro/stream path after ``execute_plan_variation``
    returns.  The caller provides ``base_state_id``, ``conversation_id``,
    and ``region_metadata`` — this function never accesses StateStore or
    EntityRegistry directly.

    Dual-write strategy:
      1. In-memory VariationStore — keeps SSE streaming and real-time lookups working.
      2. Postgres via muse_repository — durable storage that survives restarts.
    """
    from app.variation.storage.variation_store import (
        get_variation_store,
        PhraseRecord,
    )
    from app.variation.core.state_machine import VariationStatus

    project_id = project_context.get("id", "")

    # ── 1. In-memory write (existing path) ──────────────────────────────
    vstore = get_variation_store()
    record = vstore.create(
        project_id=project_id,
        base_state_id=base_state_id,
        intent=variation.intent,
        variation_id=variation.variation_id,
        conversation_id=conversation_id,
    )

    record.transition_to(VariationStatus.STREAMING)
    record.ai_explanation = variation.ai_explanation
    record.affected_tracks = variation.affected_tracks
    record.affected_regions = variation.affected_regions

    for phrase in variation.phrases:
        seq = record.next_sequence()

        r_meta = region_metadata.get(phrase.region_id, {})
        region_start_beat = r_meta.get("startBeat")
        region_duration_beats = r_meta.get("durationBeats")
        region_name = r_meta.get("name")

        record.add_phrase(PhraseRecord(
            phrase_id=phrase.phrase_id,
            variation_id=variation.variation_id,
            sequence=seq,
            track_id=phrase.track_id,
            region_id=phrase.region_id,
            beat_start=phrase.start_beat,
            beat_end=phrase.end_beat,
            label=phrase.label,
            diff_json={
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
            },
            ai_explanation=phrase.explanation,
            tags=phrase.tags,
            region_start_beat=region_start_beat,
            region_duration_beats=region_duration_beats,
            region_name=region_name,
        ))

    record.transition_to(VariationStatus.READY)

    # ── 2. Postgres write (new persistent path) ─────────────────────────
    try:
        from app.db.database import AsyncSessionLocal
        from app.services import muse_repository

        async with AsyncSessionLocal() as session:
            head = await muse_repository.get_head(session, project_id)
            parent_id = head.variation_id if head else None

            await muse_repository.save_variation(
                session,
                variation,
                project_id=project_id,
                base_state_id=base_state_id,
                conversation_id=conversation_id,
                region_metadata=region_metadata,
                parent_variation_id=parent_id,
            )
            await session.commit()
    except Exception:
        logger.warning(
            "⚠️ Postgres write failed for variation %s — in-memory copy is authoritative",
            variation.variation_id[:8],
            exc_info=True,
        )

    logger.info(
        "Variation stored: %s (%d phrases, status=READY)",
        variation.variation_id[:8],
        len(variation.phrases),
    )
