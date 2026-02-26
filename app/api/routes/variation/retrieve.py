"""GET /variation/{variation_id} — poll status + phrases."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import Field

from app.auth.dependencies import require_valid_token
from app.auth.tokens import TokenClaims
from app.models.base import CamelModel
from app.variation.core.event_envelope import PhrasePayload
from app.variation.storage.variation_store import get_variation_store

router = APIRouter()
logger = logging.getLogger(__name__)


class VariationPhraseResponse(CamelModel):
    """A single generated phrase within a polled variation.

    Each ``VariationPhraseResponse`` represents one MIDI phrase that was
    produced during a generation pass.  Phrases are ordered by ``sequence``
    and scoped to a specific track and region in the DAW project.

    Wire format: camelCase (via ``CamelModel``) — e.g. ``phraseId``,
    ``trackId``, ``beatStart``, ``aiExplanation``.

    Attributes:
        phrase_id: Stable UUID for this phrase.  Assigned at generation time;
            consistent across reconnect polls.
        sequence: Monotonically increasing integer within the variation.
            Phrases are delivered in sequence order; the last sequence number
            in the variation is ``GetVariationResponse.last_sequence``.
        track_id: ID of the DAW track this phrase belongs to.
        region_id: ID of the DAW region within the track that this phrase
            occupies.
        beat_start: Beat position (float) at which this phrase starts in the
            project timeline.
        beat_end: Beat position (float) at which this phrase ends.  The phrase
            duration in beats is ``beat_end - beat_start``.
        label: Human-readable display label for the phrase in the DAW
            (e.g. ``"Verse 1 Bass"``).
        tags: Arbitrary string tags attached to the phrase for categorisation
            or filtering (e.g. ``["groove", "verse"]``).
        ai_explanation: Natural-language explanation of what the AI generated
            in this phrase, or ``None`` if none was produced.
        diff: MIDI delta for this phrase as a raw ``dict[str, object]`` in the
            internal diff-JSON format.  Contains added/removed/modified notes
            and controller events relative to the base state.
    """

    phrase_id: str = Field(
        description="Stable UUID for this phrase, assigned at generation time."
    )
    sequence: int = Field(
        description="Monotonically increasing integer within the variation. Phrases are ordered by this field."
    )
    track_id: str = Field(
        description="ID of the DAW track this phrase belongs to."
    )
    region_id: str = Field(
        description="ID of the DAW region within the track that this phrase occupies."
    )
    beat_start: float = Field(
        description="Beat position at which this phrase starts in the project timeline."
    )
    beat_end: float = Field(
        description="Beat position at which this phrase ends. Duration = beat_end - beat_start."
    )
    label: str = Field(
        description="Human-readable display label for the phrase in the DAW."
    )
    tags: list[str] = Field(
        description="Arbitrary string tags attached to the phrase for categorisation or filtering."
    )
    ai_explanation: str | None = Field(
        description="Natural-language explanation of what the AI generated, or None."
    )
    diff: PhrasePayload = Field(
        description=(
            "MIDI delta for this phrase in the internal diff-JSON format. "
            "Contains added/removed/modified notes and controller events relative to the base state."
        )
    )


class GetVariationResponse(CamelModel):
    """Full variation status and phrase payload for polling clients.

    Returned by ``GET /variation/{variation_id}``.  Designed for clients that
    cannot maintain an SSE connection (e.g. after a disconnect / reconnect) and
    need to recover the current generation state in a single HTTP request.

    Wire format: camelCase (via ``CamelModel``) — e.g. ``variationId``,
    ``projectId``, ``baseStateId``, ``phraseCount``.

    Attributes:
        variation_id: UUID of this variation.
        project_id: UUID of the project this variation belongs to.
        base_state_id: Identifier of the StateStore snapshot that was the
            base when generation started (typically ``"muse"``).
        intent: The user's natural-language intent for this variation
            (e.g. ``"add a funky bass line"``).
        status: Current lifecycle status of the variation.  One of
            ``"streaming"``, ``"committed"``, ``"discarded"``, ``"error"``,
            or ``"pending"``.
        ai_explanation: Top-level AI explanation of the variation as a whole,
            or ``None`` if generation has not yet produced one.
        affected_tracks: List of track IDs that this variation modifies.
        affected_regions: List of region IDs that this variation modifies.
        phrases: Ordered list of all phrases generated so far, sorted by
            ``sequence`` ascending.
        phrase_count: Total number of phrases generated so far.  Equals
            ``len(phrases)``.
        last_sequence: Sequence number of the most recently delivered phrase.
            Useful for resuming a stream: the client can request events with
            sequence > ``last_sequence`` to avoid replaying already-seen data.
        created_at: ISO-8601 UTC timestamp of when the variation was created.
        updated_at: ISO-8601 UTC timestamp of when the variation was last
            updated (phrase added, status changed, etc.).
        error_message: Error description if ``status == "error"``, otherwise
            ``None``.
    """

    variation_id: str = Field(description="UUID of this variation.")
    project_id: str = Field(description="UUID of the project this variation belongs to.")
    base_state_id: str = Field(
        description="Identifier of the StateStore snapshot that was the base when generation started."
    )
    intent: str = Field(
        description="The user's natural-language intent for this variation."
    )
    status: str = Field(
        description=(
            "Current lifecycle status. "
            "One of 'streaming', 'committed', 'discarded', 'error', or 'pending'."
        )
    )
    ai_explanation: str | None = Field(
        description="Top-level AI explanation of the variation as a whole, or None."
    )
    affected_tracks: list[str] = Field(
        description="List of track IDs that this variation modifies."
    )
    affected_regions: list[str] = Field(
        description="List of region IDs that this variation modifies."
    )
    phrases: list[VariationPhraseResponse] = Field(
        description="Ordered list of all phrases generated so far, sorted by sequence ascending."
    )
    phrase_count: int = Field(
        description="Total number of phrases generated so far. Equals len(phrases)."
    )
    last_sequence: int = Field(
        description=(
            "Sequence number of the most recently delivered phrase. "
            "Use to resume a stream: request events with sequence > last_sequence."
        )
    )
    created_at: str = Field(
        description="ISO-8601 UTC timestamp of when the variation was created."
    )
    updated_at: str = Field(
        description="ISO-8601 UTC timestamp of when the variation was last updated."
    )
    error_message: str | None = Field(
        description="Error description if status == 'error', otherwise None."
    )


@router.get("/variation/{variation_id}", response_model_by_alias=True)
async def get_variation(
    variation_id: str,
    _auth: TokenClaims = Depends(require_valid_token),
) -> GetVariationResponse:
    """
    Poll variation status and phrases (for reconnect / non-streaming clients).

    Returns current status, phrases generated so far, and last sequence number.
    """
    vstore = get_variation_store()
    record = vstore.get(variation_id)
    if record is None:
        raise HTTPException(status_code=404, detail={
            "error": "Variation not found",
            "variationId": variation_id,
        })

    phrases = [
        VariationPhraseResponse(
            phrase_id=p.phrase_id,
            sequence=p.sequence,
            track_id=p.track_id,
            region_id=p.region_id,
            beat_start=p.beat_start,
            beat_end=p.beat_end,
            label=p.label,
            tags=p.tags,
            ai_explanation=p.ai_explanation,
            diff=p.diff_json,
        )
        for p in sorted(record.phrases, key=lambda pr: pr.sequence)
    ]

    return GetVariationResponse(
        variation_id=record.variation_id,
        project_id=record.project_id,
        base_state_id=record.base_state_id,
        intent=record.intent,
        status=record.status.value,
        ai_explanation=record.ai_explanation,
        affected_tracks=record.affected_tracks,
        affected_regions=record.affected_regions,
        phrases=phrases,
        phrase_count=len(record.phrases),
        last_sequence=record.last_sequence,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        error_message=record.error_message,
    )
