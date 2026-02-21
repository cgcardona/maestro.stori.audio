"""
Executor package for Stori Maestro.

Two main execution paths:
1. **Variation mode** (execute_plan_variation): simulates tool calls without
   mutating canonical state, captures base/proposed notes, computes a Variation.
2. **Phrase application** (apply_variation_phrases): applies accepted variation
   phrases to canonical state after human approval.
"""

from app.core.executor.note_utils import _NOTE_KEY_MAP, _normalize_note
from app.core.executor.models import (
    ExecutionResult,
    ExecutionContext,
    VariationContext,
    VariationApplyResult,
)
from app.core.executor.phases import (
    _PHASE1_TOOLS,
    _PHASE3_TOOLS,
    _get_instrument_for_call,
    _group_into_phases,
)
from app.core.executor.execution import _execute_single_call, _execute_generator
from app.core.executor.variation import (
    execute_plan_variation,
    _extract_notes_from_project,
    _process_call_for_variation,
    _GENERATOR_TIMEOUT,
    _MAX_PARALLEL_GROUPS,
)
from app.core.executor.apply import apply_variation_phrases

__all__ = [
    # Note utilities
    "_NOTE_KEY_MAP",
    "_normalize_note",
    # Models
    "ExecutionResult",
    "ExecutionContext",
    "VariationContext",
    "VariationApplyResult",
    # Phases
    "_PHASE1_TOOLS",
    "_PHASE3_TOOLS",
    "_get_instrument_for_call",
    "_group_into_phases",
    # Execution
    "_execute_single_call",
    "_execute_generator",
    # Constants
    "_GENERATOR_TIMEOUT",
    "_MAX_PARALLEL_GROUPS",
    # Main entrypoints
    "execute_plan_variation",
    "_extract_notes_from_project",
    "_process_call_for_variation",
    "apply_variation_phrases",
]
