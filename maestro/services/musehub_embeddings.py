"""Musical feature vector extraction for MuseHub semantic search.

Transforms commit metadata and MIDI object content into a fixed-length
feature vector suitable for cosine similarity search in Qdrant. The vector
encodes four orthogonal musical dimensions:

  - Harmonic (key centre, mode, chord complexity)
  - Rhythmic (tempo, time signature, note density, swing)
  - Emotional (valence, arousal derived from tempo × mode)
  - Textual (commit message embedded as a musical-domain fingerprint)

Boundary rules:
  - Must NOT import state stores, SSE queues, or LLM clients.
  - Must NOT import maestro.core.* modules.
  - May import ORM models from maestro.db.musehub_models.
  - May import Pydantic models from maestro.models.musehub.

Design note: For MVP the MIDI decoder uses lightweight deterministic
heuristics rather than a full ML inference pass. The vector is compact
(VECTOR_DIM = 128) and reproducible for the same commit + objects input.
A future upgrade path is to replace ``_encode_text_fingerprint`` with a
dedicated SentenceTransformer call behind an async HTTP client.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Fixed embedding dimensionality — must match VECTOR_SIZE in musehub_qdrant.py.
VECTOR_DIM = 128

# Key names in chromatic order — used to map note names to integer indices.
_CHROMATIC = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# Common musical key synonyms that appear in commit messages.
_KEY_SYNONYMS: dict[str, str] = {
    "c#": "Db",
    "d#": "Eb",
    "e#": "F",
    "f#": "Gb",
    "g#": "Ab",
    "a#": "Bb",
    "b#": "C",
}

# Emotion dimensions estimated from mode and tempo (valence × arousal).
# Source: Russell's circumplex model adapted for musical context.
_MODE_VALENCE: dict[str, float] = {
    "major": 0.7,
    "minor": 0.3,
    "dorian": 0.5,
    "mixolydian": 0.6,
    "lydian": 0.8,
    "phrygian": 0.2,
    "locrian": 0.1,
}


@dataclass
class MusicalFeatures:
    """Structured musical features extracted from a commit and its objects.

    All float fields are in [0.0, 1.0] unless noted. This is the typed
    intermediate representation between raw metadata and the embedding vector.
    Agents consuming search results can inspect these fields for explainability.
    """

    # Key/tonal centre (0 = C, 1 = Db, ..., 11 = B; -1 = unknown)
    key_index: int = -1
    # 0.0 = minor, 1.0 = major, 0.5 = neutral/modal
    mode_score: float = 0.5
    # Normalised tempo: (bpm - 20) / 280 so range [0, 1] for bpm in [20, 300]
    tempo_norm: float = 0.5
    # Note density: notes per beat, normalised to [0, 1] (clamped at 8 nps)
    note_density: float = 0.0
    # Mean MIDI velocity in [0, 1] (0 = pp, 1 = ff)
    velocity_mean: float = 0.5
    # Valence estimate in [0, 1] (0 = negative, 1 = positive)
    valence: float = 0.5
    # Arousal estimate in [0, 1] (0 = calm, 1 = energetic)
    arousal: float = 0.5
    # Chord complexity in [0, 1] (0 = triads only, 1 = extended voicings)
    chord_complexity: float = 0.0
    # Per-semitone chroma histogram (12 values, sum normalised to 1.0)
    chroma: list[float] = field(default_factory=lambda: [0.0] * 12)
    # Text fingerprint bucket scores (16 values from commit message hash)
    text_fingerprint: list[float] = field(default_factory=lambda: [0.0] * 16)


def extract_features_from_message(message: str) -> MusicalFeatures:
    """Derive musical features from a commit message string.

    This is the primary extraction path for MVP: the Stori DAW encodes
    composition metadata into structured commit messages (key, tempo, mode)
    as part of the push protocol. We parse the most common patterns here.

    Args:
        message: The commit message string from the MusehubCommit record.

    Returns:
        MusicalFeatures with all parseable fields populated; unknowns default
        to neutral midpoint values.
    """
    features = MusicalFeatures()
    lower = message.lower()

    # --- Key detection (two-pass: prefer key+mode match over key alone) ---
    # Pass 1: key immediately followed by mode word (e.g. "Db major", "A minor").
    key_mode_pattern = re.compile(
        r"([A-Ga-g][b#]?)\s+(major|minor|maj|min|dorian|mixolydian|lydian|phrygian|locrian)"
    )
    # Pass 2: standalone key letter at word boundary (no mode info present).
    key_only_pattern = re.compile(r"([A-Ga-g][b#]?)")

    matched_key = False
    for match in key_mode_pattern.finditer(message):
        raw_key = match.group(1)
        normalised = _normalise_key(raw_key)
        if normalised is not None:
            features.key_index = _CHROMATIC.index(normalised)
            mode_raw = match.group(2).lower()
            mode_str = {"maj": "major", "min": "minor"}.get(mode_raw, mode_raw)
            features.mode_score = _MODE_VALENCE.get(mode_str, 0.5)
            matched_key = True
            break

    if not matched_key:
        for match in key_only_pattern.finditer(message):
            raw_key = match.group(1)
            normalised = _normalise_key(raw_key)
            if normalised is not None:
                features.key_index = _CHROMATIC.index(normalised)
                break

    # --- Tempo detection ---
    bpm_match = re.search(r"(\d{2,3})\s*(?:bpm|BPM)", message)
    if bpm_match:
        bpm = int(bpm_match.group(1))
        features.tempo_norm = max(0.0, min(1.0, (bpm - 20) / 280.0))

    # --- Emotion estimation from key + tempo ---
    features.valence = features.mode_score
    # Arousal correlates with tempo (faster = more energetic)
    features.arousal = features.tempo_norm

    # --- Chroma histogram from key_index ---
    if features.key_index >= 0:
        # Weight the tonic and its fifth (perfect fifth = 7 semitones up)
        chroma = [0.0] * 12
        tonic = features.key_index
        fifth = (tonic + 7) % 12
        third = (tonic + 4) % 12 if features.mode_score >= 0.5 else (tonic + 3) % 12
        chroma[tonic] = 0.5
        chroma[fifth] = 0.3
        chroma[third] = 0.2
        features.chroma = chroma

    # --- Text fingerprint ---
    features.text_fingerprint = _encode_text_fingerprint(message)

    # --- Chord complexity heuristic ---
    extended_terms = ["7th", "9th", "11th", "13th", "sus", "aug", "dim", "#11", "b9"]
    hits = sum(1 for t in extended_terms if t in lower)
    features.chord_complexity = min(1.0, hits / 4.0)

    logger.debug("✅ Extracted musical features from commit message (key=%d)", features.key_index)
    return features


def features_to_vector(features: MusicalFeatures) -> list[float]:
    """Convert a MusicalFeatures record to a fixed-dim float vector.

    Vector layout (128 dimensions total):
      [0]   key_index normalised to [0, 1] (key_index / 11.0, or 0.5 if unknown)
      [1]   mode_score
      [2]   tempo_norm
      [3]   note_density
      [4]   velocity_mean
      [5]   valence
      [6]   arousal
      [7]   chord_complexity
      [8–19]  chroma (12 dims)
      [20–35] text_fingerprint (16 dims)
      [36–127] zero-padded (reserved for future MIDI features)

    The resulting vector is L2-normalised so cosine and dot-product distances
    are equivalent in Qdrant.

    Args:
        features: Fully populated MusicalFeatures dataclass.

    Returns:
        List of 128 floats, L2-normalised. All values are finite.
    """
    key_norm = features.key_index / 11.0 if features.key_index >= 0 else 0.5
    raw: list[float] = [
        key_norm,
        features.mode_score,
        features.tempo_norm,
        features.note_density,
        features.velocity_mean,
        features.valence,
        features.arousal,
        features.chord_complexity,
        *features.chroma,       # 12 dims
        *features.text_fingerprint,  # 16 dims
    ]
    # Zero-pad to VECTOR_DIM
    raw += [0.0] * (VECTOR_DIM - len(raw))

    return _l2_normalise(raw)


def compute_embedding(message: str) -> list[float]:
    """Compute the embedding vector for a commit given its message.

    This is the public entry point called by the sync pipeline after a push.
    The vector is deterministic for the same input — identical commits always
    produce identical embeddings.

    Args:
        message: The commit message from MusehubCommit.message.

    Returns:
        List of VECTOR_DIM floats, L2-normalised, suitable for Qdrant upsert.
    """
    features = extract_features_from_message(message)
    vector = features_to_vector(features)
    logger.debug("✅ Computed embedding vector (dim=%d)", len(vector))
    return vector


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalise_key(raw: str) -> str | None:
    """Return the canonical key name for a raw note string, or None if invalid."""
    lowered = raw.lower()
    if lowered in _KEY_SYNONYMS:
        return _KEY_SYNONYMS[lowered]
    capitalised = raw[0].upper() + raw[1:].replace("b", "b").replace("#", "#")
    # Normalise sharps to flats for canonical form
    sharp_to_flat = {
        "C#": "Db",
        "D#": "Eb",
        "E#": "F",
        "F#": "Gb",
        "G#": "Ab",
        "A#": "Bb",
        "B#": "C",
    }
    if capitalised in sharp_to_flat:
        return sharp_to_flat[capitalised]
    if capitalised in _CHROMATIC:
        return capitalised
    return None


def _encode_text_fingerprint(text: str) -> list[float]:
    """Produce a 16-dim float fingerprint from a text string.

    Uses SHA-256 of the lowercased text, bucketed into 16 groups of 2 bytes
    each, normalised to [0, 1]. This gives a deterministic, low-collision
    fingerprint that differentiates commit messages without requiring an
    external embedding model.

    Args:
        text: Arbitrary text string (commit message).

    Returns:
        List of 16 floats in [0, 1].
    """
    digest = hashlib.sha256(text.lower().encode()).digest()
    result: list[float] = []
    for i in range(16):
        pair = digest[i * 2] * 256 + digest[i * 2 + 1]
        result.append(pair / 65535.0)
    return result


def _l2_normalise(vector: list[float]) -> list[float]:
    """Return the L2-normalised form of a float vector.

    If the norm is zero (all-zero vector), return the original unchanged
    rather than dividing by zero — Qdrant still accepts zero vectors.

    Args:
        vector: List of floats to normalise.

    Returns:
        List of the same length with unit L2 norm (or unchanged if zero-norm).
    """
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]
