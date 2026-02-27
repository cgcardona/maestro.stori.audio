"""MetricsCollector — timers, counters, histograms persisted to metrics.jsonl."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator


@dataclass
class TimingMetric:
    name: str
    run_id: str
    start_ms: float
    end_ms: float = 0.0
    duration_ms: float = 0.0
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "metric_type": "timing",
            "name": self.name,
            "run_id": self.run_id,
            "duration_ms": self.duration_ms,
            "tags": self.tags,
        }


class MetricsCollector:
    """Collects and persists performance metrics."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._metrics_file = self._output_dir / "metrics.jsonl"
        self._lock = asyncio.Lock()
        self._counters: dict[str, int] = {}
        self._timings: list[TimingMetric] = []

    @asynccontextmanager
    async def timer(
        self,
        name: str,
        run_id: str,
        tags: dict[str, str] | None = None,
    ) -> AsyncIterator[TimingMetric]:
        """Context manager that measures and records elapsed time."""
        metric = TimingMetric(
            name=name,
            run_id=run_id,
            start_ms=time.monotonic() * 1000,
            tags=tags or {},
        )
        try:
            yield metric
        finally:
            metric.end_ms = time.monotonic() * 1000
            metric.duration_ms = metric.end_ms - metric.start_ms
            self._timings.append(metric)
            await self._persist(metric.to_dict())

    async def counter(self, name: str, run_id: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        """Increment a named counter."""
        key = f"{name}:{run_id}"
        self._counters[key] = self._counters.get(key, 0) + value
        await self._persist({
            "ts": datetime.now(timezone.utc).isoformat(),
            "metric_type": "counter",
            "name": name,
            "run_id": run_id,
            "value": self._counters[key],
            "tags": tags or {},
        })

    async def gauge(self, name: str, run_id: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a point-in-time gauge value."""
        await self._persist({
            "ts": datetime.now(timezone.utc).isoformat(),
            "metric_type": "gauge",
            "name": name,
            "run_id": run_id,
            "value": value,
            "tags": tags or {},
        })

    async def histogram(self, name: str, run_id: str, values: list[float], tags: dict[str, str] | None = None) -> None:
        """Record a distribution of values."""
        if not values:
            return
        sorted_v = sorted(values)
        n = len(sorted_v)
        await self._persist({
            "ts": datetime.now(timezone.utc).isoformat(),
            "metric_type": "histogram",
            "name": name,
            "run_id": run_id,
            "count": n,
            "min": sorted_v[0],
            "max": sorted_v[-1],
            "mean": sum(sorted_v) / n,
            "p50": sorted_v[n // 2],
            "p95": sorted_v[int(n * 0.95)] if n > 1 else sorted_v[0],
            "p99": sorted_v[int(n * 0.99)] if n > 1 else sorted_v[0],
            "tags": tags or {},
        })

    async def _persist(self, data: dict[str, Any]) -> None:
        async with self._lock:
            with open(self._metrics_file, "a") as f:
                f.write(json.dumps(data, default=str) + "\n")

    def get_timings(self, name: str | None = None) -> list[TimingMetric]:
        if name is None:
            return list(self._timings)
        return [t for t in self._timings if t.name == name]
