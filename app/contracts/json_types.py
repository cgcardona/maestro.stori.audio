"""Canonical type definitions for JSON data and music-domain dicts.

This module is the **single source of truth for every named data shape** in
Maestro.  Import from here; do not redefine shapes ad hoc.

## When to use which type

Use ``JSONValue`` / ``JSONObject`` only when the shape is genuinely unknown
(e.g. raw LLM output before validation, or an arbitrary external payload).
For every known structure, use the named TypedDict below.

Do **not** use ``JSONValue`` or ``JSONObject`` in Pydantic ``BaseModel``
fields — Pydantic v2 cannot resolve the recursive forward references and
raises ``RecursionError`` at schema generation time.  Use
``app.contracts.pydantic_types.PydanticJson`` instead.

## Conversion helpers

- ``json_list(items)`` — coerce a ``list[TypedDict]`` to ``list[JSONValue]``
  at list-insertion boundaries (Python list invariance workaround).
- ``jint(v)`` / ``jfloat(v)`` — safe numeric extraction from ``JSONValue``.
- ``is_note_dict(v)`` — ``TypeGuard`` narrowing from ``JSONValue`` → ``NoteDict``.

## Entity catalog

JSON primitives:
  JSONScalar            — str | int | float | bool | None
  JSONValue             — recursive JSON value (use sparingly; not in Pydantic)
  JSONObject            — dict[str, JSONValue] (use sparingly; not in Pydantic)

MIDI note types:
  NoteDict              — a single MIDI note (camelCase + snake_case fields, total=False)
  InternalNoteDict      — alias for NoteDict (snake_case storage path)
  CCEventDict           — a MIDI Control Change event (cc, beat, value)
  PitchBendDict         — a MIDI pitch bend event (beat, value)
  AftertouchDict        — a MIDI aftertouch event (beat, value[, pitch])

Composition types:
  SectionDict           — a composition section (verse, chorus, bridge…)

Storpheus adapter:
  StorpheusResultBucket — return shape of normalize_storpheus_tool_calls

SSE / protocol types:
  ToolCallDict          — internal tool call payload {tool, params}
  ToolCallPreviewDict   — plan preview tool call {name, params}

Summary event types:
  TrackSummaryDict      — summary.final track info
  EffectSummaryDict     — summary.final effect info
  SectionSummaryDict    — per-section summary in batch_complete result
  CCEnvelopeDict        — CC envelope info in summary.final
  CompositionSummary    — aggregated metadata for the summary.final SSE event
  AppliedRegionInfo     — per-region result from apply_variation_phrases

Variation / note change types:
  NoteChangeDict        — snapshot of a MIDI note's properties (before/after)
  NoteChangeEntryDict   — wire shape of one noteChanges entry

Generation constraints:
  GenerationConstraintsDict — serialized GenerationConstraints sent to Orpheus
  IntentGoalDict        — a single intent goal sent to Orpheus

State store types:
  StateEventData        — payload of a StateStore event's data field

Region metadata:
  RegionMetadataWire    — region position metadata in camelCase (handler path)
  RegionMetadataDB      — region position metadata in snake_case (database path)

Region event map aliases (region_id → list of events):
  RegionNotesMap        — dict[str, list[NoteDict]]
  RegionCCMap           — dict[str, list[CCEventDict]]
  RegionPitchBendMap    — dict[str, list[PitchBendDict]]
  RegionAftertouchMap   — dict[str, list[AftertouchDict]]

Protocol introspection aliases:
  EventJsonSchema       — dict[str, JSONValue] (single event JSON Schema)
  EventSchemaMap        — dict[str, EventJsonSchema] (event_type → JSON Schema)
  EnumDefinitionMap     — dict[str, list[str]] (enum name → member values)
"""

from __future__ import annotations

from typing import Iterable, Literal, TypeGuard, overload

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
"""A JSON leaf value with no recursive structure."""

JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
"""Recursive JSON value — the most precise mypy-safe alternative to ``Any``.

Use this type for JSON payloads whose shape is not statically known.

**Pydantic restriction:** Do NOT use in Pydantic ``BaseModel`` fields.
The recursive forward references (``list["JSONValue"]``, ``dict[str, "JSONValue"]``)
cannot be resolved by Pydantic v2 at schema generation time, causing
``RecursionError``.  Use ``PydanticJson`` from
``app.contracts.pydantic_types`` for Pydantic model fields instead, and
convert at the boundary with ``unwrap()`` / ``wrap()``.

**Mypy usage:** Use ``isinstance`` guards, ``jint()``, ``jfloat()``, or
``is_note_dict()`` to narrow ``JSONValue`` before dereferencing fields.
Never index a ``JSONValue`` without first narrowing to ``dict``.
"""

JSONObject = dict[str, JSONValue]
"""A JSON object with unknown key set.

Use when the object's keys are not statically known (e.g. LLM output before
validation, arbitrary config dicts).

**Pydantic restriction:** Do NOT use in Pydantic ``BaseModel`` fields.
See ``JSONValue`` for the full explanation.
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

    ``params`` is ``dict[str, JSONValue]`` — LLM-generated arguments are
    JSON values and must be narrowed before dereferencing.
    """

    tool: str
    params: dict[str, JSONValue]


class ToolCallPreviewDict(TypedDict):
    """Shape produced by ``ToolCall.to_dict()`` for plan previews.

    Distinct from ``ToolCallDict``: uses ``name`` (not ``tool``) to match
    ``ToolCall``'s dataclass field name.
    """

    name: str
    params: dict[str, JSONValue]


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
    metadata: dict[str, JSONValue]
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

EventJsonSchema = dict[str, JSONValue]
"""JSON Schema dict for a single SSE event type, as produced by Pydantic's model_json_schema()."""

EventSchemaMap = dict[str, EventJsonSchema]
"""Maps event_type → its JSON Schema.  Returned by the protocol /events.json endpoint."""

EnumDefinitionMap = dict[str, list[str]]
"""Maps enum name → sorted list of member values.  Used in the protocol /schema.json endpoint."""


# ═══════════════════════════════════════════════════════════════════════════════
# TypeGuard narrowing helpers
#
# These live here because they narrow JSONValue / dicts-from-JSON into the
# music-domain TypedDicts defined above.  Callers use them in list
# comprehensions to avoid cast() at the site where JSON is parsed.
# ═══════════════════════════════════════════════════════════════════════════════


def is_note_dict(v: JSONValue) -> TypeGuard[NoteDict]:
    """Narrow a ``JSONValue`` to ``NoteDict``.

    ``NoteDict`` is ``total=False`` — every field is optional — so any ``dict``
    that arrives from a trusted internal source (StateStore, SSE wire, Storpheus
    result) can be safely treated as ``NoteDict`` once we confirm it is a dict.

    Use in list comprehensions to filter a ``list[JSONValue]`` into
    ``list[NoteDict]`` without ``cast()``::

        notes = [n for n in raw_list if is_note_dict(n)]
    """
    return isinstance(v, dict)


def jfloat(v: JSONValue, default: float = 0.0) -> float:
    """Safely extract a ``float`` from a ``JSONValue``.

    Returns *default* when *v* is not numeric.  Use when pulling float fields
    out of a ``dict[str, JSONValue]`` — avoids the two-step ``float(v.get("x"))``
    pattern that mypy cannot narrow::

        beat = jfloat(event.get("beat"))          # 0.0 if key absent or non-numeric
        value = jfloat(event.get("value"), 0.5)   # custom default
    """
    return float(v) if isinstance(v, (int, float)) else default


def jint(v: JSONValue, default: int = 0) -> int:
    """Safely extract an ``int`` from a ``JSONValue``.

    Returns *default* when *v* is not numeric.  Same rationale as ``jfloat``::

        cc = jint(event.get("cc"))        # 0 if absent
        vel = jint(note.get("velocity"))  # 0 if absent
    """
    return int(v) if isinstance(v, (int, float)) else default


# ─── List coercion helper ──────────────────────────────────────────────────────
#
# Python lists are invariant: ``list[NoteDict]`` is NOT assignable to
# ``list[JSONValue]`` even though every NoteDict value is a valid JSONValue.
# Mypy enforces this strictly — dict itself is invariant in its value type,
# so even ``Iterable[dict[str, JSONValue]]`` rejects ``list[NoteDict]``.
#
# The principled solution (no ``cast()``, no per-call ``type: ignore``) is
# ``@overload`` declarations: enumerate every domain TypedDict explicitly so
# mypy resolves call sites against the precise overloads.  The implementation
# body uses ``Iterable[object]`` to accept any of the overloads, with a single
# ``type: ignore[arg-type]`` at the internal coercion point.
#
# To add a new TypedDict, add an ``@overload`` line here.  Do NOT add ``cast()``
# at any call site.


@overload
def json_list(items: Iterable[NoteDict]) -> list[JSONValue]: ...
@overload
def json_list(items: Iterable[CCEventDict]) -> list[JSONValue]: ...
@overload
def json_list(items: Iterable[PitchBendDict]) -> list[JSONValue]: ...
@overload
def json_list(items: Iterable[dict[str, JSONValue]]) -> list[JSONValue]: ...


def json_list(items: Iterable[object]) -> list[JSONValue]:
    """Coerce an iterable of music-domain TypedDicts to ``list[JSONValue]``.

    This is the **single designated list-coercion boundary** in the codebase.
    Call it whenever a ``list[SomeDomainDict]`` must be placed into a position
    that expects ``list[JSONValue]`` (e.g. a ``params`` dict, a ``JSONObject``
    field, an SSE event body).

    Each overload is typed precisely for a specific domain dict.  The
    implementation uses ``Iterable[object]`` so mypy validates call sites against
    the overloads, not the implementation signature.  The single
    ``type: ignore[arg-type]`` is the sole coercion point — callers never need
    ``cast()`` or ``type: ignore``::

        params["notes"] = json_list(result.notes)        # list[NoteDict]
        params["cc_events"] = json_list(result.cc_events) # list[CCEventDict]
    """
    result: list[JSONValue] = []
    for item in items:
        result.append(item)  # type: ignore[arg-type]  # boundary: domain dict → JSONValue
    return result
