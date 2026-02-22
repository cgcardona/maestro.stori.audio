"""Section-level signaling for drum-to-bass pipelining.

Enables section-level parallelism: drum section N signals completion
so bass section N can start immediately, rather than waiting for ALL
drum sections to finish.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


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
