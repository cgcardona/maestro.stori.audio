"""Canonical type definitions for JSON data and music-domain dicts.

Replaces pervasive ``dict[str, Any]`` with precise structural types.
Import from here instead of re-defining shapes ad hoc.

Hierarchy:
  JSONScalar / JSONValue — arbitrary JSON (use sparingly)
  NoteDict              — a single MIDI note (camelCase wire format)
  InternalNoteDict      — a single MIDI note (snake_case internal storage)
  CCEventDict           — a MIDI CC event  (cc, beat, value)
  PitchBendDict         — a MIDI pitch bend event (beat, value)
  AftertouchDict        — a MIDI aftertouch event (beat, value[, pitch])
  StorpheusResultBucket — return shape of normalize_storpheus_tool_calls
  ToolCallDict          — SSE tool call payload
  TrackSummaryDict      — summary.final track info
  EffectSummaryDict     — summary.final effect info
  NoteChangeDict        — MIDI note snapshot (before/after in NoteChangeEntryDict)
  NoteChangeEntryDict   — wire shape of one noteChanges entry (noteId, changeType, before, after)
  RegionMetadataWire    — region position metadata (camelCase, handler path)
  RegionMetadataDB      — region position metadata (snake_case, database path)

  Region event map aliases (used across Muse/StateStore):
  RegionNotesMap        — dict[str, list[NoteDict]]      (region_id → notes)
  RegionCCMap           — dict[str, list[CCEventDict]]   (region_id → CC events)
  RegionPitchBendMap    — dict[str, list[PitchBendDict]] (region_id → pitch bends)
  RegionAftertouchMap   — dict[str, list[AftertouchDict]](region_id → aftertouch)

  Protocol introspection aliases (used by app/protocol/responses.py):
  EventJsonSchema       — dict[str, object]              (single event JSON Schema)
  EventSchemaMap        — dict[str, EventJsonSchema]     (event_type → JSON Schema)
  EnumDefinitionMap     — dict[str, list[str]]           (enum name → member values)
"""

from __future__ import annotations

from typing import Literal

from typing_extensions import Required, TypedDict

from app.contracts.midi_types import (
    BeatDuration,
    BeatPosition,
    MidiAftertouchValue,
    MidiCC,
    MidiCCValue,
    MidiChannel,
    MidiPitch,
    MidiPitchBend,
    MidiVelocity,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Generic JSON types — use ONLY when the shape is truly unknown
#
# PYDANTIC COMPATIBILITY RULE
# ───────────────────────────
# JSONValue and JSONObject are *mypy-only* type aliases.  JSONValue is
# recursive: it contains ``list["JSONValue"]`` and ``dict[str, "JSONValue"]``
# string forward references.  Pydantic v2 must resolve those strings at runtime
# against the importing module's namespace, and fails when they cross module
# boundaries — producing a ``PydanticUserError: not fully defined`` at
# instantiation time.
#
# Rule: **never use JSONValue or JSONObject in a Pydantic BaseModel field.**
#
# Where to use each:
#   JSONValue / JSONObject  — TypedDicts, dataclasses, function signatures.
#                             Pure mypy land; Pydantic never sees them.
#   dict[str, object]       — Pydantic BaseModel fields that must hold opaque
#                             external JSON (e.g. pre-validation LLM output,
#                             external API payloads).  ``object`` is not ``Any``
#                             — mypy requires explicit narrowing before use —
#                             but carries no forward refs that Pydantic cannot
#                             resolve.
# ═══════════════════════════════════════════════════════════════════════════════

JSONScalar = str | int | float | bool | None
"""A JSON leaf value."""

JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
"""Recursive JSON value — use instead of ``Any`` for arbitrary JSON payloads.

See module docstring: do NOT use this in Pydantic BaseModel fields.
"""

JSONObject = dict[str, JSONValue]
"""A JSON object — use instead of ``dict[str, Any]`` when keys are unknown.

See module docstring: do NOT use this in Pydantic BaseModel fields.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# MIDI note types
# ═══════════════════════════════════════════════════════════════════════════════


class NoteDict(TypedDict, total=False):
    """A single MIDI note — accepts both camelCase (wire) and snake_case (internal).

    Notes flow through many layers in both naming conventions.
    Using a single dict with all valid keys avoids friction.

    Field ranges (enforced by Pydantic models at system boundaries):
        pitch         0–127   MIDI note number
        velocity      0–127   note-off at 0; audible range 1–127
        channel       0–15    MIDI channel (drums = 9)
        startBeat     ≥ 0.0   beat position (fractional allowed)
        durationBeats > 0.0   beat duration (fractional allowed)
    """

    pitch: MidiPitch
    velocity: MidiVelocity
    channel: MidiChannel
    # camelCase (wire format from DAW / to DAW)
    startBeat: BeatPosition  # noqa: N815
    durationBeats: BeatDuration  # noqa: N815
    noteId: str  # noqa: N815
    trackId: str  # noqa: N815
    regionId: str  # noqa: N815
    # snake_case (internal storage after normalization)
    start_beat: BeatPosition
    duration_beats: BeatDuration
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
    """A single MIDI Control Change event.

    Field ranges:
        cc    0–127   controller number
        beat  ≥ 0.0   beat position (fractional allowed)
        value 0–127   controller value
    """

    cc: MidiCC
    beat: BeatPosition
    value: MidiCCValue


class PitchBendDict(TypedDict):
    """A single MIDI pitch bend event.

    Field ranges:
        beat  ≥ 0.0          beat position (fractional allowed)
        value −8192–8191     14-bit signed; 0 = centre, ±8192 = full deflection
    """

    beat: BeatPosition
    value: MidiPitchBend


class AftertouchDict(TypedDict, total=False):
    """A MIDI aftertouch event (channel pressure or poly key pressure).

    ``beat`` and ``value`` are always present.
    ``pitch`` is present only for polyphonic (per-key) aftertouch.

    Field ranges:
        beat  ≥ 0.0   beat position (fractional allowed)
        value 0–127   pressure value
        pitch 0–127   note number (poly aftertouch only)
    """

    beat: Required[BeatPosition]
    value: Required[MidiAftertouchValue]
    pitch: MidiPitch


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

    ``params`` is ``dict[str, object]`` because tool call arguments are
    LLM-generated and genuinely polymorphic — we use ``object`` rather
    than ``Any`` so callers must narrow before dereferencing.
    """

    tool: str
    params: dict[str, object]


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


class CompositionSummary(TypedDict, total=False):
    """Aggregated metadata for the summary.final SSE event.

    Produced by ``_build_composition_summary`` and consumed by the
    SSE layer and frontend to display the completion paragraph.
    """

    tracksCreated: list[TrackSummaryDict]   # noqa: N815
    tracksReused: list[TrackSummaryDict]    # noqa: N815
    trackCount: int                          # noqa: N815
    regionsCreated: int                      # noqa: N815
    notesGenerated: int                      # noqa: N815
    effectsAdded: list[EffectSummaryDict]   # noqa: N815
    effectCount: int                         # noqa: N815
    sendsCreated: int                        # noqa: N815
    ccEnvelopes: list[CCEnvelopeDict]       # noqa: N815
    automationLanes: int                     # noqa: N815
    text: str


class AppliedRegionInfo(TypedDict, total=False):
    """Per-region result from applying variation phrases.

    Produced by ``apply_variation_phrases`` and carried in
    ``VariationApplyResult.updated_regions``.  All MIDI event lists are
    the *full* post-commit state for the region (not just the delta).
    """

    region_id: str
    track_id: str
    notes: list[NoteDict]
    cc_events: list[CCEventDict]
    pitch_bends: list[PitchBendDict]
    aftertouch: list[AftertouchDict]
    start_beat: float | None
    duration_beats: float | None
    name: str | None


# ═══════════════════════════════════════════════════════════════════════════════
# Variation / note change types
# ═══════════════════════════════════════════════════════════════════════════════


class NoteChangeDict(TypedDict, total=False):
    """Snapshot of a MIDI note's properties — used as ``before``/``after`` in ``NoteChangeEntryDict``.

    Serialized form of ``MidiNoteSnapshot`` (camelCase keys, matching ``by_alias=True`` output).
    Also used for CC/pitch-bend snapshots where ``cc``, ``beat``, and ``value`` apply.

    Field ranges:
        pitch         0–127    MIDI note number
        startBeat     ≥ 0.0    beat position (fractional allowed)
        durationBeats > 0.0    beat duration (fractional allowed)
        velocity      0–127    note velocity
        channel       0–15     MIDI channel
        cc            0–127    CC controller number
        beat          ≥ 0.0    CC/bend/aftertouch beat position
        value         varies   CC: 0–127; pitch bend: −8192–8191
    """

    pitch: MidiPitch
    startBeat: BeatPosition  # noqa: N815
    durationBeats: BeatDuration  # noqa: N815
    velocity: MidiVelocity
    channel: MidiChannel
    cc: MidiCC
    beat: BeatPosition
    value: int  # intentionally plain int: context-dependent (CC value vs. pitch bend)


class NoteChangeEntryDict(TypedDict, total=False):
    """Wire shape of one entry in ``PhrasePayload.noteChanges``.

    Serialized form of a ``NoteChange`` Pydantic model.  Produced by
    ``_note_change_to_wire()`` in ``propose.py`` and consumed by
    ``_record_to_variation()`` in ``commit.py``.

    ``noteId`` and ``changeType`` are always present (``Required``).
    ``before`` and ``after`` follow the same semantics as ``NoteChange``:

    - ``added``    → ``before=None``,   ``after`` is set
    - ``removed``  → ``before`` is set, ``after=None``
    - ``modified`` → both ``before`` and ``after`` are set
    """

    noteId: Required[str]  # noqa: N815
    changeType: Required[Literal["added", "removed", "modified"]]  # noqa: N815
    before: NoteChangeDict | None
    after: NoteChangeDict | None


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


# ═══════════════════════════════════════════════════════════════════════════════
# Protocol introspection types
#
# Named aliases for the multi-dimensional collections returned by the protocol
# endpoints.  Using explicit names instead of raw dict/list literals makes the
# contract between the endpoint, its response model, and callers self-evident.
# ═══════════════════════════════════════════════════════════════════════════════

EventJsonSchema = dict[str, object]
"""JSON Schema dict for a single SSE event type, as produced by Pydantic's model_json_schema()."""

EventSchemaMap = dict[str, EventJsonSchema]
"""Maps event_type → its JSON Schema.  Returned by the protocol /events.json endpoint."""

EnumDefinitionMap = dict[str, list[str]]
"""Maps enum name → sorted list of member values.  Used in the protocol /schema.json endpoint."""
