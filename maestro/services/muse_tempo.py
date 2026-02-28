"""Muse Tempo Service — read and annotate tempo (BPM) on Muse CLI commits.

Provides:

- ``extract_bpm_from_midi`` — pure function: bytes → BPM or None.
  Parses the MIDI Set Tempo meta-event (FF 51 03 tt tt tt).
  Returns the BPM from the *first* tempo event found, or ``None`` if
  the file contains no tempo events (uses MIDI default 120 BPM implicitly).

- ``detect_tempo_from_snapshot`` — highest BPM of any MIDI file in a
  snapshot manifest; ``None`` if no MIDI files or no tempo events found.

- ``MuseTempoResult`` — named result type for a single commit tempo query.

- ``MuseTempoHistoryEntry`` — one row in a ``--history`` traversal.

- ``build_tempo_history`` — ordered list of history entries, newest-first.

Boundary rules:
  - Must NOT import StateStore, EntityRegistry, or app.core.*.
  - Must NOT import LLM handlers or maestro_* modules.
  - Pure data — no FastAPI, no DB access, no side effects beyond logging.
"""
from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass

from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

# MIDI meta-event markers
_MIDI_HEADER = b"MThd"
_META_TEMPO_TYPE = 0x51
_META_EVENT_MARKER = 0xFF
# Default MIDI tempo: 500000 microseconds/beat = 120 BPM
_DEFAULT_MIDI_USPB = 500_000
_MICROSECONDS_PER_MINUTE = 60_000_000


# ---------------------------------------------------------------------------
# Named result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MuseTempoResult:
    """Result of a ``muse tempo [<commit>]`` query.

    ``tempo_bpm`` is the annotated value stored via ``--set``.
    ``detected_bpm`` is extracted from MIDI files in the snapshot.
    Either may be ``None`` when the data is unavailable.
    """

    commit_id: str
    branch: str
    message: str
    tempo_bpm: float | None
    """Explicitly annotated BPM (from ``muse tempo --set``)."""
    detected_bpm: float | None
    """Auto-detected BPM from MIDI tempo map events in the snapshot."""

    @property
    def effective_bpm(self) -> float | None:
        """Annotated value takes precedence; falls back to detected."""
        return self.tempo_bpm if self.tempo_bpm is not None else self.detected_bpm


@dataclass(frozen=True)
class MuseTempoHistoryEntry:
    """One row in a ``muse tempo --history`` traversal.

    ``delta_bpm`` is the signed change vs. the previous commit's effective
    BPM, or ``None`` for the oldest commit (no ancestor to compare against).
    """

    commit_id: str
    message: str
    effective_bpm: float | None
    delta_bpm: float | None


# ---------------------------------------------------------------------------
# Pure MIDI parsing
# ---------------------------------------------------------------------------


def extract_bpm_from_midi(data: bytes) -> float | None:
    """Return the BPM from the first Set Tempo meta-event in *data*.

    Parses a raw MIDI byte string looking for the standard Set Tempo
    meta-event (``0xFF 0x51 0x03 <3-byte big-endian microseconds/beat>``).
    Returns ``None`` when:

    - *data* is not a valid MIDI file (no ``MThd`` header).
    - No Set Tempo event is present (implicit 120 BPM per MIDI spec, but
      we return ``None`` rather than assume, so callers can distinguish
      "no event found" from "120 BPM was set explicitly").

    Only the *first* tempo event is returned.  For rubato detection
    (multiple tempo events) use ``detect_all_tempos_from_midi`` below.
    """
    if not data[:4] == _MIDI_HEADER:
        return None

    i = 0
    length = len(data)
    while i < length - 5:
        if data[i] == _META_EVENT_MARKER and data[i + 1] == _META_TEMPO_TYPE:
            # FF 51 03 tt tt tt
            meta_len = data[i + 2]
            if meta_len >= 3 and i + 2 + meta_len < length:
                raw_uspb: int = (data[i + 3] << 16) | (data[i + 4] << 8) | data[i + 5]
                if raw_uspb > 0:
                    bpm = _MICROSECONDS_PER_MINUTE / raw_uspb
                    logger.debug("✅ MIDI tempo event: %d µs/beat → %.2f BPM", raw_uspb, bpm)
                    return round(bpm, 2)
        i += 1
    return None


def detect_all_tempos_from_midi(data: bytes) -> list[float]:
    """Return BPM for every Set Tempo meta-event in *data*, in order.

    Used for drift (rubato) detection — a file with a single entry has
    a constant tempo; multiple entries indicate rubato or tempo changes.
    Returns an empty list if *data* is not a valid MIDI file or has no
    tempo events.
    """
    if not data[:4] == _MIDI_HEADER:
        return []

    tempos: list[float] = []
    i = 0
    length = len(data)
    while i < length - 5:
        if data[i] == _META_EVENT_MARKER and data[i + 1] == _META_TEMPO_TYPE:
            meta_len = data[i + 2]
            if meta_len >= 3 and i + 2 + meta_len < length:
                raw_uspb: int = (data[i + 3] << 16) | (data[i + 4] << 8) | data[i + 5]
                if raw_uspb > 0:
                    tempos.append(round(_MICROSECONDS_PER_MINUTE / raw_uspb, 2))
        i += 1
    return tempos


def detect_tempo_from_snapshot(
    manifest: dict[str, str],
    workdir: pathlib.Path,
) -> float | None:
    """Detect tempo from MIDI files listed in a snapshot manifest.

    Iterates files in the manifest, reads those with a ``.mid`` or
    ``.midi`` suffix, and returns the BPM from the first tempo event
    found across all files.  Files are processed in sorted order for
    determinism.

    Returns ``None`` when no MIDI files are present or none contain a
    Set Tempo meta-event.
    """
    for rel_path in sorted(manifest.keys()):
        if not (rel_path.lower().endswith(".mid") or rel_path.lower().endswith(".midi")):
            continue
        abs_path = workdir / rel_path
        if not abs_path.is_file():
            continue
        try:
            data = abs_path.read_bytes()
        except OSError as exc:
            logger.warning("⚠️ Could not read MIDI file %s: %s", rel_path, exc)
            continue
        bpm = extract_bpm_from_midi(data)
        if bpm is not None:
            logger.debug("✅ Detected %.2f BPM from %s", bpm, rel_path)
            return bpm
    return None


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------


def build_tempo_history(
    commits: list[MuseCliCommit],
) -> list[MuseTempoHistoryEntry]:
    """Build a tempo history list from a newest-first commit chain.

    Each entry records the commit ID, message, effective BPM, and the
    signed delta vs. the previous (older) commit.  The oldest commit has
    ``delta_bpm=None`` because it has no ancestor.

    *commits* must be ordered newest-first (as returned by ``_load_commits``
    in ``commands/log.py``).  The delta is newest-relative-to-older so a
    BPM increase shows as a positive delta.

    The effective BPM for each commit is the annotated ``tempo_bpm`` stored
    in ``metadata``, if present; auto-detected values are not stored in the
    DB, so history only reflects explicitly set annotations.
    """
    
    # Walk oldest→newest to compute deltas, then reverse for output.
    oldest_first = list(reversed(commits))
    bpms: list[float | None] = []
    for commit in oldest_first:
        meta: dict[str, object] = commit.commit_metadata or {}
        bpm_raw = meta.get("tempo_bpm")
        bpm: float | None = float(bpm_raw) if isinstance(bpm_raw, (int, float)) else None
        bpms.append(bpm)

    result: list[MuseTempoHistoryEntry] = []
    for idx, commit in enumerate(oldest_first):
        bpm = bpms[idx]
        if idx == 0:
            delta: float | None = None
        else:
            older = bpms[idx - 1]
            if bpm is not None and older is not None:
                delta = round(bpm - older, 2)
            else:
                delta = None
        result.append(
            MuseTempoHistoryEntry(
                commit_id=commit.commit_id,
                message=commit.message,
                effective_bpm=bpm,
                delta_bpm=delta,
            )
        )

    # Return newest-first (matches log convention)
    return list(reversed(result))
