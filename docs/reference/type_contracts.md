# Stori Maestro — Type Contracts Reference

> Updated: 2026-02-26 | Reflects the mypy hardening sweep that eliminated ~1,800 `Any` usages.

This document is the single source of truth for every named entity (TypedDict, dataclass, Protocol, type alias) in the Maestro and Orpheus codebases. It covers the full API surface of each type: fields, types, optionality, and intended use.

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Maestro Contracts (`app/contracts/`)](#maestro-contracts)
   - [generation_types.py](#generation_typespy)
   - [llm_types.py](#llm_typespy)
   - [json_types.py](#json_typespy)
   - [project_types.py](#project_typespy)
3. [Auth (`app/auth/tokens.py`)](#auth)
4. [Services](#services)
   - [Assets (`app/services/assets.py`)](#assets)
   - [OrpheusRawResponse](#orpheusrawresponse)
   - [SampleChange](#samplechange)
5. [Planner (`app/core/planner/`)](#planner)
   - [_ExistingTrackInfo](#_existingtrackinfo)
6. [State Store (`app/core/state_store.py`)](#state-store)
   - [_ProjectMetadataSnapshot](#_projectmetadatasnapshot)
7. [Orpheus Types (`storpheus/orpheus_types.py`)](#orpheus-types)
   - [MIDI event types](#midi-event-types)
   - [Pipeline types](#pipeline-types)
   - [Scoring types](#scoring-types)
8. [Region Event Map Aliases](#region-event-map-aliases)
9. [Tempo Convention](#tempo-convention)
10. [The `Any` Quarantine](#the-any-quarantine)
11. [Entity Hierarchy](#entity-hierarchy)

---

## Design Philosophy

Every entity in this codebase follows three rules:

1. **No naked `Any`.** The word `Any` appears in exactly one place per subsystem boundary: `llm_types.py` for LLM APIs and `orpheus_types.py` (legacy) for Orpheus wire types. Every other module imports named aliases.

2. **Boundaries own coercion.** When external data arrives as `float | str | None` (e.g., from JSON), the boundary module coerces it to the canonical internal type. Downstream code always sees clean types.

3. **TypedDicts for data, dataclasses for behavior.** TypedDicts carry structured data across function boundaries. Dataclasses are used when the entity needs default values, computed properties, or is passed as a unit of domain logic.

---

## Maestro Contracts

### `generation_types.py`

**Path:** `app/contracts/generation_types.py`

#### `GenerationContext`

`TypedDict, total=False` — All optional kwargs for `MusicGeneratorBackend.generate()`.

Backends pick the keys they need and ignore the rest. This replaces `**kwargs: Any` on the backend interface.

| Field | Type | Description |
|-------|------|-------------|
| `emotion_vector` | `EmotionVector \| None` | Resolved emotional intent vector |
| `quality_preset` | `str` | `"balanced"`, `"quality"`, or `"speed"` |
| `composition_id` | `str \| None` | UUID linking this generation to a composition |
| `seed` | `int \| None` | Deterministic seed for reproducible generation |
| `trace_id` | `str \| None` | Distributed trace ID for observability |
| `add_outro` | `bool` | Whether to append a tail/outro to the phrase |
| `music_spec` | `MusicSpec \| None` | Symbolic music specification (key, mode, rhythm) |
| `rhythm_spine` | `RhythmSpine \| None` | Groove engine rhythm constraint |
| `drum_kick_beats` | `list[float] \| None` | Explicit kick-drum beat positions for locking |
| `temperature` | `float` | LLM-style sampling temperature for generation |
| `section_type` | `str \| None` | Structural section hint (`"verse"`, `"chorus"`, etc.) |
| `num_candidates` | `int \| None` | How many candidates to generate for rejection sampling |

---

#### `CompositionContext`

`TypedDict, total=False` — Contextual data threaded through generator tool calls within a single composition turn.

Constructed by `RuntimeContext.to_composition_context()` and extended by agent code before being passed to `_apply_single_tool_call`. All fields are optional — callers populate only what they know.

| Field | Type | Description |
|-------|------|-------------|
| `style` | `str` | Genre/style string (e.g. `"house"`, `"jazz"`) |
| `tempo` | `int` | Beats per minute — always a whole integer |
| `bars` | `int` | Number of bars to generate |
| `key` | `str \| None` | Musical key (e.g. `"Am"`, `"C"`) |
| `quality_preset` | `str` | Quality tier for generation |
| `emotion_vector` | `EmotionVector \| None` | Reconstructed emotion vector for the backend |
| `section_key` | `str` | Identifies the section (e.g. `"0:verse"`) |
| `all_instruments` | `list[str]` | All instrument roles in the composition |
| `composition_id` | `str` | UUID for the composition run |
| `role` | `str` | This instrument's role (e.g. `"bass"`, `"drums"`) |
| `previous_notes` | `list[NoteDict]` | Notes from the previous section (chaining) |
| `drum_telemetry` | `dict[str, object]` | Drum energy/groove data injected for bass/chord agents |

**drum_telemetry keys** (produced by `SectionTelemetry`, consumed by bass/chord agents):

| Key | Type | Description |
|-----|------|-------------|
| `energy_level` | `float` | Drum section energy (0–1) |
| `density_score` | `float` | Notes-per-beat density |
| `groove_vector` | `list[float]` | 16-step rhythmic pattern |
| `kick_pattern_hash` | `str` | Fingerprint of the kick pattern |
| `rhythmic_complexity` | `float` | Syncopation/complexity score |

---

#### `RoleResult`

`TypedDict, total=False` — Per-instrument outcome from `execute_unified_generation`.

| Field | Type | Description |
|-------|------|-------------|
| `notes_added` | `int` | Count of notes written to the DAW region |
| `success` | `bool` | Whether generation succeeded |
| `error` | `str \| None` | Error message on failure |
| `track_id` | `str` | DAW track ID where notes were written |
| `region_id` | `str` | DAW region ID where notes were written |

---

#### `UnifiedGenerationOutput`

`TypedDict, total=False` — Full return value of `execute_unified_generation`.

Groups per-role results with aggregate statistics, replacing a mixed `dict[str, RoleResult | int]`.

| Field | Type | Description |
|-------|------|-------------|
| `per_role` | `dict[str, RoleResult]` | Results keyed by instrument role name |
| `_metadata` | `object` | Raw metadata from the backend response |
| `_duration_ms` | `int` | Total wall-clock generation time in milliseconds |

---

### `llm_types.py`

**Path:** `app/contracts/llm_types.py`

The `Any` quarantine for external LLM API boundaries. Every module that needs to work with OpenAI/Anthropic/OpenRouter responses imports from here — never spelling out `dict[str, Any]` themselves.

#### Type Aliases

| Name | Underlying Type | Represents |
|------|----------------|------------|
| `OpenAIData` | `dict[str, Any]` | Base alias — JSON-decoded dict from any LLM API |
| `OpenAIStreamChunk` | `OpenAIData` | One SSE chunk from the streaming API |
| `OpenAITool` | `OpenAIData` | A tool schema sent to the model |
| `UsageStats` | `OpenAIData` | Token usage / cost statistics |
| `OpenAIRequestPayload` | `OpenAIData` | Full request body sent to OpenRouter |
| `OpenAIResponse` | `OpenAIData` | Full (non-streaming) response body |
| `OpenAIToolChoice` | `str \| OpenAIData` | `"auto"` \| `"required"` \| `"none"` \| specific tool dict |
| `StreamEvent` | `OpenAIData` | Internal stream event yielded by `LLMClient.chat()` |

#### `ToolCallFunction`

`TypedDict` — The `function` field inside an OpenAI tool call.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Function name the model wants to call |
| `arguments` | `str` | JSON-encoded arguments string |

#### `ToolCallEntry`

`TypedDict` — One tool call in an assistant message.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique tool call ID (used in `ToolResultMessage`) |
| `type` | `str` | Always `"function"` in OpenAI format |
| `function` | `ToolCallFunction` | The function being called |

#### `SystemMessage`

`TypedDict` — A system prompt message.

| Field | Type | Description |
|-------|------|-------------|
| `role` | `Literal["system"]` | Discriminant |
| `content` | `str` | System prompt text |

#### `UserMessage`

`TypedDict` — A user message.

| Field | Type | Description |
|-------|------|-------------|
| `role` | `Literal["user"]` | Discriminant |
| `content` | `str` | User message text |

#### `AssistantMessage`

`TypedDict, total=False` — An assistant reply (may contain tool calls).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `role` | ✓ | `Literal["assistant"]` | Discriminant |
| `content` | | `str \| None` | Assistant text reply |
| `tool_calls` | | `list[ToolCallEntry]` | Tool calls requested by the model |

#### `ToolResultMessage`

`TypedDict` — A tool result returned to the LLM.

| Field | Type | Description |
|-------|------|-------------|
| `role` | `Literal["tool"]` | Discriminant |
| `tool_call_id` | `str` | Matches `ToolCallEntry.id` |
| `content` | `str` | JSON-encoded result string |

#### `ChatMessage`

`Union[SystemMessage, UserMessage, AssistantMessage, ToolResultMessage]` — Discriminated union of all OpenAI chat message shapes. Narrowed at call sites by checking `msg["role"]`.

---

### `json_types.py`

**Path:** `app/contracts/json_types.py`

#### JSON Primitive Types

| Name | Type | Use |
|------|------|-----|
| `JSONScalar` | `str \| int \| float \| bool \| None` | A JSON leaf value |
| `JSONValue` | Recursive union | Any valid JSON value — use instead of `Any` for truly unknown payloads |
| `JSONObject` | `dict[str, JSONValue]` | A JSON object with unknown key set |

#### `NoteDict`

`TypedDict, total=False` — A single MIDI note. Accepts **both** camelCase (DAW wire) and snake_case (internal) field names so that notes flow through all pipeline layers without conversion.

| Field | Format | Type | Description |
|-------|--------|------|-------------|
| `pitch` | both | `int` | MIDI pitch (0–127) |
| `velocity` | both | `int` | MIDI velocity (0–127) |
| `channel` | both | `int` | MIDI channel (0–15) |
| `startBeat` | wire | `float` | Note onset in beats |
| `durationBeats` | wire | `float` | Note duration in beats |
| `noteId` | wire | `str` | Unique note ID |
| `trackId` | wire | `str` | Foreign key to track |
| `regionId` | wire | `str` | Foreign key to region |
| `start_beat` | internal | `float` | Note onset in beats |
| `duration_beats` | internal | `float` | Note duration in beats |
| `note_id` | internal | `str` | Unique note ID |
| `track_id` | internal | `str` | Foreign key to track |
| `region_id` | internal | `str` | Foreign key to region |
| `layer` | both | `str` | Drum renderer layer tag |

`InternalNoteDict` is an alias for `NoteDict` used at storage boundaries to signal intent.

#### `CCEventDict`

`TypedDict` — A MIDI Control Change event.

| Field | Type | Description |
|-------|------|-------------|
| `cc` | `int` | CC number (0–127) |
| `beat` | `float` | Event position in beats |
| `value` | `int` | CC value (0–127) |

#### `PitchBendDict`

`TypedDict` — A MIDI Pitch Bend event.

| Field | Type | Description |
|-------|------|-------------|
| `beat` | `float` | Event position in beats |
| `value` | `int` | Pitch bend value (-8192 to 8191) |

#### `AftertouchDict`

`TypedDict, total=False` — A MIDI Aftertouch event (channel pressure or polyphonic key pressure).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `beat` | ✓ | `float` | Event position in beats |
| `value` | ✓ | `int` | Pressure value (0–127) |
| `pitch` | | `int` | Specific MIDI pitch (polyphonic aftertouch only) |

#### `ToolCallDict`

`TypedDict` — Shape of a collected tool call in `CompleteEvent.tool_calls`.

| Field | Type | Description |
|-------|------|-------------|
| `tool` | `str` | Tool name (e.g. `"stori_add_notes"`) |
| `params` | `dict[str, object]` | LLM-generated tool arguments |

#### `RegionMetadataWire`

`TypedDict, total=False` — Region position metadata in camelCase (handler → storage path).

| Field | Type | Description |
|-------|------|-------------|
| `startBeat` | `float` | Region start position in beats |
| `durationBeats` | `float` | Region duration in beats |
| `name` | `str` | Region display name |

#### `RegionMetadataDB`

`TypedDict, total=False` — Region position metadata in snake_case (database path).

| Field | Type | Description |
|-------|------|-------------|
| `start_beat` | `float` | Region start position in beats |
| `duration_beats` | `float` | Region duration in beats |
| `name` | `str` | Region display name |

---

### `project_types.py`

**Path:** `app/contracts/project_types.py`

#### `TimeSignatureDict`

`TypedDict` — Time signature in structured form. Some DAW versions send `"4/4"` (string); others send this dict.

| Field | Type | Description |
|-------|------|-------------|
| `numerator` | `int` | Beats per bar |
| `denominator` | `int` | Beat unit (4 = quarter note) |

#### `MixerSettingsDict`

`TypedDict, total=False` — Mixer state for a track.

| Field | Type | Description |
|-------|------|-------------|
| `volume` | `float` | Track volume (0.0–1.0) |
| `pan` | `float` | Pan position (-1.0 to 1.0) |
| `isMuted` | `bool` | Whether the track is muted |
| `isSolo` | `bool` | Whether the track is soloed |

#### `AutomationLaneDict`

`TypedDict, total=False` — An automation lane on a track.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Lane UUID |
| `parameter` | `str` | Automated parameter name |
| `points` | `list[dict[str, float]]` | Automation curve control points |

#### `ProjectRegion`

`TypedDict, total=False` — A MIDI region inside a track.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Region UUID |
| `name` | `str` | Region display name |
| `startBeat` | `float` | Region start position in beats |
| `durationBeats` | `float` | Region duration in beats |
| `noteCount` | `int` | Number of MIDI notes |
| `notes` | `list[NoteDict]` | Notes in this region |

#### `ProjectTrack`

`TypedDict, total=False` — A track in the DAW project.

`id` is the track's own UUID. `trackId` is reserved for foreign-key references in tool call params and event payloads (e.g. `stori_add_midi_region(trackId=…)`).

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Track UUID |
| `name` | `str` | Track display name |
| `gmProgram` | `int \| None` | General MIDI program (null on drum tracks) |
| `drumKitId` | `str \| None` | Drum kit ID (null on melodic tracks) |
| `isDrums` | `bool` | Whether this is a drum track |
| `volume` | `float` | Volume (0.0–1.0) |
| `pan` | `float` | Pan (-1.0–1.0) |
| `muted` | `bool` | Mute state |
| `solo` | `bool` | Solo state |
| `color` | `str` | Display color hex |
| `icon` | `str` | Track icon name |
| `role` | `str` | Instrument role (e.g. `"bass"`, `"drums"`) |
| `regions` | `list[ProjectRegion]` | All regions on this track |
| `mixerSettings` | `MixerSettingsDict` | Detailed mixer state |
| `automationLanes` | `list[AutomationLaneDict]` | Automation lanes |

#### `BusDict`

`TypedDict, total=False` — An audio bus.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Bus UUID |
| `name` | `str` | Bus display name |

#### `ProjectContext`

`TypedDict, total=False` — The full DAW project state sent from the Stori macOS app on every request.

`timeSignature` is polymorphic — the DAW sends it as `"4/4"` (string) in some versions and as `{"numerator": 4, "denominator": 4}` (dict) in others. Coerce at the boundary with `parse_time_signature()`.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Project UUID — canonical project identifier |
| `name` | `str` | Project display name |
| `tempo` | `int` | Project tempo in BPM (always whole integer) |
| `key` | `str` | Root key (e.g. `"Am"`, `"C"`) |
| `timeSignature` | `str \| TimeSignatureDict` | Time signature |
| `tracks` | `list[ProjectTrack]` | All tracks in the project |
| `buses` | `list[BusDict]` | All audio buses |

---

## Auth

### `app/auth/tokens.py`

#### `TokenClaims`

`TypedDict, total=False` — Decoded JWT payload returned by `validate_access_code`.

`type`, `iat`, and `exp` are always present. `sub` and `role` are optional.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `type` | ✓ | `str` | Always `"access"` |
| `iat` | ✓ | `int` | Issued-at Unix timestamp |
| `exp` | ✓ | `int` | Expiry Unix timestamp |
| `sub` | | `str` | User ID — omitted for anonymous tokens |
| `role` | | `str` | `"admin"` when token was issued with `is_admin=True` |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `hash_token` | `(token: str) -> str` | SHA-256 hash of a token for storage |
| `make_token` | `(duration_hours, user_id, is_admin) -> str` | Create a signed JWT |
| `create_access_token` | `(expires_hours, expires_days, user_id, is_admin) -> str` | Public alias for `make_token` |
| `validate_access_code` | `(token: str) -> TokenClaims` | Decode and validate; raises `AccessCodeError` on failure |
| `get_user_id_from_token` | `(token: str) -> str \| None` | Extract `sub` without full validation (not for auth) |
| `get_token_expiration` | `(token: str) -> datetime` | Extract expiry as UTC datetime |

#### `AccessCodeError`

`Exception` — Raised by `validate_access_code` when a token is invalid, expired, or malformed.

---

## Services

### Assets

**Path:** `app/services/assets.py`

#### `DrumKitInfo`

`TypedDict, total=False` — Metadata for a single drum kit from the S3 asset manifest.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Kit identifier (e.g. `"tr909"`) |
| `name` | `str` | Display name (e.g. `"TR-909"`) |
| `version` | `str` | Manifest version string |

**Default kits** (used when S3 is unavailable): `cr78`, `linndrum`, `pearl`, `tr505`, `tr909`.

#### `SoundFontInfo`

`TypedDict, total=False` — Metadata for a single soundfont from the S3 asset manifest.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Soundfont identifier (e.g. `"fluidr3_gm"`) |
| `name` | `str` | Display name (e.g. `"Fluid R3 GM"`) |
| `filename` | `str` | Filename on S3 (e.g. `"FluidR3_GM.sf2"`) |

**Default soundfonts**: `fluidr3_gm` (Fluid R3 GM).

#### S3 Client Protocols (private)

These Protocols define the structural interface for the untyped `boto3` S3 client. They live in `assets.py` and are not imported elsewhere — they exist to keep `Any` out of the production code while bridging the external library boundary.

| Protocol | Description |
|----------|-------------|
| `_S3StreamingBody` | `read() -> bytes` — streaming body returned by `get_object` |
| `_GetObjectResponse` | `TypedDict` with `Body: _S3StreamingBody` |
| `_S3Client` | Full structural interface: `get_object`, `generate_presigned_url`, `head_object`, `head_bucket` |

`_s3_client()` returns `_S3Client`. The single `cast(_S3Client, boto3.client(...))` at the boundary is the only place `boto3`'s untyped surface touches typed code.

**Public functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `list_drum_kits` | `() -> list[DrumKitInfo]` | Returns S3 manifest or `DEFAULT_DRUM_KITS` |
| `list_soundfonts` | `() -> list[SoundFontInfo]` | Returns S3 manifest or `DEFAULT_SOUNDFONTS` |
| `get_drum_kit_download_url` | `(kit_id, expires_in) -> str \| None` | Presigned S3 URL for a drum kit |
| `get_soundfont_download_url` | `(soundfont_id, expires_in) -> str \| None` | Presigned S3 URL for a soundfont |
| `check_s3_health` | `() -> bool` | Returns `True` if S3 bucket is reachable |

---

### `OrpheusRawResponse`

**Path:** `app/services/orpheus.py`

`TypedDict, total=False` — The raw JSON response from the Orpheus `/generate` endpoint.

On success: `success=True` plus `notes`/`tool_calls`/`metadata`.
On failure: `success=False` plus `error` (and optionally `message`).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `success` | ✓ | `bool` | Whether generation completed |
| `notes` | ✓ | `list[NoteDict]` | Flat note list (wire format) |
| `tool_calls` | ✓ | `list[dict[str, object]]` | Raw tool call dicts from Orpheus |
| `metadata` | ✓ | `dict[str, object]` | Generation metadata (timing, cache info, etc.) |
| `channel_notes` | | `dict[int, list[NoteDict]]` | Per-MIDI-channel note lists |
| `error` | | `str` | Error description on failure |
| `message` | | `str` | Optional human-readable message |
| `retry_count` | | `int` | Number of retries performed |

---

### `SampleChange`

**Path:** `app/services/muse_drift.py`

`TypedDict, total=False` — A single note change captured as a human-readable diff sample within a `RegionDriftSummary`.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `type` | ✓ | `Literal["added", "removed", "modified"]` | Change kind |
| `note` | | `NoteDict \| None` | The note (for `added`/`removed`) |
| `before` | | `NoteDict \| None` | Original note (for `modified`) |
| `after` | | `NoteDict \| None` | New note (for `modified`) |

---

## Planner

### `_ExistingTrackInfo`

**Path:** `app/core/planner/conversion.py`

`TypedDict, total=False` — Cached info for a track already present in the DAW project. Used by `build_execution_plan` to avoid creating duplicate tracks.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Track UUID from the DAW |
| `name` | `str` | Track display name |
| `gmProgram` | `int \| None` | General MIDI program number (0–127) |

---

## State Store

### `_ProjectMetadataSnapshot`

**Path:** `app/core/state_store.py`

`TypedDict, total=False` — Internal snapshot shape stored inside `StateSnapshot.project_metadata`. Captures the musical and note state at a specific version.

| Field | Type | Description |
|-------|------|-------------|
| `tempo` | `int` | Project tempo in BPM |
| `key` | `str` | Root key |
| `time_signature` | `tuple[int, int]` | Numerator and denominator |
| `_region_notes` | `dict[str, list[InternalNoteDict]]` | All region notes at this version |
| `_region_cc` | `dict[str, list[CCEventDict]]` | All CC events at this version |
| `_region_pitch_bends` | `dict[str, list[PitchBendDict]]` | All pitch bend events |
| `_region_aftertouch` | `dict[str, list[AftertouchDict]]` | All aftertouch events |

---

## Storpheus Types

**Path:** `storpheus/orpheus_types.py`

These types mirror the Maestro `app/contracts/json_types.py` types but are defined independently to avoid cross-container imports. Orpheus uses **snake_case** internally; camelCase types (like `WireNoteDict`) are used only at the API boundary.

### MIDI Event Types

#### `OrpheusNoteDict`

`TypedDict, total=False` — A single MIDI note as parsed from a MIDI file. Internal representation (snake_case).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `pitch` | ✓ | `int` | MIDI pitch (0–127) |
| `start_beat` | ✓ | `float` | Note onset in beats |
| `duration_beats` | ✓ | `float` | Note duration in beats |
| `velocity` | ✓ | `int` | MIDI velocity (0–127) |

#### `OrpheusCCEvent`

`TypedDict` — A MIDI Control Change event.

| Field | Type | Description |
|-------|------|-------------|
| `cc` | `int` | CC number (0–127) |
| `beat` | `float` | Event position in beats |
| `value` | `int` | CC value (0–127) |

#### `OrpheusPitchBend`

`TypedDict` — A MIDI Pitch Bend event.

| Field | Type | Description |
|-------|------|-------------|
| `beat` | `float` | Event position in beats |
| `value` | `int` | Pitch bend value (-8192 to 8191) |

#### `OrpheusAftertouch`

`TypedDict, total=False` — A MIDI Aftertouch event (channel pressure or polyphonic).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `beat` | ✓ | `float` | Event position in beats |
| `value` | ✓ | `int` | Pressure value (0–127) |
| `pitch` | | `int` | Specific pitch (poly aftertouch only) |

---

### Pipeline Types

#### `ParsedMidiResult`

`TypedDict` — Return type of `parse_midi_to_notes`. Groups all event types per MIDI channel.

| Field | Type | Description |
|-------|------|-------------|
| `notes` | `dict[int, list[OrpheusNoteDict]]` | Notes per channel |
| `cc_events` | `dict[int, list[OrpheusCCEvent]]` | CC events per channel |
| `pitch_bends` | `dict[int, list[OrpheusPitchBend]]` | Pitch bends per channel |
| `aftertouch` | `dict[int, list[OrpheusAftertouch]]` | Aftertouch per channel |
| `program_changes` | `dict[int, int]` | Program number per channel |

#### `WireNoteDict`

`TypedDict` — A single MIDI note in the camelCase wire format sent to Maestro. Used **only** in `GenerateResponse` fields that cross the HTTP boundary. All internal processing uses `OrpheusNoteDict`.

| Field | Type | Description |
|-------|------|-------------|
| `pitch` | `int` | MIDI pitch (0–127) |
| `startBeat` | `float` | Note onset in beats (camelCase) |
| `durationBeats` | `float` | Note duration in beats (camelCase) |
| `velocity` | `int` | MIDI velocity (0–127) |

#### `CacheKeyData`

`TypedDict` — Canonical request fields used to compute the generation cache key. Any change to these fields produces a cache miss.

| Field | Type | Description |
|-------|------|-------------|
| `genre` | `str` | Music genre/style |
| `tempo` | `int` | BPM |
| `key` | `str` | Musical key |
| `instruments` | `list[str]` | All instrument roles |
| `bars` | `int` | Number of bars |
| `intent_goals` | `list[str]` | Resolved intent goal strings |
| `energy` | `float` | Emotion energy (0–1) |
| `valence` | `float` | Emotion valence (0–1) |
| `tension` | `float` | Emotion tension (0–1) |
| `intimacy` | `float` | Emotion intimacy (0–1) |
| `motion` | `float` | Emotion motion (0–1) |
| `quality_preset` | `str` | Quality tier |

#### `FulfillmentReport`

`TypedDict` — Constraint-fulfillment report produced after candidate selection. Summarises how well the chosen candidate satisfied the generation constraints.

| Field | Type | Description |
|-------|------|-------------|
| `goal_scores` | `dict[str, float]` | Per-goal satisfaction score (0–1) |
| `constraint_violations` | `list[str]` | Human-readable list of violated constraints |
| `coverage_pct` | `float` | Percentage of requested instruments/channels covered |

#### `GradioGenerationParams`

`TypedDict` — Concrete Gradio API parameters derived from the generation control vector. Passed directly to the Gradio inference endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `temperature` | `float` | Sampling temperature |
| `top_p` | `float` | Nucleus sampling probability |
| `num_prime_tokens` | `int` | Tokens of musical context (priming) |
| `num_gen_tokens` | `int` | Tokens to generate |

#### `GenerationComparison`

`TypedDict` — Result of comparing two generation candidates. Used by `compare_generations` in quality metrics.

| Field | Type | Description |
|-------|------|-------------|
| `generation_a` | `dict[str, float]` | Quality metrics for candidate A |
| `generation_b` | `dict[str, float]` | Quality metrics for candidate B |
| `winner` | `str` | `"a"` \| `"b"` \| `"tie"` |
| `confidence` | `float` | How decisive the comparison was (0–1) |

#### `QualityEvalParams`

`TypedDict, total=False` — Parameters for a tool call inside a quality evaluation request. Only `addNotes` is currently scored.

| Field | Type | Description |
|-------|------|-------------|
| `notes` | `list[OrpheusNoteDict]` | Notes to evaluate |

#### `QualityEvalToolCall`

`TypedDict` — A single tool call as submitted to the `/quality/evaluate` endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `tool` | `str` | Tool name (e.g. `"addNotes"`) |
| `params` | `QualityEvalParams` | Tool parameters |

---

### Scoring Types

#### `ScoringParams`

`@dataclass` — All scoring parameters passed to `score_candidate`. Extracted from the generation request before the candidate-selection loop, replacing a loosely-typed `dict[str, Any]`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `bars` | `int` | required | Number of bars generated |
| `target_key` | `str \| None` | required | Musical key to score against |
| `expected_channels` | `int` | required | Number of MIDI channels expected |
| `target_density` | `float \| None` | `None` | Target notes-per-beat density |
| `register_center` | `int \| None` | `None` | Target MIDI pitch center |
| `register_spread` | `int \| None` | `None` | Acceptable pitch deviation |
| `velocity_floor` | `int \| None` | `None` | Minimum acceptable velocity |
| `velocity_ceiling` | `int \| None` | `None` | Maximum acceptable velocity |

#### `BestCandidate`

`@dataclass` — The winning candidate retained after rejection-sampling evaluation. Wraps everything needed to continue post-processing without carrying a loosely-typed dict through the pipeline.

| Field | Type | Description |
|-------|------|-------------|
| `midi_result` | `Sequence[object]` | Raw Gradio response tuple `[audio, plot, midi_path, …]` |
| `midi_path` | `str` | Path to the generated MIDI file |
| `parsed` | `ParsedMidiResult` | Fully parsed MIDI events by channel |
| `flat_notes` | `list[OrpheusNoteDict]` | Flattened note list across all channels |
| `batch_idx` | `int` | Index in the generation batch (for logging) |

---

## Region Event Map Aliases

**Path:** `app/contracts/json_types.py`

These type aliases replace the repeated pattern `dict[str, list[XxxDict]]` that appears 25+ times across the Muse VCS, StateStore, variation executor, and checkout pipeline. The key is always a `region_id` string; the value is the ordered list of events for that region.

| Alias | Underlying Type | Semantics |
|-------|----------------|-----------|
| `RegionNotesMap` | `dict[str, list[NoteDict]]` | All MIDI notes per region |
| `RegionCCMap` | `dict[str, list[CCEventDict]]` | All MIDI CC events per region |
| `RegionPitchBendMap` | `dict[str, list[PitchBendDict]]` | All pitch bend events per region |
| `RegionAftertouchMap` | `dict[str, list[AftertouchDict]]` | All aftertouch events per region |

**Where used:**

| Module | Usage |
|--------|-------|
| `app/core/state_store.py` | `StateStore._region_notes/cc/pitch_bends/aftertouch` fields |
| `app/core/executor/models.py` | `VariationContext` snapshot fields |
| `app/core/executor/apply.py` | Local accumulator variables in `apply_variation_phrases` |
| `app/core/executor/variation.py` | `compute_variation_from_context` signature |
| `app/services/muse_replay.py` | `HeadSnapshot` fields; `reconstruct_*` locals |
| `app/services/muse_merge.py` | `three_way_merge` accumulators; `build_merge_checkout_plan` params |
| `app/services/muse_drift.py` | `compute_drift_report` signature |
| `app/services/muse_checkout.py` | `build_checkout_plan` signature |
| `app/services/muse_history_controller.py` | `_capture_working_*` return types |
| `app/services/variation/service.py` | `compute_multi_region_variation` signature |

**Note:** `list[NoteDict]` (without the dict wrapper) remains the correct type for single-region operations — e.g. `_build_region_note_calls(region_id, target_notes: list[NoteDict], ...)`. The alias applies only at the multi-region aggregation level.

---

## Tempo Convention

**Tempo is always `int` (BPM) throughout the internal codebase.**

Beats per minute is a whole number by definition — there is no such thing as 120.5 BPM. The coercion from potentially-float JSON values happens at exactly one boundary:

```python
# app/core/maestro_agent_teams/coordinator.py — the DAW→Maestro boundary
tempo = int(round(float(parsed.tempo or project_context.get("tempo") or 120)))
```

After this point, `tempo` is `int` everywhere:
- `CompositionContext.tempo: int`
- `CompositionContract.tempo: int`
- `InstrumentContract.tempo: int`
- `SectionContract.tempo: int`
- `SectionTelemetry.tempo: int`
- `CacheKeyData.tempo: int`
- `ProjectContext.tempo: int`
- `ProjectSnapshot.tempo: int | None`

---

## The `Any` Quarantine

`Any` is confined to two locations, used by all other modules via named imports:

### `app/contracts/llm_types.py`

```python
OpenAIData = dict[str, Any]  # The only Any in Maestro production code
```

All LLM boundary types (`OpenAIStreamChunk`, `OpenAIResponse`, `StreamEvent`, etc.) are aliases of `OpenAIData`. Modules that handle LLM responses import these names — they never write `dict[str, Any]` themselves.

### External boundary coercions

When data arrives from JSON/HTTP with unknown structure, it is immediately coerced to a named type at the boundary function. Downstream code always receives typed values. If a field genuinely can be anything (e.g. a metadata bag), it is typed as `dict[str, object]` — which forces `isinstance` checks before use, making assumptions explicit.

---

## Entity Hierarchy

```
Maestro Service (app/)
│
├── Contracts (app/contracts/)
│   ├── generation_types.py
│   │   ├── GenerationContext          — backend kwargs (replaces **kwargs: Any)
│   │   ├── CompositionContext         — per-turn generation context
│   │   ├── RoleResult                 — per-instrument outcome
│   │   └── UnifiedGenerationOutput    — full generation return value
│   │
│   ├── llm_types.py                   — Any quarantine for LLM APIs
│   │   ├── OpenAIData                 — base alias (dict[str, Any])
│   │   ├── OpenAIStreamChunk          — one SSE chunk
│   │   ├── OpenAITool                 — tool schema sent to model
│   │   ├── UsageStats                 — token/cost statistics
│   │   ├── OpenAIRequestPayload       — full request body to OpenRouter
│   │   ├── OpenAIResponse             — full (non-streaming) response
│   │   ├── OpenAIToolChoice           — "auto" | tool dict
│   │   ├── StreamEvent                — internal stream event
│   │   ├── ToolCallFunction           — function inside a tool call
│   │   ├── ToolCallEntry              — one tool call in assistant message
│   │   ├── SystemMessage              — system prompt
│   │   ├── UserMessage                — user turn
│   │   ├── AssistantMessage           — assistant reply (+ tool_calls)
│   │   ├── ToolResultMessage          — tool result back to LLM
│   │   └── ChatMessage                — discriminated union of all messages
│   │
│   ├── json_types.py
│   │   ├── JSONScalar/JSONValue/JSONObject  — JSON primitive types
│   │   ├── NoteDict / InternalNoteDict — MIDI note (camelCase+snake_case)
│   │   ├── CCEventDict                — MIDI CC event
│   │   ├── PitchBendDict              — MIDI pitch bend
│   │   ├── AftertouchDict             — MIDI aftertouch (channel/poly)
│   │   ├── ToolCallDict               — collected tool call in SSE events
│   │   ├── RegionMetadataWire         — region position (camelCase)
│   │   ├── RegionMetadataDB           — region position (snake_case)
│   │   ├── RegionNotesMap             — dict[str, list[NoteDict]]
│   │   ├── RegionCCMap                — dict[str, list[CCEventDict]]
│   │   ├── RegionPitchBendMap         — dict[str, list[PitchBendDict]]
│   │   └── RegionAftertouchMap        — dict[str, list[AftertouchDict]]
│   │
│   └── project_types.py
│       ├── TimeSignatureDict          — structured time signature
│       ├── MixerSettingsDict          — track mixer state
│       ├── AutomationLaneDict         — automation lane on a track
│       ├── ProjectRegion              — a MIDI region in the DAW
│       ├── ProjectTrack               — a DAW track with all metadata
│       ├── BusDict                    — an audio bus
│       └── ProjectContext             — full DAW project state (from frontend)
│
├── Auth (app/auth/)
│   └── tokens.py
│       ├── TokenClaims                — decoded JWT payload
│       └── AccessCodeError            — validation failure exception
│
├── Services (app/services/)
│   ├── assets.py
│   │   ├── DrumKitInfo                — drum kit manifest entry
│   │   ├── SoundFontInfo              — soundfont manifest entry
│   │   ├── _S3StreamingBody           — Protocol: boto3 streaming body
│   │   ├── _GetObjectResponse         — TypedDict: boto3 get_object response
│   │   └── _S3Client                  — Protocol: boto3 S3 client interface
│   │
│   ├── backends/storpheus.py (via orpheus.py)
│   │   └── OrpheusRawResponse         — raw HTTP response from Orpheus service
│   │
│   └── muse_drift.py
│       └── SampleChange               — a single note diff sample
│
├── Core (app/core/)
│   ├── planner/conversion.py
│   │   └── _ExistingTrackInfo         — cached DAW track info for deduplication
│   │
│   └── state_store.py
│       └── _ProjectMetadataSnapshot   — versioned project state snapshot
│
└── Backends (app/services/backends/)
    └── base.py
        └── GenerationResult           — outcome of a backend generate() call
            ├── success: bool
            ├── notes: list[NoteDict]
            ├── metadata: dict[str, object]
            ├── channel_notes: RegionNotesMap | None
            └── error / cc_events / pitch_bends / aftertouch


Orpheus Service (storpheus/)
│
└── orpheus_types.py
    │
    ├── MIDI Events
    │   ├── OrpheusNoteDict            — note (snake_case, internal)
    │   ├── OrpheusCCEvent             — CC event
    │   ├── OrpheusPitchBend           — pitch bend event
    │   └── OrpheusAftertouch          — aftertouch event (channel/poly)
    │
    ├── Pipeline
    │   ├── ParsedMidiResult           — full parse output (all channels)
    │   ├── WireNoteDict               — note (camelCase, API boundary only)
    │   ├── CacheKeyData               — cache key for generation dedup
    │   ├── FulfillmentReport          — constraint satisfaction report
    │   ├── GradioGenerationParams     — parameters for Gradio inference
    │   ├── GenerationComparison       — A/B candidate comparison result
    │   ├── QualityEvalParams          — /quality/evaluate input
    │   └── QualityEvalToolCall        — single tool call for quality eval
    │
    └── Scoring
        ├── ScoringParams              — @dataclass, candidate scoring config
        └── BestCandidate              — @dataclass, winning generation candidate
```
