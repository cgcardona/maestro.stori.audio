"""EDITING handler and tool execution core for Maestro."""
from __future__ import annotations

from maestro.core.maestro_editing.routing import (
    _project_needs_structure,
    _is_additive_composition,
    _create_editing_composition_route,
)
from maestro.core.maestro_editing.continuation import (
    _get_incomplete_tracks,
    _get_missing_expressive_steps,
)
from maestro.core.maestro_editing.tool_execution import (
    _apply_single_tool_call,
    execute_unified_generation,
    phase_for_tool,
)
from maestro.core.maestro_editing.handler import (
    _handle_editing,
    _handle_editing_apply,
    _handle_editing_variation,
)

__all__ = [
    # Routing
    "_project_needs_structure",
    "_is_additive_composition",
    "_create_editing_composition_route",
    # Continuation
    "_get_incomplete_tracks",
    "_get_missing_expressive_steps",
    # Tool execution
    "_apply_single_tool_call",
    "execute_unified_generation",
    "phase_for_tool",
    # Handler (dispatcher + mode-specific)
    "_handle_editing",
    "_handle_editing_apply",
    "_handle_editing_variation",
]
