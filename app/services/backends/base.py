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
    """Result from music generation."""
    success: bool
    notes: list[dict[str, Any]]
    backend_used: GeneratorBackend
    metadata: dict[str, Any]
    error: Optional[str] = None


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
