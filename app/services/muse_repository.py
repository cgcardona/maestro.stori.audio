"""Muse persistence adapter — single point of DB access for variation history.

This module is the ONLY place that touches the variations/phrases/note_changes
tables.  Orchestration, executor, and VariationService must never import it
or depend on it structurally — they produce/consume domain models
(app.models.variation) and this module handles the storage translation.

Boundary rules:
  - Must NOT import StateStore, EntityRegistry, or get_or_create_store.
  - Must NOT import VariationService or executor modules.
  - May import domain models from app.models.variation.
  - May import ORM models from app.db.muse_models.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import muse_models as db
from app.models.variation import (
    ChangeType,
    Variation as DomainVariation,
    Phrase as DomainPhrase,
    NoteChange as DomainNoteChange,
    MidiNoteSnapshot,
)

logger = logging.getLogger(__name__)


async def save_variation(
    session: AsyncSession,
    variation: DomainVariation,
    *,
    project_id: str,
    base_state_id: str,
    conversation_id: str,
    region_metadata: dict[str, dict[str, Any]],
    status: str = "ready",
) -> None:
    """Persist a domain Variation and all its phrases/note_changes to Postgres."""
    row = db.Variation(
        variation_id=variation.variation_id,
        project_id=project_id,
        base_state_id=base_state_id,
        conversation_id=conversation_id,
        intent=variation.intent,
        explanation=variation.ai_explanation,
        status=status,
        affected_tracks=variation.affected_tracks,
        affected_regions=variation.affected_regions,
        beat_range_start=variation.beat_range[0],
        beat_range_end=variation.beat_range[1],
    )
    session.add(row)

    for seq, phrase in enumerate(variation.phrases, start=1):
        r_meta = region_metadata.get(phrase.region_id, {})
        p_row = db.Phrase(
            phrase_id=phrase.phrase_id,
            variation_id=variation.variation_id,
            sequence=seq,
            track_id=phrase.track_id,
            region_id=phrase.region_id,
            start_beat=phrase.start_beat,
            end_beat=phrase.end_beat,
            label=phrase.label,
            tags=phrase.tags or [],
            explanation=phrase.explanation,
            controller_changes=phrase.controller_changes or [],
            region_start_beat=r_meta.get("startBeat"),
            region_duration_beats=r_meta.get("durationBeats"),
            region_name=r_meta.get("name"),
        )
        session.add(p_row)

        for nc in phrase.note_changes:
            nc_row = db.NoteChange(
                id=str(uuid.uuid4()),
                phrase_id=phrase.phrase_id,
                change_type=nc.change_type,
                before_json=nc.before.model_dump() if nc.before else None,
                after_json=nc.after.model_dump() if nc.after else None,
            )
            session.add(nc_row)

    await session.flush()
    logger.info(
        "✅ Variation persisted: %s (%d phrases)",
        variation.variation_id[:8],
        len(variation.phrases),
    )


async def load_variation(
    session: AsyncSession,
    variation_id: str,
) -> DomainVariation | None:
    """Load a persisted variation and reconstruct the domain model.

    Returns None if the variation_id does not exist in the DB.
    """
    stmt = (
        select(db.Variation)
        .options(
            selectinload(db.Variation.phrases).selectinload(db.Phrase.note_changes)
        )
        .where(db.Variation.variation_id == variation_id)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None

    phrases: list[DomainPhrase] = []
    for p in sorted(row.phrases, key=lambda p: p.sequence):
        note_changes = [
            DomainNoteChange(
                note_id=nc.id,
                change_type=cast(ChangeType, nc.change_type),
                before=MidiNoteSnapshot.model_validate(nc.before_json) if nc.before_json else None,
                after=MidiNoteSnapshot.model_validate(nc.after_json) if nc.after_json else None,
            )
            for nc in p.note_changes
        ]
        phrases.append(DomainPhrase(
            phrase_id=p.phrase_id,
            track_id=p.track_id,
            region_id=p.region_id,
            start_beat=p.start_beat,
            end_beat=p.end_beat,
            label=p.label,
            note_changes=note_changes,
            controller_changes=p.controller_changes or [],
            explanation=p.explanation,
            tags=p.tags or [],
        ))

    beat_starts = [p.start_beat for p in phrases] if phrases else [0.0]
    beat_ends = [p.end_beat for p in phrases] if phrases else [0.0]

    return DomainVariation(
        variation_id=row.variation_id,
        intent=row.intent,
        ai_explanation=row.explanation,
        affected_tracks=row.affected_tracks or [],
        affected_regions=row.affected_regions or [],
        beat_range=(min(beat_starts), max(beat_ends)),
        phrases=phrases,
    )


async def get_status(
    session: AsyncSession,
    variation_id: str,
) -> str | None:
    """Return the current status string, or None if not found."""
    stmt = select(db.Variation.status).where(
        db.Variation.variation_id == variation_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_base_state_id(
    session: AsyncSession,
    variation_id: str,
) -> str | None:
    """Return the base_state_id for a variation, or None if not found."""
    stmt = select(db.Variation.base_state_id).where(
        db.Variation.variation_id == variation_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_phrase_ids(
    session: AsyncSession,
    variation_id: str,
) -> list[str]:
    """Return phrase IDs for a variation in sequence order."""
    stmt = (
        select(db.Phrase.phrase_id)
        .where(db.Phrase.variation_id == variation_id)
        .order_by(db.Phrase.sequence)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_region_metadata(
    session: AsyncSession,
    variation_id: str,
) -> dict[str, dict[str, Any]]:
    """Return region metadata keyed by region_id from persisted phrases."""
    stmt = (
        select(
            db.Phrase.region_id,
            db.Phrase.region_start_beat,
            db.Phrase.region_duration_beats,
            db.Phrase.region_name,
        )
        .where(db.Phrase.variation_id == variation_id)
    )
    result = await session.execute(stmt)
    meta: dict[str, dict[str, Any]] = {}
    for row in result:
        rid = row[0]
        if rid not in meta:
            meta[rid] = {
                "start_beat": row[1],
                "duration_beats": row[2],
                "name": row[3],
            }
    return meta


async def mark_committed(session: AsyncSession, variation_id: str) -> None:
    """Transition a variation to COMMITTED status."""
    stmt = (
        update(db.Variation)
        .where(db.Variation.variation_id == variation_id)
        .values(status="committed")
    )
    await session.execute(stmt)
    logger.info("Variation %s marked committed", variation_id[:8])


async def mark_discarded(session: AsyncSession, variation_id: str) -> None:
    """Transition a variation to DISCARDED status."""
    stmt = (
        update(db.Variation)
        .where(db.Variation.variation_id == variation_id)
        .values(status="discarded")
    )
    await session.execute(stmt)
    logger.info("Variation %s marked discarded", variation_id[:8])
