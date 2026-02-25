"""
HuggingFace-backed Neural Melody Generator.

Uses HuggingFace Inference API with emotion vector conditioning.
This is the fast path to real neural MIDI generation without
hosting our own models.

Models available:
- skytnt/midi-model-tv2o-medium (233M params, general purpose)
- asigalov61/Giant-Music-Transformer (786M params, multi-instrument)
- asigalov61/Orpheus-Music-Transformer (479M params, high quality)
"""

from __future__ import annotations

import logging
import httpx
import re
from dataclasses import dataclass
from typing import Any

from app.core.emotion_vector import EmotionVector, emotion_to_constraints
from app.services.neural.melody_generator import (
    MelodyModelBackend,
    MelodyGenerationRequest,
    MelodyGenerationResult,
)
from app.services.neural.tokenizer import MidiTokenizer
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class HFModelConfig:
    """Configuration for a HuggingFace MIDI model."""
    model_id: str
    max_tokens: int = 512
    supports_conditioning: bool = False
    token_format: str = "remi"  # remi, octuple, custom


# Available models with their configurations
HF_MODELS = {
    "skytnt": HFModelConfig(
        model_id="skytnt/midi-model-tv2o-medium",
        max_tokens=1024,
        supports_conditioning=False,
        token_format="custom",
    ),
    "giant": HFModelConfig(
        model_id="asigalov61/Giant-Music-Transformer",
        max_tokens=2048,
        supports_conditioning=False,
        token_format="custom",
    ),
    "orpheus": HFModelConfig(
        model_id="asigalov61/Orpheus-Music-Transformer",
        max_tokens=2048,
        supports_conditioning=False,
        token_format="custom",
    ),
}


class HuggingFaceMelodyBackend(MelodyModelBackend):
    """
    HuggingFace Inference API backend for melody generation.
    
    Uses emotion vector to influence:
    - Temperature (higher energy/tension → higher temperature)
    - Max length (more motion → more notes)
    - Top-p sampling (intimacy affects coherence)
    
    Falls back to mock generation if API unavailable.
    """
    
    def __init__(
        self,
        model_name: str = "skytnt",
        api_key: str | None = None,
    ):
        self.model_config = HF_MODELS.get(model_name, HF_MODELS["skytnt"])
        self.api_key = api_key or getattr(settings, "hf_api_key", None)
        self.api_url = "https://api-inference.huggingface.co/models"
        self._client: httpx.AsyncClient | None = None
        self.tokenizer = MidiTokenizer()
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=120.0,
            )
        return self._client
    
    async def is_available(self) -> bool:
        """Check if HuggingFace API is available."""
        if not self.api_key:
            logger.debug("No HuggingFace API key - will use mock backend")
            return False
        
        try:
            response = await self.client.get(
                f"{self.api_url}/{self.model_config.model_id}",
                timeout=10.0,
            )
            # 200 = ready, 503 = loading (still available)
            return response.status_code in [200, 503]
        except Exception as e:
            logger.debug(f"HuggingFace API check failed: {e}")
            return False
    
    async def generate(self, request: MelodyGenerationRequest) -> MelodyGenerationResult:
        """
        Generate melody using HuggingFace Inference API.
        
        Emotion vector influences generation parameters:
        - temperature: energy + tension → higher = more variation
        - top_p: intimacy → lower = more focused/coherent
        - max_new_tokens: motion → more = more notes
        """
        if not self.api_key:
            return await self._fallback_generate(request)
        
        try:
            # Map emotion to generation parameters
            gen_params = self._emotion_to_hf_params(request.emotion_vector, request.bars)
            
            # Build prompt/seed
            prompt = self._build_prompt(request)
            
            payload = {
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": gen_params["max_tokens"],
                    "temperature": gen_params["temperature"],
                    "top_p": gen_params["top_p"],
                    "do_sample": True,
                    "return_full_text": False,
                },
                "options": {
                    "wait_for_model": True,
                    "use_cache": False,
                }
            }
            
            logger.info(
                f"Calling HuggingFace model: {self.model_config.model_id}, "
                f"temp={gen_params['temperature']:.2f}, "
                f"emotion={request.emotion_vector}"
            )
            
            response = await self.client.post(
                f"{self.api_url}/{self.model_config.model_id}",
                json=payload,
                timeout=120.0,
            )
            
            if response.status_code == 429:
                logger.warning("HuggingFace rate limit - falling back to mock")
                return await self._fallback_generate(request)
            
            if response.status_code == 503:
                # Model loading - could retry or fallback
                logger.warning("HuggingFace model loading - falling back to mock")
                return await self._fallback_generate(request)
            
            response.raise_for_status()
            data = response.json()
            
            # Parse output
            notes = self._parse_output(data, request)
            
            # Apply emotion-based post-processing
            notes = self._apply_emotion_postprocess(notes, request.emotion_vector)
            
            return MelodyGenerationResult(
                notes=notes,
                success=True,
                model_used=f"huggingface/{self.model_config.model_id.split('/')[-1]}",
                metadata={
                    "emotion_vector": request.emotion_vector.to_dict(),
                    "hf_params": gen_params,
                    "raw_note_count": len(notes),
                },
            )
            
        except httpx.HTTPError as e:
            logger.warning(f"HuggingFace API error: {e}, falling back to mock")
            return await self._fallback_generate(request)
        except Exception as e:
            logger.exception(f"HuggingFace generation failed: {e}")
            return await self._fallback_generate(request)
    
    def _emotion_to_hf_params(self, ev: EmotionVector, bars: int) -> dict[str, Any]:
        """
        Map emotion vector to HuggingFace generation parameters.
        
        Higher energy/tension → higher temperature (more variation)
        Higher intimacy → lower top_p (more focused)
        Higher motion → more tokens (more notes)
        """
        # Temperature: 0.7 - 1.3 based on energy and tension
        base_temp = 0.85
        energy_factor = (ev.energy - 0.5) * 0.3  # -0.15 to +0.15
        tension_factor = (ev.tension - 0.5) * 0.2  # -0.1 to +0.1
        temperature = max(0.5, min(1.4, base_temp + energy_factor + tension_factor))
        
        # Top-p: 0.8 - 0.98 based on intimacy (intimate = more focused)
        top_p = 0.95 - (ev.intimacy * 0.15)  # 0.8 to 0.95
        
        # Max tokens: based on motion and bars
        base_tokens = bars * 32  # ~32 tokens per bar base
        motion_multiplier = 0.7 + (ev.motion * 0.6)  # 0.7x to 1.3x
        max_tokens = int(base_tokens * motion_multiplier)
        max_tokens = min(max_tokens, self.model_config.max_tokens)
        
        return {
            "temperature": round(temperature, 2),
            "top_p": round(top_p, 2),
            "max_tokens": max_tokens,
        }
    
    def _build_prompt(self, request: MelodyGenerationRequest) -> str:
        """
        Build a prompt/seed for the model.
        
        Different models have different prompt formats.
        This provides a reasonable default.
        """
        # Most MIDI models don't use text prompts - they use seed tokens
        # For now, we'll use a minimal seed that most models accept
        
        # Create a seed note sequence based on key and chords
        seed_notes = []
        
        if request.chords and len(request.chords) > 0:
            # Start with the root of the first chord
            chord = request.chords[0]
            root = self._chord_to_midi(chord)
            seed_notes.append(f"PITCH_{root}")
            seed_notes.append("DUR_4")
            seed_notes.append("VEL_80")
        
        if seed_notes:
            return " ".join(seed_notes)
        
        # Fallback: start with middle C
        return "PITCH_60 DUR_4 VEL_80"
    
    def _chord_to_midi(self, chord: str) -> int:
        """Convert chord symbol to MIDI root note."""
        note_map = {"C": 60, "D": 62, "E": 64, "F": 65, "G": 67, "A": 69, "B": 71}
        if not chord:
            return 60
        root = chord[0].upper()
        midi = note_map.get(root, 60)
        if len(chord) > 1:
            if chord[1] == "#":
                midi += 1
            elif chord[1] == "b":
                midi -= 1
        return midi
    
    def _parse_output(self, data: Any, request: MelodyGenerationRequest) -> list[dict[str, Any]]:
        """Parse HuggingFace model output into notes."""
        notes = []
        
        if isinstance(data, list) and len(data) > 0:
            result = data[0]
            
            if isinstance(result, dict) and "generated_text" in result:
                text = result["generated_text"]
                notes = self._parse_midi_tokens(text, request.tempo, request.bars)
        
        return notes
    
    def _parse_midi_tokens(self, text: str, tempo: int, bars: int) -> list[dict[str, Any]]:
        """Parse MIDI tokens from generated text."""
        notes = []
        max_beat = bars * 4
        
        # Common token patterns across models
        patterns = {
            "pitch": r"(?:PITCH|NOTE|NOTE_ON)[_\s]?(\d+)",
            "duration": r"(?:DUR|DURATION|TIME)[_\s]?(\d+)",
            "velocity": r"(?:VEL|VELOCITY)[_\s]?(\d+)",
            "position": r"(?:POS|POSITION)[_\s]?(\d+)",
        }
        
        # Try to extract structured data
        pitches = re.findall(patterns["pitch"], text, re.IGNORECASE)
        durations = re.findall(patterns["duration"], text, re.IGNORECASE)
        velocities = re.findall(patterns["velocity"], text, re.IGNORECASE)
        positions = re.findall(patterns["position"], text, re.IGNORECASE)
        
        current_beat = 0.0
        
        for i, pitch_str in enumerate(pitches):
            if current_beat >= max_beat:
                break
            
            try:
                pitch = int(pitch_str)
                
                # Clamp to reasonable melody range
                if pitch < 48 or pitch > 84:
                    pitch = max(48, min(84, pitch))
                
                # Get duration
                if i < len(durations):
                    dur_val = int(durations[i])
                    # Convert to beats (assuming 16th note units)
                    duration = dur_val / 4 if dur_val < 20 else dur_val / 480
                else:
                    duration = 0.5
                
                duration = max(0.125, min(2.0, duration))
                
                # Get velocity
                if i < len(velocities):
                    velocity = int(velocities[i])
                    velocity = max(40, min(127, velocity))
                else:
                    velocity = 80
                
                # Get position or advance
                if i < len(positions):
                    pos_val = int(positions[i])
                    current_beat = (pos_val / 4) % max_beat
                
                notes.append({
                    "pitch": pitch,
                    "start_beat": round(current_beat, 3),
                    "duration_beats": round(duration, 3),
                    "velocity": velocity,
                })
                
                current_beat += duration * 0.75 + 0.25  # Some overlap possible
                
            except (ValueError, IndexError):
                continue
        
        return notes
    
    def _apply_emotion_postprocess(
        self,
        notes: list[dict[str, Any]],
        emotion_vector: EmotionVector,
    ) -> list[dict[str, Any]]:
        """
        Apply emotion-based post-processing to generated notes.
        
        This adjusts the neural output to better match the requested emotion.
        """
        if not notes:
            return notes
        
        constraints = emotion_to_constraints(emotion_vector)
        
        processed = []
        for note in notes:
            n = dict(note)
            
            # Adjust velocity based on emotion
            vel_range = constraints.velocity_ceiling - constraints.velocity_floor
            n["velocity"] = constraints.velocity_floor + int(
                (note["velocity"] - 40) / 87 * vel_range  # Normalize and rescale
            )
            n["velocity"] = max(40, min(127, n["velocity"]))
            
            # Clamp to emotion-appropriate register
            min_pitch = constraints.register_center - constraints.register_spread
            max_pitch = constraints.register_center + constraints.register_spread
            
            while n["pitch"] < min_pitch and n["pitch"] < 84:
                n["pitch"] += 12
            while n["pitch"] > max_pitch and n["pitch"] > 48:
                n["pitch"] -= 12
            
            processed.append(n)
        
        return processed
    
    async def _fallback_generate(self, request: MelodyGenerationRequest) -> MelodyGenerationResult:
        """Fall back to mock generation when API unavailable."""
        from app.services.neural.melody_generator import MockNeuralMelodyBackend
        
        mock = MockNeuralMelodyBackend()
        result = await mock.generate(request)
        result.model_used = "mock_neural (hf_fallback)"
        return result
    
    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
