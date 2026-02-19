# Frontend Parity Prompt — Wire Format Contract

> **Context**: The backend enforces a strict, idiomatic key-naming contract across the entire API. All JSON on the wire uses **camelCase**. No dual-key fallbacks, no legacy aliases. This prompt describes the complete contract for every endpoint. Audit the frontend codebase and fix any mismatches.

---

## Casing Rules

| Context | Convention | Examples |
|---------|-----------|---------|
| JSON on the wire (all of it) | **camelCase** | `variationId`, `startBeat`, `noteChanges` |
| MCP tool names | **snake_case** | `stori_add_notes`, `stori_add_midi_track` |
| MCP tool parameters | **camelCase** | `trackId`, `regionId`, `startBeat` |
| Python internals | **snake_case** | `variation_id`, `start_beat` |
| Swift internals | **camelCase** | `variationId`, `startBeat` |

---

## 1. Project Context (`buildProjectContext()` → backend)

The project context is sent as the `project` field in compose/stream requests. The backend parses it with **no fallback keys**. If the wrong key name is used, the entity is silently dropped.

### Required shape

```json
{
  "id": "uuid-string",
  "name": "My Project",
  "tempo": 120,
  "key": "C",
  "timeSignature": "4/4",
  "tracks": [
    {
      "id": "uuid-string",
      "name": "Drums",
      "drumKitId": "acoustic",
      "gmProgram": null,
      "regions": [
        {
          "id": "uuid-string",
          "name": "Pattern 1",
          "startBeat": 0,
          "durationBeats": 16,
          "noteCount": 24
        }
      ]
    }
  ],
  "buses": [
    { "id": "uuid-string", "name": "Reverb" }
  ]
}
```

### Rules (enforced server-side, no fallbacks)

| Field | Canonical key | WRONG (will be ignored) |
|-------|--------------|------------------------|
| Project's own ID | `"id"` | ~~`"projectId"`~~ |
| Track identifier | `"id"` | ~~`"trackId"`~~ |
| Region identifier | `"id"` | ~~`"regionId"`~~ |
| Bus identifier | `"id"` | ~~`"busId"`~~ |
| Region collection | `"regions"` | ~~`"midiRegions"`~~ |
| Key signature | `"key"` | ~~`"keySignature"`~~ |
| Time signature | `"timeSignature"` | ~~`"time_signature"`~~ |
| Drum kit | `"drumKitId"` | ~~`"drum_kit_id"`~~ |
| GM program | `"gmProgram"` | ~~`"gm_program"`~~ |
| Region start | `"startBeat"` | ~~`"start_beat"`~~, ~~`"startTime"`~~ |
| Region duration | `"durationBeats"` | ~~`"duration_beats"`~~, ~~`"duration"`~~ |
| Note count | `"noteCount"` | ~~`"note_count"`~~ |

### Notes array behavior

- If a region includes `"notes": [...]` → backend uses those notes (even if empty array)
- If a region omits the `"notes"` key entirely (e.g., sends `"noteCount": 5` instead) → backend preserves whatever notes it already has from prior EDITING tool calls
- **Do NOT send `"notes": null`** — omit the key entirely if you don't have the note data

---

## 2. Maestro Endpoints

### `POST /api/v1/maestro/stream` — request body

```json
{
  "prompt": "add a funky bass line",
  "project": { ... },
  "conversationId": "uuid",
  "storePrompt": true,
  "model": "anthropic/claude-sonnet-4"
}
```

| Field | Key | Notes |
|-------|-----|-------|
| User prompt | `prompt` | Required |
| Project context | `project` | See §1 |
| Conversation ID | `conversationId` | Optional, links to conversation thread |
| Store prompt | `storePrompt` | Optional, default `true` |
| Model override | `model` | Optional, LLM model ID |

### `POST /api/v1/maestro/preview` — request body

Same shape as stream request.

### `POST /api/v1/maestro/preview` — response

```json
{
  "previewAvailable": true,
  "preview": { ... },
  "intent": "compose",
  "sseState": "COMPOSING"
}
```

### `GET /api/v1/validate-token` — response

```json
{
  "valid": true,
  "expiresAt": "2026-03-01T00:00:00+00:00",
  "expiresInSeconds": 86400,
  "budgetRemaining": 4.50,
  "budgetLimit": 5.00
}
```

### Maestro SSE budget error (402)

```json
{
  "detail": {
    "error": "Insufficient budget",
    "budgetRemaining": 0.0
  }
}
```

---

## 3. SSE Events (backend → frontend)

All SSE event data uses **camelCase** for both type values and payload keys.

### Event types (the `type` field value)

| Type | When |
|------|------|
| `state` | Intent classified |
| `status` | Status message |
| `content` | LLM response text |
| `reasoning` | Chain-of-thought |
| `planSummary` | Plan overview |
| `progress` | Step progress |
| `meta` | Variation metadata |
| `phrase` | Phrase data |
| `done` | Variation complete |
| `complete` | Stream complete |
| `toolCall` | Tool executed |
| `toolError` | Tool validation failed |
| `budgetUpdate` | Budget changed |
| `error` | Error occurred |

### Budget update event

```json
{
  "type": "budgetUpdate",
  "budgetRemaining": 4.32,
  "cost": 0.18
}
```

### Plan summary event

```json
{
  "type": "planSummary",
  "totalSteps": 3,
  "toolCalls": [
    { "name": "stori_add_midi_track", "arguments": {} }
  ]
}
```

### Tool call event

```json
{
  "type": "toolCall",
  "id": "call-id",
  "name": "stori_add_notes",
  "params": {
    "regionId": "uuid-string",
    "notes": [...]
  }
}
```

### Complete event (EDITING/REASONING)

```json
{
  "type": "complete",
  "success": true,
  "toolCalls": [],
  "stateVersion": 5,
  "traceId": "uuid"
}
```

### Complete event (COMPOSING)

```json
{
  "type": "complete",
  "success": true,
  "variationId": "uuid-string",
  "totalChanges": 15,
  "phraseCount": 3,
  "traceId": "uuid"
}
```

### Meta event

```json
{
  "type": "meta",
  "variationId": "uuid-string",
  "baseStateId": "42",
  "intent": "make the bass funkier",
  "aiExplanation": "Added syncopated notes...",
  "affectedTracks": ["track-uuid"],
  "affectedRegions": ["region-uuid"],
  "noteCounts": { "added": 12, "removed": 3, "modified": 0 }
}
```

### Phrase event

```json
{
  "type": "phrase",
  "phraseId": "uuid",
  "trackId": "uuid",
  "regionId": "uuid",
  "startBeat": 0.0,
  "endBeat": 4.0,
  "label": "Bars 1-4",
  "noteChanges": [
    {
      "noteId": "uuid",
      "changeType": "added",
      "before": null,
      "after": { "pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 100, "channel": 0 }
    }
  ],
  "controllerChanges": []
}
```

### Done event

```json
{
  "type": "done",
  "variationId": "uuid-string",
  "phraseCount": 3
}
```

### Frontend action required for `meta` event

1. Read `baseStateId` from the meta payload (it's a `String`, not an `Int`)
2. Store it alongside the variation
3. When committing, send this exact value as `baseStateId` in the commit request
4. **Do NOT fall back to `"0"` or the local project's `stateId`** — those will cause a 409 State Conflict

---

## 4. Event Envelope (variation streaming)

The `/variation/stream` SSE endpoint wraps events in an envelope:

```json
{
  "type": "meta|phrase|done|error|heartbeat",
  "sequence": 1,
  "variationId": "uuid",
  "projectId": "uuid",
  "baseStateId": "uuid",
  "timestampMs": 1708300000000,
  "payload": {}
}
```

---

## 5. Variation API

### `POST /api/v1/variation/propose` — request

```json
{
  "projectId": "uuid",
  "projectState": {},
  "baseStateId": "42",
  "intent": "make it minor",
  "scope": {
    "trackIds": ["uuid"],
    "regionIds": ["uuid"],
    "beatRange": [0, 16]
  },
  "options": {
    "phraseGrouping": "bars",
    "barSize": 4,
    "stream": true
  }
}
```

### `POST /api/v1/variation/propose` — response

```json
{
  "variationId": "uuid",
  "projectId": "uuid",
  "baseStateId": "42",
  "intent": "make it minor",
  "aiExplanation": null,
  "streamUrl": "/api/v1/variation/stream?variation_id=uuid"
}
```

### `POST /api/v1/variation/commit` — request

```json
{
  "projectId": "uuid",
  "baseStateId": "42",
  "variationId": "uuid",
  "acceptedPhraseIds": ["phrase-uuid-1", "phrase-uuid-2"]
}
```

### `POST /api/v1/variation/commit` — response

```json
{
  "projectId": "uuid",
  "newStateId": "43",
  "appliedPhraseIds": ["phrase-uuid-1"],
  "undoLabel": "Accept Variation: make it minor",
  "updatedRegions": []
}
```

---

## 6. User Endpoints

### `POST /api/v1/users/register` — request

```json
{
  "userId": "device-uuid"
}
```

### `POST /api/v1/users/register` — response / `GET /api/v1/users/me` — response

```json
{
  "userId": "device-uuid",
  "budgetRemaining": 5.00,
  "budgetLimit": 5.00,
  "usageCount": 0,
  "createdAt": "2026-02-19T00:00:00+00:00"
}
```

### `POST /api/v1/users/{userId}/budget` — request

```json
{
  "budgetCents": 500
}
```

---

## 7. Models Endpoint

### `GET /api/v1/models` — response

```json
{
  "models": [
    {
      "id": "anthropic/claude-sonnet-4",
      "name": "Claude Sonnet 4",
      "costPer1mInput": 3.0,
      "costPer1mOutput": 15.0,
      "supportsReasoning": false
    }
  ],
  "defaultModel": "anthropic/claude-sonnet-4"
}
```

---

## 8. Conversation Endpoints

### `POST /api/v1/conversations` — request

```json
{
  "title": "New Conversation",
  "projectId": "uuid",
  "projectContext": {}
}
```

### Conversation response shape (create / get)

```json
{
  "id": "uuid",
  "title": "My Conversation",
  "projectId": "uuid",
  "createdAt": "2026-02-19T00:00:00+00:00",
  "updatedAt": "2026-02-19T00:00:00+00:00",
  "isArchived": false,
  "projectContext": {},
  "messages": [
    {
      "id": "uuid",
      "role": "user",
      "content": "add drums",
      "timestamp": "2026-02-19T00:00:00+00:00",
      "modelUsed": null,
      "tokensUsed": null,
      "cost": 0.0,
      "toolCalls": [],
      "sseEvents": [],
      "actions": [
        {
          "id": "uuid",
          "actionType": "toolCall",
          "description": "stori_set_tempo(tempo=120)",
          "success": true,
          "errorMessage": null,
          "timestamp": "2026-02-19T00:00:00+00:00"
        }
      ]
    }
  ]
}
```

### `GET /api/v1/conversations` — response

```json
{
  "conversations": [
    {
      "id": "uuid",
      "title": "My Conversation",
      "projectId": "uuid",
      "createdAt": "...",
      "updatedAt": "...",
      "isArchived": false,
      "messageCount": 5,
      "preview": "First message text..."
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `PATCH /api/v1/conversations/{id}` — request

```json
{
  "title": "Updated Title",
  "projectId": "uuid"
}
```

### `PATCH /api/v1/conversations/{id}` — response

```json
{
  "id": "uuid",
  "title": "Updated Title",
  "projectId": "uuid",
  "updatedAt": "2026-02-19T00:00:00+00:00"
}
```

### `GET /api/v1/conversations/search` — response

```json
{
  "results": [
    {
      "id": "uuid",
      "title": "My Conversation",
      "preview": "matching text...",
      "updatedAt": "...",
      "relevanceScore": 1.0
    }
  ]
}
```

---

## 9. Token Endpoints

### `GET /api/v1/users/me/tokens` — response

```json
{
  "tokens": [
    {
      "id": "token-uuid",
      "expiresAt": "2026-03-01T00:00:00+00:00",
      "revoked": false,
      "createdAt": "2026-02-19T00:00:00+00:00"
    }
  ],
  "count": 1
}
```

### `POST /api/v1/users/me/tokens/revoke-all` — response

```json
{
  "success": true,
  "message": "Revoked 3 tokens. You will need to obtain a new token.",
  "revokedCount": 3
}
```

---

## 10. Asset Endpoints

### Presigned URL responses (drum-kits, soundfonts, bundles)

```json
{
  "url": "https://s3.amazonaws.com/...",
  "expiresAt": "2026-02-19T01:00:00+00:00"
}
```

---

## 11. MCP WebSocket Protocol

### Message types (backend → DAW)

| Type | Payload |
|------|---------|
| `connected` | `{ "type": "connected", "connectionId": "uuid" }` |
| `toolCall` | `{ "type": "toolCall", "requestId": "req-id", "tool": "stori_set_tempo", "arguments": {} }` |
| `pong` | `{ "type": "pong" }` |

### Message types (DAW → backend)

| Type | Payload |
|------|---------|
| `projectState` | `{ "type": "projectState", "state": {} }` |
| `toolResponse` | `{ "type": "toolResponse", "requestId": "req-id", "result": {} }` |
| `ping` | `{ "type": "ping" }` |

### `POST /api/v1/mcp/connection` — response

```json
{
  "connectionId": "uuid"
}
```

### `POST /api/v1/mcp/response/{connectionId}` — request

```json
{
  "requestId": "req-id",
  "result": {}
}
```

---

## 12. Audit Checklist

Search the frontend codebase for these and fix any mismatches:

### Project context
- [ ] `buildProjectContext()` — project's own ID uses `"id"` (not `"projectId"`)
- [ ] `buildProjectContext()` — entities use `"id"` (not `"trackId"` / `"regionId"` / `"busId"`)
- [ ] `buildProjectContext()` — regions under `"regions"` key (not `"midiRegions"`)
- [ ] `buildProjectContext()` — key signature under `"key"` (not `"keySignature"`)
- [ ] `buildProjectContext()` — time signature under `"timeSignature"` (not `"time_signature"`)
- [ ] `buildProjectContext()` — track instrument keys: `"drumKitId"`, `"gmProgram"` (camelCase)
- [ ] `buildProjectContext()` — region timing: `"startBeat"`, `"durationBeats"` (camelCase)

### Maestro stream
- [ ] Request body keys: `conversationId`, `storePrompt` (not `conversation_id`, `store_prompt`)
- [ ] SSE event type `"toolCall"` (not `"tool_call"`)
- [ ] SSE event type `"toolError"` (not `"tool_error"`)
- [ ] SSE event type `"planSummary"` (not `"plan_summary"`)
- [ ] SSE event type `"budgetUpdate"` (not `"budget_update"`)
- [ ] Budget SSE payload: `budgetRemaining` (not `budget_remaining`)
- [ ] Complete SSE payload: `toolCalls`, `stateVersion`, `traceId` (not snake_case)
- [ ] Plan summary payload: `totalSteps`, `toolCalls` (not snake_case)

### Validate token
- [ ] Response: `expiresAt`, `expiresInSeconds` (not `expires_at`, `expires_in_seconds`)
- [ ] Response: `budgetRemaining`, `budgetLimit` (not `budget_remaining`, `budget_limit`)

### Preview endpoint
- [ ] Response: `previewAvailable`, `sseState` (not `preview_available`, `sse_state`)

### Variation protocol
- [ ] SSE payload keys — all camelCase (`variationId`, `baseStateId`, `phraseId`, `noteChanges`, etc.)
- [ ] Meta event — parses `baseStateId` (not `base_state_id`)
- [ ] Commit request — sends `baseStateId` from the meta event
- [ ] Commit request — uses `acceptedPhraseIds` (not `accepted_phrase_ids`)
- [ ] Propose response — reads `variationId`, `streamUrl` (not `variation_id`, `stream_url`)
- [ ] Commit response — reads `newStateId`, `appliedPhraseIds`, `undoLabel`, `updatedRegions`
- [ ] EventEnvelope parsing — `variationId`, `projectId`, `baseStateId`, `timestampMs`

### User / auth
- [ ] Register request: `userId` (not `user_id`)
- [ ] User response: `userId`, `budgetRemaining`, `budgetLimit`, `usageCount`, `createdAt`
- [ ] Budget request: `budgetCents` (not `budget_cents`)

### Models
- [ ] Response: `defaultModel` (not `default_model`)
- [ ] Model fields: `costPer1mInput`, `costPer1mOutput`, `supportsReasoning`

### Conversations
- [ ] Create request: `projectId`, `projectContext` (not `project_id`, `project_context`)
- [ ] Update request: `projectId` (not `project_id`)
- [ ] Response: `projectId`, `createdAt`, `updatedAt`, `isArchived`, `projectContext`
- [ ] List item: `messageCount` (not `message_count`)
- [ ] Message: `modelUsed`, `tokensUsed`, `toolCalls`, `sseEvents` (not snake_case)
- [ ] Actions: `actionType`, `errorMessage` (not `action_type`, `error_message`)
- [ ] Update response: `projectId`, `updatedAt` (not `project_id`, `updated_at`)
- [ ] Search result: `updatedAt`, `relevanceScore` (not `updated_at`, `relevance_score`)

### Tokens
- [ ] Token info: `expiresAt`, `createdAt` (not `expires_at`, `created_at`)
- [ ] Revoke response: `revokedCount` (not `revoked_count`)

### Assets
- [ ] Presigned URL response: `expiresAt` (not `expires_at`)

### MCP WebSocket
- [ ] Connection welcome: `connectionId` (not `connection_id`)
- [ ] Tool call message: `"type": "toolCall"`, `requestId` (not `"tool_call"`, `request_id`)
- [ ] Tool response message: `"type": "toolResponse"`, `requestId` (not `"tool_response"`, `request_id`)
- [ ] Project state message: `"type": "projectState"` (not `"project_state"`)
- [ ] Connection endpoint response: `connectionId` (not `connection_id`)
- [ ] Response endpoint body: `requestId` (not `request_id`)

### General
- [ ] UUID handling — normalize comparisons with `.lowercased()`
- [ ] Notes omission — when `notes` array is unavailable, omit the key entirely (don't send `null`)
- [ ] Remove any `convertFromSnakeCase` / `convertToSnakeCase` JSON coding strategies — wire format is already camelCase
- [ ] Pydantic models with `populate_by_name=True` accept both `camelCase` and `snake_case` for requests (graceful transition), but all new code should send `camelCase`
