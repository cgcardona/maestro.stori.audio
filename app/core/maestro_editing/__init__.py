"""EDITING handler and tool execution core for Maestro."""

from app.core.maestro_editing.routing import (
    _project_needs_structure,
    _is_additive_composition,
    _create_editing_composition_route,
)
from app.core.maestro_editing.continuation import (
    _get_incomplete_tracks,
    _get_missing_expressive_steps,
)
from app.core.maestro_editing.tool_execution import _apply_single_tool_call
from app.core.maestro_editing.handler import _handle_editing

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
    # Handler
    "_handle_editing",
]
