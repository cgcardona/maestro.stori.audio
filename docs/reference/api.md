# API & MCP tools reference

Streaming (SSE), event types, models, and the full MCP tool set in one place. Tool definitions live in `app/mcp/tools.py`; validation in `app/core/tool_validation.py`.

---

## Compose stream

**Endpoint:** `POST /api/v1/compose/stream`  
**Auth:** `Authorization: Bearer <token>`  
**Body:** JSON with `prompt`, optional `project` (app state), `conversation_id`, `model`.  
**Response:** SSE stream of JSON objects; each has a `type` field.

The backend determines execution mode from intent classification: COMPOSING -> variation (human review), EDITING -> apply (immediate). See [architecture.md](architecture.md).

The `prompt` field accepts both natural language and the **Stori structured prompt** format. When a prompt begins with `STORI PROMPT`, it is parsed as a structured prompt and routed deterministically by the `Mode` field, bypassing NL classification. See [stori-prompt-spec.md](../protocol/stori-prompt-spec.md).

---

## SSE event types

### Common events (all modes)

| type | Description |
|------|-------------|
| `state` | Intent classification result: `"composing"`, `"editing"`, or `"reasoning"`. **First meaningful event.** Frontend switches UI mode based on this. |
| `status` | Human-readable status |
| `error` | Error message |

### EDITING mode events

| type | Description |
|------|-------------|
| `tool_call` | Tool name + params (client applies immediately) |
| `complete` | Stream done |

### COMPOSING mode events (Variation protocol)

| type | Description |
|------|-------------|
| `meta` | Variation summary: `variation_id`, intent, explanation, affected tracks/regions, note counts |
| `phrase` | One musical phrase: `phrase_id`, region, beat range, note changes |
| `done` | End of variation stream. Frontend enables Accept/Discard. |
| `complete` | Stream done (after `done`) |

### REASONING mode events

| type | Description |
|------|-------------|
| `reasoning` | LLM reasoning chunk (CoT) |
| `content` | User-facing text response |
| `complete` | Stream done |

**Key:** The `state` event tells the frontend which set of events to expect. For COMPOSING, the frontend enters Variation Review Mode and accumulates `meta` + `phrase` events until `done`. For EDITING, the frontend applies `tool_call` events directly.

**Variable refs:** Params can use `$N.field` (e.g. `$0.trackId`, `$1.regionId`). Backend resolves to concrete IDs.

---

## Models (OpenRouter)

All models use OpenRouter's `reasoning` parameter for Chain of Thought. Two event types: `reasoning` (CoT) and `content` (user-facing).

**Default:** `anthropic/claude-3.7-sonnet` ($3/$15 per 1M). **Also:** Claude Sonnet/Opus 4.x, `openai/o1`, `openai/o1-preview`, `openai/o1-mini`. Set `STORI_LLM_MODEL` in `.env`.

---

## MCP tool routing

- **Server-side (Composer):** Generation tools (`stori_generate_*`) run in the Composer backend and return MIDI/result payloads.
- **DAW (Swift):** All other tools are forwarded to the connected Stori app over WebSocket. The DAW executes the action and returns a `tool_response` with `request_id` and `result`.

Same tool set for Stori app (SSE) and MCP. Full list and params: `GET /api/v1/mcp/tools`.

**Parameter alignment** (with `app/core/tool_validation.py`):

- **Track volume:** `volumeDb` (dB; 0 = unity). Not 0–1.
- **Track pan:** `pan` in range -100 (left) to 100 (right).
- **Insert effect:** Prefer `stori_add_insert_effect` with param `type` (not `effectType`).
- **Send:** `stori_add_send` uses `busId` (from `stori_ensure_bus` or DAW).
- **Notes:** In `stori_add_notes`, each note uses `startBeat`, `durationBeats`, `velocity` (1–127).
- **Quantize:** `stori_quantize_notes` uses `grid`: `"1/4"`, `"1/8"`, `"1/16"`, `"1/32"`, `"1/64"`.
- **Region:** `stori_add_region` / `stori_add_midi_region` use `startBeat`, `durationBeats`.

---

## Project

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_read_project` | Read current project state (tempo, key, tracks, regions). | `include_notes`, `include_automation` (optional bools) |
| `stori_create_project` | Create a new project. | `name`, `tempo` (required); `keySignature`, `timeSignature` |
| `stori_set_tempo` | Set project tempo (BPM). | `tempo` (40–240) |
| `stori_set_key` | Set key signature (alias). | `key` (e.g. C, Am, F#m) |
| `stori_set_key_signature` | Set key signature (core name). | `key` |

---

## Track

| Tool | Description | Key parameters |
|------|-------------|-----------------|
| `stori_add_track` | Add MIDI track (drums: `drumKitId`; melodic: `gmProgram`). | `name` (required); `gmProgram` 0–127, `drumKitId`, `color`, `volume`, `pan` |
| `stori_add_midi_track` | Add MIDI track (alternative; instrument/icon). | `name`, `instrument`, `gmProgram`, `color`, `icon` |
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
| `stori_add_region` | Add MIDI region to track. | `trackId`, `startBeat`, `durationBeats` (required); `name`, `color` |
| `stori_add_midi_region` | Same, core param names. | `trackId`, `startBeat`, `durationBeats`; `name` |
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
| `stori_add_effect` | Add insert effect (alias; uses `effectType`). | `trackId`, `effectType` |
| `stori_add_insert_effect` | Add insert effect (core; use param `type`). | `trackId`, `type` (reverb, delay, compressor, eq, distortion, filter, chorus, etc.) |
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

These run in Composer and call the music model; they do not require a connected DAW. Orpheus required.

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
