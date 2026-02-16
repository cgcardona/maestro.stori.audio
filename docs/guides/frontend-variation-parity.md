# Frontend Parity — Muse Variation Review Mode (Swift/SwiftUI)

> **Context:** The backend now enforces execution mode based on intent classification. COMPOSING intents always produce a Variation for human review. The frontend must handle this new event flow.

---

## What Changed (Backend)

The backend determines the execution mode. The frontend reacts to the `state` SSE event at the start of every compose stream:

| `state` value | What the frontend receives | What the frontend should do |
|---|---|---|
| `"composing"` | `meta`, `phrase*`, `done`, `complete` events | Enter **Variation Review Mode** |
| `"editing"` | `tool_call*`, `complete` events | Apply tool calls directly (existing behavior) |
| `"reasoning"` | `reasoning`, `content`, `complete` events | Show chat response (existing behavior) |

The `execution_mode` field has been removed from `ComposeRequest`. The frontend should not send it.

---

## SSE Event Flow for COMPOSING

When the frontend receives `state: "composing"`, the stream will emit these events in order:

### 1. `state` (first event)
```json
{
  "type": "state",
  "state": "composing",
  "intent": "compose.generate_music",
  "confidence": 0.8,
  "trace_id": "uuid"
}
```
**Action:** Prepare for Variation Review Mode. Show a "Generating variation..." indicator.

### 2. `status`
```json
{ "type": "status", "message": "Generating variation..." }
```
**Action:** Update status text in UI.

### 3. `plan_summary` (optional)
```json
{ "type": "plan_summary", "total_steps": 22, "generations": 4, "edits": 8 }
```
**Action:** Optionally show progress (e.g., "Planning 22 steps...").

### 4. `meta` (variation summary)
```json
{
  "type": "meta",
  "variation_id": "uuid",
  "intent": "Make a chill lo-fi beat at 85 BPM",
  "ai_explanation": "...",
  "affected_tracks": ["uuid"],
  "affected_regions": ["uuid"],
  "note_counts": { "added": 12, "removed": 0, "modified": 3 }
}
```
**Action:** Store the `variation_id`. Show the Variation Review banner with intent text, AI explanation, and note counts (+added / -removed / ~modified).

### 5. `phrase` (one per phrase, 0 or more)
```json
{
  "type": "phrase",
  "phrase_id": "uuid",
  "track_id": "uuid",
  "region_id": "uuid",
  "start_beat": 0.0,
  "end_beat": 4.0,
  "label": "Bars 1-4",
  "tags": ["pitchChange"],
  "explanation": "Walking bass line",
  "note_changes": [
    {
      "note_id": "uuid",
      "change_type": "added",
      "before": null,
      "after": { "pitch": 43, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 90 }
    }
  ],
  "controller_changes": []
}
```
**Action:** Accumulate phrases. Render note changes in the piano roll overlay (green for added, red ghost for removed, connector for modified).

### 6. `done`
```json
{ "type": "done", "variation_id": "uuid" }
```
**Action:** Generation is complete. Enable **Accept** and **Discard** buttons.

### 7. `complete`
```json
{
  "type": "complete",
  "success": true,
  "variation_id": "uuid",
  "total_changes": 15,
  "phrase_count": 4,
  "trace_id": "uuid"
}
```
**Action:** Stream is done. If `success` is false, show an error state.

---

## Variation Review Mode UI

### Banner (always visible while reviewing)
- Intent text (from `meta.intent`)
- AI explanation (from `meta.ai_explanation`, optional)
- Counts: `+{added} / -{removed} / ~{modified}` (from `meta.note_counts`)
- Controls: **Accept All**, **Discard**, **Review Phrases** (opens phrase list)

### Piano Roll Overlay
- **Added notes:** green
- **Removed notes:** red ghost (semi-transparent)
- **Modified notes:** connector line from old position to new, highlighted proposed note
- **Unchanged notes:** normal appearance

### Phrase List (optional sheet/panel)
- One row per `phrase` event
- Each row: label, beat range, `+/-/~` counts
- Per-phrase accept/reject toggle
- "Apply Selected" button commits only accepted phrase IDs

### Audition (stretch for MVP)
- **A (Original):** Play canonical state
- **B (Variation):** Rebuild MIDI regions in-memory with proposed notes, play those
- **Delta Solo:** Play only the changed notes
- Switch at beat boundary; pause-swap-resume acceptable for MVP

---

## Accept / Discard Flow

### Accept (all or partial)
```
POST /api/v1/variation/commit
{
  "project_id": "<project_id>",
  "base_state_id": "<current_state_id>",
  "variation_id": "<from meta event>",
  "accepted_phrase_ids": ["phrase-1", "phrase-2"]
}
```
Response includes `new_state_id` and `applied_phrase_ids`. Update the project's `base_state_id`. Push one undo group. Exit Variation Review Mode.

### Discard
```
POST /api/v1/variation/discard
{
  "project_id": "<project_id>",
  "variation_id": "<from meta event>"
}
```
Response: `{ "ok": true }`. Exit Variation Review Mode. No mutation.

---

## What to Remove

- Remove `execution_mode` from the `ComposeRequest` body sent to `POST /api/v1/compose/stream`. The backend ignores it. The request body is now just:

```json
{
  "prompt": "...",
  "project": { ... },
  "conversation_id": "...",
  "model": null
}
```

---

## Implementation Priority (MVP)

1. **Parse `state` event** and branch on `"composing"` vs `"editing"` vs `"reasoning"`
2. **Accumulate `meta` + `phrase` events** during COMPOSING into a local `Variation` model
3. **Show Variation Review banner** with intent, explanation, counts, Accept/Discard buttons
4. **Wire Accept** -> `POST /variation/commit` with all phrase IDs -> apply returned state -> exit review mode
5. **Wire Discard** -> `POST /variation/discard` -> exit review mode
6. **Piano roll overlay** for note changes (green/red/modified)
7. **Per-phrase accept/reject** (phrase list UI)
8. **A/B audition** (stretch)

---

## Reference Docs

- [protocol/muse-variation-spec.md](../protocol/muse-variation-spec.md) — Full UX + technical contract
- [protocol/variation_api_v1.md](../protocol/variation_api_v1.md) — Wire contract, endpoints, SSE events
- [protocol/TERMINOLOGY.md](../protocol/TERMINOLOGY.md) — Canonical vocabulary
- [api.md](../reference/api.md) — SSE event types reference
- [architecture.md](../reference/architecture.md) — Request flow and execution mode policy
