"""Variation storage helper — persist Variation to VariationStore after generation."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

RegionMeta = dict[str, Any]


def _store_variation(
    variation: Any,
    project_context: dict[str, Any],
    base_state_id: str,
    conversation_id: str,
    region_metadata: dict[str, RegionMeta],
) -> None:
    """Persist a Variation to the VariationStore so commit/discard can find it.

    Called from the maestro/stream path after ``execute_plan_variation``
    returns.  The caller provides ``base_state_id``, ``conversation_id``,
    and ``region_metadata`` — this function never accesses StateStore or
    EntityRegistry directly.

    Args:
        variation: The computed Variation object.
        project_context: Project state dict (for project_id extraction).
        base_state_id: Optimistic concurrency token from the StateStore.
        conversation_id: Conversation/project identifier for cross-referencing.
        region_metadata: Mapping of ``region_id`` to ``{startBeat, durationBeats, name}``
            — built by the caller from the StateStore registry before calling.
    """
    from app.variation.storage.variation_store import (
        get_variation_store,
        PhraseRecord,
    )
    from app.variation.core.state_machine import VariationStatus

    project_id = project_context.get("id", "")

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
