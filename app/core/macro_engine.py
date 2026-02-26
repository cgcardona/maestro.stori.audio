"""
Macro engine (Cursor-of-DAWs)

Macros are NOT direct tools. They are *recipes* expanded into PRIMITIVE ToolCalls
based on context + slots.

This file intentionally keeps macros small and composable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from typing_extensions import TypedDict

from app.core.expansion import ToolCall


class MacroContext(TypedDict, total=False):
    """Invocation context passed to a macro expand function.

    All fields are optional — macros must guard against missing keys with .get().
    ``trackId`` is the primary targeting slot; additional keys may be added
    as new macros require them.
    """

    trackId: str


@dataclass(frozen=True)
class Macro:
    """A named recipe that expands into a list of primitive ``ToolCall``s.

    Macros are never sent to the LLM directly — the planner emits them by ID
    and the macro engine expands them into primitives before execution.  This
    keeps the LLM's tool surface small while enabling multi-step operations.

    Attributes:
        id: Stable slug used to invoke the macro (e.g. ``"mix.darker"``).
        description: Human-readable intent, shown in plan previews.
        expand: Pure function ``(MacroContext) -> list[ToolCall]`` — receives
            the targeting context and returns zero or more primitives.
            Must handle missing context keys gracefully (return ``[]``).
    """

    id: str
    description: str
    expand: Callable[[MacroContext], list[ToolCall]]


def macro_make_darker(ctx: MacroContext) -> list[ToolCall]:
    """Expand the ``mix.darker`` macro into EQ + distortion inserts.

    Conservative recipe: high-shelf EQ cut followed by subtle tube distortion
    for warmth.  Returns an empty list when ``trackId`` is absent from context.
    """
    track_id = ctx.get("trackId")
    if not track_id:
        return []
    return [
        ToolCall("stori_add_insert_effect", {"trackId": track_id, "type": "eq"}),
        ToolCall("stori_add_insert_effect", {"trackId": track_id, "type": "distortion"}),
    ]


MACROS: dict[str, Macro] = {
    "mix.darker": Macro("mix.darker", "Make the target darker (reduce highs, add warmth).", macro_make_darker),
}


def expand_macro(macro_id: str, ctx: MacroContext) -> list[ToolCall]:
    """Look up a macro by ID and expand it into primitive ``ToolCall``s.

    Returns an empty list for unknown ``macro_id`` values — callers should
    treat an empty result as a no-op rather than an error.
    """
    m = MACROS.get(macro_id)
    if not m:
        return []
    return m.expand(ctx)
