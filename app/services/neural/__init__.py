"""
Neural music generation services.

This package contains neural model wrappers for MIDI generation,
replacing rule-based renderers with learned models.

Backends available:
- MockNeuralMelodyBackend: Local placeholder (no API needed)
- HuggingFaceMelodyBackend: HuggingFace Inference API (needs HF_API_KEY)
- Text2MidiBackend: HuggingFace Spaces Gradio API (best quality, no key needed)
"""

from app.services.neural.tokenizer import MidiTokenizer, TokenizerConfig
from app.services.neural.melody_generator import (
    NeuralMelodyGenerator,
    MockNeuralMelodyBackend,
    MelodyModelBackend,
    MelodyGenerationRequest,
    MelodyGenerationResult,
)
from app.services.neural.huggingface_melody import (
    HuggingFaceMelodyBackend,
    HF_MODELS,
)
from app.services.neural.text2midi_backend import (
    Text2MidiBackend,
    Text2MidiMelodyBackend,
    emotion_to_text_description,
)

__all__ = [
    # Tokenizer
    "MidiTokenizer",
    "TokenizerConfig",
    # Generator
    "NeuralMelodyGenerator",
    "MelodyModelBackend",
    "MelodyGenerationRequest",
    "MelodyGenerationResult",
    # Backends
    "MockNeuralMelodyBackend",
    "HuggingFaceMelodyBackend",
    "HF_MODELS",
    # Text2MIDI (best quality)
    "Text2MidiBackend",
    "Text2MidiMelodyBackend",
    "emotion_to_text_description",
]
