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

All events are newline-delimited `data: {json}\n\n` lines. Every event has a `type` field. **Keys are camelCase.**

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
| `plan` | Structured plan emitted once after initial reasoning, before the first tool call. `{ "type": "plan", "planId": "uuid", "title": "Creating lo-fi intro (Cm, 72 BPM)", "steps": [{ "stepId": "1", "label": "Set tempo to 72 BPM", "status": "pending" }, { "stepId": "2", "label": "Set key to Cm", "status": "pending" }, { "stepId": "3", "label": "Create Drums track", "status": "pending" }, ...] }` |
| `planStepUpdate` | Step lifecycle update. Emitted twice per step: `active` when starting, `completed` / `failed` / `skipped` when done. `{ "type": "planStepUpdate", "stepId": "1", "status": "active" \| "completed" \| "failed" \| "skipped", "result": "optional summary" }` |
| `toolStart` | Fires **before** each `toolCall` with a human-readable label. `{ "type": "toolStart", "name": "stori_add_midi_track", "label": "Creating Drums track" }` |
| `toolCall` | Resolved tool call for the frontend to apply. `{ "type": "toolCall", "id": "...", "name": "stori_add_midi_track", "params": { "trackId": "uuid", ... } }`. **Critical: key is `"params"` (not `"arguments"`); key is `"name"` (not `"tool"`). All IDs are fully-resolved UUIDs.** |
| `toolError` | Non-fatal validation error. Stream continues. `{ "type": "toolError", "name": "stori_add_notes", "error": "Region not found", "errors": ["..."] }` |
| `complete` | Stream done. Includes `inputTokens` and `contextWindowTokens` (see global `complete` row above). |

### COMPOSING mode events (Variation protocol)

Emitted when `state.state == "composing"`. Frontend enters Variation Review Mode.

| type | Description |
|------|-------------|
| `planSummary` | High-level composition plan. `{ "type": "planSummary", "totalSteps": 6, "generations": 2, "edits": 4 }` |
| `progress` | Per-step progress. `{ "type": "progress", "currentStep": 3, "totalSteps": 6, "message": "Adding chord voicings" }` |
| `meta` | Variation summary (first composing event). `{ "type": "meta", "variationId": "uuid", "baseStateId": "42", "intent": "...", "aiExplanation": "...", "affectedTracks": [...], "affectedRegions": [...], "noteCounts": { "added": 32, "removed": 0, "modified": 0 } }`. Use `baseStateId: "0"` for first variation after editing. |
| `phrase` | One musical phrase. `{ "type": "phrase", "phraseId": "uuid", "trackId": "uuid", "regionId": "uuid", "startBeat": 0.0, "endBeat": 16.0, "label": "...", "tags": [...], "explanation": "...", "noteChanges": [...], "controllerChanges": [] }` |
| `done` | End of variation stream. Frontend enables Accept/Discard. `{ "type": "done", "variationId": "uuid", "phraseCount": 4 }` |
| `complete` | Stream done. `{ "type": "complete", "success": true, "variationId": "uuid", "phraseCount": 4, "traceId": "..." }`. Includes `inputTokens` and `contextWindowTokens` (see global `complete` row above). |

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
state → reasoning* → plan → planStepUpdate(active) → toolStart → toolCall → planStepUpdate(completed) → ... → content? → budgetUpdate → complete
```

**COMPOSING:**
```
state → reasoning* → planSummary → progress* → meta → phrase* → done → complete
```

**REASONING:**
```
state → reasoning* → content → complete
```

`*` = zero or more events. `complete` is always final, even on errors.

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
- **Insert effect:** `stori_add_insert_effect` with param `type` (e.g. `"reverb"`, `"compressor"`).
- **Send:** `stori_add_send` uses `busId` (from `stori_ensure_bus` or DAW).
- **Notes:** In `stori_add_notes`, each note uses `startBeat`, `durationBeats`, `velocity` (1–127).
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
| `stori_add_insert_effect` | Add insert effect. | `trackId`, `type` (reverb, delay, compressor, eq, distortion, filter, chorus, etc.) |
| `stori_add_send` | Send track to bus. | `trackId`, `busId`, `levelDb` |
| `stori_ensure_bus` | Create bus if missing. | `name` |

---

## Automation & MIDI control

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_automation` | Add automation. | `target`, `points` (array of `beat`, `value`, optional `curve`) |
| `stori_add_midi_cc` | Add MIDI CC events. | `regionId`, `cc` (0–127), `events` |
| `stori_add_pitch_bend` | Add pitch bend events. | `regionId`, `events` |

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

## Tool summary

| Category | Count |
|----------|-------|
| Project | 5 |
| Track | 10 |
| Region | 5 |
| Notes | 4 |
| Effects | 4 |
| Automation / MIDI control | 3 |
| Generation | 5 |
| Playback | 3 |
| UI | 2 |

**Total: 41** MCP tools. Generation tools run server-side; the rest are forwarded to the DAW when connected.

See also: [integrate.md](../guides/integrate.md) for MCP setup (stdio, Cursor, WebSocket DAW connection).
