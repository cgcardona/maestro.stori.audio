"""Canonical type definitions for JSON data and music-domain dicts.

Replaces pervasive ``dict[str, Any]`` with precise structural types.
Import from here instead of re-defining shapes ad hoc.

Hierarchy:
  JSONScalar / JSONValue — arbitrary JSON (use sparingly)
  NoteDict              — a single MIDI note (camelCase wire format)
  InternalNoteDict      — a single MIDI note (snake_case internal storage)
  CCEventDict           — a MIDI CC event
  PitchBendDict         — a MIDI pitch bend event
  AftertouchDict        — a MIDI aftertouch event (channel or poly)
  StorpheusResultBucket — return shape of normalize_storpheus_tool_calls
  ToolCallDict          — SSE tool call payload
  TrackSummaryDict      — summary.final track info
  EffectSummaryDict     — summary.final effect info
  NoteChangeDict        — before/after shape in NoteChangeSchema
  RegionMetadataWire    — region position metadata (camelCase, handler path)
  RegionMetadataDB      — region position metadata (snake_case, database path)

  Region event map aliases (used across Muse/StateStore):
  RegionNotesMap        — dict[str, list[NoteDict]]     (region_id → notes)
  RegionCCMap           — dict[str, list[CCEventDict]]  (region_id → CC events)
  RegionPitchBendMap    — dict[str, list[PitchBendDict]](region_id → pitch bends)
  RegionAftertouchMap   — dict[str, list[AftertouchDict]](region_id → aftertouch)
"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict

# ═══════════════════════════════════════════════════════════════════════════════
# Generic JSON types — use ONLY when the shape is truly unknown
# ═══════════════════════════════════════════════════════════════════════════════

JSONScalar = str | int | float | bool | None
"""A JSON leaf value."""

JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
"""Recursive JSON value — use instead of ``Any`` for arbitrary JSON payloads."""

JSONObject = dict[str, JSONValue]
"""A JSON object — use instead of ``dict[str, Any]`` when keys are unknown."""


# ═══════════════════════════════════════════════════════════════════════════════
# MIDI note types
# ═══════════════════════════════════════════════════════════════════════════════


class NoteDict(TypedDict, total=False):
    """A single MIDI note — accepts both camelCase (wire) and snake_case (internal).

    Notes flow through many layers in both naming conventions.
    Using a single dict with all valid keys avoids friction.
    """

    pitch: int
    velocity: int
    channel: int
    # camelCase (wire format from DAW / to DAW)
    startBeat: float  # noqa: N815
    durationBeats: float  # noqa: N815
    noteId: str  # noqa: N815
    trackId: str  # noqa: N815
    regionId: str  # noqa: N815
    # snake_case (internal storage after normalization)
    start_beat: float
    duration_beats: float
    note_id: str
    track_id: str
    region_id: str
    # drum renderer layer tag (core, timekeepers, fills, ghost_layer, …)
    layer: str


InternalNoteDict = NoteDict


# ═══════════════════════════════════════════════════════════════════════════════
# MIDI expression event types
# ═══════════════════════════════════════════════════════════════════════════════


class CCEventDict(TypedDict):
    """A single MIDI Control Change event."""

    cc: int
    beat: float
    value: int


class PitchBendDict(TypedDict):
    """A single MIDI pitch bend event."""

    beat: float
    value: int


class AftertouchDict(TypedDict, total=False):
    """A MIDI aftertouch event (channel pressure or poly key pressure).

    ``pitch`` is present only for polyphonic aftertouch.
    """

    beat: float
    value: int
    pitch: int


# ═══════════════════════════════════════════════════════════════════════════════
# Composition section types
# ═══════════════════════════════════════════════════════════════════════════════


class SectionDict(TypedDict, total=False):
    """A composition section — verse, chorus, bridge, etc.

    ``name``, ``start_beat``, and ``length_beats`` are always present;
    ``description`` and ``per_track_description`` are added by the
    section planner but omitted in some internal paths.
    """

    name: str
    start_beat: float
    length_beats: float
    description: str
    per_track_description: dict[str, str]


# ═══════════════════════════════════════════════════════════════════════════════
# Storpheus adapter types
# ═══════════════════════════════════════════════════════════════════════════════


class StorpheusResultBucket(TypedDict):
    """Return shape of ``normalize_storpheus_tool_calls``."""

    notes: list[NoteDict]
    cc_events: list[CCEventDict]
    pitch_bends: list[PitchBendDict]
    aftertouch: list[AftertouchDict]


# ═══════════════════════════════════════════════════════════════════════════════
# SSE / protocol types
# ═══════════════════════════════════════════════════════════════════════════════


class ToolCallDict(TypedDict):
    """Shape of a collected tool call dict in CompleteEvent.tool_calls.

    Every producer (editing handler, composing coordinator, agent teams)
    writes exactly ``{"tool": "stori_xxx", "params": {...}}``.

    ``params`` is ``dict[str, Any]`` because tool call arguments are
    LLM-generated and genuinely polymorphic — and Pydantic V2 cannot
    resolve the recursive ``JSONValue`` alias at model-build time.
    """

    tool: str
    params: dict[str, Any]


class TrackSummaryDict(TypedDict, total=False):
    """Track info in SummaryFinalEvent.tracks_created / tracks_reused."""

    name: str
    trackId: str  # noqa: N815
    instrument: str
    color: str


class EffectSummaryDict(TypedDict, total=False):
    """Effect info in SummaryFinalEvent.effects_added."""

    type: str
    trackId: str  # noqa: N815
    name: str


class SectionSummaryDict(TypedDict, total=False):
    """Per-section summary in batch_complete tool result (agent teams)."""

    name: str
    status: str
    regionId: str | None  # noqa: N815
    notesGenerated: int  # noqa: N815
    error: str


class CCEnvelopeDict(TypedDict, total=False):
    """CC envelope info in SummaryFinalEvent.cc_envelopes."""

    cc: int
    trackId: str  # noqa: N815
    name: str
    pointCount: int  # noqa: N815


# ═══════════════════════════════════════════════════════════════════════════════
# Variation / note change types
# ═══════════════════════════════════════════════════════════════════════════════


class NoteChangeDict(TypedDict, total=False):
    """Before/after shape in NoteChangeSchema and ControllerChangeSchema."""

    pitch: int
    startBeat: float  # noqa: N815
    durationBeats: float  # noqa: N815
    velocity: int
    channel: int
    cc: int
    beat: float
    value: int


# ═══════════════════════════════════════════════════════════════════════════════
# Generation constraints (typed version of the dict built in StorpheusBackend)
# ═══════════════════════════════════════════════════════════════════════════════


class GenerationConstraintsDict(TypedDict, total=False):
    """Serialized GenerationConstraints — the dict form sent to Orpheus."""

    drum_density: float
    subdivision: int
    swing_amount: float
    register_center: int
    register_spread: int
    rest_density: float
    leap_probability: float
    chord_extensions: bool
    borrowed_chord_probability: float
    harmonic_rhythm_bars: float
    velocity_floor: int
    velocity_ceiling: int


class IntentGoalDict(TypedDict):
    """A single intent goal sent to Orpheus."""

    name: str
    weight: float
    constraint_type: str


# ═══════════════════════════════════════════════════════════════════════════════
# Entity metadata
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# State store serialization types
# ═══════════════════════════════════════════════════════════════════════════════


class StateEventData(TypedDict, total=False):
    """Payload of a StateStore event's ``data`` field.

    Not all keys are present in every event — ``total=False`` allows
    the various EventType payloads to share one TypedDict.
    """

    name: str
    metadata: dict[str, object]
    parent_track_id: str
    description: str
    event_count: int
    rolled_back_events: int
    notes_count: int
    notes: list[NoteDict]
    old_tempo: int
    new_tempo: int
    old_key: str
    new_key: str
    effect_type: str


# ═══════════════════════════════════════════════════════════════════════════════
# Region metadata — position + name for a single region
# ═══════════════════════════════════════════════════════════════════════════════

class RegionMetadataWire(TypedDict, total=False):
    """Region position metadata in camelCase (handler → storage path)."""

    startBeat: float
    durationBeats: float
    name: str


class RegionMetadataDB(TypedDict, total=False):
    """Region position metadata in snake_case (database path)."""

    start_beat: float
    duration_beats: float
    name: str


# ═══════════════════════════════════════════════════════════════════════════════
# Region event map aliases
#
# These replace the repeated pattern ``dict[str, list[XxxDict]]`` across the
# Muse VCS, StateStore, and variation pipeline.  The key is always a region_id
# string; the value is the ordered list of events for that region.
# ═══════════════════════════════════════════════════════════════════════════════

RegionNotesMap = dict[str, list[NoteDict]]
"""Maps region_id → ordered list of MIDI notes for that region."""

RegionCCMap = dict[str, list[CCEventDict]]
"""Maps region_id → ordered list of MIDI CC events for that region."""

RegionPitchBendMap = dict[str, list[PitchBendDict]]
"""Maps region_id → ordered list of MIDI pitch bend events for that region."""

RegionAftertouchMap = dict[str, list[AftertouchDict]]
"""Maps region_id → ordered list of MIDI aftertouch events for that region."""
