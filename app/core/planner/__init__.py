"""
Planner package for Stori Maestro.

Converts natural language music requests into validated execution plans.

Public API:
    build_execution_plan(prompt, state, route, llm, ...) -> ExecutionPlan
    build_execution_plan_stream(...) -> AsyncIterator[ExecutionPlan | str]
    build_plan_from_dict(plan_dict, project_state) -> ExecutionPlan
    preview_plan(prompt, state, route, llm) -> dict
"""

from app.core.planner.models import ExecutionPlan
from app.core.planner.effects import (
    _ROLE_ALWAYS_EFFECTS,
    _STYLE_ROLE_EFFECTS,
    _infer_mix_steps,
)
from app.core.planner.track_matching import (
    _ROLE_INSTRUMENT_HINTS,
    _match_roles_to_existing_tracks,
    _build_role_to_track_map,
)
from app.core.planner.conversion import _schema_to_tool_calls
from app.core.planner.plan import (
    _finalise_plan,
    _try_deterministic_plan,
    build_execution_plan,
    build_execution_plan_stream,
    build_plan_from_dict,
    preview_plan,
)

__all__ = [
    # Models
    "ExecutionPlan",
    # Effects
    "_ROLE_ALWAYS_EFFECTS",
    "_STYLE_ROLE_EFFECTS",
    "_infer_mix_steps",
    # Track matching
    "_ROLE_INSTRUMENT_HINTS",
    "_match_roles_to_existing_tracks",
    "_build_role_to_track_map",
    # Conversion
    "_schema_to_tool_calls",
    # Main entrypoints
    "_finalise_plan",
    "_try_deterministic_plan",
    "build_execution_plan",
    "build_execution_plan_stream",
    "build_plan_from_dict",
    "preview_plan",
]
