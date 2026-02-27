"""Three-phase grouping of tool calls for parallel instrument execution.

Delegates to ``app.daw.stori.phase_map`` — the Stori adapter owns the
tool-to-phase classification.  This module preserves the public names
(``_PHASE1_TOOLS``, ``_PHASE3_TOOLS``, ``_get_instrument_for_call``,
``_group_into_phases``) so callers don't need import changes.
"""
from __future__ import annotations

from app.daw.stori.phase_map import (
    _SETUP_TOOLS as _PHASE1_TOOLS,
    _MIXING_TOOLS as _PHASE3_TOOLS,
    _str_param,
    get_instrument_for_call as _get_instrument_for_call,
    group_into_phases as _group_into_phases,
    InstrumentGroups,
    PhaseSplit,
)

__all__ = [
    "_PHASE1_TOOLS",
    "_PHASE3_TOOLS",
    "_str_param",
    "_get_instrument_for_call",
    "_group_into_phases",
    "InstrumentGroups",
    "PhaseSplit",
]
