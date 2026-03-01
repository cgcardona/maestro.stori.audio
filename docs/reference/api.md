# API & MCP tools reference

Streaming (SSE), event types, models, and the full MCP tool set in one place. Tool definitions live in `maestro/daw/stori/tools/`; validation in `maestro/core/tool_validation/`.

---

## Compose stream

**Endpoint:** `POST /api/v1/maestro/stream`  
**Auth:** `Authorization: Bearer <token>`  
**Body:** JSON with `prompt`, optional `project` (app state), `conversation_id`, `model`, and optional generation hints.  
**Response:** SSE stream of JSON objects; each has a `type` field.

The backend determines execution mode from intent classification: COMPOSING -> variation (human review), EDITING -> apply (immediate). See [architecture.md](architecture.md).

The `prompt` field accepts both natural language and the **Maestro structured prompt** format. When a prompt begins with `MAESTRO PROMPT`, it is parsed as a structured prompt and routed deterministically by the `Mode` field, bypassing NL classification. See [maestro_prompt_spec.md](../protocol/maestro_prompt_spec.md).

### Request body fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | yes | Natural language or MAESTRO PROMPT text |
| `project` | object | no | Full DAW project snapshot (tracks, regions, buses, tempo, key). See [fe_project_state_sync.md](../guides/fe_project_state_sync.md). |
| `conversationId` | string (UUID) | no | Conversation ID for multi-turn context. Send the same ID for every request in a session. |
| `model` | string | no | LLM model override. Supported: `anthropic/claude-sonnet-4.6` (default), `anthropic/claude-opus-4.6`. |
| `storePrompt` | bool | no | Whether to store prompt for training data (default `true`) |
| `qualityPreset` | string | no | Orpheus generation quality: `"fast"` \| `"balanced"` \| `"quality"`. Default `"quality"`. Use `"fast"` or `"balanced"` only when optimising for iteration speed over output quality. |

---

## SSE event types

All events are newline-delimited `data: {json}\n\n` lines. Every event has a `type` field and a monotonic `seq` integer (assigned at the route layer, starting at 0 per request, incrementing 0, 1, 2, ...). **Keys are camelCase.**

`seq` is the canonical ordering key for Agent Teams events that may arrive out of order. Frontend should sort by `seq` when reconstructing timeline order if needed. `seq` resets to 0 at the start of each new conversation turn.

### Events emitted in all modes

| type | Description |
|------|-------------|
| `state` | Intent classification result. **First meaningful event.** `{ "type": "state", "state": "editing" \| "composing" \| "reasoning", "intent": "..." }` |
| `reasoning` | LLM chain-of-thought chunk (streamed). `{ "type": "reasoning", "content": "..." }` |
| `content` | Final user-facing text response. `{ "type": "content", "content": "..." }` |
| `error` | Error message (non-fatal or fatal). `{ "type": "error", "error": "...", "message": "..." }` |
| `complete` | **Always the final event**, even on errors. `{ "type": "complete", "success": true \| false, "traceId": "...", "inputTokens": 42350, "contextWindowTokens": 200000 }`. `success` is `false` when: (a) an error aborted the stream, or (b) tool errors occurred and zero notes were generated (generation failed silently). `inputTokens` = full input tokens sent to the model this turn (from `usage.prompt_tokens`; reflects the entire context window occupied, including history, system prompt, and tools). `contextWindowTokens` = model capacity (200 000 for all supported Claude models). Both are `0` if unavailable — frontend should leave any usage display at its previous value in that case. |

### EDITING mode events

Emitted when `state.state == "editing"`. Applied immediately by the frontend.

| type | Description |
|------|-------------|
| `plan` | Structured plan emitted once after initial reasoning, before the first tool call. Steps are ordered per-track (contiguous) so each instrument's steps appear together. Labels follow canonical patterns for frontend timeline grouping. `toolName` is present when the step maps to a specific tool, omitted otherwise. Instrument steps carry `parallelGroup: "instruments"` — steps sharing the same `parallelGroup` value execute concurrently (see [Parallel execution](#parallel-execution) below). `{ "type": "plan", "planId": "uuid", "title": "Building Lo-Fi Groove", "steps": [{ "stepId": "1", "label": "Set tempo to 72 BPM", "toolName": "stori_set_tempo", "status": "pending" }, { "stepId": "2", "label": "Set key signature to Cm", "toolName": "stori_set_key", "status": "pending" }, { "stepId": "3", "label": "Create Drums track", "toolName": "stori_add_midi_track", "parallelGroup": "instruments", "status": "pending" }, { "stepId": "4", "label": "Add content to Drums", "toolName": "stori_add_notes", "parallelGroup": "instruments", "detail": "8 bars, boom bap drums", "status": "pending" }, { "stepId": "5", "label": "Add effects to Drums", "toolName": "stori_add_insert_effect", "parallelGroup": "instruments", "detail": "Compressor", "status": "pending" }, ...] }`. See [Execution Timeline contract](#execution-timeline-contract) below. |
| `preflight` | Emitted before Phase 2 agents start (Agent Teams only). One per expected instrument step, derived from the plan — no LLM call. Lets the frontend pre-allocate timeline rows. Includes `trackColor` (hex) from the curated 12-color composition palette. `{ "type": "preflight", "stepId": "3", "agentId": "drums", "agentRole": "drums", "label": "Create Drums track", "toolName": "stori_add_midi_track", "parallelGroup": "instruments", "confidence": 0.9, "trackColor": "#E85D75" }` |
| `planStepUpdate` | Step lifecycle update. `active` when starting, `completed` / `failed` when done. At plan completion, steps never activated are emitted as `skipped` — no step is left in `pending`. Always includes `phase`. `{ "type": "planStepUpdate", "stepId": "1", "status": "active" \| "completed" \| "failed" \| "skipped", "phase": "setup", "result": "optional summary" }` |
| `toolStart` | Fires **before** each `toolCall` with a human-readable label and composition phase. `{ "type": "toolStart", "name": "stori_add_midi_track", "label": "Create Drums track", "phase": "composition" }` |
| `toolCall` | Resolved tool call for the frontend to apply. In Agent Teams mode, includes `agentId` identifying which instrument agent produced it. Includes `label` (matching the preceding `toolStart`) and `phase`. `{ "type": "toolCall", "id": "...", "name": "stori_add_midi_track", "label": "Create Drums track", "phase": "composition", "params": { "trackId": "uuid", ... }, "agentId": "drums" }`. **Critical: key is `"params"` (not `"arguments"`); key is `"name"` (not `"tool"`). All IDs are fully-resolved UUIDs.** |
| `toolError` | Non-fatal validation error. Stream continues. `{ "type": "toolError", "name": "stori_add_notes", "error": "Region not found", "errors": ["..."] }` |
| `agentComplete` | Emitted when an instrument agent finishes all its work (success or failure). Lets the frontend distinguish "agent done" from "agent between tool calls." `{ "type": "agentComplete", "agentId": "drums", "success": true }` |
| `summary.final` | Emitted by Agent Teams handler immediately before `complete`. Rich composition summary for the "Ready!" line. `{ "type": "summary.final", "traceId": "...", "trackCount": 3, "tracksCreated": [{"name": "Drums", "instrument": "TR-808", "trackId": "uuid"}], "regionsCreated": 3, "notesGenerated": 128, "effectsAdded": [{"trackId": "uuid", "type": "compressor"}], "effectCount": 2, "sendsCreated": 1, "ccEnvelopes": [{"cc": 74, "name": "Filter Cutoff"}], "automationLanes": 0 }` |
| `complete` | Stream done. Includes `inputTokens` and `contextWindowTokens` (see global `complete` row above). |

### COMPOSING mode events (Variation protocol)

Emitted when `state.state == "composing"`. Frontend enters Variation Review Mode.

COMPOSING now emits the same `reasoning`, `plan`, `planStepUpdate`, `toolStart`, and `toolCall` events as EDITING — see the unified event ordering below. The variation-specific events (`meta`, `phrase`, `done`) follow after the tool calls complete.

| type | Description |
|------|-------------|
| `reasoning` | Streamed planner chain-of-thought (same as EDITING/REASONING). Emitted during the LLM planning phase before the plan is ready. Deterministic plans (structured prompts with all fields) skip this. |
| `plan` | Structured plan (same shape as EDITING). `{ "type": "plan", "planId": "uuid", "title": "...", "steps": [...] }` |
| `planStepUpdate` | Step lifecycle (same as EDITING, includes `phase`). Unactivated steps emitted as `skipped` at completion. `{ "type": "planStepUpdate", "stepId": "1", "status": "active" \| "completed" \| "skipped", "phase": "composition" }` |
| `toolStart` | Fires before each tool call during variation execution (includes `phase`). `{ "type": "toolStart", "name": "stori_add_midi_track", "label": "Create Drums track", "phase": "composition" }` |
| `toolCall` | Proposal tool call. **`proposal: true`** — the frontend renders this for transparency but does NOT apply it to the DAW. Includes `label` and `phase`. `{ "type": "toolCall", "id": "...", "name": "...", "label": "Create Drums track", "phase": "composition", "params": {...}, "proposal": true }` |
| `meta` | Variation summary. `{ "type": "meta", "variationId": "uuid", "baseStateId": "42", "intent": "...", "aiExplanation": "...", "affectedTracks": [...], "affectedRegions": [...], "noteCounts": { "added": 32, "removed": 0, "modified": 0 } }`. Use `baseStateId: "0"` for first variation after editing. |
| `phrase` | One musical phrase. `{ "type": "phrase", "phraseId": "uuid", "trackId": "uuid", "regionId": "uuid", "startBeat": 0.0, "endBeat": 16.0, "label": "...", "tags": [...], "explanation": "...", "noteChanges": [...], "controllerChanges": [] }` |
| `done` | End of variation stream. Frontend enables Accept/Discard. `{ "type": "done", "variationId": "uuid", "phraseCount": 4 }` |
| `complete` | Stream done. `{ "type": "complete", "success": true, "variationId": "uuid", "phraseCount": 4, "traceId": "..." }`. Includes `inputTokens` and `contextWindowTokens` (see global `complete` row above). |

#### `proposal` field on `toolCall`

COMPOSING tool calls carry `"proposal": true`. These are informational — they show what the executor is doing but the frontend must NOT apply them to the DAW. The actual note data comes through `phrase` events, and the user commits via Accept/Discard.

EDITING tool calls have `"proposal": false` (or the field is absent) and are applied immediately.

### REASONING mode events

Emitted when `state.state == "reasoning"`. No tools; chat only.

| type | Description |
|------|-------------|
| `reasoning` | Streamed CoT chunks (see above) |
| `content` | Full user-facing answer |
| `complete` | Stream done |

### MCP SSE stream events

Emitted on `GET /api/v1/mcp/stream/{connection_id}`. All events use the Stori Protocol wire format.

| type | Description |
|------|-------------|
| `mcp.message` | MCP tool-call message relayed over SSE. `{ "type": "mcp.message", "payload": { ... } }` |
| `mcp.ping` | SSE keepalive heartbeat. `{ "type": "mcp.ping" }` |

### Protocol enforcement

All SSE events across every streaming endpoint (maestro, conversations, MCP, variation) are validated through the Stori Protocol emitter (`maestro/protocol/emitter.py`). Raw `json.dumps` emission is forbidden in streaming code. If an event fails protocol validation, the stream emits an `error` event followed by `complete(success: false)` and terminates. There is no production fallback that emits unvalidated payloads.

### Event ordering

**EDITING:**
```
state → reasoning* → plan → preflight* → [planStepUpdate(active) → toolStart → toolCall → planStepUpdate(completed)]* → agentComplete* → planStepUpdate(skipped)* → summary.final? → content? → complete
```

Steps are grouped per-track (contiguous) in the `plan` event. During execution, instrument steps with the same `parallelGroup` may interleave (see [Parallel execution](#parallel-execution)). Each instrument agent emits `agentComplete` when it finishes. At completion, any steps never activated are emitted as `skipped` before `complete`.

**COMPOSING (unified):**
```
state → reasoning* → plan → [planStepUpdate(active) → toolStart → toolCall(proposal:true) → planStepUpdate(completed)]* → planStepUpdate(skipped)* → meta → phrase* → done → complete
```

**REASONING:**
```
state → reasoning* → content → complete
```

`*` = zero or more events. `complete` is always final, even on errors.

### Parallel execution (Agent Teams)

Multi-instrument MAESTRO PROMPT compositions (2+ roles) run each instrument as an independent LLM session — one dedicated HTTP call to the LLM API per instrument, all in-flight simultaneously. This is genuine Agent Teams parallelism, not sequential async task sharing.

**Event interleaving during Phase 2:**

```
Phase 1 (sequential — deterministic, no LLM):
  planStepUpdate(stepId=1, active) → toolStart → toolCall(stori_set_tempo) → planStepUpdate(stepId=1, completed)
  planStepUpdate(stepId=2, active) → toolStart → toolCall(stori_set_key)   → planStepUpdate(stepId=2, completed)

Phase 2 (Agent Teams — all instrument agents start simultaneously):
  planStepUpdate(stepId=3, active)              ← Drums agent starts its LLM call
  planStepUpdate(stepId=5, active)              ← Bass agent starts its LLM call (simultaneous)
  toolCall: stori_add_midi_track (Drums)        ← arrives as Drums LLM responds
  toolCall: stori_add_midi_track (Bass)         ← interleaved with Drums
  planStepUpdate(stepId=3, completed)           ← Drums first step done
  toolCall: stori_add_midi_region (Bass)
  toolCall: stori_add_notes (Drums)
  planStepUpdate(stepId=5, completed)
  ...                                           ← arrival order non-deterministic

Phase 3 (sequential — one coordinator LLM call after barrier):
  planStepUpdate(stepId=9, active) → toolStart → toolCall(stori_ensure_bus) → planStepUpdate(stepId=9, completed)
```

**Frontend handling:** Group `planStepUpdate` events by `stepId` — do not assume events for a single step arrive contiguously. Steps with `parallelGroup: "instruments"` may have `status: "active"` simultaneously. `ExecutionTimelineView` renders all instrument timelines progressing at once.

**Failure isolation:** If one instrument agent's LLM call fails, that agent emits `planStepUpdate(status: "failed")` for its own steps and exits. Sibling agents continue unaffected. A `complete` event is always emitted at the end regardless of per-agent failures.

### Execution Timeline contract

The `plan` event and subsequent `planStepUpdate` events are designed to power the frontend's `ExecutionTimelineView`, which renders a hierarchical, per-instrument progress timeline. The frontend groups and humanises steps client-side by parsing the `label` field. The backend guarantees the following contract:

**Step fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `stepId` | string | yes | Unique step identifier |
| `label` | string | yes | Canonical pattern — the frontend parses this for grouping (see patterns below) |
| `toolName` | string | no | Exact MCP tool name (e.g. `"stori_add_midi_track"`). Present when the step maps to a specific tool; omitted otherwise (decodes to `nil` in Swift). |
| `parallelGroup` | string | no | Steps sharing the same value execute concurrently. Currently `"instruments"` for track-bound steps. Absent on sequential setup/mixing steps. See [Parallel execution](#parallel-execution). |
| `status` | string | yes | One of `"pending"`, `"active"`, `"completed"`, `"skipped"`, `"failed"` |
| `detail` | string | no | Short, forward-looking description from a musician's perspective (e.g. "8 bars of funk bass") |
| `phase` | string | yes | DAW workflow phase (see [Composition phases](#composition-phases)). Always present on `toolStart`, `toolCall`, `plan` step entries, and `planStepUpdate`. Values: `"setup"`, `"composition"`, `"arrangement"`, `"soundDesign"`, `"expression"`, `"mixing"`. Default: `"composition"`. |
| `result` | string | no | Populated in `planStepUpdate` on completion. Backward-looking outcome (e.g. "32 notes generated - 4 bars") |

**Canonical label patterns** (used by the frontend for section grouping):

| Pattern | Track-bound | Example |
|---------|-------------|---------|
| `Create <TrackName> track` | yes | `Create Drums track` |
| `Add content to <TrackName>` | yes | `Add content to Synth Bass` |
| `Add effects to <TrackName>` | yes | `Add effects to Drums` |
| `Add MIDI CC to <TrackName>` | yes | `Add MIDI CC to Piano` |
| `Add pitch bend to <TrackName>` | yes | `Add pitch bend to Guitar Lead` |
| `Write automation for <TrackName>` | yes | `Write automation for Strings` |
| `Set tempo to <N> BPM` | no | `Set tempo to 120 BPM` |
| `Set key signature to <Key>` | no | `Set key signature to A minor` |
| `Set up shared <BusName> bus` | no | `Set up shared Reverb bus` |

Track names use title-case with spaces and are consistent across all steps referring to the same instrument.

**Step ordering:** In the `plan` event, steps are emitted in track-contiguous order. All steps for one instrument appear together (create, content, effects, expressive) before moving to the next instrument. Project-level setup steps (tempo, key) come first; shared bus setup comes last. During execution, instrument steps with `parallelGroup` may interleave across instruments (see [Parallel execution](#parallel-execution)).

**Terminal status guarantee:** Every step reaches a terminal status (`completed`, `failed`, or `skipped`) before `complete` is emitted. The backend calls `finalize_pending_as_skipped()` at plan completion to ensure no step remains in `pending`.

### Composition phases

`plan` step entries, `toolStart`, `toolCall`, and `planStepUpdate` events include a `phase` field derived from the tool name. The backend determines the phase — the frontend should not infer it from tool names. The six phases mirror a professional DAW session workflow.

| Phase | Tools | Description |
|-------|-------|-------------|
| `setup` | `stori_create_project`, `stori_set_tempo`, `stori_set_key`, `stori_add_midi_track`, `stori_add_midi_region`, `stori_set_midi_program`, `stori_set_track_name/color/icon`, `stori_play/stop`, `stori_set_playhead`, `stori_show_panel`, `stori_set_zoom` | Session scaffolding: project config, track creation, transport, UI |
| `composition` | `stori_add_notes`, `stori_generate_midi` | Creative content: writing notes, MIDI generation |
| `arrangement` | `stori_move_region`, `stori_transpose_notes`, `stori_clear_notes`, `stori_quantize_notes`, `stori_apply_swing` | Structural editing after initial writing |
| `soundDesign` | `stori_add_insert_effect` | Tone shaping: insert effects (EQ, compression, reverb…) |
| `expression` | `stori_add_midi_cc`, `stori_add_pitch_bend`, `stori_add_aftertouch` | Performance data: MIDI CC, pitch bend, humanisation |
| `mixing` | `stori_set_track_volume/pan`, `stori_mute/solo_track`, `stori_ensure_bus`, `stori_add_send`, `stori_add_automation` | Balance & routing: volume, pan, buses, sends, automation |

For full parameter documentation per tool, see the [tool reference tables](#1-setup--session-scaffolding-15-tools) below. For a flat lookup, see the [cross-reference table](#cross-reference-tool--phase).

---

## Models (OpenRouter)

All models use OpenRouter's `reasoning` parameter for Chain of Thought. Two event types: `reasoning` (CoT) and `content` (user-facing).

**Supported models (exactly two):** `anthropic/claude-sonnet-4.6` (default) · `anthropic/claude-opus-4.6`. Both have a 200 000-token context window. Set `LLM_MODEL` in `.env` to switch.

---

## Muse VCS API

Production endpoints for the musical version control system. All routes require JWT auth (`Authorization: Bearer <token>`). Prefix: `/api/v1/muse/`.

Every endpoint returns a named Pydantic entity — never a raw dict. See [`type_contracts.md`](type_contracts.md#http-response-entities) for the full entity reference.

See [muse_vcs.md](../architecture/muse_vcs.md) for the full architecture reference.

### Endpoints

| Method | Path | Response entity | Description |
|--------|------|-----------------|-------------|
| `POST` | `/muse/variations` | `SaveVariationResponse` | Save a variation (commit) into the history DAG |
| `POST` | `/muse/head` | `SetHeadResponse` | Set HEAD pointer to a specific variation |
| `GET` | `/muse/log?project_id=X` | `MuseLogGraphResponse` | Full commit DAG, topologically sorted |
| `POST` | `/muse/checkout` | `CheckoutResponse` | Checkout to a variation (time travel) |
| `POST` | `/muse/merge` | `MergeResponse` | Three-way merge of two branch tips |

### `POST /muse/variations`

Save a variation directly into the persistent history.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project_id` | string | yes | Project ID |
| `variation_id` | string | yes | Unique variation ID |
| `intent` | string | yes | Description of changes |
| `parent_variation_id` | string | no | Primary parent (lineage) |
| `parent2_variation_id` | string | no | Second parent (merge) |
| `phrases` | array | no | Phrase objects with note changes |
| `affected_tracks` | array | no | Track IDs affected |
| `affected_regions` | array | no | Region IDs affected |
| `beat_range` | tuple | no | `[start, end]` (default `[0.0, 8.0]`) |

**Response `200` — `SaveVariationResponse`:**

```json
{ "variation_id": "f3a4b..." }
```

| Field | Type | Description |
|-------|------|-------------|
| `variation_id` | string | UUID of the variation that was saved |

### `POST /muse/head`

**Request body:** `{ "variation_id": "..." }`

**Response `200` — `SetHeadResponse`:**

```json
{ "head": "f3a4b..." }
```

| Field | Type | Description |
|-------|------|-------------|
| `head` | string | UUID of the variation that is now HEAD |

### `GET /muse/log`

**Query params:** `project_id` (required)

**Response `200` — `MuseLogGraphResponse`:**

```json
{
  "projectId": "proj-uuid",
  "head": "f3a4b...",
  "nodes": [
    {
      "id": "f3a4b...",
      "parent": "a1b2c...",
      "parent2": null,
      "isHead": true,
      "timestamp": 1740614400.0,
      "intent": "add funky bass",
      "regions": ["region-uuid-1", "region-uuid-2"]
    }
  ]
}
```

Nodes are topologically sorted (Kahn's algorithm — parents always before children; ties broken by timestamp, then UUID). See `MuseLogGraphResponse` and `MuseLogNodeResponse` in [`type_contracts.md`](type_contracts.md#http-response-entities).

### `POST /muse/checkout`

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project_id` | string | yes | Project ID |
| `target_variation_id` | string | yes | Variation to checkout |
| `conversation_id` | string | no | StateStore key (default `"default"`) |
| `force` | bool | no | Force checkout despite drift (default `false`) |

**Response `200` — `CheckoutResponse`:**

```json
{
  "project_id": "proj-uuid",
  "from_variation_id": "a1b2c...",
  "to_variation_id": "f3a4b...",
  "execution": {
    "executed": 5,
    "failed": 0,
    "plan_hash": "4a7f...",
    "events": [...]
  },
  "head_moved": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | string | Project the checkout ran on |
| `from_variation_id` | string \| null | Previous HEAD (null if project had no HEAD) |
| `to_variation_id` | string | New HEAD after checkout |
| `execution` | `CheckoutExecutionStats` | Plan execution statistics (see below) |
| `head_moved` | bool | Whether HEAD was successfully updated |

**`CheckoutExecutionStats` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `executed` | int | Tool-call steps executed successfully |
| `failed` | int | Tool-call steps that failed (non-zero = partial checkout) |
| `plan_hash` | string | SHA-256 of the checkout plan — identical hashes = identical plans |
| `events` | array | SSE event payloads emitted during execution |

**Response `409` — checkout blocked by dirty working tree:**

```json
{ "error": "checkout_blocked", "severity": "dirty", "total_changes": 12 }
```

**Response `404`** — target variation not found.

### `POST /muse/merge`

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project_id` | string | yes | Project ID |
| `left_id` | string | yes | Left branch tip variation ID |
| `right_id` | string | yes | Right branch tip variation ID |
| `conversation_id` | string | no | StateStore key (default `"default"`) |
| `force` | bool | no | Force merge despite drift (default `false`) |

**Response `200` — `MergeResponse`:**

```json
{
  "project_id": "proj-uuid",
  "merge_variation_id": "d4e5f...",
  "left_id": "a1b2c...",
  "right_id": "f3a4b...",
  "execution": {
    "executed": 8,
    "failed": 0,
    "plan_hash": "9b3a...",
    "events": [...]
  },
  "head_moved": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | string | Project the merge ran on |
| `merge_variation_id` | string | UUID of the new merge commit (two parents) |
| `left_id` | string | Left branch tip that was merged |
| `right_id` | string | Right branch tip that was merged |
| `execution` | `CheckoutExecutionStats` | Plan execution statistics |
| `head_moved` | bool | Whether HEAD was moved to the merge commit |

**Response `409` — merge conflict:**

```json
{
  "error": "merge_conflict",
  "conflicts": [
    { "region_id": "...", "type": "note_conflict", "description": "..." }
  ]
}
```

---

## Other HTTP Endpoints

Non-streaming endpoints that return named Pydantic response entities. All require JWT auth unless stated otherwise. See [`type_contracts.md`](type_contracts.md#http-response-entities) for the complete entity field reference.

### `GET /api/v1/validate-token`

Validates the bearer token and returns budget info.

**Response `200` — `ValidateTokenResponse`** (camelCase wire format):

```json
{
  "valid": true,
  "expiresAt": "2026-03-01T12:00:00+00:00",
  "expiresInSeconds": 3600,
  "budgetRemaining": 4.25,
  "budgetLimit": 10.0
}
```

`budgetRemaining` and `budgetLimit` are `null` when the user record is unavailable. The endpoint raises `401` rather than returning `valid: false`.

### `POST /api/v1/maestro/preview`

Generates a plan preview without executing it. Useful for showing users what Maestro will do before committing.

**Response `200` — `PreviewMaestroResponse`** (camelCase wire format):

When the prompt is COMPOSING:
```json
{
  "previewAvailable": true,
  "intent": "COMPOSING",
  "sseState": "composing",
  "preview": {
    "valid": true,
    "totalSteps": 12,
    "generations": 3,
    "edits": 9,
    "toolCalls": [...],
    "notes": ["Detected 4/4 time signature"],
    "errors": [],
    "warnings": []
  }
}
```

When the prompt is not COMPOSING:
```json
{
  "previewAvailable": false,
  "intent": "REASONING",
  "sseState": "reasoning",
  "reason": "Preview only available for COMPOSING mode (got reasoning)"
}
```

### `POST /api/v1/mcp/connection`

Obtain a server-issued connection ID for the SSE tool-streaming flow. ID is valid for 5 minutes.

**Response `200` — `ConnectionCreatedResponse`** (camelCase wire format):

```json
{ "connectionId": "550e8400-e29b-41d4-a716-446655440000" }
```

Use the returned `connectionId` in:
- `GET /api/v1/mcp/stream/{connectionId}` — open the SSE event stream
- `POST /api/v1/mcp/response/{connectionId}` — deliver tool execution results

### `POST /api/v1/mcp/response/{connectionId}`

Post a tool execution result from the DAW back to the waiting MCP coroutine.

**Response `200` — `ToolResponseReceivedResponse`:**

```json
{ "status": "ok" }
```

Returns `404` for unknown or expired connection IDs.

### `POST /api/v1/variation/discard`

Cancel and discard a variation by ID. Idempotent — variations already discarded, or not found in the store, return `ok: true` without error.

**Response `200` — `DiscardVariationResponse`:**

```json
{ "ok": true }
```

Returns `409` if the variation is in a non-discardable terminal state other than `DISCARDED`.

### `GET /api/v1/variation/{variation_id}`

Poll the current status and phrase payload for a variation. For reconnect / non-streaming clients.

**Response `200` — `GetVariationResponse`** (camelCase wire format):

```json
{
  "variationId": "v-uuid",
  "projectId": "proj-uuid",
  "baseStateId": "muse",
  "intent": "add a funky bass line",
  "status": "committed",
  "aiExplanation": "I added a syncopated bass groove...",
  "affectedTracks": ["track-uuid"],
  "affectedRegions": ["region-uuid"],
  "phrases": [
    {
      "phraseId": "ph-uuid",
      "sequence": 0,
      "trackId": "track-uuid",
      "regionId": "region-uuid",
      "beatStart": 0.0,
      "beatEnd": 8.0,
      "label": "Verse Bass",
      "tags": ["groove", "verse"],
      "aiExplanation": "Syncopated 16th-note pattern...",
      "diff": { ... }
    }
  ],
  "phraseCount": 1,
  "lastSequence": 0,
  "createdAt": "2026-02-26T10:00:00+00:00",
  "updatedAt": "2026-02-26T10:00:05+00:00",
  "errorMessage": null
}
```

`status` values: `"streaming"` | `"committed"` | `"discarded"` | `"error"` | `"pending"`.

### `PATCH /api/v1/conversations/{conversation_id}`

Update a conversation's title or linked project.

**Request body:** `{ "title": "New title", "project_id": "proj-uuid" }` (all fields optional; send `project_id: "null"` to unlink).

**Response `200` — `ConversationUpdateResponse`** (camelCase wire format):

```json
{
  "id": "conv-uuid",
  "title": "My new title",
  "projectId": "proj-uuid",
  "updatedAt": "2026-02-26T10:00:05+00:00"
}
```

---

## MCP tool routing

- **Server-side (Maestro):** The generation tool (`stori_generate_midi`) runs in the Maestro backend and returns MIDI/result payloads.
- **DAW (Swift):** All other tools are forwarded to the connected Stori app over WebSocket. The DAW executes the action and returns a `tool_response` with `request_id` and `result`.

Same tool set for Stori app (SSE) and MCP. Full list and params: `GET /api/v1/mcp/tools`.

**Parameter alignment** (with `maestro/core/tool_validation/`):

- **Track volume:** `volumeDb` (dB; 0 = unity). Not 0–1.
- **Track pan:** `pan` in range -100 (left) to 100 (right).
- **Insert effect:** `stori_add_insert_effect` with param `type` (e.g. `"reverb"`, `"compressor"`, `"eq"`, `"overdrive"`, `"distortion"`, `"chorus"`, `"tremolo"`, `"delay"`, `"filter"`, `"phaser"`, `"flanger"`).
- **Bus send:** `stori_add_send` uses `busId` returned by `stori_ensure_bus`. Call `stori_ensure_bus` first — the server guarantees ordering.
- **Notes:** In `stori_add_notes`, each note uses `startBeat`, `durationBeats`, `velocity` (1–127). The `notes` array is required and must contain real MIDI note objects — shorthand params like `_noteCount` or `_beatRange` are rejected. For large note counts (>128), call `stori_add_notes` multiple times on the same `regionId`; each call appends.
- **MIDI CC:** `stori_add_midi_cc` uses `cc` (0–127) and `events` array of `{beat, value}`. Common numbers: CC 1 = modulation, CC 11 = expression, CC 64 = sustain (127=down / 0=up), CC 74 = filter cutoff, CC 91 = reverb send, CC 93 = chorus send.
- **Pitch bend:** `stori_add_pitch_bend` uses `events` array of `{beat, value}`. Values: 0 = center, −8192 = max down, +8191 = max up. ±4096 ≈ ±1 semitone; ±8192 ≈ ±2 semitones; ±1024 ≈ quarter-tone.
- **Automation:** `stori_add_automation` uses `target` (trackId) and `points` array of `{beat, value, curve}`. Curve values: `"Linear"`, `"Smooth"`, `"Step"`, `"Exp"`, `"Log"`. Common params: `volume`, `pan`, `reverb_wet`, `filter_cutoff`, `tremolo_rate`, `delay_feedback`.
- **Quantize:** `stori_quantize_notes` uses `grid`: `"1/4"`, `"1/8"`, `"1/16"`, `"1/32"`, `"1/64"`.
- **Region:** `stori_add_midi_region` uses `startBeat`, `durationBeats`.

---

> **Strict contract:** The macOS client enforces all required parameters — missing fields throw `invalidParameter` errors. The server guarantees all required fields are present in every SSE `toolCall` event via auto-assignment and backfill. See per-tool tables below for which fields are required.

Tools are organized below by **DAW workflow phase** — the same phases emitted in the `phase` field of SSE events. This mirrors the order a professional producer works in: set up the session → write the music → arrange and refine → shape the sounds → add the human touch → balance the mix.

---

## 1. Setup — session scaffolding (15 tools)

Project config, track/region creation, instrument selection, transport, and UI.

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_read_project` | Read current project state (tempo, key, tracks, regions). | `include_notes`, `include_automation` (optional bools) |
| `stori_create_project` | Create a new project. | `name`, `tempo` (required); `keySignature`, `timeSignature` |
| `stori_set_tempo` | Set project tempo (BPM). | `tempo` (**required**, 20–300) |
| `stori_set_key` | Set key signature. | `key` (**required**, e.g. C, Am, F#m) |
| `stori_add_midi_track` | Add MIDI track. Server auto-assigns `trackId`, `color`, `icon`, and exactly one of `_isDrums`/`gmProgram`. | `name` (**required**); `drumKitId`, `gmProgram` 0–127, `instrument`, `color`, `icon` — all guaranteed present in SSE output |
| `stori_add_midi_region` | Add MIDI region to a track. | `trackId`, `startBeat`, `durationBeats` (required); `name` |
| `stori_set_midi_program` | Set GM program (instrument voice). | `trackId`, `program` (0–127); `channel` (1–16, default 1; use 10 for drums) |
| `stori_set_track_name` | Rename track. | `trackId`, `name` |
| `stori_set_track_color` | Set track color. | `trackId`, `color` (see color table below) |
| `stori_set_track_icon` | Set track icon (SF Symbol). | `trackId`, `icon` (must be from curated list — see icon table below) |
| `stori_play` | Start playback. | `fromBeat` (optional) |
| `stori_stop` | Stop playback. | — |
| `stori_set_playhead` | Move playhead. | `bar`, `beat`, or `seconds` |
| `stori_show_panel` | Show/hide panel. | `panel`, `visible` |
| `stori_set_zoom` | Set editor zoom. | `zoomPercent` |

### Track color values

Use **named colors** (preferred — adaptive dark mode) or `#RRGGBB` hex:

| Name     | Best for                     |
|----------|------------------------------|
| `blue`   | Piano, keys, pads            |
| `indigo` | Synth, electric piano        |
| `purple` | Strings, orchestral          |
| `pink`   | Vocals, choir                |
| `red`    | Drums, kick                  |
| `orange` | Brass, horns                 |
| `yellow` | Guitar, plucked strings      |
| `green`  | Bass, sub-bass               |
| `teal`   | Woodwinds, flute             |
| `cyan`   | FX, texture, ambient         |
| `mint`   | Percussion, auxiliary        |
| `gray`   | Utility, click track         |

The server auto-assigns a named color from the track name/role when `color` is omitted or unrecognised. Palette rotation order: blue → indigo → purple → pink → red → orange → yellow → green → teal → cyan → mint → gray.

### Track icon values

Icons must be SF Symbol names from the curated allowlist. The server validates icons and auto-assigns from the track name/role when `icon` is omitted or invalid.

**Role defaults:**

| Role              | Icon                  |
|-------------------|-----------------------|
| Piano / keys      | `pianokeys`           |
| Synth / pad       | `pianokeys.inverse`   |
| Guitar (acoustic) | `guitars`             |
| Guitar (electric) | `guitars.fill`        |
| Bass              | `guitars.fill`        |
| Drums / kick      | `instrument.drum`     |
| Percussion / perc | `instrument.drum`     |
| Brass             | `instrument.trumpet`  |
| Strings           | `instrument.violin`   |
| Woodwind / flute  | `instrument.flute`    |
| Saxophone         | `instrument.saxophone`|
| Vocals            | `music.mic`           |
| Texture / ambient | `waveform`            |
| FX / utility      | `sparkles`            |
| Fallback          | `music.note`          |

Do NOT send arbitrary strings — the client rejects icons not in the compiled allowlist.

---

## 2. Composition — creative content (2 tools)

Writing notes and generating MIDI via the Orpheus music model.

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_notes` | Add MIDI notes to region. | `regionId`, `notes` — each note **must** have `pitch` (0–127), `velocity` (1–127), `startBeat` (>=0), `durationBeats` (>0). Server backfills defaults if missing. |
| `stori_generate_midi` | Generate MIDI for a role. | `role`, `style`, `tempo`, `bars` (required); `key`, `constraints` |

**Generation tool (server-side, internal):** `stori_generate_midi` runs inside Maestro and calls the Orpheus music model. It is **never emitted as a `toolCall` event** in the SSE stream — the server translates its output into `stori_add_notes` (and optionally `stori_add_midi_cc` / `stori_add_pitch_bend`) before forwarding to the client.

---

## 3. Arrangement — structural editing (7 tools)

Structural edits after initial writing: moving, duplicating, transposing, quantizing.

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_move_region` | Move region to a new position. | `regionId`, `startBeat` |
| `stori_duplicate_region` | Duplicate region. | `regionId`, `startBeat` |
| `stori_delete_region` | Delete a region. | `regionId` |
| `stori_transpose_notes` | Transpose all notes in a region. | `regionId`, `semitones` |
| `stori_quantize_notes` | Quantize to grid. | `regionId`; `grid` (1/4, 1/8, 1/16, 1/32, 1/64); `strength` 0–1 |
| `stori_apply_swing` | Apply swing. | `regionId`, `amount` (0–1) |
| `stori_clear_notes` | Clear all notes in region. | `regionId` |

---

## 4. Sound Design — tone shaping (1 tool)

Insert effects that shape the tone of each instrument.

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_insert_effect` | Add insert effect. | `trackId`, `type` (reverb, delay, compressor, eq, distortion, overdrive, filter, chorus, tremolo, phaser, flanger, modulation) |

**Auto-inference from MAESTRO PROMPTs:** The planner infers effects from `Style` and `Role` fields before any LLM call — drums always get a compressor, pads/lead get a reverb send, and style-specific inserts (distortion for rock, filter for lo-fi, etc.) are added automatically. Suppress with `Constraints: no_effects: true`.

**Translation from MAESTRO PROMPT `Effects` block:** When a structured prompt includes an `Effects:` YAML block, every entry is translated into a `stori_add_insert_effect` call. Reverb is routed via a shared `Reverb` bus (`stori_ensure_bus` → `stori_add_send`), never as a direct insert, so `stori_ensure_bus` is always guaranteed to precede any `stori_add_send` for the same bus name.

---

## 5. Expression — performance data (3 tools)

MIDI CC, pitch bend, and aftertouch — the data a performer creates in real time to make MIDI sound human.

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_midi_cc` | Add MIDI CC events to a region. | `regionId` (**required**), `cc` (**required**, 0–127), `events` — each **must** have `beat` and `value`. |
| `stori_add_pitch_bend` | Add pitch bend events to a region. | `regionId` (**required**), `events` — each **must** have `beat` and `value` (−8192 to +8191). |
| `stori_add_aftertouch` | Add aftertouch events (channel or polyphonic). | `regionId`, `events` (each `{beat, value}` or `{beat, value, pitch}`) |

**Translation from MAESTRO PROMPT `MidiExpressiveness` block:** `cc_curves` entries → `stori_add_midi_cc`; `pitch_bend` style → `stori_add_pitch_bend`; `sustain_pedal` → `stori_add_midi_cc` with CC 64 (127=down, 0=up). These calls happen after notes are added to the region.

---

## 6. Mixing — balance & routing (7 tools)

Volume, pan, mute/solo, bus routing, sends, and automation.

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_set_track_volume` | Set track volume. | `trackId`, `volume` (**required**, 0.0–1.5) |
| `stori_set_track_pan` | Set track pan. | `trackId`, `pan` (**required**, 0.0–1.0) |
| `stori_mute_track` | Mute/unmute. | `trackId`, `muted` |
| `stori_solo_track` | Solo/unsolo. | `trackId`, `solo` |
| `stori_ensure_bus` | Create bus if missing. | `name` |
| `stori_add_send` | Send track to bus. | `trackId`, `busId`, `levelDb` |
| `stori_add_automation` | Add track-level automation curves. | `trackId`, `parameter`, `points` — each point **must** have `beat` and `value`. `curve` defaults to `"linear"`. |

**Translation from MAESTRO PROMPT `Automation` block:** Each lane → `stori_add_automation` using the trackId returned by `stori_add_midi_track`.

---

## Maestro Default UI

Endpoints that power the creative launchpad in the macOS client. Content is static today; future versions will support personalisation, A/B testing, and localisation.

### Placeholders

**Endpoint:** `GET /api/v1/maestro/ui/placeholders`
**Auth:** none (public, cacheable)

Rotating strings for the hero prompt input. The client cycles through them every 4 seconds. Returns at least 3 items.

```json
{ "placeholders": ["Describe a groove…", "Build a cinematic swell…", …] }
```

### Prompt inspiration cards

**Endpoint:** `GET /api/v1/maestro/prompts`
**Auth:** none

Returns 4 randomly sampled MAESTRO PROMPT inspiration cards from a curated pool of 50. Each call returns a different set. Styles span every continent and tradition: lo-fi boom bap, melodic techno, cinematic orchestral, Afrobeats, ambient drone, jazz, dark trap, bossa nova, funk, neo-soul, drum & bass, minimal house, synthwave, post-rock, reggaeton, classical string quartet, psytrance, indie folk, New Orleans brass, Nordic ambient, flamenco, UK garage, West African polyrhythm, Ethio-jazz, Gnawa trance, North Indian raga, Balinese gamelan, Japanese zen, Korean sanjo, Qawwali devotional, Arabic maqam, Anatolian psych rock, Colombian cumbia, Argentine tango nuevo, Andean huayno, Jamaican dancehall, Trinidad soca, klezmer, Baroque suite, Balkan brass, Appalachian bluegrass, gospel, Polynesian/Taiko fusion, Sufi ney meditation, Gregorian chant, progressive rock, Afro-Cuban rumba, minimalist phasing, full hip-hop song, and through-composed cinematic score.

Every `fullPrompt` is a complete MAESTRO PROMPT YAML using the full spec breadth — injected verbatim into the compose input on tap.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique slug |
| `title` | string | Human label, e.g. `"Lo-fi boom bap · Cm · 75 BPM"` |
| `preview` | string | First 3–4 YAML lines visible in the card |
| `fullPrompt` | string | Complete MAESTRO PROMPT YAML |

```json
{
  "prompts": [
    {
      "id": "lofi_boom_bap",
      "title": "Lo-fi boom bap · Cm · 75 BPM",
      "preview": "Mode: compose · Section: verse\nStyle: lofi hip hop · Key: Cm · 75 BPM\nRole: drums, bass, piano, melody\nVibe: dusty x3, warm x2, melancholic",
      "fullPrompt": "MAESTRO PROMPT\nMode: compose\n..."
    }
  ]
}
```

### Single prompt by ID

**Endpoint:** `GET /api/v1/maestro/prompts/{prompt_id}`
**Auth:** none

Fetches a single MAESTRO PROMPT inspiration card by its stable slug ID — the same shape as the carousel. Use this to re-fetch a card the user previously tapped, deep-link to a specific style, or seed the compose input programmatically.

`prompt_id` must match an ID from the pool. Returns 404 if unknown.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique slug (same as requested) |
| `title` | string | Human label, e.g. `"Melodic techno drop · Am · 128 BPM"` |
| `preview` | string | First 3–4 YAML lines visible in the card |
| `fullPrompt` | string | Complete MAESTRO PROMPT YAML, ready for the compose input |

**Example IDs** (non-exhaustive):

| ID | Title |
|----|-------|
| `lo_fi_boom_bap` | Lo-fi boom bap · Cm · 75 BPM |
| `melodic_techno_drop` | Melodic techno drop · Am · 128 BPM |
| `jamaican_dancehall` | Jamaican dancehall · Dm · 90 BPM |
| `afrobeats_highlife` | Afrobeats highlife · Gbm · 102 BPM |
| `jazz_trio_late_night` | Jazz trio · late night · Bb · 140 BPM swing |
| `nordic_ambient_folk` | Nordic ambient folk · Dm · 72 BPM |
| `flamenco_nuevo_fusion` | Flamenco nuevo · Phrygian · Am · 112 BPM |
| `gnawa_trance` | Gnawa trance · Gm · 88 BPM |
| `gregorian_bass_drop` | Gregorian chant bass drop · Dm · 70 BPM |
| `celestial_strings` | Celestial strings · Dm · 58 BPM |

```bash
GET /api/v1/maestro/prompts/melodic_techno_drop
```

```json
{
  "id": "melodic_techno_drop",
  "title": "Melodic techno drop · Am · 128 BPM",
  "preview": "Mode: compose · Section: drop\nStyle: melodic techno · Key: Am · 128 BPM\nRole: kick, bass, lead, pads, perc\nVibe: hypnotic x3, driving x2, euphoric",
  "fullPrompt": "MAESTRO PROMPT\nMode: compose\n..."
}
```

### Budget status

**Endpoint:** `GET /api/v1/maestro/budget/status`
**Auth:** `Authorization: Bearer <token>`

Focused budget/fuel status for the Creative Fuel UI. Wraps the same data as `/api/v1/users/me` with a `state` field computed server-side.

| Field | Type | Description |
|-------|------|-------------|
| `remaining` | float | Remaining budget in dollars |
| `total` | float | Total budget limit in dollars |
| `state` | string | `"normal"` \| `"low"` \| `"critical"` \| `"exhausted"` |
| `sessionsUsed` | int | Compose requests this billing period |

**State derivation (authoritative):**

| Condition | State |
|-----------|-------|
| remaining ≤ 0 | `exhausted` |
| remaining < 0.25 | `critical` |
| remaining < 1.0 | `low` |
| else | `normal` |

---

## Tool summary by phase

| Phase | SSE value | Count | Purpose |
|-------|-----------|-------|---------|
| 1. Setup | `setup` | 15 | Project config, tracks, regions, instruments, transport, UI |
| 2. Composition | `composition` | 2 | Notes and MIDI generation (Orpheus) |
| 3. Arrangement | `arrangement` | 7 | Move, duplicate, delete, transpose, quantize, swing, clear |
| 4. Sound Design | `soundDesign` | 1 | Insert effects |
| 5. Expression | `expression` | 3 | MIDI CC, pitch bend, aftertouch |
| 6. Mixing | `mixing` | 7 | Volume, pan, mute/solo, buses, sends, automation |

**Total: 35** distinct tools. The generation tool (`stori_generate_midi`) runs server-side and is never emitted as an SSE `toolCall` event; all others are forwarded to the DAW when connected.

---

## Cross-reference: tool → phase

| Tool | Phase |
|------|-------|
| `stori_read_project` | setup |
| `stori_create_project` | setup |
| `stori_set_tempo` | setup |
| `stori_set_key` | setup |
| `stori_add_midi_track` | setup |
| `stori_add_midi_region` | setup |
| `stori_set_midi_program` | setup |
| `stori_set_track_name` | setup |
| `stori_set_track_color` | setup |
| `stori_set_track_icon` | setup |
| `stori_play` | setup |
| `stori_stop` | setup |
| `stori_set_playhead` | setup |
| `stori_show_panel` | setup |
| `stori_set_zoom` | setup |
| `stori_add_notes` | composition |
| `stori_generate_midi` | composition |
| `stori_move_region` | arrangement |
| `stori_duplicate_region` | arrangement |
| `stori_delete_region` | arrangement |
| `stori_transpose_notes` | arrangement |
| `stori_quantize_notes` | arrangement |
| `stori_apply_swing` | arrangement |
| `stori_clear_notes` | arrangement |
| `stori_add_insert_effect` | soundDesign |
| `stori_add_midi_cc` | expression |
| `stori_add_pitch_bend` | expression |
| `stori_add_aftertouch` | expression |
| `stori_set_track_volume` | mixing |
| `stori_set_track_pan` | mixing |
| `stori_mute_track` | mixing |
| `stori_solo_track` | mixing |
| `stori_ensure_bus` | mixing |
| `stori_add_send` | mixing |
| `stori_add_automation` | mixing |

See also: [integrate.md](../guides/integrate.md) for MCP setup (stdio, Cursor, WebSocket DAW connection).

---

## Muse Hub API

Remote collaboration backend for Muse VCS — the server-side equivalent of a Git remote. All endpoints are under `/api/v1/musehub/` and require `Authorization: Bearer <token>`.

### Interactive API docs

The full MuseHub API is available as a machine-readable OpenAPI 3.1 specification:

| Resource | URL |
|----------|-----|
| **OpenAPI 3.1 JSON spec** | `GET /api/v1/openapi.json` |
| **Swagger UI** (interactive, debug mode only) | `GET /docs` |
| **ReDoc** (debug mode only) | `GET /redoc` |

The spec is always available at `/api/v1/openapi.json` regardless of `DEBUG` mode, enabling agent SDK generation and third-party integrations without enabling the interactive UI in production.

```bash
# Fetch the spec for SDK generation
curl https://your-domain.com/api/v1/openapi.json | jq '.info'

# List all MuseHub operationIds
curl https://your-domain.com/api/v1/openapi.json | jq '[.paths | to_entries[] | .value | to_entries[] | .value.operationId] | sort | .[]'
```

Every endpoint has a camelCase `operationId` (e.g. `listRepoCommits`, `getAnalysisHarmony`, `mergePullRequest`) that maps 1:1 to generated SDK method names.

### POST /api/v1/musehub/repos

Create a new remote Muse repository. The `slug` is auto-generated from `name` (lowercase, hyphens). Returns `409` if the `(owner, name)` combination already exists.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Repository name (1–255 chars) |
| `owner` | string | yes | URL-safe owner username (lowercase alphanumeric + hyphens, 1–64 chars) — forms the `/{owner}/{slug}` path |
| `visibility` | string | no | `"public"` or `"private"` (default `"private"`) |

**Response (201):**

| Field | Type | Description |
|-------|------|-------------|
| `repoId` | string (UUID) | Internal unique identifier (not exposed in URLs) |
| `name` | string | Repo name |
| `owner` | string | URL-visible owner username |
| `slug` | string | URL-safe slug auto-generated from `name` |
| `visibility` | string | `"public"` or `"private"` |
| `ownerUserId` | string (UUID) | Authenticated user's ID |
| `cloneUrl` | string | CLI-usable clone path (`/{owner}/{slug}`) |
| `createdAt` | ISO 8601 | Creation timestamp |

---

### GET /api/v1/musehub/{owner}/{repo_slug}

Get metadata for a repo by its canonical owner/slug path. Returns `404` if not found. Declared last in the router so fixed-path subroutes (`/repos/...`, `/search/...`, etc.) take precedence.

---

### GET /api/v1/musehub/repos/{repo_id}

Get metadata for an existing repo by internal UUID. Returns `404` if not found.

---

### GET /api/v1/musehub/repos/{repo_id}/branches

List all branch pointers in a repo, ordered by name.

**Response:**

```json
{
  "branches": [
    { "branchId": "...", "name": "main", "headCommitId": "abc123" }
  ]
}
```

---

### GET /api/v1/musehub/repos/{repo_id}/commits

List commits for a repo, newest first.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `branch` | string | — | Filter by branch name |
| `limit` | int | 50 | Max results (1–200) |

**Response:**

```json
{
  "commits": [
    {
      "commitId": "abc123",
      "branch": "main",
      "parentIds": ["def456"],
      "message": "feat: jazz variation",
      "author": "gabriel",
      "timestamp": "2026-02-27T17:30:00Z",
      "snapshotId": "snap-xyz"
    }
  ],
  "total": 1
}
```

---

### GET /api/v1/musehub/repos/{repo_id}/context

**Agent context endpoint.** Returns a complete musical briefing for AI composition agents. This is the canonical first call an agent makes when starting a session — it aggregates musical state, commit history, analysis highlights, open PRs, open issues, and actionable suggestions into a single self-contained document.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `ref` | string | `HEAD` | Branch name or commit ID. `HEAD` resolves to the latest commit. |
| `depth` | `brief`\|`standard`\|`verbose` | `standard` | Controls response size. `brief` ≈ 2K tokens; `standard` ≈ 8K tokens; `verbose` = uncapped. |
| `format` | `json`\|`yaml` | `json` | Response format. `yaml` returns `application/x-yaml`. |

**Response (JSON):**

```json
{
  "repoId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "ref": "main",
  "depth": "standard",
  "musicalState": {
    "activeTracks": ["bass", "keys", "drums"],
    "key": null,
    "mode": null,
    "tempoBpm": null,
    "timeSignature": null,
    "form": null,
    "emotion": null
  },
  "history": [
    {
      "commitId": "abc123",
      "message": "feat: add tritone sub in bridge",
      "author": "gabriel",
      "timestamp": "2026-02-27T17:30:00Z",
      "activeTracks": []
    }
  ],
  "analysis": {
    "keyFinding": null,
    "chordProgression": null,
    "grooveScore": null,
    "emotion": null,
    "harmonicTension": null,
    "melodicContour": null
  },
  "activePrs": [
    {
      "prId": "pr-uuid",
      "title": "Add swing feel to verse",
      "fromBranch": "feat/swing",
      "toBranch": "main",
      "state": "open",
      "body": "Adds a 0.62 swing factor to 8th notes in bars 1–16."
    }
  ],
  "openIssues": [
    {
      "issueId": "issue-uuid",
      "number": 3,
      "title": "Add more harmonic tension in bridge",
      "labels": ["harmonic", "composition"],
      "body": ""
    }
  ],
  "suggestions": [
    "Set a project tempo: no BPM detected. Run `muse tempo set <bpm>` to anchor the grid.",
    "Declare a key center: no key detected. Run `muse key set <key>` to enable harmonic analysis."
  ]
}
```

**Notes:**
- `musicalState` optional fields (`key`, `tempoBpm`, etc.) are `null` until Storpheus MIDI analysis integration is complete. Agents must handle `null` gracefully.
- `analysis` fields are all `null` at MVP for the same reason.
- `brief` depth includes at most 3 history entries and 2 suggestions (designed to fit in a 2K-token context window).
- `verbose` depth includes full issue and PR bodies, and up to 50 history entries.
- The response is deterministic for the same `repo_id` + `ref` + `depth`.

**Errors:**
- `404` — repo does not exist, or `ref` has no commits.
- `401` — missing or invalid Bearer token.

---

## Muse Hub Issues API

Issue tracker for Muse Hub repos — lets musicians open, filter, and close production/creative issues (e.g. "hi-hat / synth pad clash in measure 8"). All endpoints are under `/api/v1/musehub/repos/{repo_id}/issues/` and require `Authorization: Bearer <token>`.

Issue numbers (`number`) are sequential per repo, starting at 1. Labels are free-form strings; no validation at MVP.

### POST /api/v1/musehub/repos/{repo_id}/issues

Create a new issue in `open` state.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Issue title (1–500 chars) |
| `body` | string | no | Markdown description (default `""`) |
| `labels` | string[] | no | Free-form label strings (default `[]`) |

**Response (201):**

```json
{
  "issueId": "550e8400-e29b-41d4-a716-446655440000",
  "number": 1,
  "title": "Hi-hat / synth pad clash in measure 8",
  "body": "Frequencies clash around 8 kHz.",
  "state": "open",
  "labels": ["bug", "musical"],
  "createdAt": "2026-02-27T20:00:00Z"
}
```

### GET /api/v1/musehub/repos/{repo_id}/issues

List issues for a repo.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `state` | `open` \| `closed` \| `all` | `open` | Filter by state |
| `label` | string | — | Filter to issues containing this label |

**Response (200):**

```json
{
  "issues": [
    {
      "issueId": "550e8400-e29b-41d4-a716-446655440000",
      "number": 1,
      "title": "Hi-hat / synth pad clash in measure 8",
      "body": "Frequencies clash around 8 kHz.",
      "state": "open",
      "labels": ["bug"],
      "createdAt": "2026-02-27T20:00:00Z"
    }
  ]
}
```

### GET /api/v1/musehub/repos/{repo_id}/issues/{issue_number}

Get a single issue by its per-repo sequential number.

**Response (200):** Full issue object (same shape as above). Returns **404** if the issue number does not exist.

### PATCH /api/v1/musehub/repos/{repo_id}/issues/{issue_number}

Partially update an issue's title, body, and/or labels. Only fields present in the body are modified.

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | string | — | Updated title (1–500 chars) |
| `body` | string | — | Updated Markdown body |
| `labels` | string[] | — | Replacement label list |

**Response (200):** Updated issue object.

### POST /api/v1/musehub/repos/{repo_id}/issues/{issue_number}/close

Close an issue (set `state` → `"closed"`).

**Response (200):** Updated issue object with `"state": "closed"`. Returns **404** if the issue number does not exist.

### POST /api/v1/musehub/repos/{repo_id}/issues/{issue_number}/reopen

Reopen a closed issue (set `state` → `"open"`).

**Response (200):** Updated issue object with `"state": "open"`. Returns **404** if not found.

### POST /api/v1/musehub/repos/{repo_id}/issues/{issue_number}/assign

Assign or unassign a collaborator. Pass `"assignee": null` to unassign.

| Field | Type | Required | Description |
|---|---|---|---|
| `assignee` | string\|null | yes | Collaborator display name; null to unassign |

**Response (200):** Updated issue object with `assignee` field set.

### POST /api/v1/musehub/repos/{repo_id}/issues/{issue_number}/milestone

Assign or remove a milestone. Query param `?milestone_id=<uuid>` to assign; omit to remove.

**Response (200):** Updated issue object with `milestoneId` and `milestoneTitle` set.

### GET /api/v1/musehub/repos/{repo_id}/issues/{issue_number}/comments

List all non-deleted comments on an issue, ordered chronologically (oldest first).

**Response (200):**
```json
{
  "comments": [
    {
      "commentId": "uuid",
      "issueId": "uuid",
      "author": "miles_davis",
      "body": "The section:chorus beats:16-24 has a frequency clash with track:bass.",
      "parentId": null,
      "musicalRefs": [
        {"type": "section", "value": "chorus", "raw": "section:chorus"},
        {"type": "beats", "value": "16-24", "raw": "beats:16-24"},
        {"type": "track", "value": "bass", "raw": "track:bass"}
      ],
      "isDeleted": false,
      "createdAt": "2026-02-28T12:00:00Z",
      "updatedAt": "2026-02-28T12:00:00Z"
    }
  ],
  "total": 1
}
```

### POST /api/v1/musehub/repos/{repo_id}/issues/{issue_number}/comments

Create a comment on an issue. Supports threaded replies via `parentId`.

Musical context references (`track:bass`, `section:chorus`, `beats:16-24`) are parsed automatically and returned in `musicalRefs`.

| Field | Type | Required | Description |
|---|---|---|---|
| `body` | string | yes | Markdown comment body |
| `parentId` | string | — | Parent comment UUID for threaded replies |

**Response (201):** Full updated comment list (`IssueCommentListResponse`).

### DELETE /api/v1/musehub/repos/{repo_id}/issues/{issue_number}/comments/{comment_id}

Soft-delete a comment (excluded from future list results).

**Response (204):** No content. Returns **404** if not found.

---

## Muse Hub Milestones API

Group issues into milestones (e.g. "Album v1.0", "Mix Session 3"). All endpoints are under `/api/v1/musehub/repos/{repo_id}/milestones/`.

### GET /api/v1/musehub/repos/{repo_id}/milestones

List milestones. Optional `?state=open|closed|all` (default `open`).

**Response (200):** `{ "milestones": [MilestoneResponse, ...] }`

Each milestone includes `openIssues` and `closedIssues` counts.

### POST /api/v1/musehub/repos/{repo_id}/milestones

Create a milestone.

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | string | yes | Milestone title (1–255 chars) |
| `description` | string | — | Markdown description |
| `dueOn` | string | — | ISO-8601 due date |

**Response (201):** `MilestoneResponse`

### GET /api/v1/musehub/repos/{repo_id}/milestones/{milestone_number}

Get a single milestone by its per-repo sequential number.

**Response (200):** `MilestoneResponse`. Returns **404** if not found.

---

## Muse Hub Sync Protocol

Push/pull endpoints that transfer commits and binary objects between local Muse repos and the Muse Hub. These are the core data-movement endpoints that `muse push` and `muse pull` call.

All endpoints require `Authorization: Bearer <token>`.

Object content is base64-encoded in `content_b64`. For MVP, objects up to ~1 MB are transferred inline; larger files will use pre-signed URLs in a future release.

### POST /api/v1/musehub/repos/{repo_id}/push

Upload commits and binary objects to the Hub. Enforces fast-forward semantics.

**Request body:**

```json
{
  "branch": "main",
  "headCommitId": "c123...",
  "commits": [
    {
      "commitId": "c123...",
      "parentIds": ["c000..."],
      "message": "Add jazz bassline",
      "timestamp": "2024-01-15T10:30:00Z",
      "snapshotId": "snap001",
      "author": "gabriel"
    }
  ],
  "objects": [
    {
      "objectId": "sha256:aabbcc...",
      "path": "tracks/jazz_4b.mid",
      "contentB64": "<base64>"
    }
  ],
  "force": false
}
```

**Response (200):**

```json
{ "ok": true, "remoteHead": "c123..." }
```

**Error responses:**

| Status | Body | When |
|--------|------|------|
| 404 | `"Repo not found"` | Unknown `repo_id` |
| 409 | `{"error": "non_fast_forward"}` | Remote head is not an ancestor of `headCommitId` and `force` is `false` |
| 401 | — | Missing or invalid Bearer token |

**Non-fast-forward semantics:** A push is accepted when (a) the branch has no head yet, (b) `headCommitId` equals the current remote head, or (c) the current remote head appears in the ancestry graph of the pushed commits. Set `force: true` to overwrite regardless.

**Idempotency:** Commits and objects that already exist (by ID) are silently skipped — re-pushing is safe.

### POST /api/v1/musehub/repos/{repo_id}/pull

Fetch commits and objects the caller does not yet have.

**Request body:**

```json
{
  "branch": "main",
  "haveCommits": ["c001", "c002"],
  "haveObjects": ["sha256:aaa...", "sha256:bbb..."]
}
```

`haveCommits` and `haveObjects` are exclusion lists — pass empty arrays to receive everything.

**Response (200):**

```json
{
  "commits": [
    {
      "commitId": "c003",
      "branch": "main",
      "parentIds": ["c002"],
      "message": "Add piano chord voicings",
      "author": "rene",
      "timestamp": "2024-01-16T09:00:00Z",
      "snapshotId": null
    }
  ],
  "objects": [
    {
      "objectId": "sha256:ccddee...",
      "path": "tracks/piano.mid",
      "contentB64": "<base64>"
    }
  ],
  "remoteHead": "c003"
}
```

**Error responses:**

| Status | Body | When |
|--------|------|------|
| 404 | `"Repo not found"` | Unknown `repo_id` |
| 401 | — | Missing or invalid Bearer token |

---

## Muse Hub Pull Requests API

Pull request tracking for Muse Hub repos — lets musicians propose, review, and merge branch variations. All endpoints are under `/api/v1/musehub/repos/{repo_id}/pull-requests/` and require `Authorization: Bearer <token>`.

**PR states:** `open` → `merged` | `closed`

### POST /api/v1/musehub/repos/{repo_id}/pull-requests

Open a new pull request proposing to merge `from_branch` into `to_branch`.

**Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | PR title (1–500 chars) |
| `fromBranch` | string | yes | Source branch name |
| `toBranch` | string | yes | Target branch name |
| `body` | string | no | PR description (Markdown). Defaults to `""`. |

**Response (201):**

```json
{
  "prId": "b3d9f1e2-...",
  "title": "Add neo-soul keys variation",
  "body": "Dreamy chord voicings for the bridge.",
  "state": "open",
  "fromBranch": "neo-soul-experiment",
  "toBranch": "main",
  "mergeCommitId": null,
  "createdAt": "2026-02-27T12:00:00Z"
}
```

**Errors:**
- **422** — `fromBranch == toBranch`
- **404** — `fromBranch` does not exist in the repo
- **404** — repo not found

### GET /api/v1/musehub/repos/{repo_id}/pull-requests

List pull requests for a repo, ordered by creation time ascending.

**Query params:**

| Param | Values | Default | Description |
|-------|--------|---------|-------------|
| `state` | `open` \| `merged` \| `closed` \| `all` | `all` | Filter by PR state |

**Response (200):**

```json
{
  "pullRequests": [
    {
      "prId": "...",
      "title": "...",
      "state": "open",
      "fromBranch": "feature",
      "toBranch": "main",
      "mergeCommitId": null,
      "createdAt": "..."
    }
  ]
}
```

### GET /api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}

Get a single PR by ID.

**Response (200):** Full PR object (same shape as above). Returns **404** if the PR or repo is not found.

### POST /api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/merge

Merge an open PR using `merge_commit` strategy.

Creates a merge commit on `to_branch` with parent IDs `[to_branch head, from_branch head]`, advances the `to_branch` head pointer, and sets PR state to `merged`.

**Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mergeStrategy` | `"merge_commit"` | no | Only `merge_commit` is supported at MVP. Defaults to `"merge_commit"`. |

**Response (200):**

```json
{
  "merged": true,
  "mergeCommitId": "aabbcc..."
}
```

**Errors:**
- **404** — PR or repo not found
- **409** — PR is already merged or closed

## Muse Hub Objects API

Binary artifact storage — served by the same `maestro` container. Objects are
content-addressed (`sha256:<hex>`) and written to disk during push; only metadata
lives in Postgres. All JSON endpoints require `Authorization: Bearer <token>`.

### GET /api/v1/musehub/repos/{repo_id}/objects

List metadata for all artifacts stored in the repo. Does **not** return binary
content — use the `/content` sub-resource for downloads.

**Response (200):**

```json
{
  "objects": [
    {
      "objectId": "sha256:abc123...",
      "path": "tracks/jazz_4b.mid",
      "sizeBytes": 12345,
      "createdAt": "2026-01-15T10:00:00Z"
    }
  ]
}
```

**Errors:** **404** — repo not found.

### GET /api/v1/musehub/repos/{repo_id}/objects/{object_id}/content

Stream the raw bytes of a stored artifact. Content-Type is inferred from the
path extension (`.webp` → `image/webp`, `.mid` → `audio/midi`, `.mp3` → `audio/mpeg`,
others → `application/octet-stream`).

**Response (200):** Raw binary with appropriate Content-Type.

**Errors:**
- **404** — repo or object not found
- **410** — object record exists in DB but file was removed from disk

---

### GET /api/v1/musehub/repos/{repo_id}/objects/{object_id}/parse-midi

Parse a stored MIDI artifact into a structured note representation consumed by
the Canvas-based piano roll renderer.  Reads the binary file from disk using
the `mido` library and converts all timing to quarter-note beats.

**Authentication:** Optional — public repos need no token; private repos require `Authorization: Bearer <token>`.

**Path parameters:**

| Parameter   | Description                          |
|-------------|--------------------------------------|
| `repo_id`   | Muse Hub repository UUID             |
| `object_id` | Object ID of the stored MIDI file    |

**Response (200) — `MidiParseResult`:**

```json
{
  "tracks": [
    {
      "track_id": 0,
      "channel": 0,
      "name": "Piano",
      "notes": [
        {
          "pitch": 60,
          "start_beat": 0.0,
          "duration_beats": 1.0,
          "velocity": 80,
          "track_id": 0,
          "channel": 0
        }
      ]
    }
  ],
  "tempo_bpm": 120.0,
  "time_signature": "4/4",
  "total_beats": 32.0
}
```

All timing is in quarter-note beats.  Notes within each track are sorted by
`start_beat`.  The `pitch` field uses standard MIDI numbering (60 = middle C).

**Errors:**
- **404** — repo or object not found, or object is not a `.mid` / `.midi` file
- **410** — object record exists in DB but file was removed from disk
- **422** — object bytes cannot be parsed as a Standard MIDI File

**Produced by:** `maestro.api.routes.musehub.objects.parse_midi_object()`
**Consumed by:** MuseHub piano roll page (`/musehub/ui/{owner}/{slug}/piano-roll/{ref}`)

---

## Muse Hub Piano Roll UI

### GET /musehub/ui/{owner}/{repo_slug}/piano-roll/{ref}

Canvas-based interactive piano roll showing all MIDI tracks at a given commit
ref.  No JWT required — HTML shell; JavaScript fetches authed data via
`localStorage` token.

**Features:**
- Piano keyboard strip on the left Y-axis (pitch labels, C note markers)
- Beat grid on the X-axis with measure markers
- Per-track colour coding from the design system palette
- Velocity mapped to rectangle opacity (soft notes appear lighter)
- Horizontal and vertical zoom sliders
- Click-drag panning
- Hover tooltip: pitch name, MIDI number, velocity, beat position, duration

**Path parameters:**

| Parameter    | Description                                  |
|--------------|----------------------------------------------|
| `owner`      | Repository owner username                    |
| `repo_slug`  | Repository slug                              |
| `ref`        | Commit SHA or branch name                    |

### GET /musehub/ui/{owner}/{repo_slug}/piano-roll/{ref}/{path}

Same as above but scoped to a single MIDI file identified by its repo-relative
`path` (e.g. `tracks/bass.mid`).  Useful for per-track links from the tree
browser or commit detail page.

---

## Muse Hub Semantic Search

### GET /api/v1/musehub/search/similar

Find public commits that are musically similar to a given commit SHA using
vector-based cosine similarity (Qdrant).  Only public repos appear in results.

**Authentication:** `Authorization: Bearer <token>` required.

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `commit`  | string | ✅ | — | Commit SHA to use as the similarity query |
| `limit`   | int | ❌ | 10 | Maximum results to return (1–50) |


**Response (200):**

```json
{

  "queryCommit": "abc123",
  "results": [
    {
      "commitId": "def456",
      "repoId": "repo-uuid",
      "score": 0.94,
      "branch": "main",
      "author": "composer@stori"

    }
  ]
}
```


**Result type:** `SimilarSearchResponse` — see `type_contracts.md`.

**Errors:**
- **401** — missing or invalid token
- **404** — commit SHA not found in Muse Hub
- **503** — Qdrant temporarily unavailable

**How similarity works:** The commit message is parsed for musical metadata
(key, tempo, mode, chord complexity) and encoded as a 128-dim L2-normalised
feature vector. Qdrant returns the nearest vectors by cosine distance. Results
are ranked highest-to-lowest by score (1.0 = identical, 0.0 = unrelated).

**Agent use case:** After composing a jazz ballad in Db major at 72 BPM, an
agent calls this endpoint to surface reference compositions with similar harmonic
and tempo profiles for style study or mix-in inspiration.

---


---

## Muse Hub — Webhook Subscriptions

Webhooks deliver real-time event notifications via HTTP POST to a registered
URL whenever a matching event fires on a repo.  An HMAC-SHA256 signature is
included when a secret is configured, allowing receivers to verify authenticity.

**Event types:** `push`, `pull_request`, `issue`, `release`, `branch`, `tag`,
`session`, `analysis`.

**Delivery headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `X-MuseHub-Event` | Event type string (e.g. `push`) |
| `X-MuseHub-Delivery` | UUID identifying this delivery attempt |
| `X-MuseHub-Signature` | `sha256=<hmac_hex>` (only when secret is set) |

**Retry policy:** Up to 3 attempts with exponential back-off (1 s, 2 s, 4 s).
Each attempt is logged to `musehub_webhook_deliveries`.

---

### POST /api/v1/musehub/repos/{repo_id}/webhooks

Register a new webhook subscription.

**Request body:**

```json
{
  "url": "https://your-server.example.com/hook",
  "events": ["push", "issue"],
  "secret": "optional-signing-secret"
}
```

**Response (201):**

```json
{
  "webhookId": "uuid",
  "repoId": "repo-uuid",
  "url": "https://your-server.example.com/hook",
  "events": ["push", "issue"],
  "active": true,
  "createdAt": "2026-02-27T00:00:00Z"
}
```

**Errors:**
- **404** — repo not found
- **422** — unknown event type(s)

---

### GET /api/v1/musehub/repos/{repo_id}/webhooks

List all registered webhooks for a repo, ordered by creation time.


**Response (200):**

```json
{

  "webhooks": [
    {
      "webhookId": "uuid",
      "repoId": "repo-uuid",
      "url": "https://your-server.example.com/hook",
      "events": ["push", "issue"],
      "active": true,
      "createdAt": "2026-02-27T00:00:00Z"

    }
  ]
}
```


**Errors:** **404** — repo not found.

---

### DELETE /api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}

Remove a webhook subscription and all its delivery history.

**Response (204):** No content.

**Errors:**
- **404** — repo or webhook not found

---

### GET /api/v1/musehub/repos/{repo_id}/webhooks/{webhook_id}/deliveries

List delivery attempts for a webhook, newest first.


### GET /api/v1/musehub/repos/{repo_id}/export/{ref}

Download a packaged export of stored artifacts at a given commit ref. The ref
can be a full commit ID or a branch name (resolves to branch head). Artifacts
are filtered by format and optional section names, then returned as a raw file
(single artifact) or a ZIP archive (multi-artifact or `splitTracks=true`).


**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Max records to return (1–200) |

**Response (200):**

```json
{
  "deliveries": [
    {
      "deliveryId": "uuid",
      "webhookId": "webhook-uuid",
      "eventType": "push",
      "attempt": 1,
      "success": true,
      "responseStatus": 200,
      "responseBody": "ok",
      "deliveredAt": "2026-02-27T00:00:00Z"
    }

| `format` | string | `midi` | Export format: `midi`, `json`, `musicxml`, `abc`, `wav`, `mp3` |
| `splitTracks` | bool | `false` | Bundle all matching artifacts into a ZIP archive, one file per track |
| `sections` | string | — | Comma-separated section names; only artifacts whose path contains a listed name are included (e.g. `verse,chorus`) |

**Response (200):**

- Single artifact: raw file bytes with format-appropriate Content-Type and
  `Content-Disposition: attachment; filename="<basename>"`.
- Multiple artifacts or `splitTracks=true`: `application/zip` archive with
  `Content-Disposition: attachment; filename="<repo_id>_<ref8>_<format>.zip"`.

**Format → MIME type mapping:**

| Format | MIME type |
|--------|-----------|
| `midi` | `audio/midi` |
| `json` | `application/json` |
| `musicxml` | `application/vnd.recordare.musicxml+xml` |
| `abc` | `text/plain; charset=utf-8` |
| `wav` | `audio/wav` |
| `mp3` | `audio/mpeg` |

**`format=json` response schema:**

```json
{
  "repo_id": "string",
  "ref": "string",
  "commit_id": "string",
  "objects": [
    {"object_id": "string", "path": "string", "size_bytes": 0}

  ]
}
```

**Errors:**
- **404** — repo or webhook not found

- **404** — repo not found, ref not found, or no artifacts match the requested format
- **422** — unrecognised `format` value

**Agent use case:** An AI music agent calls this endpoint after a `muse commit`
to export the session's MIDI tracks for import into another DAW or for
post-processing by downstream tools. The deterministic URL (repo + ref + format)
makes it safe to cache and replay.

---

### GET /api/v1/musehub/repos/{repo_id}/raw/{ref}/{path}

Direct file download by human-readable path and ref (branch/tag), analogous to
GitHub's `raw.githubusercontent.com` URLs. Designed for `curl`, `wget`, and
scripted pipelines.

**Auth:** No token required for **public** repos. Private repos require
`Authorization: Bearer <token>` and return 401 otherwise.

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `repo_id` | UUID of the target Muse Hub repo |
| `ref` | Branch or tag name (e.g. `main`). Accepted for URL semantics; current implementation serves the most-recently-pushed object at `path`. |
| `path` | Relative file path inside the repo (e.g. `tracks/bass.mid`). Supports nested paths. |

**Response headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | MIME type derived from file extension (see table below) |
| `Content-Disposition` | `attachment; filename="<basename>"` |
| `Accept-Ranges` | `bytes` — range requests are supported |

**MIME type resolution:**

| Extension | Content-Type |
|-----------|-------------|
| `.mid`, `.midi` | `audio/midi` |
| `.mp3` | `audio/mpeg` |
| `.wav` | `audio/wav` |
| `.json` | `application/json` |
| `.webp` | `image/webp` |
| `.xml` | `application/xml` |
| `.abc` | `text/vnd.abc` |
| Others | `application/octet-stream` |

**Range request example:**

```bash
curl -H "Range: bytes=0-1023" \
  https://musehub.stori.com/api/v1/musehub/repos/<repo_id>/raw/main/tracks/bass.mid
# → 206 Partial Content with first 1 KB
```

**Full download example:**

```bash
curl https://musehub.stori.com/api/v1/musehub/repos/<repo_id>/raw/main/tracks/bass.mid \
  -o bass.mid
```

**Cache headers:** `ETag`, `Last-Modified`, `Cache-Control: private, max-age=60`

**Errors:**
- **401** — private repo accessed without a valid Bearer token
- **404** — repo not found, or no object exists at the given path
- **410** — object metadata exists in DB but the file was removed from disk

---

## Muse Hub Analysis API

Agent-optimized endpoints that return structured JSON for all 13 musical dimensions
of a Muse commit ref.  All endpoints require `Authorization: Bearer <token>`.

### GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}

Returns all 13 dimensions in a single response.

**Path params:**
- `repo_id` — Muse Hub repo UUID
- `ref` — branch name, commit ID, or tag (e.g. `main`, `abc1234`)

**Query params:**
- `?track=<instrument>` — restrict to a named track (e.g. `bass`, `keys`)
- `?section=<label>` — restrict to a named section (e.g. `chorus`, `verse_1`)

**Response `200 application/json`:**
```json
{
  "ref": "main",
  "repoId": "...",
  "computedAt": "2026-02-27T12:00:00Z",
  "filtersApplied": { "track": null, "section": null },
  "dimensions": [
    {
      "dimension": "harmony",
      "ref": "main",
      "computedAt": "2026-02-27T12:00:00Z",
      "data": { "tonic": "C", "mode": "major", ... },
      "filtersApplied": { "track": null, "section": null }
    },
    ... (13 total)
  ]
}
```

**Cache headers:** `ETag`, `Last-Modified`, `Cache-Control: private, max-age=60`

**Errors:** `404` if repo not found.

---

### GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/{dimension}

Returns structured JSON for one musical dimension.

**Path params:**
- `repo_id` — Muse Hub repo UUID
- `ref` — commit ref
- `dimension` — one of: `harmony`, `dynamics`, `motifs`, `form`, `groove`, `emotion`,
  `chord-map`, `contour`, `key`, `tempo`, `meter`, `similarity`, `divergence`

**Query params:** same as aggregate endpoint (`?track=`, `?section=`)

**Response `200 application/json`:**
```json
{
  "dimension": "harmony",
  "ref": "main",
  "computedAt": "2026-02-27T12:00:00Z",
  "data": {
    "tonic": "C",
    "mode": "major",
    "keyConfidence": 0.87,
    "chordProgression": [
      { "beat": 0.0, "chord": "Cmaj7", "function": "Imaj7", "tension": 0.1 },
      ...
    ],
    "tensionCurve": [0.1, 0.12, ...],
    "modulationPoints": [],
    "totalBeats": 32
  },
  "filtersApplied": { "track": null, "section": null }
}
```

**Dimension-specific `data` shapes:**

| Dimension | Key fields |
|-----------|-----------|
| `harmony` | `tonic`, `mode`, `keyConfidence`, `chordProgression`, `tensionCurve`, `modulationPoints`, `totalBeats` |
| `dynamics` | `peakVelocity`, `meanVelocity`, `minVelocity`, `dynamicRange`, `velocityCurve`, `dynamicEvents` |
| `motifs` | `totalMotifs`, `motifs[]` (id, intervals, lengthBeats, occurrenceCount, occurrences, track) |
| `form` | `formLabel`, `totalBeats`, `sections[]` (label, function, startBeat, endBeat, lengthBeats) |
| `groove` | `swingFactor`, `gridResolution`, `onsetDeviation`, `grooveScore`, `style`, `bpm` |
| `emotion` | `valence` (−1..1), `arousal` (0..1), `tension` (0..1), `primaryEmotion`, `confidence` |
| `chord-map` | `progression[]`, `totalChords`, `totalBeats` |
| `contour` | `shape`, `directionChanges`, `peakBeat`, `valleyBeat`, `overallDirection`, `pitchCurve` |
| `key` | `tonic`, `mode`, `confidence`, `relativeKey`, `alternateKeys[]` |
| `tempo` | `bpm`, `stability`, `timeFeel`, `tempoChanges[]` |
| `meter` | `timeSignature`, `irregularSections[]`, `beatStrengthProfile`, `isCompound` |
| `similarity` | `similarCommits[]` (ref, score, sharedMotifs, commitMessage), `embeddingDimensions` |
| `divergence` | `divergenceScore`, `baseRef`, `changedDimensions[]` (dimension, changeMagnitude, description) |

**Cache headers:** `ETag`, `Last-Modified`, `Cache-Control: private, max-age=60`

**Errors:**
- `404` if repo not found
- `404` if `dimension` is not a supported value (response includes list of valid dimensions)
- `401` if no Bearer token

See `maestro/models/musehub_analysis.py` for full Pydantic model definitions and OpenAPI schema.


---
## Muse Hub Releases API

Releases publish a specific version of a composition with human-readable notes
and structured download package URLs. Tags are unique per repo (e.g. `v1.0`).

All endpoints require `Authorization: Bearer <token>`.

### POST `/api/v1/musehub/repos/{repo_id}/releases`

Create a new release tied to an optional commit snapshot.

**Request body:**
```json
{
  "tag": "v1.0",
  "title": "First Release",
  "body": "# Release Notes\n\nInitial jazz arrangement.",
  "commitId": "abc123..."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | Yes | Version tag — unique per repo (e.g. `v1.0`) |
| `title` | string | Yes | Human-readable release title |
| `body` | string | No | Markdown release notes |
| `commitId` | string | No | Commit SHA to pin this release to |

**Response (201):** `ReleaseResponse` — see below.

**Errors:**
- **404** — repo not found
- **409** — a release with this tag already exists in the repo

### GET `/api/v1/musehub/repos/{repo_id}/releases`

List all releases for the repo, ordered newest first.

**Response (200):**
```json
{
  "releases": [<ReleaseResponse>, ...]
}
```

**Errors:**
- **404** — repo not found

### GET `/api/v1/musehub/repos/{repo_id}/releases/{tag}`

Get a single release by its version tag.

**Response (200):** `ReleaseResponse`

**Errors:**
- **404** — repo not found, or tag not found in this repo

### `ReleaseResponse`

```json
{
  "releaseId": "uuid",
  "tag": "v1.0",
  "title": "First Release",
  "body": "# Release Notes...",
  "commitId": "abc123...",
  "downloadUrls": {
    "midiBundle": "/api/v1/musehub/repos/{id}/releases/{release_id}/packages/midi",
    "stems": "/api/v1/musehub/repos/{id}/releases/{release_id}/packages/stems",
    "mp3": "/api/v1/musehub/repos/{id}/releases/{release_id}/packages/mp3",
    "musicxml": "/api/v1/musehub/repos/{id}/releases/{release_id}/packages/musicxml",
    "metadata": "/api/v1/musehub/repos/{id}/releases/{release_id}/packages/metadata"
  },
  "createdAt": "2026-02-27T00:00:00Z"
}
```

`downloadUrls` fields are `null` when the corresponding package is not available
(e.g. no commit pinned, or no stored objects for that commit).

---

### GET /api/v1/musehub/repos/{repo_id}/groove-check

Returns rhythmic consistency metrics for the most recent commits in a repo.

**Auth:** Requires `Authorization: Bearer <token>`.

**Path params:**
- `repo_id` — Muse Hub repo UUID

**Query params:**
- `?threshold=<float>` — drift threshold in beats (default `0.1`; range `0.01–1.0`). Commits whose drift delta exceeds this are classified WARN or FAIL.
- `?limit=<int>` — maximum commits to analyse (default `10`; range `1–50`)
- `?track=<name>` — restrict analysis to a named instrument track (optional)
- `?section=<name>` — restrict analysis to a named musical section (optional)

**Response `200 application/json`:**
```json
{
  "commitRange": "HEAD~10..HEAD",
  "threshold": 0.1,
  "totalCommits": 10,
  "flaggedCommits": 2,
  "worstCommit": "a1b2c3d4",
  "entries": [
    {
      "commit": "e5f6g7h8",
      "grooveScore": 0.0312,
      "driftDelta": 0.0,
      "status": "OK",
      "track": "all",
      "section": "all",
      "midiFiles": 3
    }
  ]
}
```

**Status values:** `"OK"` (drift ≤ threshold), `"WARN"` (drift between threshold and 2×threshold), `"FAIL"` (drift > 2×threshold).

**Errors:** `401` without auth; `404` if repo not found.

**Response type:** [`GrooveCheckResponse`](./type_contracts.md#groovecheckresponse)

---

### GET /api/v1/musehub/repos/{repo_id}/raw/{ref}/{path}

Direct file download by human-readable path and ref (branch/tag), analogous to
GitHub's `raw.githubusercontent.com` URLs. Designed for `curl`, `wget`, and
scripted pipelines.

**Auth:** No token required for **public** repos. Private repos require
`Authorization: Bearer <token>` and return 401 otherwise.

**Path parameters:**

| Parameter | Description |
|-----------|-------------|
| `repo_id` | UUID of the target Muse Hub repo |
| `ref` | Branch or tag name (e.g. `main`). Accepted for URL semantics; current implementation serves the most-recently-pushed object at `path`. |
| `path` | Relative file path inside the repo (e.g. `tracks/bass.mid`). Supports nested paths. |

**Response headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | MIME type derived from file extension (see table below) |
| `Content-Disposition` | `attachment; filename="<basename>"` |
| `Accept-Ranges` | `bytes` — range requests are supported |

**MIME type resolution:**

| Extension | Content-Type |
|-----------|-------------|
| `.mid`, `.midi` | `audio/midi` |
| `.mp3` | `audio/mpeg` |
| `.wav` | `audio/wav` |
| `.json` | `application/json` |
| `.webp` | `image/webp` |
| `.xml` | `application/xml` |
| `.abc` | `text/vnd.abc` |
| Others | `application/octet-stream` |

**Range request example:**

```bash
curl -H "Range: bytes=0-1023" \
  https://musehub.stori.com/api/v1/musehub/repos/<repo_id>/raw/main/tracks/bass.mid
# → 206 Partial Content with first 1 KB
```

**Full download example:**

```bash
curl https://musehub.stori.com/api/v1/musehub/repos/<repo_id>/raw/main/tracks/bass.mid \
  -o bass.mid
```

**Errors:**
- **401** — private repo accessed without a valid Bearer token
- **404** — repo not found, or no object exists at the given path
- **410** — object metadata exists in DB but the file was removed from disk

---

## Muse Hub User Profile API

Public user profile and social endpoints. No `Authorization` header required for read-only routes (profiles are publicly discoverable).

### GET `/api/v1/musehub/users/{username}/activity`

Return the public activity feed for a Muse Hub user — a cursor-paginated, newest-first list of events the user has triggered across their public repos.

**Auth:** Optional. Unauthenticated callers see only events from public repos. The authenticated owner also sees events from their private repos.

**Path params:**
- `username` — URL-friendly Muse Hub username (e.g. `gabriel`)

**Query params:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | — | Filter by public API event type: `push` \| `pull_request` \| `issue` \| `release` \| `star` \| `fork` \| `comment` |
| `limit` | int | `30` | Maximum events to return (1–100) |
| `before_id` | UUID | — | Cursor: event UUID from a previous response's `nextCursor` |

**Response (200):** `UserActivityFeedResponse`

```json
{
  "events": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "type": "push",
      "actor": "gabriel",
      "repo": "gabriel/neo-baroque",
      "payload": {
        "sha": "abc123",
        "message": "Add jazz voicings"
      },
      "createdAt": "2026-02-28T10:00:00Z"
    }
  ],
  "nextCursor": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "typeFilter": null
}
```

**Cursor pagination:** Pass `nextCursor` from the response as `before_id` in the next request to fetch the subsequent page. When `nextCursor` is `null`, there are no more events.

**Public API `type` vocabulary → DB event types:**
| API `type` | DB `event_type` values |
|------------|------------------------|
| `push` | `commit_pushed`, `branch_created`, `branch_deleted` |
| `pull_request` | `pr_opened`, `pr_merged`, `pr_closed` |
| `issue` | `issue_opened`, `issue_closed` |
| `release` | `tag_pushed` |
| `star`, `fork`, `comment` | Not yet in DB — always returns empty feed |

**Errors:**
- **404** — username not found
- **422** — `type` param is not one of the accepted values

**Response type:** [`UserActivityFeedResponse`](./type_contracts.md#useractivityfeedresponse)

---

## Muse Hub Web UI

The following routes serve HTML pages for browser-based repo navigation. They
do **not** require an `Authorization` header — auth is handled client-side via
`localStorage` and JavaScript `fetch()` calls to the JSON API above.

All UI routes use the canonical `/{owner}/{repo_slug}` path scheme. The server resolves the slug to the internal `repo_id` before rendering.

### Content Negotiation

Key MuseHub UI routes support **dual-format responses** from the same URL.
The response format is selected in priority order:

1. `?format=json` query param — explicit override (works in any browser `<a>` link)
2. `Accept: application/json` header — standard HTTP content negotiation
3. Default (no header / any other value) — returns `text/html`

JSON responses use **camelCase keys** (via Pydantic `by_alias=True`), matching
the existing `/api/v1/musehub/...` convention so agents have a uniform contract.

**Example — AI agent fetching structured repo data:**

```
GET /musehub/ui/alice/my-song
Accept: application/json
```

Returns `RepoResponse` JSON (same shape as `/api/v1/musehub/{owner}/{repo_slug}`).

**Example — Fallback via query param:**

```
GET /musehub/ui/alice/my-song/commits?format=json
```

Returns `CommitListResponse` JSON.

Dual-format endpoints:

| Route | HTML | JSON |
|-------|------|------|
| `GET /musehub/ui/{owner}/{repo_slug}` | Repo overview page | `RepoResponse` |
| `GET /musehub/ui/{owner}/{repo_slug}/commits` | Commits list page | `CommitListResponse` |
| `GET /musehub/ui/{owner}/{repo_slug}/commits/{commit_id}` | Commit detail + artifacts | `CommitResponse` (if synced) |

HTML-only routes (no JSON path implemented):

| Route | Description |
|-------|-------------|
| `GET /musehub/ui/{owner}/{repo_slug}/pulls` | PR list with open/all filter |
| `GET /musehub/ui/{owner}/{repo_slug}/pulls/{pr_id}` | PR detail with Merge button |
| `GET /musehub/ui/{owner}/{repo_slug}/issues` | Issue list with open/closed/all filter |
| `GET /musehub/ui/{owner}/{repo_slug}/issues/{number}` | Issue detail with Close button |
| `GET /musehub/ui/{owner}/{repo_slug}/embed/{ref}` | Embeddable player widget (no auth, iframe-safe) |
| `GET /musehub/ui/{owner}/{repo_slug}/releases` | Release list |
| `GET /musehub/ui/{owner}/{repo_slug}/releases/{tag}` | Release detail with download section |
| `GET /musehub/ui/{owner}/{repo_slug}/graph` | Interactive DAG commit graph |
| `GET /musehub/ui/{owner}/{repo_slug}/timeline` | Timeline visualization |
| `GET /musehub/ui/{owner}/{repo_slug}/sessions` | Recording session list |
| `GET /musehub/ui/{owner}/{repo_slug}/sessions/{session_id}` | Session detail |
| `GET /musehub/ui/{owner}/{repo_slug}/groove-check` | Rhythmic consistency dashboard with timing deviation plots |
| `GET /musehub/ui/{owner}/{repo_slug}/context/{ref}` | Context viewer (what the agent sees) |
| `GET /musehub/ui/{owner}/{repo_slug}/search` | In-repo search (keyword / NL / pattern / musical) |
| `GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/divergence` | Divergence radar chart |
| `GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/contour` | Melodic contour |
| `GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/tempo` | Tempo analysis |
| `GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/dynamics` | Dynamics analysis |
| `GET /musehub/ui/users/{username}` | Public user profile page |
| `GET /musehub/ui/explore` | Explore public repos |
| `GET /musehub/ui/trending` | Trending repos |
| `GET /musehub/ui/search` | Global cross-repo search |

The embed route additionally sets `X-Frame-Options: ALLOWALL` — required for
cross-origin `<iframe>` embedding on external sites.

See [integrate.md — Embedding MuseHub Compositions](../guides/integrate.md#embedding-musehub-compositions-on-external-sites) for
usage and iframe code examples.

### GET /oembed

oEmbed discovery endpoint. Returns JSON metadata (including an `<iframe>` HTML
snippet) for any MuseHub embed URL. No auth required.

**Query parameters:**

| Name        | Type   | Required | Description                                    |
|-------------|--------|----------|------------------------------------------------|
| `url`       | string | yes      | MuseHub embed URL to resolve                   |
| `maxwidth`  | int    | no       | Maximum iframe width in pixels (default 560)   |
| `maxheight` | int    | no       | Maximum iframe height in pixels (default 152)  |
| `format`    | string | no       | Response format; only `json` supported         |

**Response `200`:**

```json
{
  "version": "1.0",
  "type": "rich",
  "title": "MuseHub Composition abc12345",
  "provider_name": "Muse Hub",
  "provider_url": "https://musehub.stori.app",
  "width": 560,
  "height": 152,
  "html": "<iframe ...></iframe>"
}
```

**Error responses:**
- `404` — URL does not match an embed URL pattern.
- `501` — `format` is not `json`.
