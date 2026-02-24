"""Runtime seed selector for Orpheus Music Transformer.

Picks the best seed MIDI file from the pre-built seed library based on
genre, falling back gracefully to the programmatic seeds if the library
hasn't been built yet.

Usage in music_service.py:
    from seed_selector import select_seed
    seed_path = select_seed(genre="drum_and_bass")
"""

import json
import logging
import os
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LIBRARY_DIR = Path(os.environ.get(
    "ORPHEUS_SEED_LIBRARY",
    str(Path(__file__).parent / "seed_library"),
))
_METADATA_PATH = _LIBRARY_DIR / "metadata.json"

_metadata: Optional[dict] = None
_genre_aliases: dict[str, str] = {}


def _load_metadata() -> Optional[dict]:
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
        # Build alias index for fuzzy matching
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


def select_seed(
    genre: str,
    *,
    rank: int = 0,
    randomize: bool = False,
) -> Optional[str]:
    """Select a seed MIDI file path from the library.

    Args:
        genre: Genre key (e.g. "drum_and_bass", "jazz", "trap").
        rank: Select the Nth-ranked seed (0 = best). Ignored if randomize=True.
        randomize: Pick a random seed from top candidates for variety.

    Returns:
        Absolute path to a seed .mid file, or None if unavailable.
    """
    meta = _load_metadata()
    if meta is None:
        return None

    genres = meta.get("genres", {})
    genre_key = genre.lower().replace(" ", "_").replace("-", "_")

    # Exact match
    seeds = genres.get(genre_key)

    # Fuzzy: try alias index
    if not seeds and genre_key in _genre_aliases:
        seeds = genres.get(_genre_aliases[genre_key])

    # Fuzzy: substring matching
    if not seeds:
        for gk in genres:
            if gk in genre_key or genre_key in gk:
                seeds = genres[gk]
                break

    # Fallback to "general" bucket
    if not seeds:
        seeds = genres.get("general")

    if not seeds:
        return None

    if randomize:
        entry = random.choice(seeds)
    else:
        idx = min(rank, len(seeds) - 1)
        entry = seeds[idx]

    seed_path = _LIBRARY_DIR / entry["file"]
    if not seed_path.exists():
        logger.warning(f"⚠️ Seed file missing: {seed_path}")
        return None

    logger.info(
        f"🌱 Selected seed: {entry.get('genre', genre_key)} "
        f"rank={entry.get('rank', rank)} "
        f"tokens={entry.get('token_count', '?')} "
        f"score={entry.get('score', '?')}"
    )
    return str(seed_path)
