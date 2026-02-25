"""Variation Service for the Stori Maestro."""
from __future__ import annotations

from app.services.variation.note_matching import (
    TIMING_TOLERANCE_BEATS,
    PITCH_TOLERANCE,
    NoteMatch,
    _get_note_key,
    _notes_match,
    match_notes,
)
from app.services.variation.labels import (
    _beat_to_bar,
    _generate_bar_label,
    _detect_change_tags,
)
from app.services.variation.service import (
    VariationService,
    get_variation_service,
)

__all__ = [
    # Note matching
    "TIMING_TOLERANCE_BEATS",
    "PITCH_TOLERANCE",
    "NoteMatch",
    "_get_note_key",
    "_notes_match",
    "match_notes",
    # Labels
    "_beat_to_bar",
    "_generate_bar_label",
    "_detect_change_tags",
    # Service
    "VariationService",
    "get_variation_service",
]
