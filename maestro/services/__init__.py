"""Services for Maestro."""
from __future__ import annotations

from maestro.services.storpheus import StorpheusClient
from maestro.services.music_generator import (
    MusicGenerator,
    get_music_generator,
    reset_music_generator,
)
from maestro.services.backends.base import (
    GeneratorBackend,
    GenerationResult,
    MusicGeneratorBackend,
)

__all__ = [
    "StorpheusClient",
    "MusicGenerator",
    "get_music_generator",
    "reset_music_generator",
    "GeneratorBackend",
    "GenerationResult",
    "MusicGeneratorBackend",
]
