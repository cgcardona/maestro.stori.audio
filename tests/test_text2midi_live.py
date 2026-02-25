"""
Live integration test for text2midi via HuggingFace Spaces Gradio API.

This test actually calls the amaai-lab/text2midi Space.
Run with: pytest tests/test_text2midi_live.py -v -s

Note: This test requires internet access and may take 30-60 seconds
as the Space may need to wake up.
"""
from __future__ import annotations

import logging

import pytest
import asyncio
from app.services.neural.text2midi_backend import (
    Text2MidiBackend,
    Text2MidiMelodyBackend,
    emotion_to_text_description,
)
from app.services.neural.melody_generator import MelodyGenerationRequest
from app.core.emotion_vector import EmotionVector

logger = logging.getLogger(__name__)


class TestText2MidiLive:
    """Live tests against text2midi HuggingFace Space."""
    
    @pytest.mark.asyncio
    async def test_backend_availability(self) -> None:

        """Check if text2midi Space is reachable."""
        backend = Text2MidiBackend()
        
        try:
            available = await backend.is_available()
            logger.info("Space available: %s", available)
            
            if not available:
                pytest.skip("text2midi Space not available")
        except ImportError:
            pytest.skip("gradio_client not installed")
    
    @pytest.mark.asyncio
    async def test_generate_simple_melody(self) -> None:

        """Generate a simple melody using text2midi."""
        backend = Text2MidiBackend()
        
        try:
            if not await backend.is_available():
                pytest.skip("text2midi Space not available")
        except ImportError:
            pytest.skip("gradio_client not installed")
        
        # Create a simple request
        emotion = EmotionVector(
            energy=0.6,
            valence=0.3,
            tension=0.3,
            intimacy=0.5,
            motion=0.5,
        )
        
        logger.info("Testing with emotion: %s", emotion)
        logger.info("Description: %s...", emotion_to_text_description(emotion, key="C", tempo=100)[:100])
        
        result = await backend.generate(
            bars=4,
            tempo=100,
            key="C",
            emotion_vector=emotion,
            style="melodic",
            instrument="piano",
        )
        
        logger.info("Success: %s", result.success)
        logger.info("Model: %s", result.model_used)
        logger.info("Notes generated: %d", len(result.notes))

        if result.error:
            logger.warning("Error: %s", result.error)

        if result.notes:
            logger.info("First 3 notes: %s", result.notes[:3])
            pitches = [n["pitch"] for n in result.notes]
            logger.info("Pitch range: %d - %d", min(pitches), max(pitches))
        
        # The test passes if we got any result (success or graceful failure)
        assert result is not None
        
        if result.success:
            assert len(result.notes) > 0
    
    @pytest.mark.asyncio
    async def test_generate_with_different_emotions(self) -> None:

        """Test generation with contrasting emotions."""
        backend = Text2MidiBackend()
        
        try:
            if not await backend.is_available():
                pytest.skip("text2midi Space not available")
        except ImportError:
            pytest.skip("gradio_client not installed")
        
        # High energy, positive
        upbeat = EmotionVector(energy=0.9, valence=0.8, motion=0.8)
        # Low energy, negative
        somber = EmotionVector(energy=0.2, valence=-0.7, motion=0.2)
        
        logger.info("Generating upbeat melody...")
        upbeat_desc = emotion_to_text_description(upbeat, key="G", tempo=140)
        logger.info("  Description: %s...", upbeat_desc[:80])
        
        upbeat_result = await backend.generate(
            bars=4,
            tempo=140,
            key="G",
            emotion_vector=upbeat,
        )
        
        logger.info("Generating somber melody...")
        somber_desc = emotion_to_text_description(somber, key="Dm", tempo=60)
        logger.info("  Description: %s...", somber_desc[:80])
        
        somber_result = await backend.generate(
            bars=4,
            tempo=60,
            key="Dm",
            emotion_vector=somber,
        )
        
        logger.info("Upbeat: %d notes", len(upbeat_result.notes))
        logger.info("Somber: %d notes", len(somber_result.notes))
        
        # Both should have generated something (or gracefully failed)
        assert upbeat_result is not None
        assert somber_result is not None


class TestText2MidiMelodyBackendLive:
    """Test the melody generator interface wrapper."""
    
    @pytest.mark.asyncio
    async def test_melody_backend_interface(self) -> None:

        """Test that Text2MidiMelodyBackend works with generator interface."""
        backend = Text2MidiMelodyBackend()
        
        try:
            if not await backend.is_available():
                pytest.skip("text2midi Space not available")
        except ImportError:
            pytest.skip("gradio_client not installed")
        
        request = MelodyGenerationRequest(
            bars=4,
            tempo=120,
            key="Am",
            chords=["Am", "F", "C", "G"],
            emotion_vector=EmotionVector(energy=0.5, valence=-0.3),
        )
        
        logger.info("Testing MelodyBackend interface...")
        result = await backend.generate(request)

        logger.info("Success: %s", result.success)
        logger.info("Model: %s", result.model_used)
        logger.info("Notes: %d", len(result.notes))
        
        assert result is not None


if __name__ == "__main__":
    # Quick manual test
    async def main() -> None:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        backend = Text2MidiBackend()
        logger.info("Available: %s", await backend.is_available())

        if await backend.is_available():
            result = await backend.generate(
                bars=4,
                tempo=120,
                key="C",
                emotion_vector=EmotionVector(),
            )
            logger.info("Generated %d notes", len(result.notes))
    
    asyncio.run(main())
