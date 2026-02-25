"""
Macro engine (Cursor-of-DAWs)

Macros are NOT direct tools. They are *recipes* expanded into PRIMITIVE ToolCalls
based on context + slots.

This file intentionally keeps macros small and composable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.core.expansion import ToolCall


@dataclass(frozen=True)
class Macro:
    id: str
    description: str
    expand: Callable[[dict[str, Any]], list[ToolCall]]  # context -> toolcalls


def macro_make_darker(ctx: dict[str, Any]) -> list[ToolCall]:
    # Example conservative recipe: EQ + subtle distortion for warmth
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


def expand_macro(macro_id: str, ctx: dict[str, Any]) -> list[ToolCall]:
    m = MACROS.get(macro_id)
    if not m:
        return []
    return m.expand(ctx)
