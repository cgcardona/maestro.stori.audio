"""Accessor functions for intent configuration."""

from __future__ import annotations

from app.core.intent_config.enums import Intent, SSEState
from app.core.intent_config.models import IntentConfig
from app.core.intent_config.configs import INTENT_CONFIGS


def get_intent_config(intent: Intent) -> IntentConfig:
    """Get configuration for an intent."""
    return INTENT_CONFIGS.get(intent, INTENT_CONFIGS[Intent.UNKNOWN])


def get_allowed_tools_for_intent(intent: Intent) -> frozenset[str]:
    """Get allowed tool names for an intent."""
    return get_intent_config(intent).allowed_tools


def get_sse_state_for_intent(intent: Intent) -> SSEState:
    """Get SSE state for an intent."""
    return get_intent_config(intent).sse_state
