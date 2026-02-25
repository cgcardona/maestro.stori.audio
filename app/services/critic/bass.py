"""Bass scoring functions."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def score_bass_notes(
    notes: list[dict[str, Any]],
    kick_beats: list[float] | None = None,
    *,
    window_beats: float = 0.25,
    anticipation_allowed: bool = True,
) -> tuple[float, list[str]]:
    """Score bass notes for kick alignment, anticipation, and density."""
    repair: list[str] = []
    if not notes:
        return 0.0, ["bass_empty: add bass notes"]

    scores: list[float] = []

    if kick_beats:
        kick_set = set(kick_beats)
        aligned = 0
        anticipated = 0

        for n in notes:
            start = n.get("start_beat", 0)
            for k in kick_set:
                if abs(start - k) <= window_beats:
                    aligned += 1
                    break
            else:
                if anticipation_allowed:
                    for k in kick_set:
                        if 0.0625 <= k - start <= 0.25:
                            anticipated += 1
                            break

        alignment = aligned / len(notes)
        anticipation_ratio = anticipated / len(notes)
        scores.append(min(1.0, alignment + anticipation_ratio * 0.3))

        if alignment < 0.4:
            repair.append("kick_bass_alignment_low: align more bass onsets with kick")
    else:
        scores.append(0.7)

    bars = max(1, int(max(n.get("start_beat", 0) for n in notes) / 4) + 1)
    notes_per_bar = len(notes) / bars
    if 2 <= notes_per_bar <= 8:
        scores.append(1.0)
    elif notes_per_bar < 2:
        scores.append(0.5)
        repair.append("bass_sparse: add more bass notes")
    else:
        scores.append(0.6)

    return sum(scores) / len(scores) if scores else 0.5, repair
