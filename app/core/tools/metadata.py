"""Tool metadata models and enums.

``ToolTier`` and ``ToolKind`` classify every DAW tool for routing,
planner gating, and allowlist construction.  ``ToolMeta`` is the single
authoritative record per tool — populated once in ``app/daw/stori/tool_registry.py``
and queried everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ToolTier(str, Enum):
    """Execution tier for a tool — determines where it runs.

    ``TIER1`` tools execute server-side inside Maestro (e.g. generation).
    ``TIER2`` tools are forwarded to the connected DAW for client-side
    execution and always appear in SSE ``toolCall`` events.
    """

    TIER1 = "tier1"  # server-side (Maestro / Orpheus)
    TIER2 = "tier2"  # client-side (Stori DAW via WebSocket / SSE)


class ToolKind(str, Enum):
    """Semantic category of a tool — used by the planner for step classification.

    ``PRIMITIVE`` — atomic DAW operation (add track, add notes, set tempo …).
    ``GENERATOR`` — calls Orpheus to generate MIDI; always ``TIER1`` and ``planner_only``.
    ``MACRO``     — expands into a list of primitives at plan time; never sent to the LLM.
    """

    PRIMITIVE = "primitive"
    GENERATOR = "generator"
    MACRO = "macro"


@dataclass(frozen=True)
class ToolMeta:
    """Immutable metadata record for one registered DAW tool.

    One instance is created per tool in ``build_tool_registry()`` and stored
    in the module-level ``_TOOL_META`` dict.  All downstream code reads from
    there — never constructs ``ToolMeta`` directly.

    Attributes:
        name: Canonical tool name (e.g. ``"stori_add_midi_track"``).
        tier: Whether the tool runs server-side (TIER1) or client-side (TIER2).
        kind: Semantic category — PRIMITIVE, GENERATOR, or MACRO.
        creates_entity: Entity type string when this tool registers a new DAW
            entity (``"track"``, ``"region"``, or ``"bus"``); ``None`` otherwise.
            The executor uses this to wire up ``EntityRegistry.register_*`` calls.
        id_fields: Names of output fields that contain server-generated entity UUIDs
            (e.g. ``("trackId",)``).  Used by the executor to resolve forward
            references like ``$0.trackId`` in downstream tool params.
        reversible: ``False`` for destructive operations where undo is impractical
            (e.g. ``stori_create_project``).  Advisory only today.
        planner_only: When ``True``, the tool is never exposed directly to the LLM
            and does not appear in the tool-allowlist sent to the model.  Used for
            ``GENERATOR`` tools that the planner emits on the model's behalf.
        deprecated: Signals that this tool has a preferred replacement and should
            not be emitted in new plans.
    """

    name: str
    tier: ToolTier
    kind: ToolKind
    creates_entity: str | None = None
    id_fields: tuple[str, ...] = ()
    reversible: bool = True
    planner_only: bool = False
    deprecated: bool = False
