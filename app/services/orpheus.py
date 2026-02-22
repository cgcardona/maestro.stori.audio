"""
Orpheus Music Service Client.

Client for communicating with the Orpheus music generation service.
"""
import asyncio
import httpx
import logging
from typing import Optional, Any

from app.config import settings

# Error substrings that indicate a transient Gradio/GPU failure.
# These are retried with backoff before reporting failure.
_GPU_COLD_START_PHRASES = (
    "No GPU was available",
    "GPU unavailable",
    "no gpu",
    "exceeded your",
    "gpu quota",
)

_GRADIO_TRANSIENT_PHRASES = (
    "upstream Gradio app has raised an exception",
    "is not a mapping",
    "Queue is full",
    "Connection refused",
)

_MAX_RETRIES = 4
_RETRY_DELAYS = [2, 5, 10, 20]  # seconds between attempts

logger = logging.getLogger(__name__)

# Connection pool settings: kept generous because Orpheus calls are sequential
# within a session but multiple FastAPI workers may hit it concurrently.
_CONNECTION_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)


class OrpheusClient:
    """
    Async client for the Orpheus Music Service.

    Uses a long-lived httpx.AsyncClient with keepalive connection pooling so
    the TCP/TLS handshake cost is paid once per worker process rather than on
    every generation request.  Call warmup() from the FastAPI lifespan to
    pre-establish the connection before the first user request arrives.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        hf_token: Optional[str] = None,
        max_concurrent: Optional[int] = None,
    ):
        self.base_url = (base_url or settings.orpheus_base_url).rstrip("/")
        self.timeout = timeout or settings.orpheus_timeout
        # Use HF token if provided (for Gradio Spaces)
        self.hf_token = hf_token or getattr(settings, "hf_api_key", None)
        self._client: Optional[httpx.AsyncClient] = None

        n = max_concurrent or settings.orpheus_max_concurrent
        self._semaphore = asyncio.Semaphore(n)
        self._max_concurrent = n

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.hf_token:
                headers["Authorization"] = f"Bearer {self.hf_token}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=float(self.timeout),
                    write=30.0,
                    pool=5.0,
                ),
                limits=_CONNECTION_LIMITS,
                headers=headers,
            )
        return self._client

    async def warmup(self) -> None:
        """
        Pre-establish the connection to Orpheus during application startup.

        A single lightweight health-check opens the keepalive connection so
        the first real generation request incurs no cold-start latency.
        """
        try:
            healthy = await self.health_check()
            if healthy:
                logger.info("Orpheus connection warmed up ✓")
            else:
                logger.warning(
                    "Orpheus warmup: service responded but health check failed — "
                    "generation requests will retry automatically"
                )
        except Exception as exc:
            # Non-fatal: Orpheus may still be starting; generation will fail
            # loudly with a clear error if it's still down when needed.
            logger.warning(f"Orpheus warmup failed (service may not be running): {exc}")

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def health_check(self) -> bool:
        """Check if Orpheus service is healthy.

        Uses a short probe timeout (3 s) independent of the generation timeout
        so health endpoints respond quickly even when the service is unreachable.
        """
        probe_timeout = httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0)
        try:
            response = await self.client.get(
                f"{self.base_url}/health", timeout=probe_timeout
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Orpheus health check failed: {e}")
            return False
    
    @staticmethod
    def _is_gpu_cold_start_error(text: str) -> bool:
        """Return True if the error string indicates a Gradio GPU cold-start timeout."""
        lower = text.lower()
        return any(phrase.lower() in lower for phrase in _GPU_COLD_START_PHRASES)

    @staticmethod
    def _is_transient_error(text: str) -> bool:
        """Return True if the error is transient and worth retrying."""
        lower = text.lower()
        return (
            any(phrase.lower() in lower for phrase in _GPU_COLD_START_PHRASES)
            or any(phrase.lower() in lower for phrase in _GRADIO_TRANSIENT_PHRASES)
        )

    async def generate(
        self,
        genre: str = "boom_bap",
        tempo: int = 120,
        instruments: Optional[list[str]] = None,
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

        Retries up to 3 times (delays: 5s, 15s, 30s) when the Gradio GPU pod
        returns a cold-start timeout ("No GPU was available after 60s").

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

        payload: dict[str, Any] = {
            "genre": genre,
            "tempo": tempo,
            "instruments": instruments,
            "bars": bars,
            "tone_brightness": tone_brightness,
            "tone_warmth": tone_warmth,
            "energy_intensity": energy_intensity,
            "energy_excitement": energy_excitement,
            "complexity": complexity,
            "quality_preset": quality_preset,
        }
        # Only include optional fields when they carry a value.
        # Sending null for list/string fields triggers a Gradio-level
        # TypeError ("'NoneType' object is not a mapping") on the server.
        if key:
            payload["key"] = key
        if musical_goals:
            payload["musical_goals"] = musical_goals

        logger.info(f"Generating {instruments} in {genre} style at {tempo} BPM")
        if musical_goals:
            logger.info(f"  Musical goals: {musical_goals}")
        if not self.hf_token:
            logger.warning(
                "⚠️ Orpheus request without HF token; Gradio Space may return GPU quota errors"
            )

        if self._semaphore.locked():
            logger.info(
                f"⏳ [Orpheus] All {self._max_concurrent} GPU slots in use — "
                f"request for {instruments} queued"
            )

        _queue_start = asyncio.get_event_loop().time()
        async with self._semaphore:
            _queue_waited = asyncio.get_event_loop().time() - _queue_start
            _in_use = self._max_concurrent - self._semaphore._value
            if _queue_waited > 0.1:
                logger.info(
                    f"[Orpheus] GPU slot acquired for {instruments} after "
                    f"{_queue_waited:.1f}s queue wait ({_in_use}/{self._max_concurrent} in use)"
                )
            else:
                logger.info(
                    f"[Orpheus] GPU slot acquired for {instruments} "
                    f"({_in_use}/{self._max_concurrent} in use)"
                )

            _gen_start = asyncio.get_event_loop().time()
            for attempt in range(_MAX_RETRIES):
                try:
                    response = await self.client.post(
                        f"{self.base_url}/generate",
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()

                    if data is None or not isinstance(data, dict):
                        raw = response.text[:200] if response.text else "(empty)"
                        logger.warning(
                            f"⚠️ Orpheus returned non-dict response (attempt {attempt + 1}): {raw}"
                        )
                        if self._is_transient_error(raw) and attempt < _MAX_RETRIES - 1:
                            delay = _RETRY_DELAYS[attempt]
                            logger.warning(f"⚠️ Retrying in {delay}s (GPU issue in raw body)")
                            await asyncio.sleep(delay)
                            continue
                        return {
                            "success": False,
                            "error": f"Orpheus returned invalid response: {raw}",
                            "retry_count": attempt,
                        }

                    error_text = data.get("error", "")
                    # Retry on any transient error (GPU cold-start OR Gradio-level
                    # transient failures like "NoneType object is not a mapping").
                    # Previously only GPU cold-start was retried; this caused the
                    # Gradio transient errors observed during multi-section runs to
                    # be surfaced immediately as toolError without any retry attempt.
                    if not data.get("success") and self._is_transient_error(error_text):
                        if attempt < _MAX_RETRIES - 1:
                            delay = _RETRY_DELAYS[attempt]
                            _err_label = (
                                "GPU cold-start"
                                if self._is_gpu_cold_start_error(error_text)
                                else "Gradio transient error"
                            )
                            logger.warning(
                                f"⚠️ Orpheus {_err_label} "
                                f"(attempt {attempt + 1}/{_MAX_RETRIES}) "
                                f"— retrying in {delay}s: {error_text[:120]}"
                            )
                            await asyncio.sleep(delay)
                            continue
                        _is_gpu = self._is_gpu_cold_start_error(error_text)
                        logger.error(
                            f"❌ Orpheus generation failed after {_MAX_RETRIES} attempts: "
                            f"{error_text[:120]}"
                        )
                        return {
                            "success": False,
                            "error": "gpu_unavailable" if _is_gpu else error_text,
                            "message": (
                                f"MIDI generation failed after {_MAX_RETRIES} attempts — "
                                + (
                                    "GPU unavailable. Try again in a few minutes."
                                    if _is_gpu
                                    else error_text[:200]
                                )
                            ),
                            "retry_count": attempt + 1,
                        }

                    out: dict[str, Any] = {
                        "success": data.get("success", False),
                        "notes": data.get("notes", []),
                        "tool_calls": data.get("tool_calls", []),
                        "metadata": {**(data.get("metadata") or {}), "retry_count": attempt},
                    }
                    if not out["success"] and error_text:
                        out["error"] = error_text
                    _gen_elapsed = asyncio.get_event_loop().time() - _gen_start
                    logger.info(
                        f"[Orpheus] ✅ Generation done for {instruments}: "
                        f"{len(out['notes'])} notes in {_gen_elapsed:.1f}s "
                        f"(attempt {attempt + 1})"
                    )
                    return out

                except httpx.HTTPStatusError as e:
                    body = e.response.text
                    if self._is_transient_error(body) and attempt < _MAX_RETRIES - 1:
                        delay = _RETRY_DELAYS[attempt]
                        logger.warning(
                            f"⚠️ Orpheus GPU cold-start in HTTP {e.response.status_code} "
                            f"(attempt {attempt + 1}/{_MAX_RETRIES}) — retrying in {delay}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.error(f"❌ Orpheus HTTP error: {e.response.status_code}")
                    return {
                        "success": False,
                        "error": f"HTTP {e.response.status_code}: {body}",
                        "retry_count": attempt,
                    }

                except httpx.ConnectError:
                    logger.warning("⚠️ Orpheus service not reachable")
                    return {
                        "success": False,
                        "error": "Orpheus service not available",
                        "notes": [],
                        "retry_count": attempt,
                    }

                except Exception as e:
                    err_str = str(e)
                    if self._is_transient_error(err_str) and attempt < _MAX_RETRIES - 1:
                        delay = _RETRY_DELAYS[attempt]
                        logger.warning(
                            f"⚠️ Orpheus transient error (attempt {attempt + 1}/{_MAX_RETRIES}) "
                            f"— retrying in {delay}s: {err_str[:120]}"
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.error(f"❌ Orpheus request failed: {e}")
                    return {
                        "success": False,
                        "error": err_str,
                        "retry_count": attempt,
                    }

            # Should not reach here, but satisfy return type
            return {
                "success": False,
                "error": "gpu_unavailable",
                "message": f"MIDI generation failed after {_MAX_RETRIES} attempts — GPU unavailable.",
                "retry_count": _MAX_RETRIES,
            }


# ---------------------------------------------------------------------------
# Module-level singleton — shared across all OrpheusBackend instances so the
# connection pool is reused rather than recreated per-request.
# ---------------------------------------------------------------------------

_shared_client: Optional[OrpheusClient] = None


def get_orpheus_client() -> OrpheusClient:
    """Return the process-wide OrpheusClient singleton."""
    global _shared_client
    if _shared_client is None:
        _shared_client = OrpheusClient()
    return _shared_client


async def close_orpheus_client() -> None:
    """Close the singleton client (call from FastAPI lifespan shutdown)."""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.close()
        _shared_client = None
