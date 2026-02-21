"""Tool metadata models and enums."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ToolTier(str, Enum):
    TIER1 = "tier1"  # server-side
    TIER2 = "tier2"  # client-side


class ToolKind(str, Enum):
    PRIMITIVE = "primitive"
    GENERATOR = "generator"
    MACRO = "macro"


@dataclass(frozen=True)
class ToolMeta:
    name: str
    tier: ToolTier
    kind: ToolKind
    # Safety / routing hints:
    creates_entity: Optional[str] = None      # "track" | "region" | "bus" | None
    id_fields: tuple[str, ...] = ()           # e.g. ("trackId",)
    reversible: bool = True
    # Planner gates:
    planner_only: bool = False                # True => never directly exposed to the LLM
    deprecated: bool = False
