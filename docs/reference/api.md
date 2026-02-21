# API & MCP tools reference

Streaming (SSE), event types, models, and the full MCP tool set in one place. Tool definitions live in `app/mcp/tools.py`; validation in `app/core/tool_validation.py`.

---

## Compose stream

**Endpoint:** `POST /api/v1/maestro/stream`  
**Auth:** `Authorization: Bearer <token>`  
**Body:** JSON with `prompt`, optional `project` (app state), `conversation_id`, `model`, and optional generation hints.  
**Response:** SSE stream of JSON objects; each has a `type` field.

The backend determines execution mode from intent classification: COMPOSING -> variation (human review), EDITING -> apply (immediate). See [architecture.md](architecture.md).

The `prompt` field accepts both natural language and the **Stori structured prompt** format. When a prompt begins with `STORI PROMPT`, it is parsed as a structured prompt and routed deterministically by the `Mode` field, bypassing NL classification. See [stori-prompt-spec.md](../protocol/stori-prompt-spec.md).

### Request body fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | yes | Natural language or STORI PROMPT text |
| `project` | object | no | Full DAW project snapshot (tracks, regions, buses, tempo, key). See [fe-project-state-sync.md](../guides/fe-project-state-sync.md). |
| `conversationId` | string (UUID) | no | Conversation ID for multi-turn context. Send the same ID for every request in a session. |
| `model` | string | no | LLM model override. Supported: `anthropic/claude-sonnet-4.6` (default), `anthropic/claude-opus-4.6`. |
| `storePrompt` | bool | no | Whether to store prompt for training data (default `true`) |
| `qualityPreset` | string | no | Orpheus generation quality: `"fast"` \| `"balanced"` \| `"quality"`. Default `"quality"`. Use `"fast"` or `"balanced"` only when optimising for iteration speed over output quality. |

---

## SSE event types

All events are newline-delimited `data: {json}\n\n` lines. Every event has a `type` field and a monotonic `seq` integer (assigned at the route layer, starting at 1 per request). **Keys are camelCase.**

`seq` is the canonical ordering key for Agent Teams events that may arrive out of order. Frontend should sort by `seq` when reconstructing timeline order if needed.

### Events emitted in all modes

| type | Description |
|------|-------------|
| `state` | Intent classification result. **First meaningful event.** `{ "type": "state", "state": "editing" \| "composing" \| "reasoning", "intent": "..." }` |
| `reasoning` | LLM chain-of-thought chunk (streamed). `{ "type": "reasoning", "content": "..." }` |
| `content` | Final user-facing text response. `{ "type": "content", "content": "..." }` |
| `budgetUpdate` | Cost update after LLM call. `{ "type": "budgetUpdate", "budgetRemaining": 4.50, "cost": 0.03 }` |
| `error` | Error message (non-fatal or fatal). `{ "type": "error", "error": "...", "message": "..." }` |
| `complete` | **Always the final event**, even on errors. `{ "type": "complete", "success": true \| false, "traceId": "...", "inputTokens": 42350, "contextWindowTokens": 200000 }`. On error: `success: false`. `inputTokens` = full input tokens sent to the model this turn (from `usage.prompt_tokens`; reflects the entire context window occupied, including history, system prompt, and tools). `contextWindowTokens` = model capacity (200 000 for all supported Claude models). Both are `0` if unavailable — frontend should leave any usage display at its previous value in that case. |

### EDITING mode events

Emitted when `state.state == "editing"`. Applied immediately by the frontend.

| type | Description |
|------|-------------|
| `plan` | Structured plan emitted once after initial reasoning, before the first tool call. Steps are ordered per-track (contiguous) so each instrument's steps appear together. Labels follow canonical patterns for frontend timeline grouping. `toolName` is present when the step maps to a specific tool, omitted otherwise. Instrument steps carry `parallelGroup: "instruments"` — steps sharing the same `parallelGroup` value execute concurrently (see [Parallel execution](#parallel-execution) below). `{ "type": "plan", "planId": "uuid", "title": "Building Lo-Fi Groove", "steps": [{ "stepId": "1", "label": "Set tempo to 72 BPM", "toolName": "stori_set_tempo", "status": "pending" }, { "stepId": "2", "label": "Set key signature to Cm", "toolName": "stori_set_key", "status": "pending" }, { "stepId": "3", "label": "Create Drums track", "toolName": "stori_add_midi_track", "parallelGroup": "instruments", "status": "pending" }, { "stepId": "4", "label": "Add content to Drums", "toolName": "stori_add_notes", "parallelGroup": "instruments", "detail": "8 bars, boom bap drums", "status": "pending" }, { "stepId": "5", "label": "Add effects to Drums", "toolName": "stori_add_insert_effect", "parallelGroup": "instruments", "detail": "Compressor", "status": "pending" }, ...] }`. See [Execution Timeline contract](#execution-timeline-contract) below. |
| `preflight` | Emitted before Phase 2 agents start (Agent Teams only). One per expected instrument step, derived from the plan — no LLM call. Lets the frontend pre-allocate timeline rows. `{ "type": "preflight", "stepId": "3", "agentId": "drums", "agentRole": "drums", "label": "Create Drums track", "toolName": "stori_add_midi_track", "parallelGroup": "instruments", "confidence": 0.9 }` |
| `planStepUpdate` | Step lifecycle update. `active` when starting, `completed` / `failed` when done. At plan completion, steps never activated are emitted as `skipped` — no step is left in `pending`. `{ "type": "planStepUpdate", "stepId": "1", "status": "active" \| "completed" \| "failed" \| "skipped", "result": "optional summary" }` |
| `toolStart` | Fires **before** each `toolCall` with a human-readable label. `{ "type": "toolStart", "name": "stori_add_midi_track", "label": "Create Drums track" }` |
| `toolCall` | Resolved tool call for the frontend to apply. In Agent Teams mode, includes `agentId` identifying which instrument agent produced it. `{ "type": "toolCall", "id": "...", "name": "stori_add_midi_track", "params": { "trackId": "uuid", ... }, "agentId": "drums" }`. **Critical: key is `"params"` (not `"arguments"`); key is `"name"` (not `"tool"`). All IDs are fully-resolved UUIDs.** |
| `toolError` | Non-fatal validation error. Stream continues. `{ "type": "toolError", "name": "stori_add_notes", "error": "Region not found", "errors": ["..."] }` |
| `summary.final` | Emitted by Agent Teams handler immediately before `complete`. Rich composition summary for the "Ready!" line. `{ "type": "summary.final", "traceId": "...", "trackCount": 3, "tracksCreated": [{"name": "Drums", "instrument": "TR-808", "trackId": "uuid"}], "regionsCreated": 3, "notesGenerated": 128, "effectsAdded": [{"trackId": "uuid", "type": "compressor"}], "effectCount": 2, "sendsCreated": 1, "ccEnvelopes": [{"cc": 74, "name": "Filter Cutoff"}], "automationLanes": 0 }` |
| `complete` | Stream done. Includes `inputTokens` and `contextWindowTokens` (see global `complete` row above). |

### COMPOSING mode events (Variation protocol)

Emitted when `state.state == "composing"`. Frontend enters Variation Review Mode.

COMPOSING now emits the same `reasoning`, `plan`, `planStepUpdate`, `toolStart`, and `toolCall` events as EDITING — see the unified event ordering below. The variation-specific events (`meta`, `phrase`, `done`) follow after the tool calls complete.

| type | Description |
|------|-------------|
| `reasoning` | Streamed planner chain-of-thought (same as EDITING/REASONING). Emitted during the LLM planning phase before the plan is ready. Deterministic plans (structured prompts with all fields) skip this. |
| `plan` | Structured plan (same shape as EDITING). `{ "type": "plan", "planId": "uuid", "title": "...", "steps": [...] }` |
| `planStepUpdate` | Step lifecycle (same as EDITING). Unactivated steps emitted as `skipped` at completion. `{ "type": "planStepUpdate", "stepId": "1", "status": "active" \| "completed" \| "skipped" }` |
| `toolStart` | Fires before each tool call during variation execution. `{ "type": "toolStart", "name": "stori_add_midi_track", "label": "Create Drums track" }` |
| `toolCall` | Proposal tool call. **`proposal: true`** — the frontend renders this for transparency but does NOT apply it to the DAW. `{ "type": "toolCall", "id": "...", "name": "...", "params": {...}, "proposal": true }` |
| `meta` | Variation summary. `{ "type": "meta", "variationId": "uuid", "baseStateId": "42", "intent": "...", "aiExplanation": "...", "affectedTracks": [...], "affectedRegions": [...], "noteCounts": { "added": 32, "removed": 0, "modified": 0 } }`. Use `baseStateId: "0"` for first variation after editing. |
| `phrase` | One musical phrase. `{ "type": "phrase", "phraseId": "uuid", "trackId": "uuid", "regionId": "uuid", "startBeat": 0.0, "endBeat": 16.0, "label": "...", "tags": [...], "explanation": "...", "noteChanges": [...], "controllerChanges": [] }` |
| `done` | End of variation stream. Frontend enables Accept/Discard. `{ "type": "done", "variationId": "uuid", "phraseCount": 4 }` |
| `complete` | Stream done. `{ "type": "complete", "success": true, "variationId": "uuid", "phraseCount": 4, "traceId": "..." }`. Includes `inputTokens` and `contextWindowTokens` (see global `complete` row above). |

#### Deprecated events (backward compat, will be removed)

| type | Description |
|------|-------------|
| `planSummary` | Replaced by `plan`. Still emitted alongside `plan` during transition. `{ "type": "planSummary", "totalSteps": 6, "generations": 2, "edits": 4 }` |
| `progress` | Replaced by `planStepUpdate` + `toolStart`/`toolCall`. Still emitted during transition. `{ "type": "progress", "currentStep": 3, "totalSteps": 6, "message": "..." }` |

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

### Event ordering

**EDITING:**
```
state → reasoning* → plan → [planStepUpdate(active) → toolStart → toolCall → planStepUpdate(completed)]* → planStepUpdate(skipped)* → content? → budgetUpdate → complete
```

Steps are grouped per-track (contiguous) in the `plan` event. During execution, instrument steps with the same `parallelGroup` may interleave (see [Parallel execution](#parallel-execution)). At completion, any steps never activated are emitted as `skipped` before `complete`.

**COMPOSING (unified):**
```
state → reasoning* → plan → [planStepUpdate(active) → toolStart → toolCall(proposal:true) → planStepUpdate(completed)]* → planStepUpdate(skipped)* → meta → phrase* → done → complete
```

Deprecated aliases `planSummary` and `progress` are still emitted alongside the new events during the transition period.

**REASONING:**
```
state → reasoning* → content → complete
```

`*` = zero or more events. `complete` is always final, even on errors.

### Parallel execution (Agent Teams)

Multi-instrument STORI PROMPT compositions (2+ roles) run each instrument as an independent LLM session — one dedicated HTTP call to the LLM API per instrument, all in-flight simultaneously. This is genuine Agent Teams parallelism, not sequential async task sharing.

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

**Backward compatibility:** Single-instrument requests and all non-STORI-PROMPT requests produce no `parallelGroup` annotations and execute sequentially via `_handle_editing` as before.

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

---

## Models (OpenRouter)

All models use OpenRouter's `reasoning` parameter for Chain of Thought. Two event types: `reasoning` (CoT) and `content` (user-facing).

**Supported models (exactly two):** `anthropic/claude-sonnet-4.6` (default) · `anthropic/claude-opus-4.6`. Both have a 200 000-token context window. Set `STORI_LLM_MODEL` in `.env` to switch.

---

## MCP tool routing

- **Server-side (Maestro):** Generation tools (`stori_generate_*`) run in the Maestro backend and return MIDI/result payloads.
- **DAW (Swift):** All other tools are forwarded to the connected Stori app over WebSocket. The DAW executes the action and returns a `tool_response` with `request_id` and `result`.

Same tool set for Stori app (SSE) and MCP. Full list and params: `GET /api/v1/mcp/tools`.

**Parameter alignment** (with `app/core/tool_validation.py`):

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

## Project

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_read_project` | Read current project state (tempo, key, tracks, regions). | `include_notes`, `include_automation` (optional bools) |
| `stori_create_project` | Create a new project. | `name`, `tempo` (required); `keySignature`, `timeSignature` |
| `stori_set_tempo` | Set project tempo (BPM). | `tempo` (40–240) |
| `stori_set_key` | Set key signature. | `key` (e.g. C, Am, F#m) |

---

## Track

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_midi_track` | Add MIDI track. Drums: set `drumKitId`. Melodic: set `gmProgram`. | `name` (required); `drumKitId`, `gmProgram` 0–127, `instrument`, `color`, `icon` |
| `stori_set_track_volume` | Set track volume. | `trackId`, `volumeDb` |
| `stori_set_track_pan` | Set track pan. | `trackId`, `pan` (-100–100) |
| `stori_set_track_name` | Rename track. | `trackId`, `name` |
| `stori_set_midi_program` | Set GM program (instrument voice). | `trackId`, `program` (0–127); `channel` (1–16, default 1; use 10 for drums) |
| `stori_mute_track` | Mute/unmute. | `trackId`, `muted` |
| `stori_solo_track` | Solo/unsolo. | `trackId`, `solo` |
| `stori_set_track_color` | Set track color. | `trackId`, `color` (red, orange, yellow, green, blue, purple, pink, teal, indigo) |
| `stori_set_track_icon` | Set track icon (SF Symbol). | `trackId`, `icon` (e.g. pianokeys, guitars, music.note) |

---

## Region

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_midi_region` | Add MIDI region to a track. | `trackId`, `startBeat`, `durationBeats` (required); `name` |
| `stori_delete_region` | Delete a region. | `regionId` |
| `stori_move_region` | Move region. | `regionId`, `startBeat` |
| `stori_duplicate_region` | Duplicate region. | `regionId`, `startBeat` |

---

## Notes

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_notes` | Add MIDI notes to region. | `regionId`, `notes` (array of `pitch`, `startBeat`, `durationBeats`, `velocity` 1–127) |
| `stori_clear_notes` | Clear all notes in region. | `regionId` |
| `stori_quantize_notes` | Quantize to grid. | `regionId`; `grid` (1/4, 1/8, 1/16, 1/32, 1/64); `strength` 0–1 |
| `stori_apply_swing` | Apply swing. | `regionId`, `amount` (0–1) |

---

## Effects & routing

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_insert_effect` | Add insert effect. | `trackId`, `type` (reverb, delay, compressor, eq, distortion, overdrive, filter, chorus, tremolo, phaser, flanger, modulation) |
| `stori_add_send` | Send track to bus. | `trackId`, `busId`, `levelDb` |
| `stori_ensure_bus` | Create bus if missing. | `name` |

**Auto-inference from STORI PROMPTs:** The planner infers effects from `Style` and `Role` fields before any LLM call — drums always get a compressor, pads/lead get a reverb send, and style-specific inserts (distortion for rock, filter for lo-fi, etc.) are added automatically. Suppress with `Constraints: no_effects: true`.

**Translation from STORI PROMPT `Effects` block:** When a structured prompt includes an `Effects:` YAML block, every entry is translated into a `stori_add_insert_effect` call. Reverb is routed via a shared `Reverb` bus (`stori_ensure_bus` → `stori_add_send`), never as a direct insert, so `stori_ensure_bus` is always guaranteed to precede any `stori_add_send` for the same bus name.

---

## Automation & MIDI control

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_automation` | Add track-level automation curves. | `target` (trackId), `points` (array of `{beat, value, curve?}`) |
| `stori_add_midi_cc` | Add MIDI CC events to a region. | `regionId`, `cc` (0–127), `events` (array of `{beat, value}`) |
| `stori_add_pitch_bend` | Add pitch bend events to a region. | `regionId`, `events` (array of `{beat, value}`) — values −8192 to +8191 |
| `stori_add_aftertouch` | Add aftertouch events (channel or polyphonic). | `regionId`, `events` (each `{beat, value}` or `{beat, value, pitch}`) |

**Translation from STORI PROMPT `MidiExpressiveness` block:** `cc_curves` entries → `stori_add_midi_cc`; `pitch_bend` style → `stori_add_pitch_bend`; `sustain_pedal` → `stori_add_midi_cc` with CC 64 (127=down, 0=up). These calls happen after notes are added to the region.

**Translation from STORI PROMPT `Automation` block:** Each lane → `stori_add_automation` using the trackId returned by `stori_add_midi_track`.

---

## Generation (server-side)

These run in Maestro and call the music model; they do not require a connected DAW. Orpheus required.

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_generate_midi` | Generate MIDI for a role (preferred). | `role`, `style`, `tempo`, `bars` (required); `key`, `constraints` |
| `stori_generate_drums` | Generate drum pattern. | `style`, `tempo`; `bars`, `complexity` |
| `stori_generate_bass` | Generate bass line. | `style`, `tempo`, `bars`; `key`, `chords` |
| `stori_generate_melody` | Generate melody. | `style`, `tempo`, `bars`; `key`, `scale`, `octave` |
| `stori_generate_chords` | Generate chord part. | `style`, `tempo`, `bars`; `key`, `progression` |

---

## Playback & transport

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_play` | Start playback. | `fromBeat` (optional) |
| `stori_stop` | Stop playback. | — |
| `stori_set_playhead` | Move playhead. | `bar`, `beat`, or `seconds` |

---

## UI

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_show_panel` | Show/hide panel. | `panel`, `visible` |
| `stori_set_zoom` | Set editor zoom. | `zoomPercent` |

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

Returns 4 randomly sampled STORI PROMPT inspiration cards from a curated pool of 22. Each call returns a different set. Styles span lo-fi boom bap, melodic techno, cinematic orchestral, Afrobeats, ambient drone, jazz reharmonization, dark trap, bossa nova, funk, neo-soul, drum & bass, minimal house, synthwave, post-rock, reggaeton, classical string quartet, psytrance, indie folk, New Orleans brass, Nordic ambient, flamenco fusion, and UK garage.

Every `fullPrompt` is a complete STORI PROMPT YAML using the full spec breadth — injected verbatim into the compose input on tap.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique slug |
| `title` | string | Human label, e.g. `"Lo-fi boom bap · Cm · 75 BPM"` |
| `preview` | string | First 3–4 YAML lines visible in the card |
| `fullPrompt` | string | Complete STORI PROMPT YAML |

```json
{
  "prompts": [
    {
      "id": "lofi_boom_bap",
      "title": "Lo-fi boom bap · Cm · 75 BPM",
      "preview": "Mode: compose · Section: verse\nStyle: lofi hip hop · Key: Cm · 75 BPM\nRole: drums, bass, piano, melody\nVibe: dusty x3, warm x2, melancholic",
      "fullPrompt": "STORI PROMPT\nMode: compose\n..."
    }
  ]
}
```

### Single template lookup

**Endpoint:** `GET /api/v1/maestro/prompts/{template_id}`
**Auth:** none

Returns a named prompt template with structured sections. Template IDs: `lofi_chill`, `dark_trap`, `jazz_trio`, `synthwave`, `cinematic`, `funk_groove`, `ambient`, `deep_house`, `full_production`, `beat_lab`, `mood_piece`. Returns 404 if the ID is unknown.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Template slug |
| `title` | string | Display title |
| `fullPrompt` | string | Flat prompt string |
| `sections` | object[] | Array of `{heading, content}` |

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

## Tool summary

| Category | Count |
|----------|-------|
| Project | 5 |
| Track | 10 |
| Region | 5 |
| Notes | 4 |
| Effects & routing | 3 |
| Automation / MIDI control | 4 |
| Generation | 5 |
| Playback | 3 |
| UI | 2 |

**Total: 41** MCP tools. Generation tools run server-side; the rest are forwarded to the DAW when connected.

See also: [integrate.md](../guides/integrate.md) for MCP setup (stdio, Cursor, WebSocket DAW connection).
