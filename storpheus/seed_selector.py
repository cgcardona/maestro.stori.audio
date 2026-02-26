"""Runtime seed selector for Orpheus Music Transformer.

Picks the best seed MIDI file from the pre-built seed library based on
genre and (optionally) target key.  When a target key is specified, the
selector prefers seeds whose detected key is closest, and returns the
transposition delta so the caller can shift the seed into the exact
requested key.

Usage in music_service.py:
    from seed_selector import select_seed
    result = select_seed(genre="drum_and_bass", target_key="Am")
    # result.path, result.transpose_semitones, result.detected_key
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LIBRARY_DIR = Path(os.environ.get(
    "ORPHEUS_SEED_LIBRARY",
    str(Path(__file__).parent / "seed_library"),
))
_METADATA_PATH = _LIBRARY_DIR / "metadata.json"

_metadata: dict[str, Any] | None = None
_genre_aliases: dict[str, str] = {}


@dataclass(frozen=True)
class SeedSelection:
    """Result of seed selection with optional key-aware transposition info."""
    path: str
    genre: str
    rank: int
    token_count: int
    score: float
    detected_key: str | None = None
    key_confidence: float = 0.0
    transpose_semitones: int = 0


def _load_metadata() -> dict[str, Any] | None:
    global _metadata, _genre_aliases
    if _metadata is not None:
        return _metadata
    if not _METADATA_PATH.exists():
        logger.info("⏭️ Seed library not found — using programmatic seeds")
        return None
    try:
        with open(_METADATA_PATH) as f:
            _metadata = json.load(f)
        genres = _metadata.get("genres", {})
        for genre_key in genres:
            _genre_aliases[genre_key] = genre_key
            for part in genre_key.split("_"):
                if part not in _genre_aliases:
                    _genre_aliases[part] = genre_key
        logger.info(f"✅ Seed library loaded: {len(genres)} genres")
        return _metadata
    except Exception:
        logger.warning("⚠️ Failed to load seed library metadata", exc_info=True)
        return None


def _resolve_genre_seeds(genre: str) -> list[dict[str, Any]] | None:
    """Find the seed list for a genre with fuzzy matching."""
    meta = _load_metadata()
    if meta is None:
        return None

    genres = meta.get("genres", {})
    genre_key = genre.lower().replace(" ", "_").replace("-", "_")

    seeds = genres.get(genre_key)

    if not seeds and genre_key in _genre_aliases:
        seeds = genres.get(_genre_aliases[genre_key])

    if not seeds:
        for gk in genres:
            if gk in genre_key or genre_key in gk:
                seeds = genres[gk]
                break

    if not seeds:
        seeds = genres.get("general")

    return list(seeds) if seeds else None


def _key_distance(seed_key: str | None, target_key: str) -> int:
    """Compute the absolute transposition distance in semitones [0..6].

    Returns 7 when the seed has no detected key (sort to end).
    """
    if not seed_key:
        return 7

    from key_detection import parse_key_string, transpose_distance

    parsed_seed = parse_key_string(seed_key)
    parsed_target = parse_key_string(target_key)
    if parsed_seed is None or parsed_target is None:
        return 7

    return abs(transpose_distance(
        parsed_seed[0], parsed_seed[1],
        parsed_target[0], parsed_target[1],
    ))


def select_seed(
    genre: str,
    *,
    target_key: str | None = None,
    rank: int = 0,
    randomize: bool = False,
) -> str | None:
    """Select a seed MIDI file path from the library.

    Backward-compatible wrapper that returns just the path string.
    For key-aware selection with transposition info, use
    ``select_seed_with_key()``.
    """
    result = select_seed_with_key(
        genre,
        target_key=target_key,
        rank=rank,
        randomize=randomize,
    )
    return result.path if result else None


def select_seed_with_key(
    genre: str,
    *,
    target_key: str | None = None,
    rank: int = 0,
    randomize: bool = False,
) -> SeedSelection | None:
    """Select a seed MIDI file with full key-awareness.

    When *target_key* is provided (e.g. ``"Am"``, ``"C major"``):
    1. Seeds are sorted by key distance to the target.
    2. The closest-key seed is preferred.
    3. ``transpose_semitones`` indicates how many semitones to shift the
       seed to reach the exact target key.

    When *target_key* is None, behaves identically to the old selector.

    Returns ``SeedSelection`` with path and transposition info, or None.
    """
    seeds = _resolve_genre_seeds(genre)
    if not seeds:
        return None

    if target_key:
        sorted_seeds = sorted(
            seeds,
            key=lambda s: (
                _key_distance(s.get("key"), target_key),
                -s.get("score", 0),
            ),
        )
        if randomize:
            best_dist = _key_distance(sorted_seeds[0].get("key"), target_key)
            top_tier = [s for s in sorted_seeds if _key_distance(s.get("key"), target_key) == best_dist]
            entry = random.choice(top_tier)
        else:
            idx = min(rank, len(sorted_seeds) - 1)
            entry = sorted_seeds[idx]
    else:
        if randomize:
            entry = random.choice(seeds)
        else:
            idx = min(rank, len(seeds) - 1)
            entry = seeds[idx]

    seed_path = _LIBRARY_DIR / entry["file"]
    if not seed_path.exists():
        logger.warning(f"⚠️ Seed file missing: {seed_path}")
        return None

    # Compute transposition if target_key requested
    transpose = 0
    seed_key_str = entry.get("key")
    if target_key and seed_key_str:
        from key_detection import parse_key_string, transpose_distance as td
        parsed_seed = parse_key_string(seed_key_str)
        parsed_target = parse_key_string(target_key)
        if parsed_seed and parsed_target:
            transpose = td(
                parsed_seed[0], parsed_seed[1],
                parsed_target[0], parsed_target[1],
            )

    logger.info(
        f"🌱 Selected seed: {entry.get('genre', genre)} "
        f"rank={entry.get('rank', rank)} "
        f"tokens={entry.get('token_count', '?')} "
        f"score={entry.get('score', '?')} "
        f"key={seed_key_str or '?'} "
        f"transpose={transpose:+d}"
    )

    return SeedSelection(
        path=str(seed_path),
        genre=entry.get("genre", genre),
        rank=entry.get("rank", rank),
        token_count=entry.get("token_count", 0),
        score=entry.get("score", 0.0),
        detected_key=seed_key_str,
        key_confidence=entry.get("key_confidence", 0.0),
        transpose_semitones=transpose,
    )
