"""
Groove post-process: microtiming and velocity humanization.

Uses Groove Engine with style-specific microtiming, swing grids,
accent maps, and hat articulation rules.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from app.services.groove_engine import (
    apply_groove_map,
    get_groove_profile,
    GrooveProfile,
    GROOVE_PROFILES,
)

logger = logging.getLogger(__name__)


def apply_groove_postprocess(
    notes: list[dict[str, Any]],
    tempo: int = 120,
    *,
    style: str = "trap",
    humanize_profile: str | None = None,
    layer_map: dict[int, str] | None = None,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """
    Apply groove humanization using the Groove Engine.
    
    Uses style-specific microtiming, swing, and velocity shaping instead of
    uniform jitter. Each instrument role gets appropriate timing offsets:
    - Kick: slightly early (punchy)
    - Snare: slightly late (pocket)
    - Hats: style-dependent (lazy for boom bap, tight for trap)
    - Ghosts: late (behind the beat)
    
    Args:
        notes: list of {pitch, startBeat, duration, velocity, ...}
        tempo: BPM
        style: Music style (e.g., "boom_bap", "trap", "house")
        humanize_profile: Optional feel override ("tight", "laid_back", "pushed")
        layer_map: Optional dict mapping note index -> layer name
        rng: Random number generator for reproducibility
    
    Returns:
        list of notes with groove applied (timing + velocity adjusted)
    """
    if not notes:
        return notes
    
    return apply_groove_map(
        notes,
        tempo=tempo,
        style=style,
        humanize_profile=humanize_profile,
        layer_map=layer_map,
        rng=rng,
    )


def apply_groovae_if_available(
    notes: list[dict[str, Any]],
    tempo: int,
    style: str = "trap",
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """
    Placeholder for future groove-model integration. For now, use Groove Engine.
    """
    try:
        pass
    except Exception:
        pass
    return apply_groove_postprocess(notes, tempo, style=style, **kwargs)


def get_available_profiles() -> list[str]:
    """Get list of available groove profiles."""
    return list(GROOVE_PROFILES.keys())