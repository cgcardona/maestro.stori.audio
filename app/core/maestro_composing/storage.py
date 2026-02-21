"""Variation storage helper — persist Variation to VariationStore after generation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.state_store import StateStore

logger = logging.getLogger(__name__)


def _store_variation(
    variation: Any,
    project_context: dict[str, Any],
    store: "StateStore",
) -> None:
    """Persist a Variation to the VariationStore so commit/discard can find it.

    Called from the maestro/stream path after ``execute_plan_variation`` returns.
    Mirrors the storage logic in the ``/variation/propose`` background task.
    """
    from app.variation.storage.variation_store import (
        get_variation_store,
        PhraseRecord,
    )
    from app.variation.core.state_machine import VariationStatus

    project_id = project_context.get("id", "")
    base_state_id = store.get_state_id()

    vstore = get_variation_store()
    record = vstore.create(
        project_id=project_id,
        base_state_id=base_state_id,
        intent=variation.intent,
        variation_id=variation.variation_id,
        conversation_id=store.conversation_id,
    )

    record.transition_to(VariationStatus.STREAMING)
    record.ai_explanation = variation.ai_explanation
    record.affected_tracks = variation.affected_tracks
    record.affected_regions = variation.affected_regions

    for phrase in variation.phrases:
        seq = record.next_sequence()

        region_entity = store.registry.get_region(phrase.region_id)
        region_meta = region_entity.metadata if region_entity else {}
        region_start_beat = region_meta.get("startBeat")
        region_duration_beats = region_meta.get("durationBeats")
        region_name = region_entity.name if region_entity else None

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
                "controllerChanges": phrase.controller_changes,
            },
            ai_explanation=phrase.explanation,
            tags=phrase.tags,
            region_start_beat=region_start_beat,
            region_duration_beats=region_duration_beats,
            region_name=region_name,
        ))

    record.transition_to(VariationStatus.READY)
    logger.info(
        f"Variation stored: {variation.variation_id[:8]} "
        f"({len(variation.phrases)} phrases, status=READY)"
    )
