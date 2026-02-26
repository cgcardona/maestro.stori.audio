# Stori Maestro — Type Contracts Reference

> Updated: 2026-02-26 | Reflects the full `Any`-elimination sweep and MIDI primitive constraint system. `Any` no longer exists in any production app file. All MIDI primitives carry range constraints at every layer — Pydantic validation, dataclass `__post_init__`, and named `Annotated` type aliases.

This document is the single source of truth for every named entity (TypedDict, dataclass, Protocol, type alias) in the Maestro codebase. It covers the full API surface of each type: fields, types, optionality, and intended use.

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Maestro Contracts (`app/contracts/`)](#maestro-contracts)
   - [midi_types.py](#midi_typespy)
   - [generation_types.py](#generation_typespy)
   - [llm_types.py](#llm_typespy)
   - [json_types.py](#json_typespy)
   - [project_types.py](#project_typespy)
   - [mcp_types.py](#mcp_typespy)
3. [Auth (`app/auth/tokens.py`)](#auth)
4. [Services](#services)
   - [Assets (`app/services/assets.py`)](#assets)
   - [StorpheusRawResponse](#storpheusrawresponse)
   - [SampleChange](#samplechange)
   - [ExpressivenessResult](#expressivenessresult)
5. [Variation Layer (`app/variation/`)](#variation-layer)
   - [Event Envelope payloads](#event-envelope-payloads)
   - [PhraseRecord](#phraserecord)
6. [Planner (`app/core/planner/`)](#planner)
   - [_ExistingTrackInfo](#_existingtrackinfo)
   - [_AddMidiTrackParams](#_addmiditrackparams)
   - [_AddMidiRegionParams](#_addmidiregionparams)
   - [_GenerateParams](#_generateparams)
7. [State Store (`app/core/state_store.py`)](#state-store)
   - [_ProjectMetadataSnapshot](#_projectmetadatasnapshot)
7. [Storpheus Types (`storpheus/storpheus_types.py`)](#storpheus-types)
   - [MIDI event types](#midi-event-types)
   - [Pipeline types](#pipeline-types)
   - [Scoring types](#scoring-types)
10. [Region Event Map Aliases](#region-event-map-aliases)
11. [HTTP Response Entities](#http-response-entities)
    - [Protocol Introspection](#protocol-introspection-appprotocolresponsespy)
    - [Muse VCS](#muse-vcs-appapiroutesmusepy)
    - [Maestro Core](#maestro-core-appapiroutesmaestropy)
    - [MCP Endpoints](#mcp-endpoints-appapiroutesmcppy)
    - [Variation Endpoints](#variation-endpoints)
    - [Conversations](#conversations-appapiroutesconversationsmodelspy)
12. [Tempo Convention](#tempo-convention)
13. [`Any` Status](#any-status)
14. [Entity Hierarchy](#entity-hierarchy)

---

## Design Philosophy

Every entity in this codebase follows four rules:

1. **No `Any`.** `Any` does not appear in any production app file. LLM API shapes are described with full TypedDict hierarchies in `llm_types.py`. External untyped library boundaries (boto3, Pydantic's `model_json_schema`) are handled with `dict[str, object]` and Protocol types — never `Any`.

2. **Boundaries own coercion.** When external data arrives as `float | str | None` (e.g., from JSON), the boundary module coerces it to the canonical internal type. Downstream code always sees clean types.

3. **TypedDicts for data, dataclasses for behavior.** TypedDicts carry structured data across function boundaries. Dataclasses are used when the entity needs default values, computed properties, or is passed as a unit of domain logic.

4. **MIDI primitives are range-constrained at every layer.** The canonical type aliases in `app/contracts/midi_types.py` define the single source of truth for all MIDI value ranges. These constraints propagate automatically through all three enforcement layers:
   - **Pydantic `BaseModel` fields**: `Annotated[int, Field(ge=..., le=...)]` enforces at parse time. Invalid values raise `ValidationError` before reaching business logic.
   - **Frozen dataclasses** (`contracts.py`): `__post_init__` calls `_assert_range` immediately at construction. Frozen semantics mean values are immutable and validated.
   - **TypedDicts**: `Annotated` aliases self-document ranges; enforcement occurs at the Pydantic boundary layer that wraps them. Range comments in every TypedDict docstring serve as a contract for callers.

### MIDI Primitive Ranges

Defined in `app/contracts/midi_types.py`. Import from there — never define inline.

| Type alias | Range | Use |
|---|---|---|
| `MidiPitch` | 0–127 | MIDI note number |
| `MidiVelocity` | 0–127 | Note velocity (0 = note-off; 1–127 audible) |
| `MidiChannel` | 0–15 | MIDI channel (drums = 9) |
| `MidiCC` | 0–127 | CC controller number |
| `MidiCCValue` | 0–127 | CC value |
| `MidiAftertouchValue` | 0–127 | Pressure value |
| `MidiPitchBend` | −8192–8191 | 14-bit signed; 0 = centre |
| `MidiGMProgram` | 0–127 | General MIDI program (0-indexed) |
| `MidiBPM` | 20–300 | Tempo in BPM — always an integer |
| `BeatPosition` | ≥ 0.0 | Fractional beat position (note level) |
| `BeatDuration` | > 0.0 | Fractional beat duration (note level) |
| `ArrangementBeat` | ≥ 0 (int) | Bar-aligned section offset |
| `ArrangementDuration` | ≥ 1 (int) | Section duration in beats |
| `Bars` | ≥ 1 | Bar count |

**Two-tier beat position design:** Note-level timing (`BeatPosition`, `BeatDuration`) is `float` because notes can start at fractional positions (e.g., beat 1.5 = the "and" of beat 1). Section-level timing (`ArrangementBeat`, `ArrangementDuration`) is `int` because sections are always bar-aligned — `duration_beats = bars × time_signature_numerator` is always a whole number.

---

## Maestro Contracts

### `midi_types.py`

**Path:** `app/contracts/midi_types.py`

Single source of truth for all MIDI primitive ranges. Every field that carries a MIDI value imports its type alias from here instead of repeating `Field(ge=0, le=127)` inline.

All aliases are `Annotated[int, Field(...)]` or `Annotated[float, Field(...)]`, which means:
- Pydantic `BaseModel` fields pick up the constraint automatically.
- TypedDict fields use the alias for self-documentation; enforcement happens at the Pydantic boundary.
- Dataclass `__post_init__` methods call `_assert_range` (also exported from this module) for runtime enforcement without Pydantic.

See the **MIDI Primitive Ranges** table in [Design Philosophy](#design-philosophy) for the complete listing.

**Storpheus note:** `storpheus/storpheus_types.py` cannot import from `app/` (separate container). It mirrors the range constants as module-level `_MIDI_*` values and exports its own `_assert_range` helper.

---

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

Complete TypedDict hierarchy for every shape used by `LLMClient`. No `Any` lives here — every field has a concrete type. Consumers import named entities from this module; they never write `dict[str, Any]` themselves.

All streaming event consumers narrow on `event["type"]` (not `.get("type")`) to get full discriminated-union inference from mypy.

---

#### Chat message shapes

##### `ToolCallFunction`

`TypedDict` — The `function` field inside an OpenAI tool call. `arguments` is always a JSON-encoded string; callers must `json.loads` it.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Function name the model wants to call |
| `arguments` | `str` | JSON-encoded arguments string |

##### `ToolCallEntry`

`TypedDict` — One tool call in an assistant message (streaming accumulator or final response).

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique tool call ID (matches `ToolResultMessage.tool_call_id`) |
| `type` | `str` | Always `"function"` in OpenAI format |
| `function` | `ToolCallFunction` | The function being called |

##### `SystemMessage`

`TypedDict` — A system-role prompt message.

| Field | Type | Description |
|-------|------|-------------|
| `role` | `Literal["system"]` | Discriminant |
| `content` | `str` | System prompt text |

##### `UserMessage`

`TypedDict` — A user-role message.

| Field | Type | Description |
|-------|------|-------------|
| `role` | `Literal["user"]` | Discriminant |
| `content` | `str` | User message text |

##### `AssistantMessage`

`TypedDict, total=False` — An assistant reply (may be text-only or contain tool calls).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `role` | ✓ | `Literal["assistant"]` | Discriminant |
| `content` | | `str \| None` | Assistant text reply |
| `tool_calls` | | `list[ToolCallEntry]` | Tool calls requested by the model |

##### `ToolResultMessage`

`TypedDict` — A tool result message returned to the LLM after a tool call.

| Field | Type | Description |
|-------|------|-------------|
| `role` | `Literal["tool"]` | Discriminant |
| `tool_call_id` | `str` | Matches `ToolCallEntry.id` |
| `content` | `str` | JSON-encoded result string |

##### `ChatMessage`

`Union[SystemMessage, UserMessage, AssistantMessage, ToolResultMessage]` — Discriminated union of all OpenAI chat message shapes. Narrow via `msg["role"]`.

---

#### Tool schema shapes

##### `ToolParametersDict`

`TypedDict, total=False` — JSON Schema `parameters` block inside an OpenAI tool definition.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `str` | Always `"object"` |
| `properties` | `dict[str, object]` | Per-parameter schemas |
| `required` | `list[str]` | Required parameter names |

##### `ToolFunctionDict`

`TypedDict` — The `function` field of an OpenAI tool definition.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | ✓ | `str` | Tool name |
| `description` | ✓ | `str` | Shown to the model |
| `parameters` | | `ToolParametersDict` | JSON Schema for the tool's arguments |

##### `ToolSchemaDict`

`TypedDict` — A single OpenAI-format tool definition (`{type: "function", function: {...}}`).

| Field | Type | Description |
|-------|------|-------------|
| `type` | `str` | Always `"function"` |
| `function` | `ToolFunctionDict` | The tool's function definition |

---

#### Token usage shapes

##### `PromptTokenDetails`

`TypedDict, total=False` — Nested token-detail block inside `UsageStats`. OpenRouter surfaces cache data in at least two field names depending on model and API version.

| Field | Type | Description |
|-------|------|-------------|
| `cached_tokens` | `int` | Cache read hits |
| `cache_write_tokens` | `int` | Cache write/creation |

##### `UsageStats`

`TypedDict, total=False` — Token usage and cost stats returned by OpenAI/Anthropic/OpenRouter. All fields optional — the exact set varies by model and API version.

| Field | Type | Description |
|-------|------|-------------|
| `prompt_tokens` | `int` | Input tokens billed |
| `completion_tokens` | `int` | Output tokens generated |
| `total_tokens` | `int` | Sum of prompt + completion |
| `prompt_tokens_details` | `PromptTokenDetails` | Nested cache breakdown |
| `native_tokens_cached` | `int` | OR: cache read tokens (alt field name) |
| `cache_read_input_tokens` | `int` | Anthropic: cache read tokens |
| `prompt_cache_hit_tokens` | `int` | OR: cache hit tokens (alt) |
| `cache_creation_input_tokens` | `int` | Anthropic: cache write tokens |
| `prompt_cache_miss_tokens` | `int` | OR: cache miss tokens |
| `cache_discount` | `float` | Cost discount from cache (USD) |

---

#### Request payload shapes

##### `ProviderConfig`

`TypedDict, total=False` — OpenRouter provider-routing config (`payload["provider"]`). Used to lock generation to direct Anthropic for caching and reasoning token support.

| Field | Type | Description |
|-------|------|-------------|
| `order` | `list[str]` | Ordered provider preference list (e.g. `["anthropic"]`) |
| `allow_fallbacks` | `bool` | Whether to fall back if first provider fails |

##### `ReasoningConfig`

`TypedDict, total=False` — OpenRouter extended-reasoning config (`payload["reasoning"]`).

| Field | Type | Description |
|-------|------|-------------|
| `max_tokens` | `int` | Token budget for reasoning (extended thinking) |

##### `OpenAIRequestPayload`

`TypedDict, total=False` — Full request body sent to OpenRouter's chat completions endpoint. `tools` is `list[dict[str, object]]` rather than `list[ToolSchemaDict]` because prompt-caching adds an extra `cache_control` key to the last tool definition before sending.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `model` | ✓ | `str` | Model identifier (e.g. `"anthropic/claude-sonnet-4.6"`) |
| `messages` | ✓ | `list[ChatMessage]` | Conversation history |
| `temperature` | | `float` | Sampling temperature |
| `max_tokens` | | `int` | Maximum output tokens |
| `stream` | | `bool` | Enable SSE streaming |
| `tools` | | `list[dict[str, object]]` | Tool definitions (may include `cache_control`) |
| `tool_choice` | | `str \| dict[str, object]` | `"auto"` \| `"required"` \| `"none"` \| specific tool |
| `provider` | | `ProviderConfig` | OpenRouter routing config |
| `reasoning` | | `ReasoningConfig` | Extended reasoning budget |

---

#### Non-streaming response shapes

##### `ResponseFunction`

`TypedDict, total=False` — The `function` field of a tool call in a non-streaming response.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Function name |
| `arguments` | `str` | JSON-encoded arguments string |

##### `ResponseToolCall`

`TypedDict, total=False` — One tool call in a non-streaming assistant response choice.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Tool call ID |
| `type` | `str` | Always `"function"` |
| `function` | `ResponseFunction` | The function called |

##### `ResponseMessage`

`TypedDict, total=False` — The `message` field inside a non-streaming response choice.

| Field | Type | Description |
|-------|------|-------------|
| `content` | `str \| None` | Assistant text reply |
| `tool_calls` | `list[ResponseToolCall]` | Tool calls requested |

##### `ResponseChoice`

`TypedDict, total=False` — One choice in a non-streaming API response.

| Field | Type | Description |
|-------|------|-------------|
| `message` | `ResponseMessage` | The assistant message |
| `finish_reason` | `str \| None` | `"stop"` \| `"tool_calls"` \| `"length"` \| … |

##### `OpenAIResponse`

`TypedDict, total=False` — Full (non-streaming) response body from an OpenAI-compatible API.

| Field | Type | Description |
|-------|------|-------------|
| `choices` | `list[ResponseChoice]` | Candidate completions (always 1 in practice) |
| `usage` | `UsageStats` | Token usage stats |

---

#### Streaming chunk shapes

##### `ReasoningDetail`

`TypedDict, total=False` — One element of `delta.reasoning_details` in a stream chunk. OpenRouter uses `type="reasoning.text"` for incremental text and `type="reasoning.summary"` for the final consolidated summary.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `str` | `"reasoning.text"` or `"reasoning.summary"` |
| `text` | `str` | Incremental reasoning text |
| `summary` | `str` | Final reasoning summary (summary type only) |

##### `ToolCallFunctionDelta`

`TypedDict, total=False` — Incremental function info in a streaming tool call delta.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Function name (arrives in first fragment) |
| `arguments` | `str` | Arguments fragment (concatenate across deltas) |

##### `ToolCallDelta`

`TypedDict, total=False` — One tool call fragment in a streaming delta.

| Field | Type | Description |
|-------|------|-------------|
| `index` | `int` | Position in the tool_calls array (for multi-tool accumulation) |
| `id` | `str` | Tool call ID (arrives in first fragment) |
| `type` | `str` | Always `"function"` |
| `function` | `ToolCallFunctionDelta` | Incremental function data |

##### `StreamDelta`

`TypedDict, total=False` — The `delta` field inside a streaming choice.

| Field | Type | Description |
|-------|------|-------------|
| `reasoning_details` | `list[ReasoningDetail]` | Extended-thinking fragments |
| `content` | `str` | Content text fragment |
| `tool_calls` | `list[ToolCallDelta]` | Tool call fragments |

##### `StreamChoice`

`TypedDict, total=False` — One choice in a streaming SSE chunk.

| Field | Type | Description |
|-------|------|-------------|
| `delta` | `StreamDelta` | Incremental content for this chunk |
| `finish_reason` | `str \| None` | Set on the final chunk; `None` on all others |

##### `OpenAIStreamChunk`

`TypedDict, total=False` — One SSE data chunk from the OpenRouter streaming API.

| Field | Type | Description |
|-------|------|-------------|
| `choices` | `list[StreamChoice]` | Candidate chunks (always 1 in practice) |
| `usage` | `UsageStats` | Token stats (present only on the final chunk) |

---

#### Stream event shapes (yielded by `LLMClient.chat_completion_stream`)

These are the **internal** events yielded by the LLM client — they differ from the wire SSE events emitted to the DAW.

##### `ReasoningDeltaEvent`

`TypedDict` — Incremental reasoning text from an extended-thinking model.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `Literal["reasoning_delta"]` | Discriminant |
| `text` | `str` | Reasoning text fragment |

##### `ContentDeltaEvent`

`TypedDict` — Incremental content text from the model.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `Literal["content_delta"]` | Discriminant |
| `text` | `str` | Content text fragment |

##### `DoneStreamEvent`

`TypedDict` — Terminal event yielded when streaming completes. `tool_calls` holds the fully-accumulated list built up from all `ToolCallDelta` fragments — consumers should not read it before this event arrives.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `Literal["done"]` | Discriminant |
| `content` | `str \| None` | Full accumulated content (may be `None` for tool-call-only responses) |
| `tool_calls` | `list[ToolCallEntry]` | All tool calls, fully accumulated |
| `finish_reason` | `str \| None` | Final finish reason |
| `usage` | `UsageStats` | Token usage for the full request |

##### `StreamEvent`

`Union[ReasoningDeltaEvent, ContentDeltaEvent, DoneStreamEvent]` — Discriminated union of all events yielded by `chat_completion_stream`. Narrow via `event["type"]`:

```python
if event["type"] == "reasoning_delta":
    text: str = event["text"]           # mypy knows this is ReasoningDeltaEvent
elif event["type"] == "content_delta":
    text: str = event["text"]           # ContentDeltaEvent
elif event["type"] == "done":
    calls: list[ToolCallEntry] = event["tool_calls"]  # DoneStreamEvent
```

##### `OpenAIToolChoice`

Type alias: `str | dict[str, object]` — Either a string shorthand (`"auto"`, `"none"`, `"required"`) or an explicit tool-selector dict. Used in `OpenAIRequestPayload.tool_choice`.

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

### `mcp_types.py`

**Path:** `app/contracts/mcp_types.py`

Named TypedDicts for every entity in the MCP protocol layer: tool schema shapes, server capabilities, JSON-RPC 2.0 messages, and the DAW communication channel. No `dict[str, object]` is used here — every shape is named and documented.

#### Tool schema shapes

| Type | Kind | Description |
|------|------|-------------|
| `MCPInputSchema` | `TypedDict, total=False` | JSON Schema for an MCP tool's accepted arguments. `type` and `properties` are `Required`; `required` is optional. |
| `MCPToolDef` | `TypedDict, total=False` | Full definition of one MCP tool. `name`, `description`, `inputSchema` are `Required`; `server_side` is optional. |
| `MCPContentBlock` | `TypedDict` | A content block in an MCP tool result — always `{"type": "text", "text": "..."}`. |

#### Server capability shapes

| Type | Kind | Description |
|------|------|-------------|
| `MCPToolsCapability` | `TypedDict, total=False` | The `tools` entry in `MCPCapabilities`. Currently always `{}` — reserved for future metadata. |
| `MCPResourcesCapability` | `TypedDict, total=False` | The `resources` entry in `MCPCapabilities`. Currently always `{}` — reserved for future metadata. |
| `MCPCapabilities` | `TypedDict, total=False` | MCP server capabilities advertised during the `initialize` handshake. Fields: `tools`, `resources`. |
| `MCPServerInfo` | `TypedDict` | Server info returned in `initialize` responses and `get_server_info()`. Fields: `name`, `version`, `protocolVersion`, `capabilities`. |

#### JSON-RPC 2.0 message shapes

| Type | Kind | Description |
|------|------|-------------|
| `MCPRequest` | `TypedDict, total=False` | Incoming JSON-RPC 2.0 message. `jsonrpc` and `method` are `Required`; `id` (absent for notifications) and `params` (absent for no-arg methods) are optional. |
| `MCPSuccessResponse` | `TypedDict` | JSON-RPC 2.0 success response with `jsonrpc`, `id`, and `result: dict[str, object]`. |
| `MCPErrorDetail` | `TypedDict, total=False` | The `error` object in an error response. `code` and `message` are `Required`; `data` is optional. |
| `MCPErrorResponse` | `TypedDict` | JSON-RPC 2.0 error response with `jsonrpc`, `id`, and `error: MCPErrorDetail`. |
| `MCPResponse` | `Union` | `MCPSuccessResponse \| MCPErrorResponse` — discriminated union of all response shapes. |

#### DAW channel shapes

| Type | Kind | Description |
|------|------|-------------|
| `DAWToolCallMessage` | `TypedDict` | Message sent from MCP server → DAW over WebSocket to trigger tool execution. Fields: `type: Literal["toolCall"]`, `requestId: str`, `tool: str`, `arguments: dict[str, object]`. |
| `DAWToolResponse` | `TypedDict, total=False` | Response sent from DAW → MCP server after tool execution. `success: Required[bool]`; `content: list[MCPContentBlock]` and `isError: bool` are optional. |

**Deserialization boundary:** Raw WebSocket / HTTP payloads are parsed into `DAWToolResponse` by `_parse_daw_response(raw: object) -> DAWToolResponse` in `app/api/routes/mcp.py`. This is the single point where untyped JSON becomes a typed shape. The `is True` comparison (not `bool()`) ensures only JSON `true` counts as success.

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

### `StorpheusRawResponse`

**Path:** `app/services/storpheus.py`

`TypedDict, total=False` — The raw JSON response from the Storpheus `/generate` endpoint.

On success: `success=True` plus `notes`/`tool_calls`/`metadata`.
On failure: `success=False` plus `error` (and optionally `message`).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `success` | ✓ | `bool` | Whether generation completed |
| `notes` | ✓ | `list[NoteDict]` | Flat note list (wire format) |
| `tool_calls` | ✓ | `list[dict[str, object]]` | Raw tool call dicts from Storpheus |
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

### `ExpressivenessResult`

**Path:** `app/services/expressiveness.py`

`TypedDict` — Return shape of `apply_expressiveness`. The `notes` list is mutated in-place (velocity + timing humanization); `cc_events` and `pitch_bends` are freshly generated.

| Field | Type | Description |
|-------|------|-------------|
| `notes` | `list[NoteDict]` | Source notes with humanized velocity and timing, same key format (camelCase/snake_case) as input |
| `cc_events` | `list[CCEventDict]` | Generated CC automation (sustain, expression, mod wheel) |
| `pitch_bends` | `list[PitchBendDict]` | Generated pitch-bend automation |

---

## Variation Layer

**Path:** `app/variation/`

### Event Envelope payloads

**Path:** `app/variation/core/event_envelope.py`

Every variation event is wrapped in an `EventEnvelope`. The `payload` field holds one of four typed shapes depending on `envelope.type`. The union `EnvelopePayload` makes this explicit.

```
EnvelopePayload = MetaPayload | PhrasePayload | DonePayload | ErrorPayload
```

Consumers must narrow on `envelope.type` before accessing payload fields.

#### `MetaPayload`

`TypedDict, total=False` — Payload for `type="meta"` envelopes (always `sequence=1`). Describes the scope of the variation before any phrases arrive.

| Field | Type | Description |
|-------|------|-------------|
| `intent` | `str` | User's natural-language request |
| `aiExplanation` | `str \| None` | AI's top-level description of the plan |
| `affectedTracks` | `list[str]` | Track IDs that will be modified |
| `affectedRegions` | `list[str]` | Region IDs that will be modified |
| `noteCounts` | `dict[str, int]` | Per-region note counts in the base state |

#### `PhrasePayload`

`TypedDict, total=False` — Payload for `type="phrase"` envelopes. One generated MIDI phrase. Both camelCase (wire) and snake_case fallback keys are present; consumers should use camelCase.

| Field | Type | Description |
|-------|------|-------------|
| `phraseId` / `phrase_id` | `str` | Stable UUID for this phrase |
| `trackId` / `track_id` | `str` | Target DAW track |
| `regionId` / `region_id` | `str` | Target DAW region |
| `startBeat` / `start_beat` | `float` | Phrase start in beats |
| `endBeat` / `end_beat` | `float` | Phrase end in beats |
| `label` | `str` | Human-readable display label |
| `tags` | `list[str]` | Categorisation tags |
| `explanation` | `str \| None` | AI explanation for this specific phrase |
| `noteChanges` / `note_changes` | `list[dict[str, object]]` | Added/removed/modified notes (shape matches `NoteChangeDict`) |
| `ccEvents` / `cc_events` | `list[CCEventDict]` | CC automation events |
| `pitchBends` / `pitch_bends` | `list[PitchBendDict]` | Pitch-bend events |
| `aftertouch` | `list[AftertouchDict]` | Aftertouch events |

> **Why `list[dict[str, object]]` for note changes?** `noteChanges` is populated from `model_dump(by_alias=True)`, which returns `dict[str, Any]`. List invariance prevents assigning `list[dict[str, Any]]` to `list[NoteChangeDict]` in mypy. The shape is documented by `NoteChangeDict` in `json_types.py`; the type at this boundary is `dict[str, object]`.

#### `DonePayload`

`TypedDict, total=False` — Payload for `type="done"` envelopes (always last in a variation stream).

| Field | Type | Description |
|-------|------|-------------|
| `status` | `str` | `"ready"` (success) or `"failed"` |
| `phraseCount` / `phrase_count` | `int` | Total number of phrases emitted |

#### `ErrorPayload`

`TypedDict, total=False` — Payload for `type="error"` envelopes.

| Field | Type | Description |
|-------|------|-------------|
| `message` | `str` | Human-readable error description |
| `code` | `str \| None` | Optional machine-readable error code |

### `PhraseRecord`

**Path:** `app/variation/storage/variation_store.py`

`dataclass` — Persists one generated phrase for the lifetime of a variation. Held in `VariationRecord.phrases`.

| Field | Type | Description |
|-------|------|-------------|
| `phrase_id` | `str` | Stable UUID |
| `variation_id` | `str` | Parent variation UUID |
| `sequence` | `int` | Emission sequence number |
| `track_id` | `str` | Target DAW track |
| `region_id` | `str` | Target DAW region |
| `beat_start` | `float` | Phrase start in beats |
| `beat_end` | `float` | Phrase end in beats |
| `label` | `str` | Display label |
| `diff_json` | `PhrasePayload` | Full phrase payload as emitted (used by commit + retrieve routes) |
| `ai_explanation` | `str \| None` | AI explanation text |
| `tags` | `list[str]` | Categorisation tags |
| `region_start_beat` | `float \| None` | Region start — populated at store time so commit doesn't need to re-query StateStore |
| `region_duration_beats` | `float \| None` | Region duration |
| `region_name` | `str \| None` | Region display name |

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

### `_AddMidiTrackParams`

**Path:** `app/core/planner/conversion.py`

`TypedDict, total=False` — Shape of `params` for a `stori_add_midi_track` tool call built by the planner. `name`, `color`, and `icon` are always present; `gmProgram` is omitted for drum tracks (which use `drumKitId` instead).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | ✓ | `str` | Track display name |
| `color` | ✓ | `str` | Track hex colour |
| `icon` | ✓ | `str` | Track icon identifier |
| `gmProgram` | | `int` | GM program number (0–127); absent for drum tracks |

### `_AddMidiRegionParams`

**Path:** `app/core/planner/conversion.py`

`TypedDict, total=False` — Shape of `params` for a `stori_add_midi_region` tool call.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | ✓ | `str` | Region display name |
| `trackName` | ✓ | `str` | Display name of the parent track |
| `startBeat` | ✓ | `float` | Region start position in beats |
| `durationBeats` | ✓ | `float` | Region duration in beats |
| `trackId` | | `str` | Track UUID — present when targeting an existing (non-new) track |

### `_GenerateParams`

**Path:** `app/core/planner/conversion.py`

`TypedDict, total=False` — Shape of `params` for a `stori_generate_midi` (or similar) tool call.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `role` | ✓ | `str` | Instrument role (e.g. `"bass"`, `"keys"`) |
| `style` | ✓ | `str` | Normalised style string (underscores replaced with spaces) |
| `tempo` | ✓ | `int` | Project tempo in BPM |
| `bars` | ✓ | `int` | Number of bars to generate |
| `key` | ✓ | `str` | Root key (e.g. `"Am"`) |
| `trackName` | ✓ | `str` | Target track display name |
| `constraints` | | `dict[str, object]` | Per-role generation constraints (intentional open shape — populated from emotion vector) |
| `trackId` | | `str` | Track UUID — present when targeting an existing track |

> **Note:** All three planner TypedDicts (`_AddMidiTrackParams`, `_AddMidiRegionParams`, `_GenerateParams`) exist for documentation only. The local variables in `_schema_to_tool_calls` are annotated as `dict[str, object]` because mypy's dict invariance prevents assigning a TypedDict to `dict[str, object]`. The TypedDicts define the shape; the annotation preserves compatibility with `ToolCall.params`.

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

**Path:** `storpheus/storpheus_types.py`

These types mirror the Maestro `app/contracts/json_types.py` types but are defined independently to avoid cross-container imports. Storpheus uses **snake_case** internally; camelCase types (like `WireNoteDict`) are used only at the API boundary.

### MIDI Event Types

#### `StorpheusNoteDict`

`TypedDict, total=False` — A single MIDI note as parsed from a MIDI file. Internal representation (snake_case).

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `pitch` | ✓ | `int` | MIDI pitch (0–127) |
| `start_beat` | ✓ | `float` | Note onset in beats |
| `duration_beats` | ✓ | `float` | Note duration in beats |
| `velocity` | ✓ | `int` | MIDI velocity (0–127) |

#### `StorpheusCCEvent`

`TypedDict` — A MIDI Control Change event.

| Field | Type | Description |
|-------|------|-------------|
| `cc` | `int` | CC number (0–127) |
| `beat` | `float` | Event position in beats |
| `value` | `int` | CC value (0–127) |

#### `StorpheusPitchBend`

`TypedDict` — A MIDI Pitch Bend event.

| Field | Type | Description |
|-------|------|-------------|
| `beat` | `float` | Event position in beats |
| `value` | `int` | Pitch bend value (-8192 to 8191) |

#### `StorpheusAftertouch`

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
| `notes` | `dict[int, list[StorpheusNoteDict]]` | Notes per channel |
| `cc_events` | `dict[int, list[StorpheusCCEvent]]` | CC events per channel |
| `pitch_bends` | `dict[int, list[StorpheusPitchBend]]` | Pitch bends per channel |
| `aftertouch` | `dict[int, list[StorpheusAftertouch]]` | Aftertouch per channel |
| `program_changes` | `dict[int, int]` | Program number per channel |

#### `WireNoteDict`

`TypedDict` — A single MIDI note in the camelCase wire format sent to Maestro. Used **only** in `GenerateResponse` fields that cross the HTTP boundary. All internal processing uses `StorpheusNoteDict`.

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
| `notes` | `list[StorpheusNoteDict]` | Notes to evaluate |

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
| `flat_notes` | `list[StorpheusNoteDict]` | Flattened note list across all channels |
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

## HTTP Response Entities

> Updated: 2026-02-26 | Reflects the named-entity sweep that eliminated all `dict[str, object]` and `dict[str, str]` return types from route handlers.

All HTTP route handlers return **named Pydantic `BaseModel` entities**, never anonymous dicts. This makes the wire contract explicit, type-safe, and self-documenting. Every entity below has a class docstring and per-field `Field(description=...)` annotation in the source.

**Base classes:**
- `BaseModel` — plain Pydantic v2 model; wire format is snake_case.
- `CamelModel` — extends `BaseModel` with `alias_generator=to_camel`; wire format is camelCase (matches the Stori DAW Swift convention). Routes using `CamelModel` responses must set `response_model_by_alias=True` on the decorator.

---

### Protocol Introspection (`app/protocol/responses.py`)

#### `ProtocolInfoResponse`

`BaseModel` — `GET /api/v1/protocol`

Lightweight version/hash/event-list snapshot. Used for polling and drift detection.

| Field | Type | Description |
|-------|------|-------------|
| `protocolVersion` | `str` | Semver string from `pyproject.toml` (e.g. `"1.4.2"`) |
| `protocolHash` | `str` | SHA-256 hex hash of the full serialised schema |
| `eventTypes` | `list[str]` | Alphabetically sorted registered SSE event type names |
| `eventCount` | `int` | `len(eventTypes)` |

#### `ProtocolEventsResponse`

`BaseModel` — `GET /api/v1/protocol/events.json`

Full JSON Schema for every registered SSE event type.

| Field | Type | Description |
|-------|------|-------------|
| `protocolVersion` | `str` | Protocol version that produced these schemas |
| `events` | `EventSchemaMap` | `dict[str, dict[str, object]]` — event name → JSON Schema object |

#### `ProtocolToolsResponse`

`BaseModel` — `GET /api/v1/protocol/tools.json`

All registered MCP tool definitions in MCP wire format.

| Field | Type | Description |
|-------|------|-------------|
| `protocolVersion` | `str` | Protocol version that produced these definitions |
| `tools` | `list[MCPToolDef]` | Ordered list of MCP tool definitions |
| `toolCount` | `int` | `len(tools)` |

#### `ProtocolSchemaResponse`

`BaseModel` — `GET /api/v1/protocol/schema.json`

Unified schema snapshot. Cacheable by `protocolHash`. The DAW frontend uses this for full Swift type generation.

| Field | Type | Description |
|-------|------|-------------|
| `protocolVersion` | `str` | Protocol version that produced this snapshot |
| `protocolHash` | `str` | SHA-256 hex content hash of this snapshot |
| `events` | `EventSchemaMap` | `dict[str, dict[str, object]]` — all event schemas |
| `enums` | `EnumDefinitionMap` | `dict[str, list[str]]` — enum name → sorted allowed values |
| `tools` | `list[MCPToolDef]` | All registered tool definitions |
| `toolCount` | `int` | `len(tools)` |
| `eventCount` | `int` | `len(events)` |

---

### Muse VCS (`app/api/routes/muse.py`)

#### `SaveVariationResponse`

`BaseModel` — `POST /api/v1/muse/variations`

Confirmation that a variation was persisted.

| Field | Type | Description |
|-------|------|-------------|
| `variation_id` | `str` | UUID of the variation that was saved |

#### `SetHeadResponse`

`BaseModel` — `POST /api/v1/muse/head`

Confirmation that the HEAD pointer was moved.

| Field | Type | Description |
|-------|------|-------------|
| `head` | `str` | UUID of the variation that is now HEAD |

#### `CheckoutExecutionStats`

`BaseModel` — shared sub-entity embedded in `CheckoutResponse` and `MergeResponse`.

Execution statistics from a single plan-execution pass.

| Field | Type | Description |
|-------|------|-------------|
| `executed` | `int` | Tool-call steps executed successfully |
| `failed` | `int` | Tool-call steps that failed (non-zero = partial checkout) |
| `plan_hash` | `str` | SHA-256 content hash of the checkout plan (hex) |
| `events` | `list[dict[str, object]]` | SSE event payloads emitted during execution, in order |

#### `CheckoutResponse`

`BaseModel` — `POST /api/v1/muse/checkout`

Full summary of a checkout operation (musical `git checkout`). Returns `409` instead if working tree is dirty and `force=false`.

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | `str` | UUID of the project |
| `from_variation_id` | `str \| None` | Previous HEAD variation UUID (null if project had no HEAD) |
| `to_variation_id` | `str` | New HEAD variation UUID after checkout |
| `execution` | `CheckoutExecutionStats` | Plan execution statistics |
| `head_moved` | `bool` | Whether HEAD was successfully updated |

#### `MergeResponse`

`BaseModel` — `POST /api/v1/muse/merge`

Full summary of a three-way merge (musical `git merge`). Returns `409` with conflict details instead if the merge cannot auto-resolve.

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | `str` | UUID of the project |
| `merge_variation_id` | `str` | UUID of the new merge commit (has two parents) |
| `left_id` | `str` | UUID of the left branch tip |
| `right_id` | `str` | UUID of the right branch tip |
| `execution` | `CheckoutExecutionStats` | Plan execution statistics for the merge-checkout pass |
| `head_moved` | `bool` | Whether HEAD was moved to the merge commit |

#### `MuseLogNodeResponse`

`BaseModel` — embedded in `MuseLogGraphResponse`. Produced by `MuseLogNode.to_response()`.

Wire representation of a single commit node in the Muse DAG. All field names are camelCase (by declaration, not via `CamelModel`).

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | UUID of this variation (commit) |
| `parent` | `str \| None` | UUID of the first parent (null for root commit) |
| `parent2` | `str \| None` | UUID of the second parent (null for non-merge commits) |
| `isHead` | `bool` | True if this is the current HEAD variation |
| `timestamp` | `float` | POSIX timestamp (seconds since epoch) of commit time |
| `intent` | `str \| None` | Free-text intent supplied at commit time |
| `regions` | `list[str]` | Region IDs affected by this variation |

**Method:**

| Method | Returns | Description |
|--------|---------|-------------|
| `to_response()` (on `MuseLogNode`) | `MuseLogNodeResponse` | Converts internal `MuseLogNode` dataclass to this wire entity. Translates snake_case field names to camelCase and converts `affected_regions` tuple to list. |

#### `MuseLogGraphResponse`

`BaseModel` — `GET /api/v1/muse/log`. Produced by `MuseLogGraph.to_response()`.

Full commit DAG for a project, topologically sorted.

| Field | Type | Description |
|-------|------|-------------|
| `projectId` | `str` | UUID of the project |
| `head` | `str \| None` | UUID of the current HEAD variation (null if none set) |
| `nodes` | `list[MuseLogNodeResponse]` | Topologically sorted nodes (parents before children) |

**Method:**

| Method | Returns | Description |
|--------|---------|-------------|
| `to_response()` (on `MuseLogGraph`) | `MuseLogGraphResponse` | Converts internal `MuseLogGraph` dataclass to this wire entity. Calls `MuseLogNode.to_response()` on each node in order. |

---

### Maestro Core (`app/api/routes/maestro.py`)

All entities extend `CamelModel` (wire format is camelCase; routes use `response_model_by_alias=True`).

#### `ValidateTokenResponse`

`CamelModel` — `GET /api/v1/validate-token`

JWT validation result. Budget fields are populated when the `sub` claim resolves to a known user.

| Field | Type | Wire key | Description |
|-------|------|----------|-------------|
| `valid` | `bool` | `valid` | Always `True` — endpoint raises `401` instead of returning `False` |
| `expires_at` | `str` | `expiresAt` | ISO-8601 UTC timestamp of token expiry |
| `expires_in_seconds` | `int` | `expiresInSeconds` | Seconds until expiry, clamped to `0` if past |
| `budget_remaining` | `float \| None` | `budgetRemaining` | Remaining credit balance in cents; `null` if user record unavailable |
| `budget_limit` | `float \| None` | `budgetLimit` | Maximum credit balance in cents; `null` if user record unavailable |

#### `PlanPreviewResponse`

`CamelModel` — embedded in `PreviewMaestroResponse`. Maps directly from `PlanPreview` TypedDict.

| Field | Type | Wire key | Description |
|-------|------|----------|-------------|
| `valid` | `bool \| None` | `valid` | True if the plan passed validation |
| `total_steps` | `int \| None` | `totalSteps` | Total tool-call steps in the plan |
| `generations` | `int \| None` | `generations` | Number of generation tool calls (Tier 1) |
| `edits` | `int \| None` | `edits` | Number of editor tool calls (Tier 2) |
| `tool_calls` | `list[dict[str, object]]` | `toolCalls` | Ordered tool-call descriptors |
| `notes` | `list[str]` | `notes` | Planner annotations (e.g. tempo/key inferences) |
| `errors` | `list[str]` | `errors` | Validation errors that make the plan unexecutable |
| `warnings` | `list[str]` | `warnings` | Non-fatal warnings |

#### `PreviewMaestroResponse`

`CamelModel` — `POST /api/v1/maestro/preview`

Top-level preview envelope. `preview` is populated only when `previewAvailable=true`.

| Field | Type | Wire key | Description |
|-------|------|----------|-------------|
| `preview_available` | `bool` | `previewAvailable` | True if the intent supports previews (COMPOSING only) |
| `intent` | `str` | `intent` | Classified intent value (`"COMPOSING"`, `"REASONING"`, etc.) |
| `sse_state` | `str` | `sseState` | SSE state string (`"composing"`, `"reasoning"`, etc.) |
| `reason` | `str \| None` | `reason` | Why preview is unavailable; `null` when `previewAvailable=true` |
| `preview` | `PlanPreviewResponse \| None` | `preview` | The generated plan; `null` when `previewAvailable=false` |

---

### MCP Endpoints (`app/api/routes/mcp.py`)

All entities extend `CamelModel`.

#### `ConnectionCreatedResponse`

`CamelModel` — `POST /api/v1/mcp/connection`

Server-issued connection ID for the MCP SSE flow. Valid for 5 minutes.

| Field | Type | Wire key | Description |
|-------|------|----------|-------------|
| `connection_id` | `str` | `connectionId` | Server-issued UUID. Use in `/mcp/stream/{id}` and `/mcp/response/{id}` |

#### `ToolResponseReceivedResponse`

`CamelModel` — `POST /api/v1/mcp/response/{connection_id}`

Acknowledgement that the DAW's tool-execution result was received.

| Field | Type | Wire key | Description |
|-------|------|----------|-------------|
| `status` | `str` | `status` | Always `"ok"` on success; endpoint raises `404` for invalid connection IDs |

---

### Variation Endpoints

#### `DiscardVariationResponse`

`BaseModel` — `POST /api/v1/variation/discard`

Acknowledgement that a variation was discarded (or was already in a discarded/not-found state). Endpoint raises `409` for non-discardable terminal states.

| Field | Type | Description |
|-------|------|-------------|
| `ok` | `bool` | Always `True` in the response body |

#### `VariationPhraseResponse`

`CamelModel` — embedded in `GetVariationResponse`

One generated MIDI phrase within a polled variation.

| Field | Type | Wire key | Description |
|-------|------|----------|-------------|
| `phrase_id` | `str` | `phraseId` | Stable UUID assigned at generation time |
| `sequence` | `int` | `sequence` | Monotonically increasing index within the variation |
| `track_id` | `str` | `trackId` | DAW track this phrase belongs to |
| `region_id` | `str` | `regionId` | DAW region this phrase occupies |
| `beat_start` | `float` | `beatStart` | Phrase start position in beats |
| `beat_end` | `float` | `beatEnd` | Phrase end position in beats (`beat_end − beat_start` = duration) |
| `label` | `str` | `label` | Human-readable display label |
| `tags` | `list[str]` | `tags` | Categorisation tags |
| `ai_explanation` | `str \| None` | `aiExplanation` | Natural-language explanation of what was generated |
| `diff` | `PhrasePayload` | `diff` | Full phrase payload as originally emitted (see [PhrasePayload](#phraserecord) above) |

#### `GetVariationResponse`

`CamelModel` — `GET /api/v1/variation/{variation_id}`

Full variation status and phrase payload for polling / reconnect clients.

| Field | Type | Wire key | Description |
|-------|------|----------|-------------|
| `variation_id` | `str` | `variationId` | UUID of this variation |
| `project_id` | `str` | `projectId` | Project UUID |
| `base_state_id` | `str` | `baseStateId` | StateStore snapshot ID at generation start (typically `"muse"`) |
| `intent` | `str` | `intent` | User's natural-language intent |
| `status` | `str` | `status` | `"streaming"` \| `"committed"` \| `"discarded"` \| `"error"` \| `"pending"` |
| `ai_explanation` | `str \| None` | `aiExplanation` | Top-level AI explanation of the variation |
| `affected_tracks` | `list[str]` | `affectedTracks` | Track IDs modified by this variation |
| `affected_regions` | `list[str]` | `affectedRegions` | Region IDs modified by this variation |
| `phrases` | `list[VariationPhraseResponse]` | `phrases` | All phrases, ordered by `sequence` ascending |
| `phrase_count` | `int` | `phraseCount` | `len(phrases)` |
| `last_sequence` | `int` | `lastSequence` | Sequence number of the most recent phrase |
| `created_at` | `str` | `createdAt` | ISO-8601 UTC creation timestamp |
| `updated_at` | `str` | `updatedAt` | ISO-8601 UTC last-updated timestamp |
| `error_message` | `str \| None` | `errorMessage` | Error description when `status == "error"` |

---

### Conversations (`app/api/routes/conversations/models.py`)

#### `ConversationUpdateResponse`

`CamelModel` — `PATCH /api/v1/conversations/{conversation_id}`

Minimal confirmation of a successful conversation metadata update. Contains only the fields that may have changed.

| Field | Type | Wire key | Description |
|-------|------|----------|-------------|
| `id` | `str` | `id` | UUID of the conversation that was updated |
| `title` | `str` | `title` | Current title after the update |
| `project_id` | `str \| None` | `projectId` | Linked project UUID; `null` if unlinked (`project_id: "null"` in request) |
| `updated_at` | `str` | `updatedAt` | ISO-8601 UTC timestamp of the modification |

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

## `Any` Status

`Any` does not appear in any production app file. The table below summarises how every historical use was eliminated:

| Location | Old pattern | Replacement |
|----------|-------------|-------------|
| `app/contracts/llm_types.py` | `OpenAIData = dict[str, Any]` + aliases | Full TypedDict hierarchy (see above) |
| `app/db/muse_models.py` | `list[dict[str, Any]]` mapped columns | `list[CCEventDict]`, `list[PitchBendDict]`, etc. |
| `app/services/expressiveness.py` | `dict[str, Any]` return type | `ExpressivenessResult(TypedDict)` |
| `app/protocol/hash.py` | `cast(JSONValue, ...)` calls | `list[dict[str, object]]` return types |
| `app/core/llm_client.py` | `or {}` default patterns | Explicit `None` narrowing with `if chunk is None: continue` |

### Remaining `dict[str, object]` uses

`dict[str, object]` is the correct type for genuinely polymorphic bags (e.g. tool call `params`, generation `metadata`). It is **not** `Any` — mypy requires explicit narrowing before any field access, making all assumptions visible.

### External library boundary

The only `cast()` that survives is in `app/services/assets.py`:

```python
return cast(_S3Client, boto3.client("s3", ...))
```

This is a structural Protocol cast at the boto3 boundary (boto3 ships no type stubs). The `_S3Client` Protocol documents every method we call — the cast is the API contract.

### Boundary rule

When external data arrives from JSON/HTTP with unknown structure, it is immediately coerced to a named type at the boundary function. Downstream code always receives typed values.

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
│   ├── llm_types.py                   — complete TypedDict hierarchy, no Any
│   │   │
│   │   ├── Chat messages (discriminated on role)
│   │   │   ├── ToolCallFunction       — function{name, arguments} inside a tool call
│   │   │   ├── ToolCallEntry          — one complete tool call {id, type, function}
│   │   │   ├── SystemMessage          — role:"system"
│   │   │   ├── UserMessage            — role:"user"
│   │   │   ├── AssistantMessage       — role:"assistant" (+ optional tool_calls)
│   │   │   ├── ToolResultMessage      — role:"tool"
│   │   │   └── ChatMessage            — Union of all four (discriminated on role)
│   │   │
│   │   ├── Tool schemas
│   │   │   ├── ToolParametersDict     — JSON Schema parameters block
│   │   │   ├── ToolFunctionDict       — {name, description, parameters}
│   │   │   └── ToolSchemaDict         — {type:"function", function:ToolFunctionDict}
│   │   │
│   │   ├── Token usage
│   │   │   ├── PromptTokenDetails     — nested cache token breakdown
│   │   │   └── UsageStats             — all OpenRouter/Anthropic usage fields
│   │   │
│   │   ├── Request payload
│   │   │   ├── ProviderConfig         — OpenRouter provider routing {order, allow_fallbacks}
│   │   │   ├── ReasoningConfig        — extended reasoning {max_tokens}
│   │   │   └── OpenAIRequestPayload   — full request body to OpenRouter
│   │   │
│   │   ├── Non-streaming response
│   │   │   ├── ResponseFunction       — {name, arguments} in response tool call
│   │   │   ├── ResponseToolCall       — one tool call in response
│   │   │   ├── ResponseMessage        — {content, tool_calls}
│   │   │   ├── ResponseChoice         — {message, finish_reason}
│   │   │   └── OpenAIResponse         — full non-streaming response body
│   │   │
│   │   ├── Streaming chunks
│   │   │   ├── ReasoningDetail        — one reasoning_details element
│   │   │   ├── ToolCallFunctionDelta  — incremental {name, arguments}
│   │   │   ├── ToolCallDelta          — incremental tool call fragment
│   │   │   ├── StreamDelta            — {reasoning_details, content, tool_calls}
│   │   │   ├── StreamChoice           — {delta, finish_reason}
│   │   │   └── OpenAIStreamChunk      — one SSE data line {choices, usage}
│   │   │
│   │   └── Stream events (yielded by LLMClient.chat_completion_stream)
│   │       ├── ReasoningDeltaEvent    — type:"reasoning_delta", text
│   │       ├── ContentDeltaEvent      — type:"content_delta", text
│   │       ├── DoneStreamEvent        — type:"done", content, tool_calls, usage
│   │       ├── StreamEvent            — Union of all three (discriminated on type)
│   │       └── OpenAIToolChoice       — str | dict[str, object] (request tool_choice)
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
├── HTTP Response Entities (Pydantic BaseModel — wire contract layer)
│   │  All route handlers return a named entity; no anonymous dicts.
│   │  BaseModel = snake_case wire.  CamelModel = camelCase wire.
│   │
│   ├── app/protocol/responses.py
│   │   ├── ProtocolInfoResponse       — GET /protocol (version+hash+event list)
│   │   ├── ProtocolEventsResponse     — GET /protocol/events.json (all schemas)
│   │   ├── ProtocolToolsResponse      — GET /protocol/tools.json (all tools)
│   │   └── ProtocolSchemaResponse     — GET /protocol/schema.json (unified snapshot)
│   │
│   ├── app/services/muse_log_graph.py
│   │   ├── MuseLogNodeResponse        — one commit node in the DAG (camelCase fields)
│   │   └── MuseLogGraphResponse       — full DAG for a project (camelCase fields)
│   │
│   ├── app/api/routes/muse.py
│   │   ├── SaveVariationResponse      — POST /muse/variations confirmation
│   │   ├── SetHeadResponse            — POST /muse/head confirmation
│   │   ├── CheckoutExecutionStats     — shared sub-entity (checkout + merge)
│   │   ├── CheckoutResponse           — POST /muse/checkout full summary
│   │   └── MergeResponse              — POST /muse/merge full summary
│   │
│   ├── app/api/routes/maestro.py      — all CamelModel, response_model_by_alias=True
│   │   ├── ValidateTokenResponse      — GET /validate-token
│   │   ├── PlanPreviewResponse        — sub-entity embedded in PreviewMaestroResponse
│   │   └── PreviewMaestroResponse     — POST /maestro/preview
│   │
│   ├── app/api/routes/mcp.py          — all CamelModel
│   │   ├── ConnectionCreatedResponse  — POST /mcp/connection
│   │   └── ToolResponseReceivedResponse — POST /mcp/response/{id}
│   │
│   ├── app/api/routes/variation/
│   │   ├── DiscardVariationResponse   — POST /variation/discard (BaseModel)
│   │   ├── VariationPhraseResponse    — sub-entity for GetVariationResponse (CamelModel)
│   │   └── GetVariationResponse       — GET /variation/{id} (CamelModel)
│   │
│   └── app/api/routes/conversations/models.py
│       └── ConversationUpdateResponse — PATCH /conversations/{id} (CamelModel)
│
├── Services (app/services/)
│   ├── assets.py
│   │   ├── DrumKitInfo                — drum kit manifest entry
│   │   ├── SoundFontInfo              — soundfont manifest entry
│   │   ├── _S3StreamingBody           — Protocol: boto3 streaming body
│   │   ├── _GetObjectResponse         — TypedDict: boto3 get_object response
│   │   └── _S3Client                  — Protocol: boto3 S3 client interface
│   │
│   ├── backends/storpheus.py (via services/storpheus.py)
│   │   └── StorpheusRawResponse         — raw HTTP response from Storpheus service
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


Storpheus Service (storpheus/)
│
└── storpheus_types.py
    │
    ├── MIDI Events
    │   ├── StorpheusNoteDict            — note (snake_case, internal)
    │   ├── StorpheusCCEvent             — CC event
    │   ├── StorpheusPitchBend           — pitch bend event
    │   └── StorpheusAftertouch          — aftertouch event (channel/poly)
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
