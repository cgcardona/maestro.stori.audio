# Stori Maestro — Type Contracts Reference

> Generated: 2026-02-26 | Reflects the mypy hardening sweep that eliminated ~1,800 `Any` usages.

This document is the single source of truth for every named entity (TypedDict, dataclass, type alias) in the Maestro and Orpheus codebases. It covers the full API surface of each type: fields, types, optionality, and intended use.

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Maestro Contracts (`app/contracts/`)](#maestro-contracts)
   - [generation_types.py](#generation_typespy)
   - [llm_types.py](#llm_typespy)
   - [json_types.py (key types)](#json_typespy)
   - [project_types.py (key types)](#project_typespy)
3. [Auth (`app/auth/tokens.py`)](#auth)
4. [Services](#services)
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
8. [Tempo Convention](#tempo-convention)
9. [The `Any` Quarantine](#the-any-quarantine)
10. [Entity Hierarchy](#entity-hierarchy)

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

**Path:** `app/contracts/json_types.py` (selected key types)

#### `NoteDict`

`TypedDict` — A single MIDI note in the camelCase wire format used across the Maestro<→>DAW boundary.

| Field | Type | Description |
|-------|------|-------------|
| `pitch` | `int` | MIDI pitch (0–127) |
| `startBeat` | `float` | Note onset in beats |
| `durationBeats` | `float` | Note duration in beats |
| `velocity` | `int` | MIDI velocity (0–127) |

#### `ToolCallDict`

`TypedDict` — Shape of a collected tool call in `CompleteEvent.tool_calls`.

| Field | Type | Description |
|-------|------|-------------|
| `tool` | `str` | Tool name (e.g. `"stori_add_notes"`) |
| `params` | `dict[str, Any]` | LLM-generated tool arguments (genuinely polymorphic) |

---

### `project_types.py`

**Path:** `app/contracts/project_types.py` (selected key types)

#### `ProjectContext`

`TypedDict, total=False` — The current DAW project state as sent from the frontend.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Project UUID (canonical ID — `trackId` is removed) |
| `name` | `str` | Project display name |
| `tempo` | `int` | Project tempo in BPM |
| `key` | `str` | Root key (e.g. `"Am"`) |
| `timeSignature` | `str \| TimeSignatureDict` | Time signature |
| `tracks` | `list[ProjectTrack]` | All tracks in the project |
| `buses` | `list[BusDict]` | All buses in the project |

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
│   │   ├── GenerationContext          — backend kwargs
│   │   ├── CompositionContext         — per-turn generation context
│   │   ├── RoleResult                 — per-instrument outcome
│   │   └── UnifiedGenerationOutput    — full generation return value
│   │
│   ├── llm_types.py
│   │   ├── OpenAIData                 — Any quarantine alias
│   │   ├── OpenAIStreamChunk/Tool/Response/etc.  — LLM boundary types
│   │   ├── ToolCallFunction/Entry     — tool call wire format
│   │   ├── SystemMessage/UserMessage  — chat message types
│   │   ├── AssistantMessage           — assistant reply (with tool_calls)
│   │   ├── ToolResultMessage          — tool result back to LLM
│   │   └── ChatMessage                — discriminated union of all messages
│   │
│   ├── json_types.py
│   │   ├── NoteDict                   — MIDI note (camelCase, DAW wire format)
│   │   ├── CCEventDict                — MIDI CC event
│   │   ├── PitchBendDict              — MIDI pitch bend
│   │   ├── AftertouchDict             — MIDI aftertouch
│   │   ├── ToolCallDict               — collected tool call in SSE events
│   │   ├── JSONScalar/JSONValue/JSONObject  — JSON primitive types
│   │   └── SSEEventInput              — SSE event dict
│   │
│   └── project_types.py
│       ├── ProjectContext             — DAW project state (from frontend)
│       ├── ProjectTrack               — a single DAW track
│       ├── BusDict                    — a mixer bus
│       └── TimeSignatureDict          — time signature representation
│
├── Auth (app/auth/)
│   └── tokens.py
│       ├── TokenClaims                — decoded JWT payload
│       └── AccessCodeError            — validation failure exception
│
├── Services (app/services/)
│   ├── orpheus.py
│   │   └── OrpheusRawResponse         — raw HTTP response from Storpheus service
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
            ├── channel_notes: dict[str, list[NoteDict]] | None
            └── error/cc_events/pitch_bends/aftertouch


Orpheus Service (storpheus/)
│
└── orpheus_types.py
    │
    ├── MIDI Events
    │   ├── OrpheusNoteDict            — note (snake_case, internal)
    │   ├── OrpheusCCEvent             — CC event
    │   ├── OrpheusPitchBend           — pitch bend event
    │   └── OrpheusAftertouch          — aftertouch event
    │
    ├── Pipeline
    │   ├── ParsedMidiResult           — full parse output (all channels)
    │   ├── WireNoteDict               — note (camelCase, API boundary only)
    │   ├── CacheKeyData               — cache key fields
    │   ├── FulfillmentReport          — constraint satisfaction report
    │   ├── GradioGenerationParams     — parameters for Gradio inference
    │   ├── GenerationComparison       — A/B candidate comparison
    │   ├── QualityEvalParams          — /quality/evaluate input
    │   └── QualityEvalToolCall        — single tool call for quality eval
    │
    └── Scoring
        ├── ScoringParams              — @dataclass, candidate scoring config
        └── BestCandidate              — @dataclass, winning generation candidate
```
