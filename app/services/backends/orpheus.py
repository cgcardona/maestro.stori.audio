"""Orpheus Music Transformer backend."""
import logging
from typing import Optional

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.services.orpheus import OrpheusClient

logger = logging.getLogger(__name__)


class OrpheusBackend(MusicGeneratorBackend):
    """
    Orpheus Music Transformer backend.
    
    Best quality but requires GPU server running Orpheus.
    """
    
    def __init__(self):
        self.client = OrpheusClient()
    
    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.ORPHEUS
    
    async def is_available(self) -> bool:
        return await self.client.health_check()
    
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
        result = await self.client.generate(
            genre=style,
            tempo=tempo,
            instruments=[instrument],
            bars=bars,
            key=key,
        )
        
        if result.get("success"):
            # Extract notes from Orpheus tool_calls response
            # Orpheus returns: {"success": true, "tool_calls": [{tool: "addNotes", params: {notes: [...]}}]}
            notes = []
            tool_calls = result.get("tool_calls", [])
            
            for tool_call in tool_calls:
                if tool_call.get("tool") == "addNotes":
                    params = tool_call.get("params", {})
                    call_notes = params.get("notes", [])
                    notes.extend(call_notes)
            
            if notes:
                logger.info(f"Orpheus generated {len(notes)} notes")
                return GenerationResult(
                    success=True,
                    notes=notes,
                    backend_used=self.backend_type,
                    metadata={"source": "orpheus", "tool_calls_count": len(tool_calls)},
                )
            else:
                logger.warning("Orpheus returned success but no notes found in tool_calls")
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata={},
                    error="No notes found in Orpheus response",
                )
        else:
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=result.get("error", "Orpheus generation failed"),
            )
