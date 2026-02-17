"""HuggingFace Inference API backend for MIDI generation."""
import logging
import httpx
import json
from typing import Optional, Any

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.config import settings

logger = logging.getLogger(__name__)


class HuggingFaceBackend(MusicGeneratorBackend):
    """
    HuggingFace Inference API backend.
    
    Uses HuggingFace's hosted models for MIDI generation.
    Good models available:
    - asigalov61/Anticipatory-Music-Transformer-X
    - asigalov61/Multi-Instrumental-Music-Transformer
    - asigalov61/Giant-Music-Transformer
    
    Pros: No infrastructure needed, good quality
    Cons: Rate limited, requires API key
    """
    
    # Real working MIDI models on HuggingFace (verified Jan 2026)
    MODELS = {
        "drums": "skytnt/midi-model-tv2o-medium",  # Good for all instruments
        "bass": "skytnt/midi-model-tv2o-medium",
        "lead": "skytnt/midi-model-tv2o-medium",
        "piano": "skytnt/midi-model-tv2o-medium",
        "general": "skytnt/midi-model-tv2o-medium",  # Fallback for all
    }
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or getattr(settings, "hf_api_key", None)
        self.api_url = "https://api-inference.huggingface.co/models"
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.HUGGINGFACE
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=120.0,  # HF can be slow on cold starts
            )
        return self._client
    
    async def is_available(self) -> bool:
        """Check if HF API is available."""
        if not self.api_key:
            logger.debug("No HuggingFace API key configured")
            return False
        
        try:
            # Try a quick request to a small model
            model = self.MODELS["general"]
            response = await self.client.get(
                f"{self.api_url}/{model}",
                timeout=5.0,
            )
            return response.status_code in [200, 503]  # 503 = model loading
        except Exception as e:
            logger.debug(f"HuggingFace not available: {e}")
            return False
    
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
        """Generate using HuggingFace Inference API."""
        
        if not self.api_key:
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error="No HuggingFace API key configured",
            )
        
        # Select the best model for this instrument
        model = self.MODELS.get(instrument, self.MODELS["general"])
        
        try:
            # Build the prompt/input for the model
            prompt = self._build_prompt(instrument, style, tempo, bars, key, chords)
            
            payload = {
                "inputs": prompt,
                "parameters": {
                    "max_length": bars * 64,  # Rough estimate
                    "temperature": kwargs.get("temperature", 0.9),
                    "top_p": 0.95,
                    "num_return_sequences": 1,
                },
                "options": {
                    "wait_for_model": True,  # Wait if model is loading
                }
            }
            
            logger.info(f"Calling HuggingFace model: {model}")
            response = await self.client.post(
                f"{self.api_url}/{model}",
                json=payload,
                timeout=120.0,
            )
            
            # Handle rate limiting
            if response.status_code == 429:
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata={},
                    error="HuggingFace API rate limit exceeded",
                )
            
            response.raise_for_status()
            data = response.json()
            
            # Parse the generated MIDI tokens/notes
            notes = self._parse_hf_output(data, tempo)
            
            return GenerationResult(
                success=True,
                notes=notes,
                backend_used=self.backend_type,
                metadata={
                    "source": "huggingface",
                    "model": model.split("/")[-1],
                },
            )
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                error = "Rate limit exceeded"
            else:
                error = f"HTTP {e.response.status_code}"
            
            logger.warning(f"HuggingFace API error: {error}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=error,
            )
        except Exception as e:
            logger.exception(f"HuggingFace generation failed: {e}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=str(e),
            )
    
    def _build_prompt(
        self,
        instrument: str,
        style: str,
        tempo: int,
        bars: int,
        key: Optional[str],
        chords: Optional[list[str]],
    ) -> str:
        """Build a prompt for the model."""
        prompt_parts = [
            f"INSTRUMENT={instrument}",
            f"STYLE={style}",
            f"TEMPO={tempo}",
            f"BARS={bars}",
        ]
        
        if key:
            prompt_parts.append(f"KEY={key}")
        
        if chords:
            prompt_parts.append(f"CHORDS={','.join(chords)}")
        
        return " ".join(prompt_parts) + " GENERATE:"
    
    def _parse_hf_output(self, data: Any, tempo: int) -> list[dict]:
        """Parse HuggingFace model output into notes."""
        notes = []
        
        # HF models return different formats
        # Try to extract notes from common formats
        
        if isinstance(data, list) and len(data) > 0:
            result = data[0]
            
            # Check if it's already note data
            if isinstance(result, dict) and "notes" in result:
                return list(result["notes"]) if isinstance(result["notes"], list) else []
            
            # Check if it's generated text that needs parsing
            if isinstance(result, dict) and "generated_text" in result:
                text = result["generated_text"]
                # Parse MIDI tokens or note events from text
                notes = self._parse_midi_tokens(text, tempo)
        
        return notes
    
    def _parse_midi_tokens(self, text: str, tempo: int) -> list[dict]:
        """Parse MIDI tokens from generated text."""
        notes = []
        
        # Many HF MIDI models use token formats like:
        # NOTE_ON_60, TIME_100, NOTE_OFF_60, TIME_200
        # or similar representations
        
        # This is a simplified parser - real implementation would need
        # to match the specific model's token format
        
        import re
        
        # Try to find note events
        note_pattern = r"NOTE[_\s]ON[_\s](\d+)|PITCH[_\s](\d+)"
        time_pattern = r"TIME[_\s](\d+)|POS[_\s](\d+)"
        
        note_ons = re.findall(note_pattern, text, re.IGNORECASE)
        times = re.findall(time_pattern, text, re.IGNORECASE)
        
        for i, (pitch_match1, pitch_match2) in enumerate(note_ons[:32]):  # Limit notes
            pitch = int(pitch_match1 or pitch_match2 or 60)
            
            # Get timing
            if i < len(times):
                time_match1, time_match2 = times[i]
                time_ticks = int(time_match1 or time_match2 or 0)
                start_beat = (time_ticks / 100) * 4  # Convert to beats
            else:
                start_beat = i * 0.5
            
            notes.append({
                "pitch": pitch,
                "start_beat": start_beat,
                "duration_beats": 0.5,
                "velocity": 90,
            })
        
        return notes
    
    async def close(self):
        if self._client:
            await self._client.aclose()
