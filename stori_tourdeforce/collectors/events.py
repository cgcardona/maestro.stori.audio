"""EventCollector — append-only JSONL event stream with thread-safe writes."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from stori_tourdeforce.models import (
    Component,
    Event,
    EventType,
    Severity,
    TraceContext,
)

logger = logging.getLogger(__name__)


class EventCollector:
    """Collects structured events to events.jsonl and optionally runs.jsonl."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._events_file = self._output_dir / "events.jsonl"
        self._runs_file = self._output_dir / "runs.jsonl"
        self._lock = asyncio.Lock()
        self._count = 0

    async def emit(
        self,
        *,
        run_id: str,
        scenario: str,
        component: Component | str,
        event_type: EventType | str,
        trace: TraceContext,
        severity: Severity | str = Severity.INFO,
        tags: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        """Create and persist a structured event."""
        event = Event(
            ts=Event.now(),
            run_id=run_id,
            scenario=scenario,
            component=str(component.value if isinstance(component, Component) else component),
            event_type=str(event_type.value if isinstance(event_type, EventType) else event_type),
            trace_id=trace.trace_id,
            span_id=trace.current_span,
            parent_span_id=trace.parent_span,
            severity=str(severity.value if isinstance(severity, Severity) else severity),
            tags=tags or {},
            data=data or {},
        )

        async with self._lock:
            with open(self._events_file, "a") as f:
                f.write(event.to_json() + "\n")
            self._count += 1

        return event

    async def emit_run(self, run_data: dict[str, Any]) -> None:
        """Persist a run summary to runs.jsonl."""
        async with self._lock:
            with open(self._runs_file, "a") as f:
                f.write(json.dumps(run_data, default=str) + "\n")

    @property
    def count(self) -> int:
        return self._count
