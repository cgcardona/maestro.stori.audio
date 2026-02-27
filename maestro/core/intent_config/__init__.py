"""
Centralized Intent Configuration for Maestro.

Single source of truth for:
1. Intent → Allowed Tools mapping
2. Intent → SSE State routing
3. Intent → Execution policy (force_stop, tool_choice)
"""
from __future__ import annotations

from maestro.core.intent_config.enums import SSEState, Intent
from maestro.core.intent_config.models import IntentConfig, IdiomMatch
from maestro.core.intent_config.configs import (
    INTENT_CONFIGS,
    _PRIMITIVES_MIXING,
    _PRIMITIVES_TRACK,
    _PRIMITIVES_REGION,
    _PRIMITIVES_FX,
)
from maestro.core.intent_config.accessors import (
    get_intent_config,
    get_allowed_tools_for_intent,
    get_sse_state_for_intent,
)
from maestro.core.intent_config.idioms import (
    PRODUCER_IDIOMS,
    match_producer_idiom,
    match_weighted_vibes,
)

__all__ = [
    "SSEState",
    "Intent",
    "IntentConfig",
    "IdiomMatch",
    "INTENT_CONFIGS",
    "_PRIMITIVES_MIXING",
    "_PRIMITIVES_TRACK",
    "_PRIMITIVES_REGION",
    "_PRIMITIVES_FX",
    "get_intent_config",
    "get_allowed_tools_for_intent",
    "get_sse_state_for_intent",
    "PRODUCER_IDIOMS",
    "match_producer_idiom",
    "match_weighted_vibes",
]
