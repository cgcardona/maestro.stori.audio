"""Orpheus Music Service Client.

Client for communicating with the Orpheus music generation service.

The ``normalize_storpheus_tool_calls`` function is the adapter boundary:
all Orpheus responses that contain ``tool_calls`` MUST pass through it
before Maestro consumes the data.  Orpheus's internal tool names
(``addNotes``, ``addMidiCC``, ``addPitchBend``, ``addAftertouch``) are
an implementation detail of the Orpheus service and must not leak into
Maestro's core.
"""
from __future__ import annotations

import asyncio
import httpx
import logging
import time as _time

from typing_extensions import TypedDict

from app.config import settings
from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    JSONValue,
    NoteDict,
    StorpheusResultBucket,
    PitchBendDict,
)


class StorpheusRawResponse(TypedDict, total=False):
    """Raw HTTP response shape returned by generate_async.

    On success: ``success`` is True plus notes/tool_calls/metadata.
    On failure: ``success`` is False plus ``error`` (and optionally ``message``).
    ``channel_notes`` is present only on success when Orpheus returns them.
    """

    success: bool
    notes: list[NoteDict]
    tool_calls: list[dict[str, object]]
    metadata: dict[str, object]
    channel_notes: dict[int, list[NoteDict]]
    error: str
    message: str
    retry_count: int

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
    "read operation timed out",
    "timed out",
)

_MAX_RETRIES = 4
_RETRY_DELAYS = [2, 5, 10, 20]  # seconds between attempts

logger = logging.getLogger(__name__)


class _CircuitBreaker:
    """Prevents cascading failures when Orpheus is unavailable.

    After ``threshold`` consecutive failures the circuit opens and all
    subsequent calls fail immediately for ``cooldown`` seconds.  After the
    cooldown one probe request is allowed (half-open).  Success closes the
    circuit; failure re-opens it.

    Thread-safety is not needed — asyncio is single-threaded per event loop.
    """

    def __init__(self, threshold: int = 3, cooldown: float = 60.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if _time.monotonic() - self._opened_at >= self.cooldown:
            return False
        return True

    def record_success(self) -> None:
        if self._opened_at is not None:
            logger.info("🟢 Orpheus circuit breaker CLOSED (successful request)")
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold and self._opened_at is None:
            self._opened_at = _time.monotonic()
            logger.error(
                f"🔴 Orpheus circuit breaker OPEN after {self._failures} "
                f"consecutive failures — failing fast for {self.cooldown}s"
            )
        elif self._opened_at is not None:
            elapsed = _time.monotonic() - self._opened_at
            if elapsed >= self.cooldown:
                self._opened_at = _time.monotonic()
                logger.error(
                    "🔴 Orpheus circuit breaker re-opened (probe failed) "
                    f"— failing fast for another {self.cooldown}s"
                )

# Connection pool settings: kept generous because Orpheus calls are sequential
# within a session but multiple FastAPI workers may hit it concurrently.
_CONNECTION_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)


class StorpheusClient:
    """
    Async client for the Storpheus Music Service.

    Uses a long-lived httpx.AsyncClient with keepalive connection pooling so
    the TCP/TLS handshake cost is paid once per worker process rather than on
    every generation request.  Call warmup() from the FastAPI lifespan to
    pre-establish the connection before the first user request arrives.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
        hf_token: str | None = None,
        max_concurrent: int | None = None,
    ):
        self.base_url = (base_url or settings.storpheus_base_url).rstrip("/")
        self.timeout = timeout or settings.storpheus_timeout
        # Use HF token if provided (for Gradio Spaces)
        self.hf_token = hf_token or getattr(settings, "hf_api_key", None)
        self._client: httpx.AsyncClient | None = None

        n = max_concurrent or settings.storpheus_max_concurrent
        self._semaphore = asyncio.Semaphore(n)
        self._max_concurrent = n

        self._cb = _CircuitBreaker(
            threshold=settings.storpheus_cb_threshold,
            cooldown=float(settings.storpheus_cb_cooldown),
        )

    @property
    def circuit_breaker_open(self) -> bool:
        """True when the circuit breaker is tripped (Orpheus is down)."""
        return self._cb.is_open

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

    async def close(self) -> None:
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
        instruments: list[str] | None = None,
        bars: int = 4,
        key: str | None = None,
        quality_preset: str = "balanced",
        composition_id: str | None = None,
        # ── Canonical intent blocks ──
        emotion_vector: dict[str, float] | None = None,
        role_profile_summary: dict[str, float] | None = None,
        generation_constraints: dict[str, object] | None = None,
        intent_goals: list[dict[str, object]] | None = None,
        seed: int | None = None,
        trace_id: str | None = None,
        intent_hash: str | None = None,
        # ── Unified generation ──
        add_outro: bool = False,
        unified_output: bool = False,
    ) -> StorpheusRawResponse:
        """Generate MIDI via Orpheus using the async submit + long-poll pattern.

        1. POST /generate → returns immediately with {jobId, status}.
           Cache hits arrive pre-completed (no queue slot used).
        2. GET /jobs/{jobId}/wait?timeout=30 in a loop until complete/failed.

        The full canonical intent blocks (emotion_vector, role_profile_summary,
        generation_constraints, intent_goals) are included so Orpheus
        consumes them directly.
        """
        if instruments is None:
            instruments = ["drums", "bass"]

        payload: dict[str, object] = {
            "genre": genre,
            "tempo": tempo,
            "instruments": instruments,
            "bars": bars,
            "quality_preset": quality_preset,
        }
        if key:
            payload["key"] = key
        if composition_id:
            payload["composition_id"] = composition_id
        if emotion_vector is not None:
            payload["emotion_vector"] = emotion_vector
        if role_profile_summary is not None:
            payload["role_profile_summary"] = role_profile_summary
        if generation_constraints is not None:
            payload["generation_constraints"] = generation_constraints
        if intent_goals is not None:
            payload["intent_goals"] = intent_goals
        if seed is not None:
            payload["seed"] = seed
        if trace_id is not None:
            payload["trace_id"] = trace_id
        if intent_hash is not None:
            payload["intent_hash"] = intent_hash
        if add_outro:
            payload["add_outro"] = True
        if unified_output:
            payload["unified_output"] = True

        _log_prefix = f"[{composition_id[:8]}]" if composition_id else ""

        if self._cb.is_open:
            return {
                "success": False,
                "error": "storpheus_circuit_open",
                "message": (
                    "Orpheus music service is unavailable (circuit breaker open). "
                    "Do not retry — the service will be probed automatically."
                ),
                "retry_count": 0,
            }

        logger.info(f"{_log_prefix} Generating {instruments} in {genre} style at {tempo} BPM")
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

            # ── Submit phase ──────────────────────────────────────────
            _submit_timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
            job_id: str | None = None

            for attempt in range(_MAX_RETRIES):
                try:
                    response = await self.client.post(
                        f"{self.base_url}/generate",
                        json=payload,
                        timeout=_submit_timeout,
                    )

                    if response.status_code == 503:
                        if attempt < _MAX_RETRIES - 1:
                            delay = _RETRY_DELAYS[attempt]
                            logger.warning(
                                f"⚠️ Orpheus queue full (503) — retrying in {delay}s"
                            )
                            await asyncio.sleep(delay)
                            continue
                        self._cb.record_failure()
                        return {
                            "success": False,
                            "error": "Orpheus queue full",
                            "message": "Generation queue is full — try again shortly.",
                            "retry_count": attempt + 1,
                        }

                    response.raise_for_status()
                    data = response.json()

                    if not isinstance(data, dict):
                        self._cb.record_failure()
                        return {
                            "success": False,
                            "error": f"Invalid submit response: {response.text[:200]}",
                            "retry_count": attempt,
                        }

                    status = data.get("status")

                    if status == "complete":
                        result = data.get("result", {})
                        self._cb.record_success()
                        _elapsed = asyncio.get_event_loop().time() - _gen_start
                        logger.info(
                            f"{_log_prefix}[Orpheus] ✅ Cache hit for {instruments} in {_elapsed:.1f}s"
                        )
                        _raw_meta = result.get("metadata")
                        _cache_resp = StorpheusRawResponse(
                            success=bool(result.get("success", False)),
                            notes=result.get("notes", []),
                            tool_calls=result.get("tool_calls", []),
                            metadata={
                                **(_raw_meta if isinstance(_raw_meta, dict) else {}),
                                "retry_count": attempt,
                            },
                        )
                        if result.get("channel_notes"):
                            _cache_resp["channel_notes"] = result["channel_notes"]
                        return _cache_resp

                    job_id = data.get("jobId")
                    if not job_id:
                        self._cb.record_failure()
                        return {
                            "success": False,
                            "error": "No jobId in Orpheus submit response",
                            "retry_count": attempt,
                        }
                    logger.info(
                        f"{_log_prefix}[Orpheus] 📥 Job {job_id[:8]} submitted for {instruments} "
                        f"(position {data.get('position', '?')})"
                    )
                    break

                except httpx.ConnectError:
                    self._cb.record_failure()
                    logger.warning("⚠️ Orpheus service not reachable")
                    return {
                        "success": False,
                        "error": "Orpheus service not available",
                        "notes": [],
                        "retry_count": attempt,
                    }

                except (httpx.ReadTimeout, httpx.HTTPStatusError) as exc:
                    if attempt < _MAX_RETRIES - 1:
                        delay = _RETRY_DELAYS[attempt]
                        logger.warning(
                            f"⚠️ Orpheus submit error ({type(exc).__name__}) "
                            f"(attempt {attempt + 1}/{_MAX_RETRIES}) — retrying in {delay}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                    self._cb.record_failure()
                    return {
                        "success": False,
                        "error": f"Orpheus submit failed: {exc}",
                        "retry_count": attempt + 1,
                    }

                except Exception as exc:
                    self._cb.record_failure()
                    logger.error(f"❌ Orpheus submit error: {exc}")
                    return {
                        "success": False,
                        "error": str(exc),
                        "retry_count": attempt,
                    }

            if job_id is None:
                self._cb.record_failure()
                return {
                    "success": False,
                    "error": "Failed to submit job after retries",
                    "retry_count": _MAX_RETRIES,
                }

            # ── Poll phase ────────────────────────────────────────────
            poll_timeout = settings.storpheus_poll_timeout
            max_polls = settings.storpheus_poll_max_attempts
            _poll_httpx_timeout = httpx.Timeout(
                connect=5.0,
                read=float(poll_timeout + 5),
                write=5.0,
                pool=5.0,
            )

            for poll_num in range(max_polls):
                try:
                    response = await self.client.get(
                        f"{self.base_url}/jobs/{job_id}/wait",
                        params={"timeout": poll_timeout},
                        timeout=_poll_httpx_timeout,
                    )
                    response.raise_for_status()
                    data = response.json()
                    status = data.get("status")

                    if status in ("complete", "failed"):
                        result = data.get("result", {})
                        error_text = result.get("error") or data.get("error", "")
                        _elapsed = asyncio.get_event_loop().time() - _gen_start

                        if status == "failed" or not result.get("success"):
                            self._cb.record_failure()
                            logger.error(
                                f"❌ Orpheus job {job_id[:8]} failed after "
                                f"{_elapsed:.1f}s: {error_text[:120]}"
                            )
                            return {
                                "success": False,
                                "error": error_text or "Generation failed",
                                "retry_count": 0,
                            }

                        self._cb.record_success()
                        logger.info(
                            f"{_log_prefix}[Orpheus] ✅ Job {job_id[:8]} complete for "
                            f"{instruments} in {_elapsed:.1f}s "
                            f"(poll {poll_num + 1}/{max_polls})"
                        )
                        _poll_meta = result.get("metadata")
                        _poll_resp = StorpheusRawResponse(
                            success=True,
                            notes=result.get("notes", []),
                            tool_calls=result.get("tool_calls", []),
                            metadata={
                                **(_poll_meta if isinstance(_poll_meta, dict) else {}),
                                "retry_count": 0,
                            },
                        )
                        if result.get("channel_notes"):
                            _poll_resp["channel_notes"] = result["channel_notes"]
                        return _poll_resp

                    logger.debug(
                        f"[Orpheus] Job {job_id[:8]} still {status} "
                        f"(poll {poll_num + 1}/{max_polls})"
                    )

                except httpx.ReadTimeout:
                    logger.debug(
                        f"[Orpheus] Poll timeout for {job_id[:8]} "
                        f"(poll {poll_num + 1}/{max_polls}) — job still running"
                    )

                except httpx.ConnectError:
                    self._cb.record_failure()
                    logger.warning(
                        f"⚠️ Orpheus connection lost while polling job {job_id[:8]}"
                    )
                    return {
                        "success": False,
                        "error": "Orpheus connection lost during polling",
                        "notes": [],
                        "retry_count": 0,
                    }

                except Exception as exc:
                    logger.warning(
                        f"⚠️ Poll error for job {job_id[:8]}: {exc}"
                    )

            self._cb.record_failure()
            _total = poll_timeout * max_polls
            logger.error(
                f"❌ Orpheus job {job_id[:8]} did not complete within {_total}s"
            )
            return {
                "success": False,
                "error": f"Generation did not complete within {_total}s",
                "retry_count": 0,
            }


def normalize_storpheus_tool_calls(
    tool_calls: list[dict[str, object]],
) -> StorpheusResultBucket:
    """Translate Orpheus-format tool_calls into Maestro-internal flat lists.

    Orpheus returns DAW-style tool names (``addNotes``, ``addMidiCC``,
    ``addPitchBend``, ``addAftertouch``).  This adapter extracts the
    musical content into plain lists keyed by data type so that callers
    never handle Storpheus-specific tool names.

    This is the quarantine boundary: raw ``dict[str, Any]`` from Storpheus
    enters, typed ``StorpheusResultBucket`` exits.
    """
    notes: list[NoteDict] = []
    cc_events: list[CCEventDict] = []
    pitch_bends: list[PitchBendDict] = []
    aftertouch: list[AftertouchDict] = []

    for tc in tool_calls:
        _tool_raw = tc.get("tool")
        tool_name = str(_tool_raw) if _tool_raw is not None else ""
        _params_raw = tc.get("params")
        params: dict[str, object] = _params_raw if isinstance(_params_raw, dict) else {}

        if tool_name == "addNotes":
            _notes_raw = params.get("notes")
            if isinstance(_notes_raw, list):
                notes.extend(_notes_raw)

        elif tool_name == "addMidiCC":
            _cc_raw = params.get("cc")
            cc_num = int(_cc_raw) if isinstance(_cc_raw, (int, float)) else 0
            _evts = params.get("events")
            for ev in (_evts if isinstance(_evts, list) else []):
                if not isinstance(ev, dict):
                    continue
                cc_events.append(CCEventDict(
                    cc=cc_num,
                    beat=ev.get("beat", 0),
                    value=ev.get("value", 0),
                ))

        elif tool_name == "addPitchBend":
            _evts = params.get("events")
            for ev in (_evts if isinstance(_evts, list) else []):
                if not isinstance(ev, dict):
                    continue
                pitch_bends.append(PitchBendDict(
                    beat=ev.get("beat", 0),
                    value=ev.get("value", 0),
                ))

        elif tool_name == "addAftertouch":
            _evts = params.get("events")
            for ev in (_evts if isinstance(_evts, list) else []):
                if not isinstance(ev, dict):
                    continue
                entry = AftertouchDict(
                    beat=ev.get("beat", 0),
                    value=ev.get("value", 0),
                )
                if "pitch" in ev:
                    entry["pitch"] = ev["pitch"]
                aftertouch.append(entry)

    return StorpheusResultBucket(
        notes=notes,
        cc_events=cc_events,
        pitch_bends=pitch_bends,
        aftertouch=aftertouch,
    )


# ---------------------------------------------------------------------------
# Module-level singleton — shared across all StorpheusBackend instances so the
# connection pool is reused rather than recreated per-request.
# ---------------------------------------------------------------------------

_shared_client: StorpheusClient | None = None


def get_storpheus_client() -> StorpheusClient:
    """Return the process-wide StorpheusClient singleton."""
    global _shared_client
    if _shared_client is None:
        _shared_client = StorpheusClient()
    return _shared_client


async def close_storpheus_client() -> None:
    """Close the singleton client (call from FastAPI lifespan shutdown)."""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.close()
        _shared_client = None
