"""Structured prompt base types.

``StructuredPrompt`` is the generic base for any structured prompt dialect
recognised by Maestro.  ``MaestroPrompt`` (in ``app.prompts.maestro``) is
the canonical — and currently only — subclass.

Data-class types shared across prompt parsing and downstream routing
(``TargetSpec``, ``PositionSpec``, ``VibeWeight``) also live here so that
consumers import from one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from maestro.contracts.json_types import JSONValue

# ─── Named collection aliases ─────────────────────────────────────────────────

MaestroDimensions = dict[str, JSONValue]
"""Open-vocabulary Maestro dimension block (Harmony, Melody, Rhythm, …).

Unknown top-level YAML keys in a MAESTRO PROMPT land here and are injected
verbatim into the LLM system prompt.  The vocabulary is open — invent new
dimensions and they work immediately.
"""

PromptConstraints = dict[str, JSONValue]
"""Generation constraint block parsed from the ``Constraints:`` YAML key.

Keys are lowercased at parse time.  Values are scalars or simple structures
that downstream generation code interprets (e.g. ``bars``, ``density``,
``no_effects``).
"""


@dataclass
class TargetSpec:
    """Scope anchor for a structured prompt editing operation.

    Identifies which DAW entity the operation targets.  When ``kind`` is
    ``"track"`` or ``"region"``, ``name`` holds the human-readable label
    (e.g. ``"Drums"``); the server resolves it to a UUID via EntityRegistry.
    """

    kind: Literal["project", "selection", "track", "region"]
    name: str | None = None


@dataclass
class PositionSpec:
    """Arrangement placement.

    kind        description
    ──────────  ────────────────────────────────────────────────────────────
    after       sequential — start after ref section ends
    before      insert / pickup — start before ref section begins
    alongside   parallel layer — same start beat as ref
    between     transition bridge — fills gap between ref and ref2
    within      nested — relative offset inside ref
    absolute    explicit beat number
    last        after all existing content in the project
    """
    kind: Literal["after", "before", "alongside", "between", "within", "absolute", "last"]
    ref: str | None = None
    ref2: str | None = None
    offset: float = 0.0
    beat: float | None = None


AfterSpec = PositionSpec


@dataclass
class VibeWeight:
    """A single vibe keyword with an optional repetition weight.

    Parsed from the ``Vibe:`` block, e.g. ``dusty x3`` → ``VibeWeight("dusty", 3)``.
    Higher weights bias the EmotionVector derivation toward that mood axis
    (the weight is applied as a multiplier when blending vibe contributions).
    """

    vibe: str
    weight: int = 1


@dataclass
class StructuredPrompt:
    """Base class for all structured prompt dialects.

    Routing fields are typed attributes.  All other top-level YAML keys land
    in ``extensions`` and are injected verbatim into the Maestro LLM system
    prompt.

    Subclasses (e.g. ``MaestroPrompt``) narrow ``prompt_kind`` to a literal
    and may add dialect-specific invariants.
    """

    raw: str
    mode: Literal["compose", "edit", "ask"]
    request: str
    prompt_kind: str = "maestro"
    version: int = 1
    section: str | None = None
    position: PositionSpec | None = None
    target: TargetSpec | None = None
    style: str | None = None
    key: str | None = None
    tempo: int | None = None
    energy: str | None = None
    roles: list[str] = field(default_factory=list)
    constraints: PromptConstraints = field(default_factory=dict)
    vibes: list[VibeWeight] = field(default_factory=list)
    extensions: MaestroDimensions = field(default_factory=dict)

    @property
    def after(self) -> PositionSpec | None:
        """Backwards-compatible alias for position."""
        return self.position

    @property
    def has_maestro_fields(self) -> bool:
        """True when the prompt includes unrecognised top-level YAML keys.

        Unknown keys land in ``extensions`` and are injected verbatim into the
        Maestro LLM system prompt, giving power users a direct channel to add
        extra instructions without modifying the parser.
        """
        return bool(self.extensions)
