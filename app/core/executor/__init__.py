"""Executor package for Stori Maestro.

Variation pipeline (two-phase):

1. **Maestro orchestration** (``execute_tools_for_variation``): dispatches tool
   calls without mutating canonical state, collects base/proposed notes into a
   ``VariationContext``.
2. **Muse computation** (``compute_variation_from_context``): takes only plain
   musical data (no StateStore) and produces a ``Variation`` diff.

``execute_plan_variation`` is a convenience wrapper that runs both phases.

Post-approval:

- **Phrase application** (``apply_variation_phrases``): applies accepted
  variation phrases to canonical state after human approval.
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
    execute_tools_for_variation,
    compute_variation_from_context,
    execute_plan_variation,
    _extract_notes_from_project,
    _process_call_for_variation,
    _collect_region_start_beats,
    _GENERATOR_TIMEOUT,
    _MAX_PARALLEL_GROUPS,
)
from app.core.executor.apply import apply_variation_phrases
from app.core.executor.snapshots import (
    capture_base_snapshot,
    capture_proposed_snapshot,
    StoreSnapshot,
)

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
    # Variation pipeline — Maestro orchestration
    "execute_tools_for_variation",
    "_extract_notes_from_project",
    "_process_call_for_variation",
    "_collect_region_start_beats",
    # Variation pipeline — Muse computation
    "compute_variation_from_context",
    # Variation pipeline — convenience wrapper
    "execute_plan_variation",
    # Post-approval
    "apply_variation_phrases",
    # Snapshot boundary
    "capture_base_snapshot",
    "capture_proposed_snapshot",
    "StoreSnapshot",
]
