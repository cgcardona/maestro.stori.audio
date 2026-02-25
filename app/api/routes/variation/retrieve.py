"""GET /variation/{variation_id} — poll status + phrases."""

from __future__ import annotations

import logging

from typing import Any

from fastapi import APIRouter, HTTPException, Depends

from app.auth.dependencies import require_valid_token
from app.variation.storage.variation_store import get_variation_store

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/variation/{variation_id}")
async def get_variation(
    variation_id: str,
    token_claims: dict[str, Any] = Depends(require_valid_token),
) -> dict[str, Any]:
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

    phrases_data = []
    for p in sorted(record.phrases, key=lambda pr: pr.sequence):
        phrases_data.append({
            "phraseId": p.phrase_id,
            "sequence": p.sequence,
            "trackId": p.track_id,
            "regionId": p.region_id,
            "beatStart": p.beat_start,
            "beatEnd": p.beat_end,
            "label": p.label,
            "tags": p.tags,
            "aiExplanation": p.ai_explanation,
            "diff": p.diff_json,
        })

    return {
        "variationId": record.variation_id,
        "projectId": record.project_id,
        "baseStateId": record.base_state_id,
        "intent": record.intent,
        "status": record.status.value,
        "aiExplanation": record.ai_explanation,
        "affectedTracks": record.affected_tracks,
        "affectedRegions": record.affected_regions,
        "phrases": phrases_data,
        "phraseCount": len(record.phrases),
        "lastSequence": record.last_sequence,
        "createdAt": record.created_at.isoformat(),
        "updatedAt": record.updated_at.isoformat(),
        "errorMessage": record.error_message,
    }
