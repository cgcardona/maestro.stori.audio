"""Music generation backends."""
from app.services.backends.drum_ir import DrumSpecBackend
from app.services.backends.bass_ir import BassSpecBackend
from app.services.backends.harmonic_ir import HarmonicSpecBackend
from app.services.backends.melody_ir import MelodySpecBackend
from app.services.backends.orpheus import OrpheusBackend
from app.services.backends.huggingface import HuggingFaceBackend
from app.services.backends.llm import LLMGeneratorBackend

__all__ = [
    "DrumSpecBackend",
    "BassSpecBackend",
    "HarmonicSpecBackend",
    "MelodySpecBackend",
    "OrpheusBackend",
    "HuggingFaceBackend",
    "LLMGeneratorBackend",
]
