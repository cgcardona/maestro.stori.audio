"""
Intent routing package for Stori Maestro.

Routes user prompts to the appropriate execution path (REASONING/EDITING/COMPOSING).

Public API:
    get_intent_result(prompt, project_context) -> IntentResult          # sync, pattern-only
    get_intent_result_with_llm(prompt, project_context, llm) -> IntentResult  # async + LLM

Re-exports Intent and SSEState for backward compatibility.
"""

from app.core.intent.models import IntentResult, Slots, Rule
from app.core.intent.normalization import normalize, _extract_quoted, _num
from app.core.intent.detection import (
    _is_question,
    _is_stori_question,
    _is_generation_request,
    _is_vague,
    _is_affirmative,
    _is_negative,
)
from app.core.intent.patterns import RULES, _extract_slots
from app.core.intent.builder import _build_result, _clarify
from app.core.intent.structured import (
    _route_from_parsed_prompt,
    _infer_edit_intent,
    _MODE_TO_INTENT,
    _EDIT_DEFAULT_INTENT,
)
from app.core.intent.routing import (
    get_intent_result,
    classify_with_llm,
    _category_to_result,
    get_intent_result_with_llm,
)

# Re-export for backward compatibility
from app.core.intent_config import Intent, SSEState

__all__ = [
    # Models
    "IntentResult",
    "Slots",
    "Rule",
    # Normalization
    "normalize",
    # Detection
    "_is_question",
    "_is_stori_question",
    "_is_generation_request",
    "_is_vague",
    "_is_affirmative",
    "_is_negative",
    # Patterns
    "RULES",
    "_extract_slots",
    # Builder
    "_build_result",
    "_clarify",
    # Structured
    "_route_from_parsed_prompt",
    "_infer_edit_intent",
    # Routing (main entrypoints)
    "get_intent_result",
    "classify_with_llm",
    "get_intent_result_with_llm",
    # Backward compat
    "Intent",
    "SSEState",
]
