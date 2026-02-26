"""Agent contracts for the three-level architecture.

Contracts are frozen dataclasses that define immutable handoffs between
agent layers.  They replace loose dicts and natural-language prompt
passthrough, preventing "semantic telephone" where each layer
reinterprets the task instead of executing a protocol.

Contract hierarchy:
  L1 → L2:  ``InstrumentContract``  (coordinator builds, agent executes)
  L2 → L3:  ``SectionContract``     (dispatch builds, section child executes)

  ``RuntimeContext``  carries pure data (prompt, emotion, quality preset).
  ``ExecutionServices`` carries mutable coordination primitives (signals,
  state) and is passed alongside contracts — never inside them.

Design rules:
  - Structural fields (beat ranges, section names, roles) are IMMUTABLE.
    ``frozen=True`` enforces this at runtime.
  - Child agents may only reason about HOW to execute (e.g. refining an
    Orpheus prompt), never WHAT to do (e.g. which section, which beat range).
  - Advisory fields (``l2_generate_prompt``) are explicitly marked and may
    be overridden by canonical descriptions baked into the contract.
  - No free-form reasoning transfer between layers — only typed fields.
  - Mutable coordination primitives (SectionSignals, SectionState) live
    in ``ExecutionServices``, never in frozen contracts or RuntimeContext.
"""

from __future__ import annotations


from dataclasses import dataclass, field


from app.contracts.generation_types import CompositionContext
from app.contracts.json_types import JSONValue
from app.core.maestro_agent_teams.signals import SectionSignals, SectionState


# ═══════════════════════════════════════════════════════════════════════════════
# Composition-level contract (global lineage anchor)
# ═══════════════════════════════════════════════════════════════════════════════


class ProtocolViolationError(Exception):
    """Raised when cryptographic lineage or contract integrity is violated."""


@dataclass(frozen=True)
class CompositionContract:
    """Root anchor for the entire composition lineage chain.

    Built by the coordinator (L1) after all SectionSpecs are sealed.
    The canonical dict includes ``sections`` as a tuple of their
    contract hashes (not full objects), ensuring the root hash
    captures the structural identity of every section.

    Lineage chain::

        CompositionContract → InstrumentContract → SectionContract → Execution
    """

    composition_id: str
    sections: tuple["SectionSpec", ...]
    style: str
    tempo: int
    key: str

    contract_version: int = 2
    contract_hash: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Section-level contracts (L2 → L3)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SectionSpec:
    """One section's layout in the composition plan.

    Built by the coordinator (L1) from ``parse_sections`` output and
    canonical templates.  Immutable — L2 and L3 execute against these
    values, they never recompute or reinterpret them.

    ``section_id`` is the stable unique key used for signal/state
    coordination.  It prevents collisions when a composition has
    repeated section names (e.g. two "verse" sections).

    ``contract_hash`` is set by the coordinator after construction via
    ``seal_contract()`` — it captures the structural identity of this
    spec for lineage verification downstream.
    """

    section_id: str
    """Stable unique key, e.g. ``"0:intro"``. Used for signal/state keying."""
    name: str
    index: int
    start_beat: int
    duration_beats: int
    bars: int
    character: str
    """Canonical overall description (from ``_section_overall_description``)."""
    role_brief: str
    """Canonical per-role description (from ``_get_section_role_description``)."""

    # ── Contract identity (set post-construction via seal_contract) ──
    contract_version: int = 1
    contract_hash: str = ""


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

    ``contract_hash`` captures the structural identity; ``parent_contract_hash``
    links back to the InstrumentContract that spawned this SectionContract,
    enabling lineage verification without trusting agents.
    """

    # ── Immutable structural fields ──
    section: SectionSpec
    track_id: str
    instrument_name: str
    role: str
    style: str
    tempo: int
    key: str
    region_name: str

    # ── Advisory (L3 uses for Orpheus prompt, may override) ──
    l2_generate_prompt: str = ""

    # ── Contract identity (set post-construction via seal_contract) ──
    contract_version: int = 1
    contract_hash: str = ""
    parent_contract_hash: str = ""

    # ── Derived properties (computed, not reinterpretable) ──

    @property
    def is_drum(self) -> bool:
        """``True`` when the instrument role is a drums/drum kit part."""
        return self.role.lower() in ("drums", "drum")

    @property
    def is_bass(self) -> bool:
        """``True`` when the instrument role is bass (affects MIDI channel assignment)."""
        return self.role.lower() == "bass"

    @property
    def start_beat(self) -> int:
        """Absolute beat number where this section begins in the arrangement."""
        return self.section.start_beat

    @property
    def duration_beats(self) -> int:
        """Total length of this section in beats."""
        return self.section.duration_beats

    @property
    def section_name(self) -> str:
        """Human-readable section name (e.g. ``"verse"``, ``"chorus"``)."""
        return self.section.name

    @property
    def section_index(self) -> int:
        """Zero-based index of this section in the composition's section list."""
        return self.section.index

    @property
    def bars(self) -> int:
        """Length of this section in bars."""
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

    ``contract_hash`` captures structural identity; ``parent_contract_hash``
    is the joined hash of all constituent SectionSpec hashes, linking this
    contract to the L1-built section layout.
    """

    instrument_name: str
    role: str
    style: str
    bars: int
    tempo: int
    key: str
    start_beat: int
    sections: tuple[SectionSpec, ...]
    existing_track_id: str | None
    assigned_color: str | None
    gm_guidance: str

    # ── Contract identity (set post-construction via seal_contract) ──
    contract_version: int = 1
    contract_hash: str = ""
    parent_contract_hash: str = ""

    @property
    def is_drum(self) -> bool:
        """``True`` when the instrument role is a drums/drum kit part."""
        return self.role.lower() in ("drums", "drum")

    @property
    def is_bass(self) -> bool:
        """``True`` when the instrument role is bass."""
        return self.role.lower() == "bass"

    @property
    def multi_section(self) -> bool:
        """``True`` when this instrument spans more than one section.

        Multi-section instruments dispatch parallel ``SectionContract``s so
        children can generate each section concurrently.
        """
        return len(self.sections) > 1

    @property
    def reusing_track(self) -> bool:
        """``True`` when this instrument already has a DAW track to write into.

        When ``True`` the executor skips ``stori_add_midi_track`` and writes
        regions directly into the existing track identified by
        ``existing_track_id``.
        """
        return self.existing_track_id is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Execution services (mutable coordination — NOT frozen)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ExecutionServices:
    """Mutable coordination primitives passed alongside contracts.

    Explicitly NOT frozen.  These are live asyncio-backed objects that
    must be shared by reference across concurrent agent tasks.  They
    are separated from ``RuntimeContext`` so that frozen data contracts
    never wrap mutable synchronization state.
    """

    section_signals: SectionSignals | None = None
    section_state: SectionState | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# Runtime context (pure data, travels alongside contracts)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RuntimeContext:
    """Frozen runtime context carrying pure data alongside contracts.

    Contains prompt text, emotion conditioning, and quality preset —
    all immutable.  Mutable coordination primitives (signals, state)
    live in ``ExecutionServices``, never here.

    ``emotion_vector`` is stored as a frozen tuple-of-pairs so no
    mutable dict references leak through the immutability boundary.
    ``drum_telemetry`` follows the same pattern.
    """

    raw_prompt: str = ""
    emotion_vector: tuple[tuple[str, float], ...] | None = None
    quality_preset: str = "quality"
    drum_telemetry: tuple[tuple[str, JSONValue], ...] | None = None

    @staticmethod
    def freeze_emotion_vector(ev: object) -> tuple[tuple[str, float], ...]:
        """Convert an EmotionVector or dict to a frozen tuple-of-pairs."""
        if hasattr(ev, "to_dict"):
            d = ev.to_dict()
        elif isinstance(ev, dict):
            d = ev
        else:
            raise TypeError(
                f"Cannot freeze emotion vector of type {type(ev).__name__}"
            )
        return tuple(sorted((k, float(v)) for k, v in d.items()))

    def with_emotion_vector(self, ev: object) -> RuntimeContext:
        """Return a new RuntimeContext with a frozen emotion vector."""
        frozen = RuntimeContext.freeze_emotion_vector(ev)
        return RuntimeContext(
            raw_prompt=self.raw_prompt,
            emotion_vector=frozen,
            quality_preset=self.quality_preset,
            drum_telemetry=self.drum_telemetry,
        )

    def with_drum_telemetry(self, telemetry: dict[str, JSONValue]) -> RuntimeContext:
        """Return a new RuntimeContext with an immutable telemetry snapshot."""
        return RuntimeContext(
            raw_prompt=self.raw_prompt,
            emotion_vector=self.emotion_vector,
            quality_preset=self.quality_preset,
            drum_telemetry=tuple(telemetry.items()),
        )

    def to_composition_context(self) -> CompositionContext:
        """Build a CompositionContext from this RuntimeContext.

        Exposes only the fields that generation tool calls need —
        does NOT include mutable services (signals, state).

        Reconstructs ``EmotionVector`` from the frozen tuple so downstream
        consumers (Orpheus backends) can access ``.energy``, ``.valence``,
        etc. as attributes. Passes ``drum_telemetry`` as a plain dict so
        bass/chord sections can read drum energy and groove data.
        """
        ctx = CompositionContext(quality_preset=self.quality_preset)
        if self.emotion_vector is not None:
            from app.core.emotion_vector import EmotionVector

            ctx["emotion_vector"] = EmotionVector(**dict(self.emotion_vector))
        if self.drum_telemetry is not None:
            ctx["drum_telemetry"] = dict(self.drum_telemetry)
        return ctx
