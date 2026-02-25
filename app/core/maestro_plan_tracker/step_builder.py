"""Step result helper — shared by editing and agent-teams."""

from __future__ import annotations

from typing import Any

from app.core.maestro_helpers import _human_label_for_tool


def _build_step_result(
    tool_name: str,
    params: dict[str, Any],
    existing: str | None = None,
) -> str:
    """Build a human-readable result string for a plan step."""
    part = _human_label_for_tool(tool_name, params)
    if existing:
        return f"{existing}; {part}"
    return part
