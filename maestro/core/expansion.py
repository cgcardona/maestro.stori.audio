"""
Expansion utilities.

Defines ToolCall (execution primitive) and helpers for validation/dedup.

This module should be dependency-free so it can run in worker contexts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from maestro.contracts.json_types import JSONValue, ToolCallPreviewDict


@dataclass(frozen=True)
class ToolCall:
    """Canonical tool call used throughout the execution pipeline.

    ``name`` and ``params`` are always present.
    ``id`` is populated only for calls that originated from an LLM response
    (needed to send ``tool_call_id`` back in the conversation history).
    Planner-generated calls leave ``id`` empty — it is never read for those.
    """

    name: str
    params: dict[str, JSONValue]
    id: str = ""  # LLM-assigned call ID; empty for planner-generated calls

    def to_dict(self) -> ToolCallPreviewDict:
        """Serialise to ``{"name": ..., "params": ...}`` (omits ``id``)."""
        return ToolCallPreviewDict(name=self.name, params=self.params)

    def fingerprint(self) -> str:
        """Return a 16-char SHA-256 content hash of ``name`` + ``params``.

        ``id`` is intentionally excluded: two structurally identical calls
        (same name + params) are considered duplicates regardless of their
        LLM-assigned ``id``.  Used by ``dedupe_tool_calls``.
        """
        blob = json.dumps({"name": self.name, "params": self.params}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def dedupe_tool_calls(calls: list[ToolCall]) -> list[ToolCall]:
    """Remove structurally duplicate tool calls, preserving order.

    Two calls are duplicates when they share the same ``name`` and ``params``
    (``id`` is ignored).  The first occurrence is kept; subsequent identical
    calls are dropped.  Used before executing a plan to prevent the LLM from
    accidentally emitting the same action twice.
    """
    seen = set()
    out: list[ToolCall] = []
    for c in calls:
        fp = c.fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        out.append(c)
    return out
