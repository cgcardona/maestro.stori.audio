"""Base classes for music generation backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

from app.contracts.generation_types import GenerationContext
from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    JSONValue,
    NoteDict,
    PitchBendDict,
)


class GeneratorBackend(str, Enum):
    """Available generation backends."""
    STORPHEUS = "storpheus"
    TEXT2MIDI = "text2midi"
    DRUM_IR = "drum_ir"
    BASS_IR = "bass_ir"
    HARMONIC_IR = "harmonic_ir"
    MELODY_IR = "melody_ir"


class GenerationMetadata(TypedDict, total=False):
    """Accumulated metadata bag attached to every ``GenerationResult``.

    All fields are optional (``total=False``) because metadata is populated
    incrementally: the backend contributes its own fields first, then the
    critic layer appends scoring fields via individual key assignment.

    Grouping by source (for readability — not enforced at runtime):

    Bass IR backend
    ---------------
    total_notes, kick_aligned_count, anticipation_count, style, used_rhythm_spine

    Drum IR backend
    ---------------
    style, groove_template, humanize_profile, bars, tempo

    Neural backends (melody_generator, huggingface_melody, text2midi)
    -----------------------------------------------------------------
    emotion_vector, constraints, error, description, temperature, bars_requested

    Critic layer (music_generator, after parallel scoring)
    ------------------------------------------------------
    critic_score, rejection_attempts, all_scores, parallel_candidates
    """

    # Bass IR
    total_notes: int
    kick_aligned_count: int
    anticipation_count: int
    style: str
    used_rhythm_spine: bool

    # Drum IR
    groove_template: str
    humanize_profile: str
    bars: int
    tempo: int

    # Neural backends — open sub-dicts kept as JSONValue-compatible
    emotion_vector: dict[str, float]   # EmotionVector.to_dict()
    constraints: dict[str, float]      # GenerationConstraints subset
    error: str
    description: str
    temperature: float
    bars_requested: int

    # Critic layer
    critic_score: float
    rejection_attempts: int
    all_scores: list[JSONValue]
    parallel_candidates: int
    candidate_idx: int

    # Backend provenance — which backend/section produced the result
    source: str                 # e.g. "bass_ir", "storpheus", "text2midi"
    coupling: str               # coupling description (bass IR: rhythm spine alignment)
    kick_count: int             # drum kick count used for bass IR alignment
    unified_section: str        # section key when extracted from a unified generation
    extracted_channel: str      # instrument channel extracted from unified output
    model: str                  # model identifier used (neural backends)
    instrument: str             # instrument name passed to the backend
    trace_id: str               # per-request trace ID propagated from the generation context
    intent_hash: str            # hash of the intent vector for cache keying
    unified_instruments: list[str]  # instrument list for unified generation
    distinct_pitches: int       # distinct MIDI pitches in the output (drum IR)
    repaired: bool              # True when the drum/IR output was repaired after scoring
    hf_params: dict[str, JSONValue]  # HuggingFace inference params (huggingface_melody)
    raw_note_count: int         # raw note count before post-processing (huggingface_melody)
    storpheus_metadata: dict[str, JSONValue]  # raw metadata blob from Storpheus response


@dataclass
class GenerationResult:
    """Result from music generation.

    Carries the full range of MIDI expressiveness:
    - notes: pitch, velocity, duration, channel
    - cc_events: Control Change 0-127 (sustain, expression, mod, volume, …)
    - pitch_bends: 14-bit pitch bend (-8192 to 8191)
    - aftertouch: channel pressure and polyphonic key pressure

    ``metadata`` is a ``GenerationMetadata`` bag populated by the backend and
    augmented by the critic layer.  All fields are optional; only those
    relevant to the generating backend are present.
    """
    success: bool
    notes: list[NoteDict]
    backend_used: GeneratorBackend
    metadata: GenerationMetadata
    error: str | None = None
    cc_events: list[CCEventDict] = field(default_factory=list)
    pitch_bends: list[PitchBendDict] = field(default_factory=list)
    aftertouch: list[AftertouchDict] = field(default_factory=list)
    channel_notes: dict[str, list[NoteDict]] | None = None


class MusicGeneratorBackend(ABC):
    """Abstract base for music generation backends."""

    @abstractmethod
    async def generate(
        self,
        instrument: str,
        style: str,
        tempo: int,
        bars: int,
        key: str | None = None,
        chords: list[str] | None = None,
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        """Generate MIDI notes for the given parameters."""
        pass

    async def generate_unified(
        self,
        instruments: list[str],
        style: str,
        tempo: int,
        bars: int,
        key: str | None = None,
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        """Generate all instruments together in a single call.

        Default implementation falls back to single-instrument generate().
        StorpheusBackend overrides this to produce coherent multi-instrument output.
        """
        return await self.generate(
            instrument=instruments[0] if instruments else "drums",
            style=style,
            tempo=tempo,
            bars=bars,
            key=key,
            context=context,
        )

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this backend is available."""
        pass

    @property
    @abstractmethod
    def backend_type(self) -> GeneratorBackend:
        """Get the backend type."""
        pass
