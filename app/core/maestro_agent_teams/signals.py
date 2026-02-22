"""Shared cross-instrument coordination for section-level parallelism.

Two complementary systems:

- **SectionSignals** — asyncio.Event per section for drum-to-bass
  pipelining (readiness gating).
- **SectionState** — immutable telemetry snapshots per section per
  instrument for deterministic cross-instrument musical awareness
  (no LLM cost).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.telemetry import SectionTelemetry

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# SectionSignals — readiness gating
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SectionSignals:
    """Per-section event signaling for drum-to-bass RhythmSpine coupling.

    The coordinator creates one ``SectionSignals`` instance shared across
    drum and bass instrument parents.  Each drum section child calls
    ``signal_complete`` after generating, storing its notes and setting
    the corresponding asyncio.Event.  The matching bass section child
    calls ``wait_for`` before generating, receiving the drum notes so it
    can build a per-section RhythmSpine.
    """

    events: dict[str, asyncio.Event] = field(default_factory=dict)
    drum_data: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_sections(cls, sections: list[dict[str, Any]]) -> SectionSignals:
        return cls(
            events={s["name"]: asyncio.Event() for s in sections},
        )

    def signal_complete(
        self, section_name: str, drum_notes: list[dict] | None = None
    ) -> None:
        if drum_notes is not None:
            self.drum_data[section_name] = {"drum_notes": drum_notes}
        evt = self.events.get(section_name)
        if evt:
            evt.set()

    async def wait_for(self, section_name: str) -> dict[str, Any] | None:
        evt = self.events.get(section_name)
        if evt:
            await evt.wait()
            return self.drum_data.get(section_name)
        return None


# ─────────────────────────────────────────────────────────────────────
# SectionState — musical telemetry
# ─────────────────────────────────────────────────────────────────────


def _state_key(instrument: str, section_name: str) -> str:
    """Canonical key format: ``"Drums: Verse"``."""
    return f"{instrument}: {section_name.title()}"


@dataclass
class SectionState:
    """Thread-safe, write-once telemetry store for cross-instrument awareness.

    Keys follow ``"Instrument: Section"`` format (e.g. ``"Drums: Verse"``).
    Values are frozen ``SectionTelemetry`` dataclasses — immutable after write.

    All access goes through ``set`` / ``get`` which acquire an asyncio.Lock,
    making concurrent writes from parallel section children safe.
    """

    _data: dict[str, SectionTelemetry] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def set(self, key: str, telemetry: SectionTelemetry) -> None:
        async with self._lock:
            self._data[key] = telemetry
        logger.debug(
            f"[SectionState] Updated: {key} "
            f"energy={telemetry.energy_level:.2f} "
            f"density={telemetry.density_score:.2f}"
        )

    async def get(self, key: str) -> Optional[SectionTelemetry]:
        async with self._lock:
            return self._data.get(key)

    def snapshot(self) -> dict[str, SectionTelemetry]:
        """Return a shallow copy for diagnostic logging (not locked — use after composition)."""
        return dict(self._data)
