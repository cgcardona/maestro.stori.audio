"""Note wire-format normalization utilities.

Storpheus returns notes in camelCase wire format (``startBeat``,
``durationBeats``); the internal pipeline uses snake_case (``start_beat``,
``duration_beats``).  This module provides a single boundary-crossing function
``_normalize_note`` that converts incoming MCP/Storpheus payloads to the
canonical internal format.

Design decision — explicit field extraction over dynamic key remapping
----------------------------------------------------------------------
Dynamic key remapping (``for k, v in note.items(): result[MAP[k]] = v``)
produces ``dict[str, object]`` at runtime and breaks static type-checking on
``NoteDict`` because TypedDicts are invariant in their key types.  Explicit
per-field extraction lets mypy verify that every field's type contract is
honoured at the normalization boundary without any casts or ``type: ignore``
comments.

Boundary position
-----------------
Call ``_normalize_note`` exactly once, at the point where a raw note payload
enters the pipeline (executor or MCP tool handler).  Downstream code always
works in snake_case and may assume the conversion has already happened.
"""

from __future__ import annotations

from maestro.contracts.json_types import NoteDict

# MCP tool payloads may use camelCase field names; canonical format is snake_case.
# Only the two timing fields differ; all other NoteDict keys are case-invariant.
_NOTE_KEY_MAP: dict[str, str] = {
    "startBeat": "start_beat",
    "durationBeats": "duration_beats",
}


def _normalize_note(note: NoteDict) -> NoteDict:
    """Return a copy of *note* with camelCase timing keys converted to snake_case.

    ``startBeat`` → ``start_beat``, ``durationBeats`` → ``duration_beats``.
    All other fields are preserved unchanged.  Operates on known NoteDict
    fields explicitly so the type system can verify correctness statically.
    """
    result: NoteDict = {}

    # Fixed-type MIDI fields
    pitch = note.get("pitch")
    if pitch is not None:
        result["pitch"] = pitch

    velocity = note.get("velocity")
    if velocity is not None:
        result["velocity"] = velocity

    channel = note.get("channel")
    if channel is not None:
        result["channel"] = channel

    layer = note.get("layer")
    if layer is not None:
        result["layer"] = layer

    # ID fields — preserve whichever case variant is present
    note_id = note.get("noteId")
    if note_id is not None:
        result["noteId"] = note_id

    note_id_snake = note.get("note_id")
    if note_id_snake is not None:
        result["note_id"] = note_id_snake

    track_id = note.get("trackId")
    if track_id is not None:
        result["trackId"] = track_id

    track_id_snake = note.get("track_id")
    if track_id_snake is not None:
        result["track_id"] = track_id_snake

    region_id = note.get("regionId")
    if region_id is not None:
        result["regionId"] = region_id

    region_id_snake = note.get("region_id")
    if region_id_snake is not None:
        result["region_id"] = region_id_snake

    # Timing — prefer existing snake_case; fall back to camelCase alias
    start_beat = note.get("start_beat")
    if start_beat is None:
        start_beat = note.get("startBeat")
    if start_beat is not None:
        result["start_beat"] = start_beat

    duration_beats = note.get("duration_beats")
    if duration_beats is None:
        duration_beats = note.get("durationBeats")
    if duration_beats is not None:
        result["duration_beats"] = duration_beats

    return result
