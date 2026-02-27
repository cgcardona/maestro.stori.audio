"""StorpheusClient — submit generation jobs, poll for completion, capture MIDI."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from tourdeforce.config import TDFConfig
from tourdeforce.models import (
    Component, EventType, Severity, TraceContext, sha256_payload,
)
from tourdeforce.collectors.events import EventCollector
from tourdeforce.collectors.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class StorpheusClient:
    """Submits generation requests and polls for MIDI output."""

    def __init__(
        self,
        config: TDFConfig,
        event_collector: EventCollector,
        metrics: MetricsCollector,
        payload_dir: Path,
        midi_dir: Path,
    ) -> None:
        self._config = config
        self._events = event_collector
        self._metrics = metrics
        self._req_dir = payload_dir / "storpheus_requests"
        self._resp_dir = payload_dir / "storpheus_responses"
        self._midi_dir = midi_dir
        self._req_dir.mkdir(parents=True, exist_ok=True)
        self._resp_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(
            base_url=config.storpheus_url,
            timeout=httpx.Timeout(connect=10.0, read=config.storpheus_job_timeout, write=10.0, pool=10.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def generate(
        self,
        run_id: str,
        trace: TraceContext,
        *,
        genre: str = "boom_bap",
        tempo: int = 90,
        instruments: list[str] | None = None,
        bars: int = 4,
        key: str | None = None,
        seed: int | None = None,
        add_outro: bool = False,
        unified_output: bool = True,
    ) -> StorpheusResult:
        """Submit a generation request and wait for completion."""
        span = trace.new_span("storpheus_generate")

        payload: dict[str, Any] = {
            "genre": genre,
            "tempo": tempo,
            "instruments": instruments or ["drums", "bass"],
            "bars": bars,
            "quality_preset": self._config.quality_preset,
            "unified_output": unified_output,
        }
        if key:
            payload["key"] = key
        if seed is not None:
            payload["seed"] = seed
        if add_outro:
            payload["add_outro"] = True

        payload_json = json.dumps(payload, default=str)
        payload_hash = sha256_payload(payload_json)

        # Persist request
        req_file = self._req_dir / f"{run_id}_request.json"
        req_file.write_text(json.dumps(payload, indent=2))

        await self._events.emit(
            run_id=run_id,
            scenario="storpheus_generate",
            component=Component.ORPHEUS,
            event_type=EventType.HTTP_REQUEST,
            trace=trace,
            data={"payload_hash": payload_hash, **payload},
        )

        submit_start = time.monotonic()

        async with self._metrics.timer("storpheus_total", run_id):
            # Submit job
            async with self._metrics.timer("storpheus_submit", run_id):
                resp = await self._client.post("/generate", json=payload)

            if resp.status_code != 200:
                trace.end_span()
                raise StorpheusError(f"Storpheus /generate returned {resp.status_code}: {resp.text[:500]}")

            submit_data = resp.json()
            job_id = submit_data.get("jobId", "")
            status = submit_data.get("status", "")

            # If cache hit, result is immediate
            if status == "complete":
                result_data = submit_data.get("result", {})
                queue_wait_ms = 0.0
                infer_ms = (time.monotonic() - submit_start) * 1000
            else:
                # Poll for completion
                queue_wait_ms, infer_ms, result_data = await self._poll_job(
                    run_id, job_id, trace,
                )

        total_ms = (time.monotonic() - submit_start) * 1000

        # Persist response
        resp_file = self._resp_dir / f"{run_id}_response.json"
        resp_file.write_text(json.dumps({
            "job_id": job_id,
            "status": status,
            "result": result_data,
        }, indent=2, default=str))

        result = StorpheusResult(
            job_id=job_id,
            status="complete",
            result=result_data,
            queue_wait_ms=queue_wait_ms,
            infer_ms=infer_ms,
            total_ms=total_ms,
            payload_hash=payload_hash,
        )

        # Record metrics
        await self._metrics.gauge("storpheus.queue_wait_ms", run_id, queue_wait_ms)
        await self._metrics.gauge("storpheus.infer_ms", run_id, infer_ms)
        await self._metrics.gauge("storpheus.total_ms", run_id, total_ms)

        await self._events.emit(
            run_id=run_id,
            scenario="storpheus_generate",
            component=Component.ORPHEUS,
            event_type=EventType.HTTP_RESPONSE,
            trace=trace,
            data={
                "job_id": job_id,
                "queue_wait_ms": queue_wait_ms,
                "infer_ms": infer_ms,
                "total_ms": total_ms,
                "note_count": result.note_count,
                "tool_call_count": len(result.tool_calls),
            },
        )

        trace.end_span()
        return result

    async def _poll_job(
        self,
        run_id: str,
        job_id: str,
        trace: TraceContext,
    ) -> tuple[float, float, dict]:
        """Long-poll until job completes or times out."""
        queue_start = time.monotonic()
        queue_wait_ms = 0.0
        retries = 0
        max_retries = 60  # ~30 min with 30s polls

        while retries < max_retries:
            try:
                resp = await self._client.get(
                    f"/jobs/{job_id}/wait",
                    params={"timeout": 30},
                )
            except httpx.ReadTimeout:
                retries += 1
                await self._metrics.counter("storpheus.retries", run_id)
                continue

            if resp.status_code != 200:
                retries += 1
                await self._metrics.counter("storpheus.retries", run_id)
                continue

            data = resp.json()
            status = data.get("status", "")

            if status == "queued":
                queue_wait_ms = (time.monotonic() - queue_start) * 1000
                retries += 1
                continue
            elif status == "running":
                if queue_wait_ms == 0:
                    queue_wait_ms = (time.monotonic() - queue_start) * 1000
                retries += 1
                continue
            elif status == "complete":
                if queue_wait_ms == 0:
                    queue_wait_ms = (time.monotonic() - queue_start) * 1000
                infer_ms = (time.monotonic() - queue_start) * 1000 - queue_wait_ms
                return queue_wait_ms, infer_ms, data.get("result", {})
            elif status == "failed":
                error = data.get("error", "Unknown Storpheus failure")
                raise StorpheusError(f"Storpheus job {job_id} failed: {error}")
            elif status == "canceled":
                raise StorpheusError(f"Storpheus job {job_id} was canceled")

        raise StorpheusError(f"Storpheus job {job_id} timed out after {max_retries} polls")

    async def health_check(self) -> dict:
        """Quick health check against Storpheus."""
        resp = await self._client.get("/health")
        return resp.json()


class StorpheusResult:
    """Structured result from a Storpheus generation job."""

    def __init__(
        self,
        job_id: str,
        status: str,
        result: dict,
        queue_wait_ms: float,
        infer_ms: float,
        total_ms: float,
        payload_hash: str,
    ) -> None:
        self.job_id = job_id
        self.status = status
        self.result = result
        self.queue_wait_ms = queue_wait_ms
        self.infer_ms = infer_ms
        self.total_ms = total_ms
        self.payload_hash = payload_hash

    @property
    def success(self) -> bool:
        return self.result.get("success", False)

    @property
    def tool_calls(self) -> list[dict]:
        return self.result.get("tool_calls", [])

    @property
    def notes(self) -> list[dict]:
        return self.result.get("notes", [])

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def metadata(self) -> dict:
        return self.result.get("metadata", {})

    @property
    def channel_notes(self) -> dict[str, list[dict]]:
        return self.result.get("channel_notes", {})

    @property
    def wav_path(self) -> str | None:
        return self.metadata.get("wav_path")

    @property
    def plot_path(self) -> str | None:
        return self.metadata.get("plot_path")

    @property
    def midi_path(self) -> str | None:
        return self.metadata.get("midi_path")

    @property
    def unified_channels(self) -> list[str]:
        return self.metadata.get("unified_channels", [])

    def to_summary(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "success": self.success,
            "note_count": self.note_count,
            "tool_call_count": len(self.tool_calls),
            "queue_wait_ms": self.queue_wait_ms,
            "infer_ms": self.infer_ms,
            "total_ms": self.total_ms,
            "unified_channels": self.unified_channels,
            "has_wav": self.wav_path is not None,
            "has_plot": self.plot_path is not None,
            "has_midi": self.midi_path is not None,
        }


class StorpheusError(Exception):
    pass
