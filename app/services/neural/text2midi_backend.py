"""
Text2MIDI Backend via HuggingFace Spaces Gradio API.

Uses the amaai-lab/text2midi model which generates MIDI from text descriptions.
This is currently the best open-source text-to-MIDI model available.

Paper: https://arxiv.org/abs/2412.16526
Demo: https://huggingface.co/spaces/amaai-lab/text2midi
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.core.emotion_vector import EmotionVector

logger = logging.getLogger(__name__)


@dataclass
class Text2MidiResult:
    """Result from text2midi generation."""
    notes: list[dict]
    success: bool
    midi_path: Optional[str] = None
    model_used: str = "text2midi"
    metadata: Optional[dict] = None
    error: Optional[str] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


def emotion_to_text_description(
    emotion_vector: EmotionVector,
    key: str = "C",
    tempo: int = 120,
    style: Optional[str] = None,
    instrument: str = "piano",
) -> str:
    """
    Convert emotion vector to a text description for text2midi.
    
    text2midi accepts descriptions like:
    "A melodic electronic song with ambient elements, featuring piano,
     acoustic guitar, alto saxophone. Set in G minor with a 4/4 time signature,
     it moves at a lively Presto tempo. The composition evokes a blend of
     relaxation and darkness, with hints of happiness and a meditative quality."
    """
    # Map energy to tempo description
    if emotion_vector.energy > 0.8:
        tempo_desc = "Presto"
        energy_desc = "energetic and driving"
    elif emotion_vector.energy > 0.6:
        tempo_desc = "Allegro"
        energy_desc = "lively and upbeat"
    elif emotion_vector.energy > 0.4:
        tempo_desc = "Moderato"
        energy_desc = "moderate and flowing"
    elif emotion_vector.energy > 0.2:
        tempo_desc = "Andante"
        energy_desc = "gentle and relaxed"
    else:
        tempo_desc = "Adagio"
        energy_desc = "slow and contemplative"
    
    # Map valence to mood
    if emotion_vector.valence > 0.5:
        mood = "joyful and uplifting"
        mode = "major"
    elif emotion_vector.valence > 0.0:
        mood = "hopeful with a touch of melancholy"
        mode = "major"
    elif emotion_vector.valence > -0.5:
        mood = "melancholic and introspective"
        mode = "minor"
    else:
        mood = "dark and somber"
        mode = "minor"
    
    # Map tension to style hints
    if emotion_vector.tension > 0.7:
        tension_desc = "with building tension and dramatic moments"
    elif emotion_vector.tension > 0.4:
        tension_desc = "with subtle tension and dynamic contrast"
    else:
        tension_desc = "with a calm and peaceful atmosphere"
    
    # Map intimacy to arrangement
    if emotion_vector.intimacy > 0.7:
        arrangement = "sparse and intimate"
    elif emotion_vector.intimacy > 0.4:
        arrangement = "balanced and expressive"
    else:
        arrangement = "full and lush"
    
    # Map motion to rhythmic character
    if emotion_vector.motion > 0.7:
        motion_desc = "with driving rhythmic patterns"
    elif emotion_vector.motion > 0.4:
        motion_desc = "with flowing melodic lines"
    else:
        motion_desc = "with sustained notes and slow movement"
    
    # Build the description
    parts = [
        f"A {arrangement} {style or 'melodic'} piece",
        f"featuring {instrument}",
        f"Set in {key} {mode} with a 4/4 time signature",
        f"moving at a {tempo_desc} tempo around {tempo} BPM",
        f"The composition is {mood}",
        f"{tension_desc}",
        f"{motion_desc}",
        f"evoking {energy_desc} feelings",
    ]
    
    return ". ".join(parts) + "."


class Text2MidiBackend:
    """
    Backend that uses the text2midi HuggingFace Space.
    
    Converts emotion vectors to text descriptions and calls the Gradio API.
    """
    
    SPACE_NAME = "amaai-lab/text2midi"
    
    def __init__(self):
        self._client = None
    
    @property
    def client(self):
        """Lazy load the Gradio client."""
        if self._client is None:
            try:
                from gradio_client import Client
                from app.config import settings
                
                hf_token = settings.hf_api_key
                
                if hf_token:
                    logger.info(f"Connecting to {self.SPACE_NAME} with HF Pro authentication")
                    self._client = Client(self.SPACE_NAME, token=hf_token)
                else:
                    logger.warning(f"No HF token - connecting to {self.SPACE_NAME} anonymously (limited quota)")
                    self._client = Client(self.SPACE_NAME)
                    
                logger.info(f"Connected to {self.SPACE_NAME} Gradio Space")
            except ImportError:
                logger.error("gradio_client not installed. Run: pip install gradio_client")
                raise
            except Exception as e:
                logger.error(f"Failed to connect to {self.SPACE_NAME}: {e}")
                raise
        return self._client
    
    async def is_available(self) -> bool:
        """Check if the text2midi Space is available."""
        try:
            # Try to create the client
            _ = self.client
            return True
        except Exception as e:
            logger.debug(f"text2midi Space not available: {e}")
            return False
    
    def _beats_to_max_length(self, target_beats: float) -> int:
        """
        Map target beat count to text2midi max_length parameter.
        
        Based on empirical testing of the text2midi model:
        - Valid range: 500-2000 (model constraints)
        - Relationship is non-linear and somewhat unstable
        - Uses conservative known-good values
        
        Args:
            target_beats: Desired duration in beats (e.g., 64 for 16 bars)
            
        Returns:
            max_length parameter for text2midi API
        """
        # Empirically derived mapping (from comprehensive testing)
        # Format: (target_beats, max_length, actual_beats_achieved)
        mapping = [
            (16, 500, 15.5),   # 4 bars
            (40, 700, 40.9),   # 10 bars
            (64, 900, 60.0),   # 16 bars (most common)
            (100, 1400, 100.0), # 25 bars
        ]
        
        # Find closest match or interpolate
        if target_beats <= mapping[0][0]:
            max_len = mapping[0][1]
            logger.debug(f"[text2midi] {target_beats} beats → max_length={max_len} (minimum)")
            return max_len
        
        if target_beats >= mapping[-1][0]:
            # For very large requests, cap at highest known-good value
            max_len = mapping[-1][1]
            logger.info(
                f"[text2midi] {target_beats} beats requested, capped at {mapping[-1][2]:.1f} beats "
                f"(max_length={max_len})"
            )
            return max_len
        
        # Linear interpolation between known points
        for i in range(len(mapping) - 1):
            beats1, ml1, actual1 = mapping[i]
            beats2, ml2, actual2 = mapping[i + 1]
            
            if beats1 <= target_beats <= beats2:
                ratio = (target_beats - beats1) / (beats2 - beats1)
                max_len = int(ml1 + ratio * (ml2 - ml1))
                logger.debug(
                    f"[text2midi] {target_beats} beats → max_length={max_len} "
                    f"(interpolated between {beats1}-{beats2})"
                )
                return max_len
        
        # Fallback (should never reach here)
        logger.warning(f"[text2midi] Could not map {target_beats} beats, using default")
        return 900  # 16 bars, safe default
    
    async def generate(
        self,
        bars: int,
        tempo: int,
        key: str,
        emotion_vector: EmotionVector,
        style: Optional[str] = None,
        instrument: str = "piano",
        temperature: float = 1.0,
        **kwargs,
    ) -> Text2MidiResult:
        """
        Generate MIDI using text2midi.
        
        Converts emotion vector to text description, calls the Space,
        and parses the resulting MIDI file.
        """
        try:
            # Calculate target duration in beats
            target_beats = bars * 4  # 4 beats per bar
            max_length = self._beats_to_max_length(target_beats)
            
            # Generate text description from emotion vector
            description = emotion_to_text_description(
                emotion_vector=emotion_vector,
                key=key,
                tempo=tempo,
                style=style,
                instrument=instrument,
            )
            
            logger.info(
                f"[text2midi] Generating {bars} bars ({target_beats} beats) with max_length={max_length}"
            )
            logger.info(f"[text2midi] Description: {description[:100]}...")
            
            # Call the Gradio API in a thread pool (it's synchronous)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._call_gradio,
                description,
                temperature,
                max_length,
            )
            
            if result is None:
                return Text2MidiResult(
                    notes=[],
                    success=False,
                    error="Failed to generate MIDI from text2midi Space",
                )
            
            # Parse the MIDI file
            midi_path, notes = result
            
            return Text2MidiResult(
                notes=notes,
                success=True,
                midi_path=midi_path,
                model_used="text2midi/amaai-lab",
                metadata={
                    "description": description,
                    "temperature": temperature,
                    "emotion_vector": emotion_vector.to_dict(),
                    "bars_requested": bars,
                },
            )
            
        except Exception as e:
            logger.exception(f"[text2midi] Generation failed: {e}")
            return Text2MidiResult(
                notes=[],
                success=False,
                error=str(e),
            )
    
    def _call_gradio(
        self,
        description: str,
        temperature: float,
        max_length: int,
    ) -> Optional[tuple[str, list[dict]]]:
        """
        Call the Gradio API synchronously.
        
        Args:
            description: Text description of desired music
            temperature: Sampling temperature (0.8-1.1)
            max_length: Maximum sequence length (controls duration)
        
        Returns (midi_path, notes) or None on failure.
        """
        try:
            # The text2midi Space interface
            # Signature: predict(prompt, temperature, max_length)
            result = self.client.predict(
                description,
                temperature,
                max_length,
                api_name="/predict",
            )
            
            logger.debug(f"[text2midi] Raw result: {result}, type: {type(result)}")
            
            midi_path = None
            
            # Handle different result formats
            if isinstance(result, tuple):
                # text2midi returns (wav_path, midi_path)
                for item in result:
                    if isinstance(item, str) and item.endswith(".mid"):
                        midi_path = item
                        break
            elif isinstance(result, str) and result.endswith(".mid"):
                midi_path = result
            elif isinstance(result, dict):
                # Try common keys
                for key in ["midi", "output", "file", "path"]:
                    if key in result and isinstance(result[key], str):
                        midi_path = result[key]
                        break
            
            if midi_path:
                logger.info(f"[text2midi] Got MIDI file: {midi_path}")
                notes = self._parse_midi_file(midi_path)
                return (midi_path, notes)
            else:
                logger.warning(f"[text2midi] Could not find MIDI in result: {result}")
                return None
                
        except Exception as e:
            logger.error(f"[text2midi] Gradio call failed: {e}")
            return None
    
    def _parse_midi_file(self, midi_path: str) -> list[dict]:
        """
        Parse a MIDI file into our note format.
        
        Returns list of notes with pitch, start_beat, duration_beats, velocity.
        """
        try:
            # Try using mido library
            import mido
            
            mid = mido.MidiFile(midi_path)
            notes = []
            
            # Get tempo (microseconds per beat)
            tempo = 500000  # Default 120 BPM
            for track in mid.tracks:
                for msg in track:
                    if msg.type == "set_tempo":
                        tempo = msg.tempo
                        break
            
            # Ticks per beat
            tpb = mid.ticks_per_beat
            
            # Parse all tracks
            for track in mid.tracks:
                current_tick = 0
                active_notes = {}  # pitch -> (start_tick, velocity)
                
                for msg in track:
                    current_tick += msg.time
                    
                    if msg.type == "note_on" and msg.velocity > 0:
                        active_notes[msg.note] = (current_tick, msg.velocity)
                    elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                        if msg.note in active_notes:
                            start_tick, velocity = active_notes.pop(msg.note)
                            
                            # Convert ticks to beats
                            start_beat = start_tick / tpb
                            duration = (current_tick - start_tick) / tpb
                            
                            notes.append({
                                "pitch": msg.note,
                                "start_beat": round(start_beat, 3),
                                "duration_beats": max(0.125, round(duration, 3)),
                                "velocity": velocity,
                            })
            
            # Sort by start time
            notes.sort(key=lambda n: n["start_beat"])
            
            logger.info(f"[text2midi] Parsed {len(notes)} notes from MIDI")
            return notes
            
        except ImportError:
            logger.warning("mido library not installed, returning empty notes")
            return []
        except Exception as e:
            logger.error(f"[text2midi] Failed to parse MIDI: {e}")
            return []


# Integration with our melody generator interface
class Text2MidiMelodyBackend:
    """
    Wrapper that makes Text2MidiBackend compatible with MelodyModelBackend interface.
    """
    
    def __init__(self):
        self._backend = Text2MidiBackend()
    
    async def is_available(self) -> bool:
        return await self._backend.is_available()
    
    async def generate(self, request: Any) -> Any:
        """Generate melody using text2midi."""
        from app.services.neural.melody_generator import MelodyGenerationResult
        
        result = await self._backend.generate(
            bars=request.bars,
            tempo=request.tempo,
            key=request.key,
            emotion_vector=request.emotion_vector,
            style=getattr(request, "style", None),
            instrument="piano",  # text2midi is best with piano
            temperature=getattr(request, "temperature", 1.0),
        )
        
        return MelodyGenerationResult(
            notes=result.notes,
            success=result.success,
            model_used=result.model_used,
            metadata=result.metadata or {},
        )
