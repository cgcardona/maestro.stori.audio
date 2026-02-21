"""Base classes for music generation backends."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional
from enum import Enum


class GeneratorBackend(str, Enum):
    """Available generation backends."""
    # Neural backends (primary)
    TEXT2MIDI = "text2midi"  # Text-to-MIDI via HuggingFace Spaces (best quality)
    
    # IR-based backends (fallback)
    DRUM_IR = "drum_ir"  # IR-based drum renderer
    BASS_IR = "bass_ir"  # IR-based bass
    HARMONIC_IR = "harmonic_ir"  # IR-based chords
    MELODY_IR = "melody_ir"  # IR-based melody
    
    # Legacy backends
    ORPHEUS = "orpheus"
    HUGGINGFACE = "huggingface"
    LLM = "llm"


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
    notes: list[dict[str, Any]]
    backend_used: GeneratorBackend
    metadata: dict[str, Any]
    error: Optional[str] = None
    cc_events: list[dict[str, Any]] = None  # type: ignore[assignment]
    pitch_bends: list[dict[str, Any]] = None  # type: ignore[assignment]
    aftertouch: list[dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.cc_events is None:
            self.cc_events = []
        if self.pitch_bends is None:
            self.pitch_bends = []
        if self.aftertouch is None:
            self.aftertouch = []


class MusicGeneratorBackend(ABC):
    """Abstract base for music generation backends."""
    
    @abstractmethod
    async def generate(
        self,
        instrument: str,
        style: str,
        tempo: int,
        bars: int,
        key: Optional[str] = None,
        chords: Optional[list[str]] = None,
        **kwargs,
    ) -> GenerationResult:
        """Generate MIDI notes for the given parameters."""
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this backend is available."""
        pass
    
    @property
    @abstractmethod
    def backend_type(self) -> GeneratorBackend:
        """Get the backend type."""
        pass
