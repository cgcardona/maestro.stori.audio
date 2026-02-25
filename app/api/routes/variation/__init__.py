"""
Variation route package.

Implements the Muse/Variation protocol:
- POST /variation/propose  — create record, launch background generation
- GET  /variation/stream   — real SSE stream with envelopes + replay
- GET  /variation/{id}     — poll status + phrases (reconnect support)
- POST /variation/commit   — apply accepted phrases from store
- POST /variation/discard  — cancel generation, transition to DISCARDED

Public re-exports for test imports:
    _record_to_variation — used by test_variation_protocol.py
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.variation import propose, stream, retrieve, commit, discard
from app.api.routes.variation.commit import _record_to_variation

router = APIRouter()

# stream must be included before retrieve to avoid /variation/stream
# being shadowed by /variation/{variation_id}
router.include_router(propose.router)
router.include_router(stream.router)
router.include_router(commit.router)
router.include_router(discard.router)
router.include_router(retrieve.router)

__all__ = ["router", "_record_to_variation"]
