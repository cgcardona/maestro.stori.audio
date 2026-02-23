"""Shared cross-instrument coordination for section-level parallelism.

Two complementary systems:

- **SectionSignals** — asyncio.Event per section for drum-to-bass
  pipelining (readiness gating).  Keyed by ``section_id`` (not section
  name) to prevent collisions when a composition has repeated section
  names (e.g. two "verse" sections).
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
# SectionSignalResult — typed outcome for wait_for
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SectionSignalResult:
    """Typed outcome returned by ``SectionSignals.wait_for``.

    Allows callers to distinguish success (drum notes available) from
    failure (drums failed, bass should proceed without spine).
    """

    success: bool
    drum_notes: list[dict[str, Any]] | None = None


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

    All keys are ``section_id`` (e.g. ``"0:intro"``) — NOT section name.
    This prevents collisions when a composition reuses section names.

    ``signal_complete`` is idempotent: calling it twice for the same
    ``section_id`` is a no-op (first write wins).
    """

    events: dict[str, asyncio.Event] = field(default_factory=dict)
    _results: dict[str, SectionSignalResult] = field(default_factory=dict)

    @classmethod
    def from_section_ids(cls, section_ids: list[str]) -> SectionSignals:
        """Build signals for a list of section IDs."""
        return cls(
            events={sid: asyncio.Event() for sid in section_ids},
        )

    def signal_complete(
        self,
        section_id: str,
        *,
        success: bool = True,
        drum_notes: list[dict[str, Any]] | None = None,
    ) -> None:
        """Signal that a drum section has completed (success or failure).

        Idempotent: subsequent calls for the same ``section_id`` are
        silently ignored (first write wins).  ``drum_notes`` is stored
        before the event is set to guarantee store-before-signal ordering.
        """
        if section_id in self._results:
            return
        self._results[section_id] = SectionSignalResult(
            success=success,
            drum_notes=drum_notes,
        )
        evt = self.events.get(section_id)
        if evt:
            evt.set()

    async def wait_for(
        self,
        section_id: str,
        *,
        timeout: float = 240.0,
    ) -> SectionSignalResult | None:
        """Wait for a drum section to complete with typed result.

        Returns ``None`` if ``section_id`` has no registered event
        (e.g. composition has no drums).  Raises ``asyncio.TimeoutError``
        if the drum section does not signal within ``timeout`` seconds.
        """
        evt = self.events.get(section_id)
        if not evt:
            return None
        await asyncio.wait_for(evt.wait(), timeout=timeout)
        return self._results.get(section_id)


# ─────────────────────────────────────────────────────────────────────
# SectionState — musical telemetry
# ─────────────────────────────────────────────────────────────────────


def _state_key(instrument: str, section_id: str) -> str:
    """Canonical key format: ``"Drums: 0:verse"``."""
    return f"{instrument}: {section_id}"


@dataclass
class SectionState:
    """Thread-safe, write-once telemetry store for cross-instrument awareness.

    Keys follow ``"Instrument: section_id"`` format (e.g. ``"Drums: 0:verse"``).
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

    async def snapshot(self) -> dict[str, SectionTelemetry]:
        """Return a shallow copy (locked to prevent races during execution)."""
        async with self._lock:
            return dict(self._data)
