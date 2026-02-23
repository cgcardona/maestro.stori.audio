"""Agent contracts for the three-level architecture.

Contracts are frozen dataclasses that define immutable handoffs between
agent layers.  They replace loose dicts and natural-language prompt
passthrough, preventing "semantic telephone" where each layer
reinterprets the task instead of executing a protocol.

Contract hierarchy:
  L1 → L2:  ``InstrumentContract``  (coordinator builds, agent executes)
  L2 → L3:  ``SectionContract``     (dispatch builds, section child executes)

  ``RuntimeContext``  travels alongside contracts for dynamic state
  (section signals, emotion vector, raw prompt) that is NOT structural.

Design rules:
  - Structural fields (beat ranges, section names, roles) are IMMUTABLE.
    ``frozen=True`` enforces this at runtime.
  - Child agents may only reason about HOW to execute (e.g. refining an
    Orpheus prompt), never WHAT to do (e.g. which section, which beat range).
  - Advisory fields (``l2_generate_prompt``) are explicitly marked and may
    be overridden by canonical descriptions baked into the contract.
  - No free-form reasoning transfer between layers — only typed fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.maestro_agent_teams.signals import SectionSignals, SectionState


# ═══════════════════════════════════════════════════════════════════════════════
# Section-level contracts (L2 → L3)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SectionSpec:
    """One section's layout in the composition plan.

    Built by the coordinator (L1) from ``parse_sections`` output and
    canonical templates.  Immutable — L2 and L3 execute against these
    values, they never recompute or reinterpret them.
    """

    name: str
    index: int
    start_beat: int
    duration_beats: int
    bars: int
    character: str
    """Canonical overall description (from ``_section_overall_description``)."""
    role_brief: str
    """Canonical per-role description (from ``_get_section_role_description``)."""


@dataclass(frozen=True)
class SectionContract:
    """Immutable contract from L2 instrument parent to L3 section child.

    L3 MUST use structural fields exactly as provided.  L3 may only
    reason about the Orpheus generation prompt (HOW to describe the music),
    never about WHAT section it is, WHERE to place regions, or WHAT role
    it plays.

    ``l2_generate_prompt`` is advisory — the section child should prefer
    ``section.character`` and ``section.role_brief`` when they conflict
    with the L2's suggestion.
    """

    # ── Immutable structural fields ──
    section: SectionSpec
    track_id: str
    instrument_name: str
    role: str
    style: str
    tempo: float
    key: str
    region_name: str

    # ── Advisory (L3 uses for Orpheus prompt, may override) ──
    l2_generate_prompt: str = ""

    # ── Derived properties (computed, not reinterpretable) ──

    @property
    def is_drum(self) -> bool:
        return self.role.lower() in ("drums", "drum")

    @property
    def is_bass(self) -> bool:
        return self.role.lower() == "bass"

    @property
    def start_beat(self) -> int:
        return self.section.start_beat

    @property
    def duration_beats(self) -> int:
        return self.section.duration_beats

    @property
    def section_name(self) -> str:
        return self.section.name

    @property
    def section_index(self) -> int:
        return self.section.index

    @property
    def bars(self) -> int:
        return self.section.bars


# ═══════════════════════════════════════════════════════════════════════════════
# Instrument-level contract (L1 → L2)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class InstrumentContract:
    """Immutable contract from L1 coordinator to L2 instrument parent.

    The coordinator builds one per instrument.  L2 MUST use these values
    for track creation, section dispatching, and system prompt construction.
    L2 may only reason about musical character and generate prompts —
    it must not reinterpret structural fields.
    """

    instrument_name: str
    role: str
    style: str
    bars: int
    tempo: float
    key: str
    start_beat: int
    sections: tuple[SectionSpec, ...]
    existing_track_id: Optional[str]
    assigned_color: Optional[str]
    gm_guidance: str

    @property
    def is_drum(self) -> bool:
        return self.role.lower() in ("drums", "drum")

    @property
    def is_bass(self) -> bool:
        return self.role.lower() == "bass"

    @property
    def multi_section(self) -> bool:
        return len(self.sections) > 1

    @property
    def reusing_track(self) -> bool:
        return self.existing_track_id is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Runtime context (travels alongside contracts, not structural)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RuntimeContext:
    """Frozen runtime context that travels alongside contracts.

    Contains dynamic state needed during execution but NOT structural
    decisions.  Frozen to prevent accidental mutation — when bass needs
    to add ``drum_telemetry``, it creates a NEW RuntimeContext via
    ``with_drum_telemetry()``.
    """

    raw_prompt: str = ""
    emotion_vector: Any = None
    quality_preset: str = "quality"
    section_signals: Optional[SectionSignals] = None
    section_state: Optional[SectionState] = None
    drum_telemetry: Optional[dict[str, Any]] = field(default=None)

    def with_drum_telemetry(self, telemetry: dict[str, Any]) -> RuntimeContext:
        """Return a new RuntimeContext with drum telemetry injected."""
        return RuntimeContext(
            raw_prompt=self.raw_prompt,
            emotion_vector=self.emotion_vector,
            quality_preset=self.quality_preset,
            section_signals=self.section_signals,
            section_state=self.section_state,
            drum_telemetry=telemetry,
        )

    def to_composition_context(self) -> dict[str, Any]:
        """Bridge to legacy code that expects dict[str, Any].

        Used at boundaries where downstream code (e.g. ``_apply_single_tool_call``)
        still reads ``composition_context.get("emotion_vector")``.  This will
        be removed once all downstream consumers are typed.
        """
        ctx: dict[str, Any] = {
            "_raw_prompt": self.raw_prompt,
            "quality_preset": self.quality_preset,
        }
        if self.emotion_vector is not None:
            ctx["emotion_vector"] = self.emotion_vector
        if self.section_signals is not None:
            ctx["section_signals"] = self.section_signals
        if self.section_state is not None:
            ctx["section_state"] = self.section_state
        if self.drum_telemetry is not None:
            ctx["drum_telemetry"] = self.drum_telemetry
        return ctx
