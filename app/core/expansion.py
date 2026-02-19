"""
Expansion utilities.

Defines ToolCall (execution primitive) and helpers for validation/dedup.

This module should be dependency-free so it can run in worker contexts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import hashlib
import json


@dataclass(frozen=True)
class ToolCall:
    """Canonical tool call used throughout the execution pipeline.

    ``name`` and ``params`` are always present.
    ``id`` is populated only for calls that originated from an LLM response
    (needed to send ``tool_call_id`` back in the conversation history).
    Planner-generated calls leave ``id`` empty — it is never read for those.
    """

    name: str
    params: dict[str, Any]
    id: str = ""  # LLM-assigned call ID; empty for planner-generated calls

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "params": self.params}

    def fingerprint(self) -> str:
        # id is intentionally excluded: two structurally identical calls
        # (same name + params) are considered duplicates regardless of id.
        blob = json.dumps({"name": self.name, "params": self.params}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def dedupe_tool_calls(calls: list[ToolCall]) -> list[ToolCall]:
    seen = set()
    out: list[ToolCall] = []
    for c in calls:
        fp = c.fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        out.append(c)
    return out
