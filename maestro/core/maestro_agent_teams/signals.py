"""Shared cross-instrument coordination for section-level parallelism.

Two complementary systems:

- **SectionSignals** — asyncio.Event per section for drum-to-bass
  pipelining (readiness gating).  Keyed by ``"{section_id}:{contract_hash}"``
  to bind signals to specific contract lineage (swarm safety).
- **SectionState** — immutable telemetry snapshots per section per
  instrument for deterministic cross-instrument musical awareness
  (no LLM cost).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from maestro.contracts.json_types import NoteDict
from maestro.core.telemetry import SectionTelemetry

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
    drum_notes: list[NoteDict] | None = None
    contract_hash: str = ""


def _signal_key(section_id: str, contract_hash: str) -> str:
    """Key format: ``"section_id:contract_hash"``."""
    return f"{section_id}:{contract_hash}"


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

    All keys are ``"{section_id}:{contract_hash}"`` — lineage-bound
    to prevent cross-composition signal leaks in swarm execution.

    ``signal_complete`` is idempotent: calling it twice for the same
    key is a no-op (first write wins).
    """

    events: dict[str, asyncio.Event] = field(default_factory=dict)
    _results: dict[str, SectionSignalResult] = field(default_factory=dict)

    @classmethod
    def from_section_ids(
        cls,
        section_ids: list[str],
        contract_hashes: list[str],
    ) -> SectionSignals:
        """Build signals for a list of section IDs with their contract hashes."""
        if len(section_ids) != len(contract_hashes):
            raise ValueError(
                f"section_ids ({len(section_ids)}) and contract_hashes "
                f"({len(contract_hashes)}) must have equal length"
            )
        events: dict[str, asyncio.Event] = {}
        for sid, ch in zip(section_ids, contract_hashes):
            events[_signal_key(sid, ch)] = asyncio.Event()
        return cls(events=events)

    def signal_complete(
        self,
        section_id: str,
        *,
        contract_hash: str,
        success: bool = True,
        drum_notes: list[NoteDict] | None = None,
    ) -> None:
        """Signal that a drum section has completed (success or failure).

        Idempotent: subsequent calls for the same key are silently
        ignored (first write wins).  ``drum_notes`` is stored before
        the event is set to guarantee store-before-signal ordering.
        """
        key = _signal_key(section_id, contract_hash)
        if key in self._results:
            return
        self._results[key] = SectionSignalResult(
            success=success,
            drum_notes=drum_notes,
            contract_hash=contract_hash,
        )
        evt = self.events.get(key)
        if evt:
            evt.set()

    async def wait_for(
        self,
        section_id: str,
        *,
        contract_hash: str,
        timeout: float = 240.0,
    ) -> SectionSignalResult | None:
        """Wait for a drum section to complete with typed result.

        Verifies the signal's ``contract_hash`` matches.  Raises
        ``ProtocolViolationError`` on mismatch (swarm safety).

        Returns ``None`` if the key has no registered event (e.g.
        composition has no drums).  Raises ``asyncio.TimeoutError``
        if the drum section does not signal within ``timeout`` seconds.
        """
        key = _signal_key(section_id, contract_hash)
        evt = self.events.get(key)
        if not evt:
            return None
        await asyncio.wait_for(evt.wait(), timeout=timeout)
        result = self._results.get(key)
        if result and result.contract_hash != contract_hash:
            from maestro.core.maestro_agent_teams.contracts import (
                ProtocolViolationError,
            )

            raise ProtocolViolationError(
                f"Signal lineage mismatch for section '{section_id}': "
                f"expected contract_hash={contract_hash}, "
                f"got={result.contract_hash}"
            )
        return result


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
        """Write ``telemetry`` for ``key`` (acquires lock — safe for concurrent callers).

        ``key`` must follow ``"Instrument: section_id"`` format (e.g.
        ``"Drums: 0:verse"``).  Written values are frozen ``SectionTelemetry``
        dataclasses — subsequent reads return the same object.
        """
        async with self._lock:
            self._data[key] = telemetry
        logger.debug(
            f"[SectionState] Updated: {key} "
            f"energy={telemetry.energy_level:.2f} "
            f"density={telemetry.density_score:.2f}"
        )

    async def get(self, key: str) -> SectionTelemetry | None:
        """Return the ``SectionTelemetry`` for ``key``, or ``None`` if not yet written."""
        async with self._lock:
            return self._data.get(key)

    async def snapshot(self) -> dict[str, SectionTelemetry]:
        """Return a shallow copy (locked to prevent races during execution)."""
        async with self._lock:
            return dict(self._data)
