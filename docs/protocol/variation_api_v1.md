# Variation API v1 — Wire Contract

> Canonical backend-first specification for the Muse/Variation protocol.
> All endpoints are under `/api/v1/`.

---

## Execution Mode Policy (Backend-Owned)

The backend determines execution mode from intent classification. The frontend does not choose.

| Intent state | `execution_mode` | Behavior |
|---|---|---|
| **COMPOSING** | `variation` (forced) | All tool calls produce a Variation for human review |
| **EDITING** | `apply` (forced) | Structural ops apply immediately (tool_call events) |
| **REASONING** | n/a | Chat only, no tools |

**Every COMPOSING request produces a Variation** — including purely additive ones (first-time MIDI generation, "create a new song").

The frontend knows which mode is active from the `state` SSE event (`"composing"` / `"editing"` / `"reasoning"`) emitted at the start of every compose stream.

**Compose stream path (primary):** When the user sends a prompt via `POST /api/v1/compose/stream`, the backend classifies the intent and either:
- Streams `tool_call` events directly (EDITING), or
- Streams `meta` / `phrase*` / `done` Variation events (COMPOSING)

**Variation endpoints (secondary):** The dedicated `/variation/propose`, `/variation/stream`, `/variation/commit`, `/variation/discard` endpoints are available for explicit programmatic variation management.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/variation/propose` | Create a variation proposal (async generation) |
| GET | `/variation/stream` | SSE stream of variation events |
| GET | `/variation/{variation_id}` | Poll variation status + phrases |
| POST | `/variation/commit` | Apply accepted phrases to canonical state |
| POST | `/variation/discard` | Cancel and discard a variation |

---

## 1. POST /variation/propose

Create a variation proposal. Returns immediately with `variation_id` and `stream_url`.
Background task generates the variation (CREATED → STREAMING → READY).

### Request

```json
{
  "project_id": "uuid",
  "base_state_id": "42",
  "intent": "add a jazz bass line to bars 5-8",
  "model": "default",
  "scope": null,
  "options": {
    "phrase_grouping": "bars",
    "bar_size": 4,
    "stream": true
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project_id` | string | yes | Project UUID |
| `base_state_id` | string | yes | Current project version (optimistic concurrency) |
| `intent` | string | yes | What the user wants to change |
| `model` | string | no | LLM model override |
| `scope` | object | no | Target scope (regions, tracks) |
| `options` | object | no | Phrase grouping, streaming preference |

### Response (200)

```json
{
  "variation_id": "uuid",
  "project_id": "uuid",
  "base_state_id": "42",
  "intent": "add a jazz bass line to bars 5-8",
  "ai_explanation": null,
  "stream_url": "/api/v1/variation/stream?variation_id=uuid"
}
```

### Errors

| Code | Condition |
|------|-----------|
| 402 | Insufficient budget |
| 409 | `base_state_id` mismatch (project changed) |

---

## 2. GET /variation/stream

Real-time SSE stream of variation events. Supports late-join replay via `from_sequence`.

### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `variation_id` | string | required | Variation UUID |
| `from_sequence` | int | 0 | Resume from sequence N (skip events <= N) |

### Event Envelope (all events)

Every SSE event uses the transport-agnostic envelope:

```
event: <type>
data: {
  "type": "meta|phrase|done|error|heartbeat",
  "sequence": 1,
  "variation_id": "uuid",
  "project_id": "uuid",
  "base_state_id": "42",
  "timestamp_ms": 1708099200000,
  "payload": { ... }
}
```

### Event: `meta` (sequence = 1, always first)

```json
{
  "type": "meta",
  "sequence": 1,
  "payload": {
    "intent": "add a jazz bass line",
    "ai_explanation": "Adding a walking bass pattern...",
    "affected_tracks": ["track-bass"],
    "affected_regions": ["region-bass-1"],
    "note_counts": { "added": 12, "removed": 0, "modified": 3 }
  }
}
```

### Event: `phrase` (sequence = 2..N)

```json
{
  "type": "phrase",
  "sequence": 2,
  "payload": {
    "phrase_id": "uuid",
    "track_id": "track-bass",
    "region_id": "region-bass-1",
    "start_beat": 16.0,
    "end_beat": 32.0,
    "label": "Bars 5-8",
    "tags": ["pitchChange", "rhythmChange"],
    "explanation": "Walking bass line following ii-V-I",
    "note_changes": [
      {
        "note_id": "nc-uuid",
        "change_type": "added",
        "before": null,
        "after": { "pitch": 43, "start_beat": 16.0, "duration_beats": 1.0, "velocity": 90, "channel": 0 }
      }
    ],
    "controller_changes": []
  }
}
```

### Event: `done` (always last)

```json
{
  "type": "done",
  "sequence": 5,
  "payload": {
    "status": "ready",
    "phrase_count": 3
  }
}
```

Status values: `ready`, `failed`, `discarded`.

### Event: `error` (followed by `done(status=failed)`)

```json
{
  "type": "error",
  "sequence": 4,
  "payload": {
    "message": "Generation failed: timeout",
    "code": "GENERATION_ERROR"
  }
}
```

### Event: `heartbeat` (keep-alive, no sequence)

```
event: heartbeat
data: {}
```

### Sequence Ordering Rules

1. `meta` is always sequence = 1
2. `phrase` events are sequence = 2..N (strictly increasing)
3. `done` is always the last sequenced event
4. If error occurs: `error` then `done(status=failed)`
5. Sequences are strictly monotonic per variation

### Errors

| Code | Condition |
|------|-----------|
| 404 | `variation_id` not found |

---

## 3. GET /variation/{variation_id}

Poll variation status and phrases. Used for reconnect or non-streaming clients.

### Response (200)

```json
{
  "variation_id": "uuid",
  "project_id": "uuid",
  "base_state_id": "42",
  "intent": "add a jazz bass line",
  "status": "ready",
  "ai_explanation": "Adding a walking bass pattern...",
  "affected_tracks": ["track-bass"],
  "affected_regions": ["region-bass-1"],
  "phrases": [
    {
      "phrase_id": "uuid",
      "sequence": 2,
      "track_id": "track-bass",
      "region_id": "region-bass-1",
      "beat_start": 16.0,
      "beat_end": 32.0,
      "label": "Bars 5-8",
      "tags": ["pitchChange"],
      "ai_explanation": "Walking bass",
      "diff": { ... }
    }
  ],
  "phrase_count": 3,
  "last_sequence": 5,
  "created_at": "2026-02-16T10:00:00Z",
  "updated_at": "2026-02-16T10:00:05Z",
  "error_message": null
}
```

### Errors

| Code | Condition |
|------|-----------|
| 404 | `variation_id` not found |

---

## 4. POST /variation/commit

Apply accepted phrases to canonical state. Loads variation from backend store (no client-provided `variation_data` required).

### Request

```json
{
  "project_id": "uuid",
  "base_state_id": "42",
  "variation_id": "uuid",
  "accepted_phrase_ids": ["phrase-1", "phrase-3"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project_id` | string | yes | Project UUID |
| `base_state_id` | string | yes | Must match current project version |
| `variation_id` | string | yes | Variation to commit |
| `accepted_phrase_ids` | string[] | yes | Phrase IDs to apply (subset selection) |
| `variation_data` | object | no | **Deprecated.** Backward compat only |
| `request_id` | string | no | Idempotency key |

### Response (200)

```json
{
  "project_id": "uuid",
  "new_state_id": "43",
  "applied_phrase_ids": ["phrase-1", "phrase-3"],
  "undo_label": "Accept Variation: add a jazz bass line",
  "updated_regions": [
    {
      "region_id": "uuid",
      "track_id": "uuid",
      "notes": [
        { "pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100, "channel": 0 }
      ]
    }
  ]
}
```

`updated_regions` contains the **full** materialized note state for every region
affected by the accepted phrases.  The frontend should replace its local region
notes with this data — no need to re-read project state or apply diffs locally.

### Errors

| Code | Condition |
|------|-----------|
| 400 | Invalid phrase IDs, invalid variation_data |
| 404 | Variation not found (and no variation_data provided) |
| 409 | `base_state_id` mismatch |
| 409 | Variation not in READY state |
| 409 | Variation already committed (double commit) |

---

## 5. POST /variation/discard

Discard a variation. If generation is in progress, cancels it.

### Request

```json
{
  "project_id": "uuid",
  "variation_id": "uuid"
}
```

### Response (200)

```json
{
  "ok": true
}
```

### Behavior

- If STREAMING: cancels background generation, emits `done(status=discarded)` to SSE
- If CREATED or READY: transitions to DISCARDED immediately
- If already DISCARDED: returns `ok: true` (idempotent)

### Errors

| Code | Condition |
|------|-----------|
| 409 | Variation in terminal state (COMMITTED, FAILED, EXPIRED) |

---

## State Machine

```
CREATED → STREAMING → READY → COMMITTED
    |          |         |
    |          |         +→ DISCARDED
    |          |         +→ FAILED
    |          |         +→ EXPIRED
    |          +→ DISCARDED
    |          +→ FAILED
    |          +→ EXPIRED
    +→ DISCARDED
    +→ FAILED
    +→ EXPIRED
```

Terminal states: `COMMITTED`, `DISCARDED`, `FAILED`, `EXPIRED`. No transitions from terminal states.

---

## Invariants

1. **No mutation during proposal** — canonical state is never modified during CREATED/STREAMING/READY
2. **Baseline safety** — `base_state_id` validated at both propose and commit
3. **Commit only from READY** — no early commit from STREAMING or CREATED
4. **Partial acceptance** — commit applies only selected `accepted_phrase_ids`
5. **Removals work** — `removed` and `modified` note changes are properly applied
6. **Strict sequencing** — meta=1, phrases=2..N, done=last, monotonically increasing
7. **Transport-agnostic envelope** — all events use the same envelope shape
8. **Discard cancels generation** — background task is cancelled, done(discarded) emitted

---

## Change Types (NoteChange)

| `change_type` | `before` | `after` | Commit Action |
|---------------|----------|---------|---------------|
| `added` | null | note | Add note to region |
| `removed` | note | null | Remove note from region |
| `modified` | old note | new note | Remove old + add new |
