"""Typed context for music generation backends and composition tool calls.

Replaces ``**kwargs: Any`` on ``MusicGeneratorBackend.generate()``
with an explicit ``GenerationContext`` TypedDict that lists every
parameter any backend ever consumes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import TypedDict

if TYPE_CHECKING:
    from maestro.contracts.json_types import NoteDict
    from maestro.core.emotion_vector import EmotionVector
    from maestro.core.music_spec_ir import MusicSpec
    from maestro.services.groove_engine import RhythmSpine

    _EmotionVectorOrNone = EmotionVector | None
else:
    _EmotionVectorOrNone = object


class GenerationContext(TypedDict, total=False):
    """All optional kwargs for ``MusicGeneratorBackend.generate()``.

    Backends pick the keys they need and ignore the rest.
    """

    emotion_vector: EmotionVector | None
    quality_preset: str
    composition_id: str | None
    seed: int | None
    trace_id: str | None
    add_outro: bool
    music_spec: MusicSpec | None
    rhythm_spine: RhythmSpine | None
    drum_kick_beats: list[float] | None
    temperature: float
    section_type: str | None
    num_candidates: int | None


class CompositionContext(TypedDict, total=False):
    """Contextual data threaded through generator tool calls.

    Carries resolved intent signals (emotion, quality, section continuity)
    that backends consume when executing ``stori_generate_midi``.
    All fields are optional — callers populate only what they know.
    """

    style: str
    tempo: int
    bars: int
    key: str | None
    quality_preset: str
    emotion_vector: EmotionVector | None
    section_key: str
    all_instruments: list[str]
    composition_id: str
    role: str
    previous_notes: list[NoteDict]
    drum_telemetry: dict[str, object]


class RoleResult(TypedDict, total=False):
    """Per-instrument result from ``execute_unified_generation``."""

    notes_added: int
    success: bool
    error: str | None
    track_id: str
    region_id: str


class UnifiedGenerationOutput(TypedDict, total=False):
    """Full return value of ``execute_unified_generation``.

    ``per_role`` is keyed by instrument role name.
    ``_metadata`` and ``_duration_ms`` are aggregated stats.
    """

    per_role: dict[str, RoleResult]
    _metadata: object
    _duration_ms: int
