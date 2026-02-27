"""MaestroClient — POST /maestro/stream, consume SSE, capture everything."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx

from tourdeforce.config import TDFConfig
from tourdeforce.models import (
    Component, EventType, ParsedSSEEvent, Severity, TraceContext,
    sha256_payload,
)
from tourdeforce.sse_parser import (
    extract_complete,
    extract_generator_events,
    extract_state,
    extract_tool_calls,
    parse_sse_line,
)
from tourdeforce.collectors.events import EventCollector
from tourdeforce.collectors.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class MaestroClient:
    """Streams a prompt through Maestro and captures the full SSE lifecycle."""

    def __init__(
        self,
        config: TDFConfig,
        event_collector: EventCollector,
        metrics: MetricsCollector,
        payload_dir: Path,
    ) -> None:
        self._config = config
        self._events = event_collector
        self._metrics = metrics
        self._req_dir = payload_dir / "maestro_requests"
        self._sse_dir = payload_dir / "maestro_sse"
        self._req_dir.mkdir(parents=True, exist_ok=True)
        self._sse_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(
            connect=10.0,
            read=config.maestro_stream_timeout,
            write=10.0,
            pool=10.0,
        ))

    async def close(self) -> None:
        await self._client.aclose()

    async def compose(
        self,
        run_id: str,
        prompt_text: str,
        trace: TraceContext,
        *,
        project: dict[str, Any] | None = None,
        mode: str = "generate",
        conversation_id: str | None = None,
    ) -> MaestroResult:
        """Send prompt in compose mode and consume the full SSE stream."""
        span = trace.new_span("maestro_compose")
        conv_id = conversation_id or str(uuid.uuid4())

        payload = {
            "prompt": prompt_text,
            "mode": mode,
            "project": project,
            "conversationId": conv_id,
            "qualityPreset": self._config.quality_preset,
            "storePrompt": False,
        }

        payload_json = json.dumps(payload, default=str)
        payload_hash = sha256_payload(payload_json)

        # Persist request
        req_file = self._req_dir / f"{run_id}_request.json"
        req_file.write_text(json.dumps(payload, indent=2, default=str))

        await self._events.emit(
            run_id=run_id,
            scenario="maestro_compose",
            component=Component.MAESTRO,
            event_type=EventType.HTTP_REQUEST,
            trace=trace,
            data={
                "url": self._config.maestro_url,
                "method": "POST",
                "payload_hash": payload_hash,
                "prompt_length": len(prompt_text),
                "mode": mode,
                "conversation_id": conv_id,
            },
        )

        parsed_events: list[ParsedSSEEvent] = []
        raw_lines: list[str] = []

        async with self._metrics.timer("maestro_stream", run_id) as timer:
            try:
                async with self._client.stream(
                    "POST",
                    self._config.maestro_url,
                    json=payload,
                    headers=self._config.auth_headers,
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        trace.end_span()
                        raise MaestroError(
                            f"Maestro returned HTTP {resp.status_code}: {body.decode()[:500]}"
                        )

                    async for line in resp.aiter_lines():
                        raw_lines.append(line)
                        event = parse_sse_line(line)
                        if event is None:
                            continue
                        parsed_events.append(event)

                        await self._events.emit(
                            run_id=run_id,
                            scenario="maestro_compose",
                            component=Component.MAESTRO,
                            event_type=EventType.SSE_EVENT,
                            trace=trace,
                            data={
                                "sse_type": event.event_type,
                                "seq": event.seq,
                            },
                        )

            except httpx.ReadTimeout:
                trace.end_span()
                raise MaestroError(
                    f"Maestro stream timed out after {self._config.maestro_stream_timeout}s"
                )

        # Persist raw SSE
        sse_file = self._sse_dir / f"{run_id}_sse_raw.txt"
        sse_file.write_text("\n".join(raw_lines))

        # Persist parsed events
        parsed_file = self._sse_dir / f"{run_id}_sse_parsed.jsonl"
        with open(parsed_file, "w") as f:
            for e in parsed_events:
                f.write(json.dumps({"type": e.event_type, "seq": e.seq, "data": e.data}, default=str) + "\n")

        # Extract structured data
        state = extract_state(parsed_events)
        complete = extract_complete(parsed_events)
        tool_calls = extract_tool_calls(parsed_events)
        generators = extract_generator_events(parsed_events)

        result = MaestroResult(
            events=parsed_events,
            tool_calls=tool_calls,
            state=state,
            complete=complete,
            generators=generators,
            payload_hash=payload_hash,
            conversation_id=conv_id,
            duration_ms=timer.duration_ms,
        )

        # Validate invariants
        if not state:
            logger.warning("No state event in Maestro stream for run %s", run_id)
        elif mode == "generate" and state.get("state") != "composing":
            logger.warning(
                "Expected COMPOSING state for compose, got %s (run %s)",
                state.get("state"), run_id,
            )

        if not complete:
            logger.warning("No complete event in Maestro stream for run %s", run_id)

        success = complete.get("success", False)

        await self._events.emit(
            run_id=run_id,
            scenario="maestro_compose",
            component=Component.MAESTRO,
            event_type=EventType.HTTP_RESPONSE,
            trace=trace,
            severity=Severity.INFO if success else Severity.ERROR,
            data={
                "success": success,
                "event_count": len(parsed_events),
                "tool_call_count": len(tool_calls),
                "generator_count": len(generators),
                "duration_ms": timer.duration_ms,
                "state": state.get("state", ""),
                "intent": state.get("intent", ""),
                "trace_id": complete.get("traceId", ""),
            },
        )

        trace.end_span()
        return result

    async def edit(
        self,
        run_id: str,
        edit_prompt: str,
        trace: TraceContext,
        *,
        project: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> MaestroResult:
        """Send an edit prompt through Maestro (EDITING mode)."""
        return await self.compose(
            run_id=run_id,
            prompt_text=edit_prompt,
            trace=trace,
            project=project,
            mode="edit",
            conversation_id=conversation_id,
        )


class MaestroResult:
    """Structured result from a Maestro stream."""

    def __init__(
        self,
        events: list[ParsedSSEEvent],
        tool_calls: list[dict],
        state: dict,
        complete: dict,
        generators: list[dict],
        payload_hash: str,
        conversation_id: str,
        duration_ms: float,
    ) -> None:
        self.events = events
        self.tool_calls = tool_calls
        self.state = state
        self.complete = complete
        self.generators = generators
        self.payload_hash = payload_hash
        self.conversation_id = conversation_id
        self.duration_ms = duration_ms

    @property
    def success(self) -> bool:
        return self.complete.get("success", False)

    @property
    def trace_id(self) -> str:
        return self.complete.get("traceId", self.state.get("traceId", ""))

    @property
    def intent(self) -> str:
        return self.state.get("intent", "")

    @property
    def sse_state(self) -> str:
        return self.state.get("state", "")

    @property
    def execution_mode(self) -> str:
        return self.state.get("executionMode", "")

    @property
    def variation_id(self) -> str:
        return self.complete.get("variationId", "")

    def to_summary(self) -> dict:
        return {
            "success": self.success,
            "trace_id": self.trace_id,
            "intent": self.intent,
            "sse_state": self.sse_state,
            "execution_mode": self.execution_mode,
            "event_count": len(self.events),
            "tool_call_count": len(self.tool_calls),
            "generator_count": len(self.generators),
            "duration_ms": self.duration_ms,
            "conversation_id": self.conversation_id,
        }


class MaestroError(Exception):
    pass
