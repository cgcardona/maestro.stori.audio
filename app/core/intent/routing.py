"""Main intent routing entrypoints (sync pattern-only + async LLM fallback)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.contracts.llm_types import ChatMessage

if TYPE_CHECKING:
    from app.contracts.project_types import ProjectContext
    from app.core.llm_client import LLMClient

from app.core.intent_config import Intent, match_producer_idiom
from app.core.intent.models import IntentResult, Slots, SlotsExtrasDict
from app.core.intent.normalization import normalize
from app.core.intent.detection import (
    _is_question,
    _is_stori_question,
    _is_generation_request,
    _is_vague,
    _is_affirmative,
)
from app.core.intent.patterns import RULES, _extract_slots
from app.core.intent.builder import _build_result, _clarify
from app.core.intent.structured import _route_from_parsed_prompt
from app.core.prompt_parser import parse_prompt
from app.core.prompts import intent_classification_prompt, INTENT_CLASSIFICATION_SYSTEM

logger = logging.getLogger(__name__)


def get_intent_result(
    prompt: str,
    project_context: ProjectContext | None = None,
) -> IntentResult:
    """
    Synchronous intent routing using patterns only.

    For comprehensive routing with LLM fallback, use get_intent_result_with_llm().
    """
    parsed = parse_prompt(prompt)
    if parsed is not None:
        return _route_from_parsed_prompt(parsed)

    raw = prompt
    norm = normalize(prompt)

    if _is_question(norm):
        intent = Intent.ASK_STORI_DOCS if _is_stori_question(norm) else Intent.ASK_GENERAL
        return _build_result(intent, 0.75, Slots(value_str=raw), ("question",))

    for rule in RULES:
        m = rule.pattern.search(norm)
        if m:
            slots = _extract_slots(rule, m, raw, norm)
            return _build_result(rule.intent, rule.confidence, slots, (f"rule:{rule.name}",))

    if _is_vague(norm):
        return _clarify(raw, "vague")

    idiom = match_producer_idiom(norm)
    if idiom:
        idiom_extras: SlotsExtrasDict = {"matched_phrase": idiom.phrase}
        if idiom.target is not None:
            idiom_extras["target"] = idiom.target
        slots = Slots(
            value_str=raw,
            idiom_match=idiom,
            direction=idiom.direction,
            extras=idiom_extras,
        )
        return _build_result(idiom.intent, 0.85, slots, (f"idiom:{idiom.phrase}",))

    # "Add...to..." → EDITING, not COMPOSING (must precede generation check)
    if ("add" in norm or "insert" in norm or "write" in norm) and " to " in norm:
        return _build_result(
            Intent.NOTES_ADD,
            confidence=0.82,
            slots=Slots(value_str=raw, action="add", target_type="notes"),
            reasons=("add_to_existing",),
        )

    if _is_generation_request(norm):
        return _build_result(
            Intent.GENERATE_MUSIC,
            confidence=0.80,
            slots=Slots(value_str=raw),
            reasons=("generation_phrase",),
        )

    return _build_result(
        Intent.UNKNOWN,
        confidence=0.25,
        slots=Slots(value_str=raw),
        reasons=("no_match",),
    )


async def classify_with_llm(prompt: str, llm: "LLMClient") -> tuple[str, float]:
    """Use LLM to classify intent when patterns fail."""
    try:
        response = await llm.chat(
            system=INTENT_CLASSIFICATION_SYSTEM,
            user=intent_classification_prompt(prompt),
            tools=[],
            tool_choice="none",
            context={},
        )

        category = (response.content or "other").strip().lower()

        category_map = {
            "transport": "transport",
            "track_edit": "track",
            "track": "track",
            "region_edit": "region",
            "region": "region",
            "effects": "effects",
            "mix_vibe": "mix",
            "mix": "mix",
            "generation": "generation",
            "question": "question",
            "clarify": "clarify",
            "other": "other",
        }

        normalized = category_map.get(category, "other")
        confidence = 0.75 if normalized != "other" else 0.4

        logger.info(
            f"🤖 LLM classified '{prompt[:50]}...' as '{normalized}' (conf: {confidence})"
        )
        return normalized, confidence

    except Exception as e:
        logger.warning(f"LLM classification failed: {e}")
        return "other", 0.3


def _category_to_result(
    category: str,
    confidence: float,
    raw: str,
    norm: str,
) -> IntentResult:
    """Convert LLM category string to IntentResult."""
    slots = Slots(value_str=raw)

    intent_map: dict[str, Intent] = {
        "transport": Intent.PLAY,
        "track": Intent.TRACK_ADD,
        "region": Intent.REGION_ADD,
        "effects": Intent.FX_ADD_INSERT,
        "mix": Intent.MIX_ENERGY,
        "generation": Intent.GENERATE_MUSIC,
        "question": Intent.ASK_STORI_DOCS if _is_stori_question(norm) else Intent.ASK_GENERAL,
        "clarify": Intent.NEEDS_CLARIFICATION,
        "other": Intent.UNKNOWN,
    }

    intent = intent_map.get(category, Intent.UNKNOWN)
    return _build_result(intent, confidence, slots, (f"llm:{category}",))


async def get_intent_result_with_llm(
    prompt: str,
    project_context: ProjectContext | None = None,
    llm: "LLMClient | None" = None,
    conversation_history: list[ChatMessage] | None = None,
) -> IntentResult:
    """
    Comprehensive intent routing with LLM fallback.

    Flow:
    0. Structured prompt fast path (bypass everything)
    1. Check for affirmative responses with conversation context
    2. Try pattern-based routing (fast)
    3. If UNKNOWN with low confidence, use LLM classification
    """
    parsed = parse_prompt(prompt)
    if parsed is not None:
        return _route_from_parsed_prompt(parsed)

    norm = normalize(prompt)
    conversation_history = conversation_history or []

    if _is_affirmative(norm) and conversation_history:
        for msg in reversed(conversation_history):
            if msg.get("role") == "assistant":
                content = (msg.get("content") or "").lower()
                if "?" in content or any(
                    word in content
                    for word in ["would you like", "should i", "want me to"]
                ):
                    logger.info(
                        "🔄 Detected affirmative response to previous question, routing to COMPOSING"
                    )
                    return _build_result(
                        Intent.GENERATE_MUSIC,
                        confidence=0.85,
                        slots=Slots(value_str=prompt),
                        reasons=("affirmative_confirmation",),
                    )
                break

    result = get_intent_result(prompt, project_context)

    if not result.needs_llm_fallback or llm is None:
        return result

    logger.info("🤖 Pattern routing returned UNKNOWN, using LLM fallback")

    category, confidence = await classify_with_llm(prompt, llm)

    return _category_to_result(category, confidence, prompt, norm)
