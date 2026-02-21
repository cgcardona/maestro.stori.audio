"""Text normalization utilities for intent pattern matching."""

from __future__ import annotations

import re
from typing import Optional

_FILLER = {
    "please", "pls", "plz", "please can you", "please could you",
    "thank you", "thanks", "thx", "ty",
    "hey", "hi", "hello", "yo", "sup", "wassup", "whats up", "what's up",
    "umm", "uh", "uhh", "um", "hmm", "well", "so", "like", "kinda", "sorta",
    "maybe", "perhaps", "i think", "i guess", "probably",
    "can you", "could you", "would you", "will you", "can u", "could u", "would u",
    "i want you to", "i need you to", "i'd like you to", "id like you to",
    "would you mind", "could you please", "can you please",
    "just", "really", "very", "quite", "pretty", "super", "totally",
    "you know", "i mean", "basically", "actually", "literally",
}


def normalize(text: str) -> str:
    """Normalize text for pattern matching."""
    t = text.strip().lower()
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    t = re.sub(r"\s+", " ", t)
    for f in sorted(_FILLER, key=len, reverse=True):
        t = t.replace(f, "")
    return re.sub(r"\s+", " ", t).strip()


def _extract_quoted(text: str) -> Optional[str]:
    """Extract the first quoted string from text."""
    m = re.search(r'"([^"]+)"', text)
    if m:
        return m.group(1).strip()
    m = re.search(r"'([^']+)'", text)
    if m:
        return m.group(1).strip()
    return None


def _num(x: str) -> Optional[float]:
    """Parse a number from a string, returning None on failure."""
    try:
        return float(x)
    except Exception:
        return None
