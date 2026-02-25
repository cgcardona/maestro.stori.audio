"""Note normalization utilities (camelCase → snake_case)."""

from __future__ import annotations

from typing import Any

# MCP tool payloads may use camelCase field names; canonical format is snake_case.
_NOTE_KEY_MAP: dict[str, str] = {
    "startBeat": "start_beat",
    "durationBeats": "duration_beats",
}


def _normalize_note(note: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *note* with canonical snake_case field names."""
    out: dict[str, Any] = {}
    for k, v in note.items():
        out[_NOTE_KEY_MAP.get(k, k)] = v
    return out
