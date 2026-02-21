"""Low-level helper functions shared across critic scorers."""

import math
from collections import Counter
from typing import Optional


def _distinct_pitches(notes: list[dict]) -> int:
    return len(set(n.get("pitch", 0) for n in notes))


def _velocity_entropy_normalized(notes: list[dict]) -> float:
    """Normalized velocity entropy: 0 = all same, 1 = well spread."""
    if not notes:
        return 0.0
    vels = [n.get("velocity", 80) for n in notes]
    bins = Counter(min(16, v // 8) for v in vels)
    n = len(vels)
    if n <= 1:
        return 0.0
    ent = -sum((c / n) * math.log2(c / n) for c in bins.values())
    return min(1.0, ent / 4.0)


def _offbeat_ratio(notes: list[dict], beat_resolution: float = 0.25) -> float:
    """Fraction of onsets that are offbeat (not on a quarter note)."""
    if not notes:
        return 0.0
    off = sum(1 for n in notes if (n.get("start_beat", 0) * 4) % 4 != 0)
    return off / len(notes)


def _get_notes_by_layer(notes: list[dict], layer_map: Optional[dict] = None) -> dict:
    """Group notes by layer name."""
    by_layer: dict[str, list[dict]] = {}
    for i, n in enumerate(notes):
        layer = n.get("layer")
        if layer is None and layer_map:
            layer = layer_map.get(i, "unknown")
        if layer is None:
            layer = "unknown"
        by_layer.setdefault(layer, []).append(n)
    return by_layer
