"""LLM-based MIDI generation backend."""
import logging
import json
import re
from typing import Optional

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)

logger = logging.getLogger(__name__)


LLM_GENERATION_PROMPT = """You are a music generation AI. Generate MIDI note data for the requested pattern.

Output ONLY a JSON array of notes. Each note has:
- pitch: MIDI note number (0-127, where 60 = Middle C)
- start_beat: Position in beats (0 = start of pattern)
- duration_beats: Length in beats
- velocity: Dynamics (0-127, typically 70-110)

{instrument_guidance}

Generate {bars} bars of {style} {instrument} at {tempo} BPM in {key}.
{chord_context}

Requirements:
- Vary velocities for humanization (+/- 10 from base)
- Use appropriate octave range for the instrument
- Create musically coherent patterns
- Include rhythmic variation and interest

Output the JSON array only, no explanation:"""

INSTRUMENT_GUIDANCE = {
    "drums": """Drum MIDI mapping:
- 36: Kick
- 38: Snare
- 42: Closed Hi-Hat
- 44: Pedal Hi-Hat
- 46: Open Hi-Hat
- 49: Crash
- 51: Ride
- 37: Rimshot
- 39: Clap

Create a full drum pattern with kick, snare, and hi-hats at minimum.""",
    
    "bass": """Bass notes should be in octave 2-3 (MIDI 36-60).
Root notes: A=45, B=47, C=48, D=50, E=52, F=53, G=55
Follow the chord roots but add passing tones and rhythmic interest.""",
    
    "piano": """Chord voicings in octave 3-4 (MIDI 48-72).
Use proper jazz voicings with extensions (7ths, 9ths).
Vary rhythm - don't just play on beat 1 of each bar.""",
    
    "lead": """Melody in octave 4-5 (MIDI 60-84).
Create memorable phrases with space between them.
Use the scale/mode appropriate to the key.""",
}


class LLMGeneratorBackend(MusicGeneratorBackend):
    """
    LLM-based music generation using the orchestration model.
    
    Uses the same Qwen 72B model that powers the composition orchestration
    to directly generate MIDI note patterns. Quality is good for most use cases.
    """
    
    def __init__(self):
        from app.core.llm_client import LLMClient
        self.client = LLMClient()
    
    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.LLM
    
    async def is_available(self) -> bool:
        # LLM backend is always available if client is configured
        return True
    
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
        try:
            # Build the prompt
            instrument_guidance = INSTRUMENT_GUIDANCE.get(
                instrument,
                f"Generate appropriate MIDI notes for {instrument}."
            )
            
            chord_context = ""
            if chords:
                chord_context = f"Chord progression: {' | '.join(chords)}"
            
            prompt = LLM_GENERATION_PROMPT.format(
                instrument_guidance=instrument_guidance,
                bars=bars,
                style=style,
                instrument=instrument,
                tempo=tempo,
                key=key or "C",
                chord_context=chord_context,
            )
            
            # Call LLM
            response = await self.client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,  # More creative for music
                max_tokens=4096,
            )
            
            # Parse the JSON response
            content = response.content or ""
            
            # Try to extract JSON array from response
            notes = self._parse_notes(content)
            
            if notes:
                return GenerationResult(
                    success=True,
                    notes=notes,
                    backend_used=self.backend_type,
                    metadata={"source": "llm", "raw_response": content[:200]},
                )
            else:
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata={},
                    error="Failed to parse LLM response as notes",
                )
                
        except Exception as e:
            logger.exception(f"LLM generation failed: {e}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=str(e),
            )
    
    def _parse_notes(self, content: str) -> list[dict]:
        """Try to parse notes from LLM response."""
        # Try direct JSON parse
        try:
            notes = json.loads(content)
            if isinstance(notes, list):
                return self._validate_notes(notes)
        except json.JSONDecodeError:
            pass
        
        # Try to find JSON array in response
        match = re.search(r'\[[\s\S]*?\]', content)
        if match:
            try:
                notes = json.loads(match.group())
                if isinstance(notes, list):
                    return self._validate_notes(notes)
            except json.JSONDecodeError:
                pass
        
        return []
    
    def _validate_notes(self, notes: list) -> list[dict]:
        """Validate and clean note data."""
        valid_notes = []
        for note in notes:
            if isinstance(note, dict):
                try:
                    valid_note = {
                        "pitch": int(note.get("pitch", 60)),
                        "start_beat": float(note.get("start_beat", 0)),
                        "duration_beats": float(note.get("duration_beats", 0.5)),
                        "velocity": int(note.get("velocity", 100)),
                    }
                    # Basic validation
                    if 0 <= valid_note["pitch"] <= 127 and valid_note["duration_beats"] > 0:
                        valid_notes.append(valid_note)
                except (ValueError, TypeError):
                    continue
        return valid_notes
