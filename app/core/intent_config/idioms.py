"""Producer idiom lexicon with polarity matching."""

from __future__ import annotations


from app.core.intent_config.enums import Intent
from app.core.intent_config.models import IdiomMatch


PRODUCER_IDIOMS: dict[str, IdiomMatch] = {
    # Tonality
    "darker": IdiomMatch(Intent.MIX_TONALITY, "darker", "decrease", "highs", frozenset({"stori_add_insert_effect"})),
    "brighter": IdiomMatch(Intent.MIX_TONALITY, "brighter", "increase", "highs", frozenset({"stori_add_insert_effect"})),
    "warmer": IdiomMatch(Intent.MIX_TONALITY, "warmer", "increase", "low_mids", frozenset({"stori_add_insert_effect"})),
    "colder": IdiomMatch(Intent.MIX_TONALITY, "colder", "decrease", "low_mids", frozenset({"stori_add_insert_effect"})),
    "too bright": IdiomMatch(Intent.MIX_TONALITY, "too bright", "decrease", "highs", frozenset({"stori_add_insert_effect"})),
    "too dark": IdiomMatch(Intent.MIX_TONALITY, "too dark", "increase", "highs", frozenset({"stori_add_insert_effect"})),

    # Dynamics
    "punchier": IdiomMatch(Intent.MIX_DYNAMICS, "punchier", "increase", "attack", frozenset({"stori_add_insert_effect"})),
    "more punch": IdiomMatch(Intent.MIX_DYNAMICS, "more punch", "increase", "attack", frozenset({"stori_add_insert_effect"})),
    "tighter": IdiomMatch(Intent.MIX_DYNAMICS, "tighter", "decrease", "release", frozenset({"stori_add_insert_effect"})),
    "fatter": IdiomMatch(Intent.MIX_DYNAMICS, "fatter", "increase", "saturation", frozenset({"stori_add_insert_effect"})),
    "thicker": IdiomMatch(Intent.MIX_DYNAMICS, "thicker", "increase", "lows", frozenset({"stori_add_insert_effect"})),
    "less muddy": IdiomMatch(Intent.MIX_DYNAMICS, "less muddy", "decrease", "low_mids", frozenset({"stori_add_insert_effect"})),

    # Space
    "wider": IdiomMatch(Intent.MIX_SPACE, "wider", "increase", "stereo_width", frozenset({"stori_add_insert_effect", "stori_set_track_pan"})),
    "bigger": IdiomMatch(Intent.MIX_SPACE, "bigger", "increase", "reverb", frozenset({"stori_add_insert_effect", "stori_add_send"})),
    "more space": IdiomMatch(Intent.MIX_SPACE, "more space", "increase", "reverb", frozenset({"stori_add_insert_effect", "stori_add_send"})),
    "more depth": IdiomMatch(Intent.MIX_SPACE, "more depth", "increase", "delay", frozenset({"stori_add_insert_effect", "stori_add_send"})),
    "closer": IdiomMatch(Intent.MIX_SPACE, "closer", "decrease", "reverb", frozenset({"stori_add_insert_effect"})),
    "more intimate": IdiomMatch(Intent.MIX_SPACE, "more intimate", "decrease", "reverb", frozenset({"stori_add_insert_effect"})),

    # Energy
    "more energy": IdiomMatch(Intent.MIX_ENERGY, "more energy", "increase", "dynamics", frozenset({"stori_add_insert_effect"})),
    "more movement": IdiomMatch(Intent.MIX_ENERGY, "more movement", "add", "modulation", frozenset({"stori_add_automation", "stori_add_insert_effect"})),
    "add life": IdiomMatch(Intent.MIX_ENERGY, "add life", "add", "variation", frozenset({"stori_add_automation"})),
    "too static": IdiomMatch(Intent.MIX_ENERGY, "too static", "add", "modulation", frozenset({"stori_add_automation", "stori_add_insert_effect"})),
    "boring": IdiomMatch(Intent.MIX_ENERGY, "boring", "add", "variation", frozenset({"stori_add_automation"})),
}


def match_producer_idiom(text: str) -> IdiomMatch | None:
    """Match a producer idiom phrase in text. Returns the first match or None."""
    text_lower = text.lower()
    for phrase, match in PRODUCER_IDIOMS.items():
        if phrase in text_lower:
            return match
    return None


def match_weighted_vibes(vibes: list[tuple[str, int]]) -> list[IdiomMatch]:
    """
    Match weighted vibes from a structured prompt against the idiom lexicon.

    Args:
        vibes: list of (vibe_text, weight) tuples from ParsedPrompt.vibes

    Returns:
        IdiomMatch objects with weights set, sorted by weight descending.
        Unknown vibes are silently skipped.
    """
    matches: list[IdiomMatch] = []
    for vibe_text, weight in vibes:
        idiom = match_producer_idiom(vibe_text)
        if idiom:
            weighted = IdiomMatch(
                intent=idiom.intent,
                phrase=idiom.phrase,
                direction=idiom.direction,
                target=idiom.target,
                suggested_tools=idiom.suggested_tools,
                weight=weight,
            )
            matches.append(weighted)
    matches.sort(key=lambda m: m.weight, reverse=True)
    return matches
