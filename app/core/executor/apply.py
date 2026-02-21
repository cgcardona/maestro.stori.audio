"""Apply accepted variation phrases to canonical state."""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.state_store import get_or_create_store
from app.core.tracing import get_trace_context, trace_span
from app.core.executor.models import VariationApplyResult
from app.models.variation import Variation

logger = logging.getLogger(__name__)


async def apply_variation_phrases(
    variation: Variation,
    accepted_phrase_ids: list[str],
    project_state: dict[str, Any],
    conversation_id: Optional[str] = None,
) -> VariationApplyResult:
    """
    Apply accepted phrases from a variation to canonical state.

    This is the commit phase — only called after user accepts phrases.
    Changes are applied server-side via StateStore transaction.
    """
    trace = get_trace_context()

    with trace_span(trace, "apply_variation_phrases", {"phrase_count": len(accepted_phrase_ids)}):
        store = get_or_create_store(
            conversation_id=conversation_id or "default",
            project_id=project_state.get("id"),
        )
        # Only sync when a real project snapshot is provided — passing an empty dict
        # would wipe server-side state built during compose phase.
        if project_state:
            store.sync_from_client(project_state)

        try:
            notes_added = 0
            notes_removed = 0
            notes_modified = 0
            applied_phrases = []

            region_adds: dict[str, list[dict]] = {}
            region_removals: dict[str, list[dict]] = {}
            region_track_map: dict[str, str] = {}
            region_cc: dict[str, list[dict]] = {}
            region_pitch_bends: dict[str, list[dict]] = {}
            region_aftertouch: dict[str, list[dict]] = {}

            for phrase_id in accepted_phrase_ids:
                phrase = variation.get_phrase(phrase_id)
                if not phrase:
                    logger.warning(f"Phrase {phrase_id[:8]} not found in variation")
                    continue

                region_id = phrase.region_id
                region_track_map[region_id] = phrase.track_id

                region_adds.setdefault(region_id, [])
                region_removals.setdefault(region_id, [])

                for nc in phrase.note_changes:
                    if nc.change_type == "added":
                        notes_added += 1
                        if nc.after:
                            region_adds[region_id].append(nc.after.to_note_dict())
                    elif nc.change_type == "removed":
                        notes_removed += 1
                        if nc.before:
                            region_removals[region_id].append(nc.before.to_note_dict())
                    elif nc.change_type == "modified":
                        notes_modified += 1
                        if nc.before:
                            region_removals[region_id].append(nc.before.to_note_dict())
                        if nc.after:
                            region_adds[region_id].append(nc.after.to_note_dict())

                for cc_change in phrase.controller_changes:
                    kind = cc_change.get("kind", "cc")
                    if kind == "pitch_bend":
                        region_pitch_bends.setdefault(region_id, []).append(cc_change)
                    elif kind == "aftertouch":
                        region_aftertouch.setdefault(region_id, []).append(cc_change)
                    else:
                        region_cc.setdefault(region_id, []).append(cc_change)

                applied_phrases.append(phrase_id)

            tx = store.begin_transaction(f"Accept Variation: {len(accepted_phrase_ids)} phrases")

            for region_id, criteria in region_removals.items():
                if criteria:
                    store.remove_notes(region_id, criteria, transaction=tx)

            for region_id, notes in region_adds.items():
                if notes:
                    store.add_notes(region_id, notes, transaction=tx)

            for region_id, cc_events in region_cc.items():
                if cc_events:
                    store.add_cc(region_id, cc_events)

            for region_id, pb_events in region_pitch_bends.items():
                if pb_events:
                    store.add_pitch_bends(region_id, pb_events)

            for region_id, at_events in region_aftertouch.items():
                if at_events:
                    store.add_aftertouch(region_id, at_events)

            store.commit(tx)

            affected_region_ids = set(region_adds.keys()) | set(region_removals.keys())
            updated_regions: list[dict[str, Any]] = []

            for rid in sorted(affected_region_ids):
                track_id = region_track_map.get(rid) or store.get_region_track_id(rid) or ""
                notes = store.get_region_notes(rid)
                # Fallback: use the adds directly if the store is empty
                if not notes and rid in region_adds:
                    notes = region_adds[rid]

                region_entity = store.registry.get_region(rid)
                region_meta = region_entity.metadata if region_entity else {}

                updated_regions.append({
                    "region_id": rid,
                    "track_id": track_id,
                    "notes": notes,
                    "cc_events": store.get_region_cc(rid),
                    "pitch_bends": store.get_region_pitch_bends(rid),
                    "aftertouch": store.get_region_aftertouch(rid),
                    "start_beat": region_meta.get("startBeat"),
                    "duration_beats": region_meta.get("durationBeats"),
                    "name": region_entity.name if region_entity else None,
                })

            logger.info(
                "Applied variation phrases",
                extra={
                    "phrase_count": len(applied_phrases),
                    "notes_added": notes_added,
                    "notes_removed": notes_removed,
                    "notes_modified": notes_modified,
                    "updated_region_count": len(updated_regions),
                },
            )

            return VariationApplyResult(
                success=True,
                applied_phrase_ids=applied_phrases,
                notes_added=notes_added,
                notes_removed=notes_removed,
                notes_modified=notes_modified,
                updated_regions=updated_regions,
            )

        except Exception as e:
            logger.error(f"❌ Failed to apply variation phrases: {e}")
            return VariationApplyResult(
                success=False,
                applied_phrase_ids=[],
                notes_added=0,
                notes_removed=0,
                notes_modified=0,
                error=str(e),
            )
