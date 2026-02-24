# Muse / Variation Specification — End-to-End UX + Technical Contract (Stori)

> **Status:** Implementation Specification (v1)  
> **Date:** February 2026  
> **Target:** Stori DAW (Swift/SwiftUI) + Maestro/Intent Engine (Python)  
> **Goal:** Ship a *demo-grade* implementation inside Stori that proves the "Cursor of DAWs" paradigm: **reviewable, audible, non-destructive AI changes**.

> **Canonical Time Unit:** All Muse and Variation data structures use **beats** as the canonical time unit. Seconds are a derived, playback-only representation. Muse reasons musically, not in wall-clock time.

> **Canonical Backend References:**
> For backend wire contract, state machine, and terminology, these docs are authoritative:
> - [variation-api.md](variation-api.md) — Wire contract, endpoints, SSE events, error codes
> - [terminology.md](terminology.md) — Canonical vocabulary (normative)

---

## What Is Muse?

**Muse** is Stori's change-proposal system for music.

Just as Git is a system for proposing, reviewing, and applying changes to source code, Muse is a system for proposing, reviewing, and applying changes to musical material.

Muse does not edit music directly.

Muse computes **Variations** — structured, reviewable descriptions of how one musical state differs from another — and presents them for human evaluation.

---

### Muse's Role in the System

Muse sits between **intent** and **mutation**.


## 0) Canonical Terms (Do Not Drift)

This vocabulary is **normative**. Use these exact words in code, UI, docs, and agent prompts.

| Software analogy | Stori term | Definition |
|---|---|---|
| Git | **Muse** | The creative intelligence / system that proposes musical ideas |
| Diff | **Variation** | A proposed musical interpretation expressed as a semantic, audible change set |
| Hunk | **Phrase** | An independently reviewable/applicable musical phrase (bars/region slice) |
| Commit | **Accept Variation** | Apply selected phrases to canonical state; creates a single undo boundary |
| Reject | **Discard Variation** | Close the proposal without mutating canonical state |
| Revert | **Undo Variation** | Uses DAW undo/redo; engine-aware and audio-safe |
| Branch (future) | Alternate Interpretation | Parallel musical directions |
| Merge (future) | Blend Variations | Combine harmony from A + rhythm from B + etc. |

> **Key concept:** A diff is read. A Variation is **heard**.  
> **Time unit:** Muse reasons in **beats**, not seconds. Time is a playback concern.

---

## 1) When Variations Appear (Execution Mode Policy)

The backend enforces execution mode based on intent classification. The frontend does not choose the mode — it reacts to the `state` SSE event emitted at the start of every compose stream.

### 1.1 Core Rule — COMPOSING Always Produces a Variation

| Intent state | `execution_mode` | Behavior |
|---|---|---|
| **COMPOSING** | `variation` (forced by backend) | All tool calls produce a Variation for human review |
| **EDITING** | `apply` (forced by backend) | Structural ops (add track, set tempo, mute, etc.) apply immediately |
| **REASONING** | n/a | Chat only, no tools |

**Every COMPOSING request produces a Variation** — including purely additive ones (first-time MIDI generation, creating a new song from scratch). This mirrors the "Cursor of DAWs" paradigm: AI-generated musical content always requires human approval before becoming canonical state.

**Examples (Variation Review UI — COMPOSING):**
- "Create a new song in the style of Phish" — additive, but COMPOSING -> Variation
- "Make a chill lo-fi beat at 85 BPM" — additive, COMPOSING -> Variation
- "Make that minor" (transforms pitches) — COMPOSING -> Variation
- "Simplify the melody" (removals/modifications) — COMPOSING -> Variation
- "Change the bassline to be more syncopated" (re-writes notes) — COMPOSING -> Variation

**Examples (direct apply, no Variation — EDITING):**
- "Add a drum track" — structural, EDITING -> apply
- "Set the tempo to 120 BPM" — structural, EDITING -> apply
- "Mute the bass" — structural, EDITING -> apply

### 1.2 "Create a new song in the style of ..." (Multi-step Tool Flow)

When the user asks to create a song from scratch, the backend classifies this as COMPOSING and the entire plan (tracks + regions + notes + FX) is proposed as a **single Variation** for review.

**Behavior:**
1. The planner generates a full plan (create tracks -> add regions -> generate MIDI -> add FX).
2. The executor simulates the plan without mutation and computes a Variation with Phrases.
3. The SSE stream emits `meta` -> `phrase*` -> `done` events.
4. The frontend enters **Variation Review Mode** showing the proposed changes.
5. The user reviews, auditions (A/B), and accepts or discards.

This ensures the user always has agency over AI-generated content, even during initial creation. The UX is a single review step at the end of generation — not repeated pop-ups per tool call.

### 1.3 User Trust Overrides
Always show Variation UI when:
- The change is **destructive** (deletes/overwrites notes/regions)
- The target material is **user-edited** (has `userTouched=true`) or "pinned/locked"
- The change is **large-scope** (multi-track rewrite)
- The model's confidence is low OR the engine produced a best-effort fallback

### 1.4 Quick Setting (future)
Add a user preference (later):
- **Muse Review Mode:** `Always` | `Smart (default)` | `Never (power users)`

When implemented, this preference will be stored server-side and consulted in `orchestrate()`. Even in `Never` mode, destructive changes should warn.

---

## 2) System Model

### 2.1 Canonical vs Proposed State
- **Canonical State**: the DAW's real project state (undoable, playable, saved).
- **Proposed State**: an ephemeral, derived state computed by backend to propose a Variation.

**Important:** The backend does **not** mutate canonical state during proposal.

### 2.2 Variation Lifecycle

1. **Propose**: Muse generates a Variation from intent.
2. **Stream**: Phrases (hunks) stream to the frontend as soon as they're computed.
3. **Review**: FE enters Variation Review Mode (overlay + A/B audition).
4. **Accept**: FE sends accepted phrase IDs; BE applies them transactionally.
5. **Discard**: FE discards; no mutation.

---

## 3) API Contract (Backend <-> Frontend)

This spec assumes HTTP + **SSE** (server-sent events) for streaming. WebSockets also acceptable; SSE is simpler for v1.

### 3.1 Identifiers & Concurrency
All Variation operations must carry:
- `project_id`
- `base_state_id` (monotonic project version, e.g., UUID or int)
- `variation_id`
- Optional `request_id` for idempotency

Backend must reject commits if `base_state_id` mismatches (optimistic concurrency) unless FE explicitly requests rebase.

### 3.2 Endpoints

#### (A) Propose Variation
`POST /variation/propose`

**Request**
```json
{
  "project_id": "uuid",
  "base_state_id": "uuid-or-int",
  "intent": "make that minor",
  "scope": {
    "track_ids": ["uuid"],
    "region_ids": ["uuid"],
    "beat_range": [4.0, 8.0]
  },
  "options": {
    "phrase_grouping": "bars", 
    "bar_size": 4,
    "stream": true
  },
  "request_id": "uuid"
}
```

**Immediate Response (fast)**
```json
{
  "variation_id": "uuid",
  "project_id": "uuid",
  "base_state_id": "uuid-or-int",
  "intent": "make that minor",
  "ai_explanation": null,
  "stream_url": "/variation/stream?variation_id=uuid"
}
```

#### (B) Stream Variation (phrases/hunks)
`GET /variation/stream?variation_id=...` (SSE)

All events are wrapped in a transport-agnostic `EventEnvelope`:
```json
{
  "type": "meta|phrase|done|error|heartbeat",
  "sequence": 1,
  "variation_id": "uuid",
  "project_id": "uuid",
  "base_state_id": "uuid-or-int",
  "timestamp_ms": 1700000000000,
  "payload": { }
}
```
`sequence` is strictly increasing per variation (meta=1, then phrases, then done last).
The event-specific data lives in `payload`; outer fields provide routing and ordering context.

**SSE Events**
- `meta` — overall summary + UX copy + counts
- `phrase` — one musical phrase at a time
- `done` — end of stream
- `error` — terminal
- `heartbeat` — keepalive (no payload significance)

> `progress` events are not yet implemented.

**Example: `meta`** (this is the `payload` field inside the `EventEnvelope`; `variation_id`, `project_id`, `base_state_id`, and `sequence` are in the outer envelope)
```json
{
  "intent": "make that minor",
  "ai_explanation": "Lowered scale degrees 3 and 7",
  "affected_tracks": ["uuid"],
  "affected_regions": ["uuid"],
  "note_counts": { "added": 12, "removed": 4, "modified": 8 }
}
```

**Example: `phrase`**
```json
{
  "phrase_id": "uuid",
  "track_id": "uuid",
  "region_id": "uuid",
  "start_beat": 16.0,
  "end_beat": 32.0,
  "label": "Bars 5-8",
  "tags": ["harmonyChange","scaleChange"],
  "explanation": "Converted major 3rds to minor 3rds",
  "note_changes": [
    {
      "note_id": "uuid",
      "change_type": "modified",
      "before": { "pitch": 64, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 90 },
      "after":  { "pitch": 63, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 90 }
    }
  ],
  "controller_changes": [
    { "kind": "cc", "cc": 64, "beat": 0.0, "value": 127 },
    { "kind": "pitch_bend", "beat": 1.5, "value": 4096 },
    { "kind": "aftertouch", "beat": 2.0, "value": 80 }
  ]
}
```

> **Beat semantics:** `phrase.start_beat` / `phrase.end_beat` are **absolute project positions**. Note `start_beat` values inside `note_changes` are **region-relative** (offset from the region's start beat). This matches how DAWs universally store MIDI data within regions.

**Example: `done`**

The `variation_id` is carried in the outer `EventEnvelope` wrapper (not repeated in the payload).
```json
{ "status": "ready", "phrase_count": 3 }
```

#### (C) Commit (Accept Variation)
`POST /variation/commit`

**Request**
```json
{
  "project_id": "uuid",
  "base_state_id": "uuid-or-int",
  "variation_id": "uuid",
  "accepted_phrase_ids": ["uuid","uuid"],
  "request_id": "uuid"
}
```

**Response**
```json
{
  "project_id": "uuid",
  "new_state_id": "uuid-or-int",
  "applied_phrase_ids": ["uuid","uuid"],
  "undo_label": "Accept Variation: make that minor",
  "updated_regions": [
    {
      "region_id": "uuid",
      "track_id": "uuid",
      "notes": [
        { "pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100, "channel": 0 }
      ],
      "cc_events": [
        { "cc": 64, "beat": 0.0, "value": 127 }
      ],
      "pitch_bends": [],
      "aftertouch": []
    }
  ]
}
```

#### (D) Poll Variation Status
`GET /variation/{variation_id}`

Returns the current status and accumulated phrases for a variation. Useful for
reconnect flows and clients that can't maintain a long-lived SSE connection.

**Response**
```json
{
  "variation_id": "uuid",
  "status": "ready",
  "intent": "make that minor",
  "phrases": []
}
```

#### (E) Discard Variation
`POST /variation/discard`

```json
{
  "project_id": "uuid",
  "variation_id": "uuid",
  "request_id": "uuid"
}
```

Returns `{ "ok": true }`.

---

## 4) Variation Data Shapes (Canonical JSON)

### 4.1 Variation (meta)
```json
{
  "variation_id": "uuid",
  "intent": "string",
  "ai_explanation": "string|null",
  "affected_tracks": ["uuid"],
  "affected_regions": ["uuid"],
  "beat_range": [0.0, 16.0],
  "note_counts": { "added": 0, "removed": 0, "modified": 0 }
}
```

### 4.2 Phrase
```json
{
  "phrase_id": "uuid",
  "track_id": "uuid",
  "region_id": "uuid",
  "start_beat": 0.0,
  "end_beat": 4.0,
  "label": "Bars 1-4",
  "tags": [],
  "explanation": "string|null",
  "note_changes": [],
  "controller_changes": []
}
```

### 4.3 NoteChange
```json
{
  "note_id": "uuid",
  "change_type": "added|removed|modified",
  "before": { "pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 90 },
  "after":  { "pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 90 }
}
```

Rules:
- `added` -> `before` must be null (enforced by backend)
- `removed` -> `after` must be null (enforced by backend)
- `modified` -> both `before` and `after` must be present
- All positions in **beats** (not seconds)
- `start_beat` within `before`/`after` is **region-relative** (offset from the region's start)

### 4.4 Controller Changes (Expressive MIDI)

Phrases carry `controller_changes` — expressive MIDI data beyond notes. The
pipeline supports the **complete** set of musically relevant MIDI messages:

| `kind` | Fields | MIDI byte | Coverage |
|--------|--------|-----------|----------|
| `cc` | `cc`, `beat`, `value` | Control Change (0xBn) | All 128 CC numbers: sustain (64), expression (11), modulation (1), volume (7), pan (10), filter cutoff (74), resonance (71), reverb send (91), chorus send (93), attack (73), release (72), soft pedal (67), sostenuto (66), legato (68), breath (2), etc. |
| `pitch_bend` | `beat`, `value` | Pitch Bend (0xEn) | 14-bit signed (−8192 to 8191) |
| `aftertouch` | `beat`, `value` | Channel Pressure (0xDn) | No `pitch` field → channel-wide pressure |
| `aftertouch` | `beat`, `value`, `pitch` | Poly Key Pressure (0xAn) | `pitch` present → per-note pressure |

Program Change is handled at track level (`stori_set_midi_program`).
Track-level automation curves (volume, pan, FX params) are handled by
`stori_add_automation`.

After commit, the full expressive state is materialized in `updated_regions`
as three separate arrays: `cc_events`, `pitch_bends`, `aftertouch`.

---

## 5) Backend Implementation Guidance

### 5.1 Execution Mode Policy (Backend-Owned)

The backend determines `execution_mode` based on intent classification. The frontend's `execution_mode` field is deprecated and ignored.

- **COMPOSING** -> `execution_mode="variation"` -> Variation proposal (no mutation)
- **EDITING** -> `execution_mode="apply"` -> Immediate tool call execution
- **REASONING** -> no tools

This is enforced in `orchestrate()` (`app/core/maestro_handlers.py`). The frontend knows which mode is active from the `state` SSE event (`"composing"` / `"editing"` / `"reasoning"`) emitted at the start of every stream.

### 5.2 Proposed State Construction
Avoid copying whole projects:
- Identify affected regions/tracks
- Clone only those regions (notes + essential metadata)
- Apply existing transform functions onto the clones

### 5.3 Diffing / Matching Notes
Start simple:
- Match by `(pitch, start)` proximity with a tolerance (e.g., 1/16 note)
- If ambiguous, prefer same pitch then closest start-time
- Emit `modified` rather than `remove+add` when a single note clearly moved

### 5.4 Phrase Grouping (MVP)
- Group changes by **bar windows** (e.g., 4 bars per phrase)
- Or by region boundaries if the region already stores bar markers

### 5.5 Streaming
Compute hunks incrementally and stream as soon as available:
- `meta` ASAP
- then `phrase` events
- progress optional

Streaming is what makes the UI feel alive and Cursor-like.

---

## 6) Frontend UX Spec (Variation Review Mode)

### 6.1 Entry
Variation Review Mode enters when the compose stream emits a `state` event with `state: "composing"`, followed by `meta` and `phrase` events. The frontend must:
1. Detect `state: "composing"` -> prepare for Variation Review Mode
2. Receive `meta` event -> show banner with intent, explanation, counts
3. Receive `phrase` events -> accumulate phrases for review
4. Receive `done` event -> enable Accept/Discard controls

For `state: "editing"`, the frontend applies `toolCall` events directly. The backend also emits `plan` and `planStepUpdate` events to render a step-by-step checklist. See [api.md](../reference/api.md) for the full event reference.

### 6.2 Chrome (always visible while reviewing)
Banner containing:
- Intent text
- AI explanation (optional)
- Counts: +added / -removed / ~modified
- Controls: **A/B**, **Delta Solo**, **Accept**, **Discard**, **Review Phrases**

### 6.3 Visual Language (Piano Roll + Score)
- Added: green
- Removed: red ghost
- Modified: connector + highlighted proposed note
- Unchanged: normal

### 6.4 Audition
Required:
- Play Original (A)
- Play Variation (B)
- Delta Solo (changes only)
- Loop selected phrase

MVP audio strategy:
- Rebuild MIDI regions in-memory for audition modes and switch at beat boundary.
- If switching causes glitches, pause -> swap -> resume at same transport time (acceptable for MVP).

### 6.5 Partial Acceptance
In the "Review Phrases" sheet/list:
- Each phrase row shows summary `+ / - / ~`
- Accept / reject per phrase
- "Apply Selected" commits accepted phrase IDs

### 6.6 Exit
- Accept -> applies to project, pushes one undo group, exits review mode
- Discard -> exits review mode without changes

---

## 7) Failure Modes & UX Rules

### 7.1 If streaming fails mid-way
- Keep received hunks
- Show a "Retry stream" button
- Allow Discard

### 7.2 If commit fails due to `base_state_id` mismatch
- Offer: "Rebase Variation" (future)
- MVP: show message: "Project changed while reviewing; regenerate variation."

### 7.3 If the user edits while reviewing
MVP rule:
- Block destructive edits to affected regions, or
- Allow edits but invalidate Variation (recommended: invalidate with clear toast)

---

## 8) MVP Cut (What to Ship First)

1. **Variation propose + stream hunks (SSE)**
2. **Piano roll overlay rendering**
3. **A/B audition (pause/swap/resume acceptable)**
4. **Accept all / Discard**
5. **Per-phrase accept (optional but high value)**

Score view diff + controller diffs can come after the demo.

---

## 9) Demo Script (Suggested)

1. Generate a major piano riff.
2. Ask: "Make that minor and more mysterious."
3. Variation Review appears:
   - green/red note overlay
   - A/B toggle + Delta Solo
4. Accept only bars 5-8, discard rest.
5. Undo to prove it's safe.

---

## 10) Appendix: Implementation Checklist

### Backend

**Core (Implemented & Tested):**
- [x] `POST /variation/propose` returns `variation_id` + `stream_url`
- [x] `POST /variation/commit` accepts `accepted_phrase_ids`
- [x] `POST /variation/discard` returns `{"ok": true}`
- [x] SSE stream emits `meta`, `phrase*`, `done` (via `/maestro/stream`)
- [x] Phrase grouping by bars (4 bars per phrase default)
- [x] Commit applies accepted phrases only, returns `new_state_id`
- [x] No mutation in variation mode
- [x] All data uses beats as canonical unit (not seconds/milliseconds)
- [x] Optimistic concurrency via `base_state_id` checks
- [x] Zero Git terminology — pristine musical language
- [x] `VariationService` computes variations (not "diffs")
- [x] `Phrase` model for independently reviewable changes
- [x] `NoteChange` model for note transformations
- [x] Beat-based fields: `start_beat`, `duration_beats`, `beat_range`

**v1 Infrastructure (State Machine + Envelope + Store):**
- [x] `VariationStatus` enum: CREATED -> STREAMING -> READY -> COMMITTED/DISCARDED/FAILED/EXPIRED
- [x] `assert_transition()` enforces valid state machine transitions
- [x] `EventEnvelope` with type, sequence, variation_id, project_id, base_state_id, payload
- [x] `SequenceCounter` for per-variation monotonic sequence numbers
- [x] `VariationStore` (in-memory) for variation records + phrase storage
- [x] `SSEBroadcaster` with publish, subscribe, replay, late-join support
- [x] Builder helpers: `build_meta_envelope`, `build_phrase_envelope`, `build_done_envelope`, `build_error_envelope`

**v1 Supercharge (Complete):**
- [x] Wired infrastructure into endpoints (propose/commit/discard)
- [x] `GET /variation/stream` — real SSE with envelopes, replay, heartbeat
- [x] `GET /variation/{variation_id}` — status polling + reconnect
- [x] Note removals implemented in commit engine
- [x] Background generation task (async propose via `asyncio.create_task`)
- [x] Discard cancels in-flight generation
- [x] `stream_router.py` — single publish entry point (WS-ready)
- [x] Commit loads variation from store

**Execution Mode Policy (New):**
- [x] Backend forces `execution_mode="variation"` for all COMPOSING intents
- [x] Backend forces `execution_mode="apply"` for all EDITING intents
- [x] Frontend reacts to `state` SSE event; backend determines mode from intent

### Frontend (Not Yet Started)
- [ ] Detect `state: "composing"` SSE event and enter Variation Review Mode
- [ ] Detect `state: "editing"` SSE event and apply tool calls directly (existing behavior)
- [ ] Parse and accumulate `meta`, `phrase`, `done` events during COMPOSING
- [ ] Variation Review Mode overlay chrome (banner, counts, intent)
- [ ] Render note states (added/removed/modified) in piano roll
- [ ] Phrase list UI with accept/reject per phrase
- [ ] A/B + Delta Solo audition
- [ ] Commit/discard flows with state-id checks
- [ ] Convert beats to audio time for playback only

---

## North-Star Reminder

> **Muse proposes Variations organized as Phrases.**  
> **Humans choose the music.**  
> **Everything is measured in beats.**

If this sticks, it becomes a new creative primitive for the entire industry.
