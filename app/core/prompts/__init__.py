"""
Prompt templates for Maestro.

Core principles:
- Never hallucinate tool arguments
- Never call tools outside allowlist (server enforces too)
- Prefer clarification over guessing
- For "required" tool_choice: call exactly one tool then stop
- For multi-step editing: only chain PRIMITIVES; never call GENERATORS directly
"""
from __future__ import annotations

from app.core.prompts.system import system_prompt_base, wrap_user_request
from app.core.prompts.modes import editing_prompt, editing_composition_prompt, composing_prompt
from app.core.prompts.intent import intent_classification_prompt, INTENT_CLASSIFICATION_SYSTEM
from app.core.prompts.structured import (
    structured_prompt_routing_context,
    structured_prompt_context,
)
from app.core.prompts.position import (
    resolve_position,
    resolve_after_beat,
    sequential_context,
)

__all__ = [
    "system_prompt_base",
    "wrap_user_request",
    "editing_prompt",
    "editing_composition_prompt",
    "composing_prompt",
    "intent_classification_prompt",
    "INTENT_CLASSIFICATION_SYSTEM",
    "structured_prompt_routing_context",
    "structured_prompt_context",
    "resolve_position",
    "resolve_after_beat",
    "sequential_context",
]
