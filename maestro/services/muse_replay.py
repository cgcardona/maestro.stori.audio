"""Muse Replay Engine — deterministic history reconstruction from persisted data.

Builds replay plans by walking the variation lineage graph. A ReplayPlan
describes the ordered sequence of variations and phrases needed to
reconstruct musical state at any point in history.

Also provides HEAD snapshot reconstruction for drift detection.

Boundary rules:
  - Must NOT import StateStore, EntityRegistry, or get_or_create_store.
  - Must NOT import executor modules.
  - Must NOT import LLM handlers or maestro_* modules.
  - May import muse_repository (for lineage queries and domain loading).
  - May import domain models from maestro.models.variation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.contracts.json_types import (
    NoteDict,
    RegionAftertouchMap,
    RegionCCMap,
    RegionNotesMap,
    RegionPitchBendMap,
)
from maestro.services import muse_repository
from maestro.services.muse_repository import HistoryNode

logger = logging.getLogger(__name__)



@dataclass(frozen=True)
class RegionUpdate:
    """A region affected by a replay step."""

    region_id: str
    track_id: str
    start_beat: float
    end_beat: float


@dataclass(frozen=True)
class ReplayPlan:
    """Deterministic reconstruction plan from root to target variation.

    Contains everything needed to rebuild musical state without touching
    StateStore or executor. Pure data.
    """

    ordered_variation_ids: list[str]
    ordered_phrase_ids: list[str]
    region_updates: list[RegionUpdate]
    lineage: list[HistoryNode] = field(default_factory=list)


async def build_replay_plan(
    session: AsyncSession,
    project_id: str,
    target_variation_id: str,
) -> ReplayPlan | None:
    """Build a replay plan from root to target variation.

    Walks the lineage graph via parent_variation_id to find the path from
    root to target, then collects phrases and region updates in order.

    Returns None if the target variation does not exist.
    """
    lineage = await muse_repository.get_lineage(session, target_variation_id)
    if not lineage:
        return None

    ordered_variation_ids: list[str] = []
    ordered_phrase_ids: list[str] = []
    region_updates: list[RegionUpdate] = []
    seen_regions: set[str] = set()

    for node in lineage:
        ordered_variation_ids.append(node.variation_id)

        variation = await muse_repository.load_variation(
            session, node.variation_id,
        )
        if variation is None:
            continue

        for phrase in variation.phrases:
            ordered_phrase_ids.append(phrase.phrase_id)
            if phrase.region_id not in seen_regions:
                seen_regions.add(phrase.region_id)
                region_updates.append(RegionUpdate(
                    region_id=phrase.region_id,
                    track_id=phrase.track_id,
                    start_beat=phrase.start_beat,
                    end_beat=phrase.end_beat,
                ))

    logger.info(
        "✅ Replay plan: %d variations, %d phrases, %d regions",
        len(ordered_variation_ids),
        len(ordered_phrase_ids),
        len(region_updates),
    )

    return ReplayPlan(
        ordered_variation_ids=ordered_variation_ids,
        ordered_phrase_ids=ordered_phrase_ids,
        region_updates=region_updates,
        lineage=lineage,
    )


# ── HEAD Snapshot Reconstruction (Phase 6) ────────────────────────────────


@dataclass(frozen=True)
class HeadSnapshot:
    """Snapshot reconstructed from HEAD variation's persisted data.

    Contains notes that Muse has committed (added/modified) and all
    controller data (CC, pitch bends, aftertouch) from persisted phrases.

    Notes that existed before Muse touched a region but were unchanged
    are not included.
    """

    variation_id: str
    notes: RegionNotesMap
    cc: RegionCCMap
    pitch_bends: RegionPitchBendMap
    aftertouch: RegionAftertouchMap
    track_regions: dict[str, str]
    region_start_beats: dict[str, float]


async def reconstruct_head_snapshot(
    session: AsyncSession,
    project_id: str,
) -> HeadSnapshot | None:
    """Reconstruct a snapshot from the HEAD variation's persisted phrases.

    Walks the full lineage from root to HEAD and collects the cumulative
    note state for each Muse-touched region.  For each NoteChange:

    - ``added``: the ``after`` note is included in the result.
    - ``modified``: the ``after`` note is included.
    - ``removed``: no note is added (Muse removed it).

    This is a *partial* reconstruction — it only reflects notes that Muse
    created or modified.  Notes that existed before Muse involvement are
    not tracked.

    Returns None if no HEAD exists for the project.
    """
    head = await muse_repository.get_head(session, project_id)
    if head is None:
        return None

    lineage = await muse_repository.get_lineage(session, head.variation_id)
    if not lineage:
        return None

    notes: RegionNotesMap = {}
    cc: RegionCCMap = {}
    pitch_bends: RegionPitchBendMap = {}
    aftertouch: RegionAftertouchMap = {}
    track_regions: dict[str, str] = {}
    region_start_beats: dict[str, float] = {}

    for node in lineage:
        variation = await muse_repository.load_variation(
            session, node.variation_id,
        )
        if variation is None:
            continue

        for phrase in variation.phrases:
            rid = phrase.region_id
            track_regions[rid] = phrase.track_id
            region_start_beats[rid] = phrase.start_beat

            region_notes = notes.setdefault(rid, [])
            for nc in phrase.note_changes:
                if nc.change_type in ("added", "modified") and nc.after:
                    region_notes.append(nc.after.to_note_dict())

            cc.setdefault(rid, []).extend(phrase.cc_events)
            pitch_bends.setdefault(rid, []).extend(phrase.pitch_bends)
            aftertouch.setdefault(rid, []).extend(phrase.aftertouch)

    logger.info(
        "✅ HEAD snapshot reconstructed: %d regions, %d notes, %d cc, %d pb, %d at",
        len(notes),
        sum(len(n) for n in notes.values()),
        sum(len(e) for e in cc.values()),
        sum(len(e) for e in pitch_bends.values()),
        sum(len(e) for e in aftertouch.values()),
    )

    return HeadSnapshot(
        variation_id=head.variation_id,
        notes=notes,
        cc=cc,
        pitch_bends=pitch_bends,
        aftertouch=aftertouch,
        track_regions=track_regions,
        region_start_beats=region_start_beats,
    )


async def reconstruct_variation_snapshot(
    session: AsyncSession,
    variation_id: str,
) -> HeadSnapshot | None:
    """Reconstruct snapshot at any variation (not necessarily HEAD).

    Same lineage-walking logic as ``reconstruct_head_snapshot`` but targets
    a specific variation_id instead of the project's current HEAD.

    Returns None if the variation does not exist.
    """
    lineage = await muse_repository.get_lineage(session, variation_id)
    if not lineage:
        return None

    notes: RegionNotesMap = {}
    cc: RegionCCMap = {}
    pitch_bends: RegionPitchBendMap = {}
    aftertouch: RegionAftertouchMap = {}
    track_regions: dict[str, str] = {}
    region_start_beats: dict[str, float] = {}

    for node in lineage:
        variation = await muse_repository.load_variation(
            session, node.variation_id,
        )
        if variation is None:
            continue

        for phrase in variation.phrases:
            rid = phrase.region_id
            track_regions[rid] = phrase.track_id
            region_start_beats[rid] = phrase.start_beat

            region_notes = notes.setdefault(rid, [])
            for nc in phrase.note_changes:
                if nc.change_type in ("added", "modified") and nc.after:
                    region_notes.append(nc.after.to_note_dict())

            cc.setdefault(rid, []).extend(phrase.cc_events)
            pitch_bends.setdefault(rid, []).extend(phrase.pitch_bends)
            aftertouch.setdefault(rid, []).extend(phrase.aftertouch)

    return HeadSnapshot(
        variation_id=variation_id,
        notes=notes,
        cc=cc,
        pitch_bends=pitch_bends,
        aftertouch=aftertouch,
        track_regions=track_regions,
        region_start_beats=region_start_beats,
    )
