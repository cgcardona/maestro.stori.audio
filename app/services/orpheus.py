"""
Orpheus Music Service Client.

Client for communicating with the Orpheus music generation service.
"""
import httpx
import logging
from typing import Optional, Any

from app.config import settings

logger = logging.getLogger(__name__)


class OrpheusClient:
    """Async client for the Orpheus Music Service."""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        hf_token: Optional[str] = None,
    ):
        self.base_url = (base_url or settings.orpheus_base_url).rstrip("/")
        self.timeout = timeout or settings.orpheus_timeout
        # Use HF token if provided (for Gradio Spaces)
        self.hf_token = hf_token or getattr(settings, "hf_api_key", None)
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            # Add HF authentication if token is available (for Gradio Spaces)
            if self.hf_token:
                headers["Authorization"] = f"Bearer {self.hf_token}"
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=headers,
            )
        return self._client
    
    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def health_check(self) -> bool:
        """Check if Orpheus service is healthy."""
        try:
            response = await self.client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Orpheus health check failed: {e}")
            return False
    
    async def generate(
        self,
        genre: str = "boom_bap",
        tempo: int = 90,
        instruments: list[str] = None,
        bars: int = 4,
        key: Optional[str] = None,
        # Intent vector fields (from LLM classification)
        musical_goals: Optional[list[str]] = None,
        tone_brightness: float = 0.0,
        tone_warmth: float = 0.0,
        energy_intensity: float = 0.0,
        energy_excitement: float = 0.0,
        complexity: float = 0.5,
        quality_preset: str = "balanced",
    ) -> dict[str, Any]:
        """
        Generate MIDI notes using Orpheus with rich intent support.
        
        Args:
            genre: Musical genre/style
            tempo: Tempo in BPM
            instruments: List of instruments to generate
            bars: Number of bars to generate
            key: Musical key (e.g., "Am", "C")
            musical_goals: List like ["dark", "energetic"] (from intent system)
            tone_brightness: -1 (dark) to +1 (bright)
            tone_warmth: -1 (cold) to +1 (warm)
            energy_intensity: -1 (calm) to +1 (intense)
            energy_excitement: -1 (laid back) to +1 (exciting)
            complexity: 0 (simple) to 1 (complex)
            quality_preset: "fast", "balanced", or "quality"
            
        Returns:
            Dict with success status and notes or error
        """
        if instruments is None:
            instruments = ["drums", "bass"]
        
        payload = {
            "genre": genre,
            "tempo": tempo,
            "instruments": instruments,
            "bars": bars,
            # Pass through intent fields
            "musical_goals": musical_goals,
            "tone_brightness": tone_brightness,
            "tone_warmth": tone_warmth,
            "energy_intensity": energy_intensity,
            "energy_excitement": energy_excitement,
            "complexity": complexity,
            "quality_preset": quality_preset,
        }
        if key:
            payload["key"] = key
        
        logger.info(f"Generating {instruments} in {genre} style at {tempo} BPM")
        if musical_goals:
            logger.info(f"  Musical goals: {musical_goals}")
        
        try:
            response = await self.client.post(
                f"{self.base_url}/generate",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            
            return {
                "success": data.get("success", False),
                "notes": data.get("notes", []),
                "tool_calls": data.get("tool_calls", []),
                "metadata": data.get("metadata", {}),
            }
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Orpheus HTTP error: {e.response.status_code}")
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text}",
            }
        except httpx.ConnectError:
            logger.warning("Orpheus service not available - returning empty result")
            return {
                "success": False,
                "error": "Orpheus service not available",
                "notes": [],
            }
        except Exception as e:
            logger.error(f"Orpheus request failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }
