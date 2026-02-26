"""Structured prompt fast-path routing (bypasses NL pattern matching)."""

from __future__ import annotations

from app.core.intent.models import SlotsExtrasDict

from app.core.intent_config import Intent, IdiomMatch, match_producer_idiom
from app.core.intent.models import IntentResult, Slots
from app.core.intent.builder import _build_result
from app.core.prompt_parser import ParsedPrompt

_MODE_TO_INTENT: dict[str, Intent] = {
    "compose": Intent.GENERATE_MUSIC,
    "ask": Intent.ASK_GENERAL,
}

# Default edit intent when vibes don't match anything more specific.
_EDIT_DEFAULT_INTENT = Intent.MIX_ENERGY


def _infer_edit_intent(parsed: ParsedPrompt) -> Intent:
    """Pick the most appropriate edit intent from vibes/constraints."""
    if parsed.vibes:
        best_match: IdiomMatch | None = None
        best_weight = 0
        for vw in parsed.vibes:
            idiom = match_producer_idiom(vw.vibe)
            if idiom and vw.weight > best_weight:
                best_match = idiom
                best_weight = vw.weight
        if best_match:
            return best_match.intent

    effect_keys = {"compressor", "eq", "reverb", "delay", "chorus", "distortion"}
    if any(k in effect_keys for k in parsed.constraints):
        return Intent.FX_ADD_INSERT

    return _EDIT_DEFAULT_INTENT


def _route_from_parsed_prompt(parsed: ParsedPrompt) -> IntentResult:
    """
    Build an IntentResult directly from a parsed structured prompt.

    Mode is a hard routing signal — no pattern matching or LLM classification.
    """
    extras: SlotsExtrasDict = {"parsed_prompt": parsed}

    target_type: str | None = None
    target_name: str | None = None
    if parsed.target:
        target_type = parsed.target.kind
        target_name = parsed.target.name

    if parsed.mode == "compose":
        intent = Intent.GENERATE_MUSIC
    elif parsed.mode == "ask":
        intent = Intent.ASK_GENERAL
    else:
        intent = _infer_edit_intent(parsed)

    slots = Slots(
        value_str=parsed.request,
        target_type=target_type,
        target_name=target_name,
        extras=extras,
    )

    return _build_result(
        intent,
        confidence=0.99,
        slots=slots,
        reasons=("structured_prompt",),
    )
