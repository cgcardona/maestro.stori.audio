"""
Neural Melody Generator.

Wraps a pre-trained music transformer model for melody generation,
conditioned on:
- Emotion vectors (energy, valence, tension, intimacy, motion)
- Chord progressions
- Section constraints (bars, tempo, key)

This is the MVP replacement for melody_ir_renderer.py.

See NEURAL_MIDI_ROADMAP.md for architecture details.
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.contracts.json_types import NoteDict
from app.core.emotion_vector import EmotionVector, emotion_to_constraints, GenerationConstraints
from app.services.neural.tokenizer import MidiTokenizer, TokenizerConfig

logger = logging.getLogger(__name__)


@dataclass
class MelodyGenerationRequest:
    """Request for melody generation."""
    bars: int
    tempo: int
    key: str
    chords: list[str]  # One chord per bar (or empty)
    emotion_vector: EmotionVector
    
    # Optional conditioning
    seed_notes: list[NoteDict] | None = None  # Prime with these notes
    temperature: float = 1.0
    top_p: float = 0.9


@dataclass
class MelodyGenerationResult:
    """Result of melody generation."""
    notes: list[NoteDict]
    success: bool
    model_used: str
    metadata: dict[str, Any]


class MelodyModelBackend(ABC):
    """Abstract base for melody model backends."""
    
    @abstractmethod
    async def generate(self, request: MelodyGenerationRequest) -> MelodyGenerationResult:
        """Generate melody notes."""
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        """Check if backend is available."""
        pass


class MockNeuralMelodyBackend(MelodyModelBackend):
    """
    Mock neural melody backend for development/testing.
    
    Generates plausible melody using emotion-constrained rules.
    Replace this with real model integration.
    """
    
    def __init__(self) -> None:
        self.tokenizer = MidiTokenizer()
    
    async def is_available(self) -> bool:
        return True
    
    async def generate(self, request: MelodyGenerationRequest) -> MelodyGenerationResult:
        """
        Generate melody using emotion-constrained generation.
        
        This is a placeholder that demonstrates the interface.
        Replace with actual model inference.
        """
        try:
            constraints = emotion_to_constraints(request.emotion_vector)
            notes = self._generate_with_constraints(request, constraints)
            
            return MelodyGenerationResult(
                notes=notes,
                success=True,
                model_used="mock_neural",
                metadata={
                    "emotion_vector": request.emotion_vector.to_dict(),
                    "constraints": {
                        "register_center": constraints.register_center,
                        "register_spread": constraints.register_spread,
                        "rest_density": constraints.rest_density,
                    },
                },
            )
        except Exception as e:
            logger.exception(f"Mock neural generation failed: {e}")
            return MelodyGenerationResult(
                notes=[],
                success=False,
                model_used="mock_neural",
                metadata={"error": str(e)},
            )
    
    def _generate_with_constraints(
        self,
        request: MelodyGenerationRequest,
        constraints: GenerationConstraints,
    ) -> list[NoteDict]:
        """
        Generate notes using constraints derived from emotion vector.
        
        This placeholder generates more musical output than the old
        rule-based system by using emotion constraints.
        """
        notes: list[NoteDict] = []
        rng = random.Random()
        
        # Parse key for scale
        scale = self._get_scale(request.key)
        
        # Calculate note density based on motion and energy
        notes_per_bar = int(4 + (1 - constraints.rest_density) * 8)  # 4-12 notes/bar
        
        # Starting pitch based on register
        current_pitch = constraints.register_center
        
        for bar_idx in range(request.bars):
            bar_start = bar_idx * 4.0  # 4 beats per bar
            
            # Get chord for this bar
            chord_idx = bar_idx % len(request.chords) if request.chords else 0
            chord = request.chords[chord_idx] if request.chords else "C"
            chord_root = self._chord_root(chord)
            
            # Generate notes for this bar
            beat = 0.0
            notes_in_bar = 0
            
            while beat < 4.0 and notes_in_bar < notes_per_bar:
                # Skip based on rest density
                if rng.random() < constraints.rest_density * 0.5:
                    beat += 0.5
                    continue
                
                # Pitch selection
                if rng.random() < 0.3:  # 30% chance to hit chord tone
                    pitch = chord_root + rng.choice([0, 4, 7]) + 60
                else:
                    # Scale tone with contour
                    step = rng.choice([-2, -1, 0, 1, 2])
                    if rng.random() < constraints.leap_probability:
                        step = rng.choice([-5, -4, 4, 5])
                    
                    current_pitch += step
                    
                    # Constrain to register
                    min_pitch = constraints.register_center - constraints.register_spread
                    max_pitch = constraints.register_center + constraints.register_spread
                    current_pitch = max(min_pitch, min(max_pitch, current_pitch))
                    
                    pitch = current_pitch
                
                # Quantize to scale
                pitch = self._quantize_to_scale(pitch, scale)
                
                # Duration
                duration = rng.choice([0.25, 0.5, 0.5, 1.0, 1.0])
                if constraints.rest_density > 0.3:
                    duration = rng.choice([0.5, 1.0, 1.5, 2.0])
                
                # Velocity from constraints
                velocity = rng.randint(
                    constraints.velocity_floor,
                    constraints.velocity_ceiling,
                )
                
                notes.append({
                    "pitch": int(pitch),
                    "start_beat": round(bar_start + beat, 3),
                    "duration_beats": round(duration, 3),
                    "velocity": velocity,
                })
                
                beat += duration * 0.75 + rng.random() * 0.5
                notes_in_bar += 1
        
        return notes
    
    def _get_scale(self, key: str) -> list[int]:
        """Get scale degrees for key."""
        # Simplified: just use major or minor
        is_minor = "m" in key.lower() and "maj" not in key.lower()
        if is_minor:
            return [0, 2, 3, 5, 7, 8, 10]  # Natural minor
        return [0, 2, 4, 5, 7, 9, 11]  # Major
    
    def _chord_root(self, chord: str) -> int:
        """Get chord root as pitch class (0-11)."""
        note_map = {
            "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
        }
        if not chord:
            return 0
        root = chord[0].upper()
        pc = note_map.get(root, 0)
        if len(chord) > 1 and chord[1] == "#":
            pc += 1
        elif len(chord) > 1 and chord[1] == "b":
            pc -= 1
        return pc % 12
    
    def _quantize_to_scale(self, pitch: int, scale: list[int]) -> int:
        """Quantize pitch to nearest scale degree."""
        pc = pitch % 12
        octave = pitch // 12
        
        # Find nearest scale degree
        best = scale[0]
        best_dist = abs(pc - best)
        
        for degree in scale:
            dist = min(abs(pc - degree), abs(pc - degree - 12), abs(pc - degree + 12))
            if dist < best_dist:
                best_dist = dist
                best = degree
        
        return octave * 12 + best


# =============================================================================
# TODO: Real model backends (see docs/neural-midi-roadmap.md)
# =============================================================================

# class MuseCocoBackend(MelodyModelBackend):
#     """
#     MuseCoco model integration.
#     
#     MuseCoco is designed for text-to-symbolic music generation
#     with attribute conditioning.
#     
#     See: https://github.com/microsoft/muzic/tree/main/musecoco
#     """
#     pass


# =============================================================================
# Main Generator Class
# =============================================================================

class NeuralMelodyGenerator:
    """
    Main interface for neural melody generation.
    
    Wraps model backends and handles:
    - Emotion vector conditioning
    - Chord alignment
    - Tokenization (when using token-based models)
    """
    
    def __init__(self, backend: MelodyModelBackend | None = None):
        self.backend = backend or MockNeuralMelodyBackend()
        self.tokenizer = MidiTokenizer()
    
    async def generate(
        self,
        bars: int,
        tempo: int,
        key: str,
        chords: list[str] | None = None,
        emotion_vector: EmotionVector | None = None,
        temperature: float = 1.0,
        **kwargs: object,
    ) -> MelodyGenerationResult:
        """
        Generate a melody.
        
        Args:
            bars: Number of bars to generate
            tempo: Tempo in BPM
            key: Musical key (e.g., "C", "Am", "F#m")
            chords: Optional list of chords (one per bar or repeating)
            emotion_vector: Optional emotion conditioning (defaults to neutral)
            temperature: Generation temperature (higher = more random)
            
        Returns:
            MelodyGenerationResult with notes and metadata
        """
        # Default emotion vector
        if emotion_vector is None:
            emotion_vector = EmotionVector()
        
        # Default chords
        if not chords:
            chords = [key] * bars  # Just use tonic
        
        request = MelodyGenerationRequest(
            bars=bars,
            tempo=tempo,
            key=key,
            chords=chords,
            emotion_vector=emotion_vector,
            temperature=temperature,
        )
        
        logger.info(
            f"Generating melody: {bars} bars, key={key}, "
            f"emotion={emotion_vector}"
        )
        
        result = await self.backend.generate(request)
        
        if result.success:
            logger.info(f"Generated {len(result.notes)} notes using {result.model_used}")
        else:
            logger.warning(f"Generation failed: {result.metadata.get('error', 'unknown')}")
        
        return result
    
    async def is_available(self) -> bool:
        """Check if the generator backend is available."""
        return await self.backend.is_available()
