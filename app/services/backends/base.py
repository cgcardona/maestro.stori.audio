"""Base classes for music generation backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

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
    ORPHEUS = "orpheus"
    TEXT2MIDI = "text2midi"
    DRUM_IR = "drum_ir"
    BASS_IR = "bass_ir"
    HARMONIC_IR = "harmonic_ir"
    MELODY_IR = "melody_ir"


@dataclass
class GenerationResult:
    """Result from music generation.

    Carries the full range of MIDI expressiveness:
    - notes: pitch, velocity, duration, channel
    - cc_events: Control Change 0-127 (sustain, expression, mod, volume, …)
    - pitch_bends: 14-bit pitch bend (-8192 to 8191)
    - aftertouch: channel pressure and polyphonic key pressure
    """
    success: bool
    notes: list[NoteDict]
    backend_used: GeneratorBackend
    metadata: dict[str, object]
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
        Orpheus overrides this to produce coherent multi-instrument output.
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
