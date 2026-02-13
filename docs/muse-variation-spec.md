# Muse / Variation Specification — End-to-End UX + Technical Contract (Stori)

> **Status:** Implementation Specification (v1)  
> **Date:** February 2026  
> **Target:** Stori DAW (Swift/SwiftUI) + Composer/Intent Engine (Python)  
> **Goal:** Ship a *demo-grade* implementation inside Stori that proves the “Cursor of DAWs” paradigm: **reviewable, audible, non-destructive AI changes**.

> **Canonical Time Unit:** All Muse and Variation data structures use **beats** as the canonical time unit. Seconds are a derived, playback-only representation. Muse reasons musically, not in wall-clock time.

---

## What Is Muse?

**Muse** is Stori’s change-proposal system for music.

Just as Git is a system for proposing, reviewing, and applying changes to source code, Muse is a system for proposing, reviewing, and applying changes to musical material.

Muse does not edit music directly.

Muse computes **Variations** — structured, reviewable descriptions of how one musical state differs from another — and presents them for human evaluation.

---

### Muse’s Role in the System

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

## 1) When Variations Appear (UX Policy)

Variations must feel *musical* and *trust-building*, not interruptive.

### 1.1 Default Rule
Show a **Variation Review UI** whenever Muse proposes a change that **modifies or removes** existing musical material that the user can reasonably consider “already there.”

**Examples (show Variation UI):**
- “Make that minor” (transforms pitches)
- “Quantize tighter” (timing modifications)
- “Add swing” (timing modifications)
- “Simplify the melody” (removals/modifications)
- “Change the bassline to be more syncopated” (re-writes notes)
- “Make the chorus hit harder” (multiple edits across regions)

### 1.2 Additive vs Transformative
For actions that are **purely additive** and do not overwrite existing user-authored material, you have two options:

**Option A (recommended for MVP):**  
Auto-apply additive changes *during project creation*, but show a lightweight **Generation Summary** (not Variation UI).

**Option B:**  
Always represent new content as a Variation even when additive. This is powerful but can slow down first-run UX.

**MVP Recommendation:**  
- **Project creation**: auto-apply additive ops; avoid forcing Variation review for every track creation step.  
- **After creation**: any transformation/edit of existing notes uses Variation review.

### 1.3 “Create a new song in the style of …” (Multi-step Tool Flow)
During an agent “create song” flow (create tracks → rename → instruments → regions → notes → FX), the Variation UI should **not pop up repeatedly**.

**MVP Behavior (Strongly Recommended):**
1. Treat the entire build as **Generation Mode** (temporary staging).  
2. Show a single **Build Progress UI** (already exists / easy to extend).  
3. When generation completes, show a **Generation Summary** with:
   - Track list created
   - Regions added
   - Notes generated
   - FX inserted
   - A “Review Changes” button that optionally opens a **Variation-like review** for the whole build (stretch).
4. Once the user hits **“Keep This”** (or simply interacts), the project becomes canonical.

**When to show real Variations in this flow:**
- Only if the agent tries to **rewrite** something already generated and “locked in” (e.g., user already auditioned/edited), or if it performs destructive actions (delete/replace regions).
- If the user interrupts mid-generation with a transform request (“make the piano darker”), handle that transform as a **Variation** against the current canonical.

### 1.4 User Trust Overrides
Always show Variation UI when:
- The change is **destructive** (deletes/overwrites notes/regions)
- The target material is **user-edited** (has `userTouched=true`) or “pinned/locked”
- The change is **large-scope** (multi-track rewrite)
- The model’s confidence is low OR the engine produced a best-effort fallback

### 1.5 Quick Setting
Add a user preference (later):
- **Muse Review Mode:** `Always` | `Smart (default)` | `Never (power users)`

---

## 2) System Model

### 2.1 Canonical vs Proposed State
- **Canonical State**: the DAW’s real project state (undoable, playable, saved).
- **Proposed State**: an ephemeral, derived state computed by backend to propose a Variation.

**Important:** The backend does **not** mutate canonical state during proposal.

### 2.2 Variation Lifecycle

1. **Propose**: Muse generates a Variation from intent.
2. **Stream**: Phrases (hunks) stream to the frontend as soon as they’re computed.
3. **Review**: FE enters Variation Review Mode (overlay + A/B audition).
4. **Accept**: FE sends accepted phrase IDs; BE applies them transactionally.
5. **Discard**: FE discards; no mutation.

---

## 3) API Contract (Backend ⇄ Frontend)

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

**SSE Events**
- `meta` — overall summary + UX copy + counts
- `phrase` — one musical phrase at a time
- `progress` — optional
- `done` — end of stream
- `error` — terminal

**Example: `meta`**
```json
{
  "variation_id": "uuid",
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
  "start_beat": 4.0,
  "end_beat": 8.0,
  "label": "Bars 5–8",
  "tags": ["harmonyChange","scaleChange"],
  "explanation": "Converted major 3rds to minor 3rds",
  "note_changes": [
    {
      "note_id": "uuid",
      "change_type": "modified",
      "before": { "pitch": 64, "start_beat": 4.0, "duration_beats": 0.5, "velocity": 90 },
      "after":  { "pitch": 63, "start_beat": 4.0, "duration_beats": 0.5, "velocity": 90 }
    }
  ],
  "controller_changes": []
}
```

**Example: `done`**
```json
{ "variation_id": "uuid" }
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
    { "region_id": "uuid", "track_id": "uuid", "midi": "..." }
  ]
}
```

#### (D) Discard Variation
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
  "label": "Bars 1–4",
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
- `added` → `before` may be null
- `removed` → `after` may be null
- `modified` → both present
- All positions in **beats** (not seconds)

---

## 5) Backend Implementation Guidance (Minimal Churn)

### 5.1 Add Execution Mode Flag
Keep existing behavior intact.

- `mode="apply"` → current mutation path
- `mode="variation"` → run transforms on a **temporary clone** and diff vs base

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
- then `hunk` events
- progress optional

Streaming is what makes the UI feel “alive” and Cursor-like.

---

## 6) Frontend UX Spec (Variation Review Mode)

### 6.1 Entry
Variation Review Mode enters when:
- A Variation stream begins (`meta` received), or
- FE receives a non-streamed full Variation

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
- If switching causes glitches, pause → swap → resume at same transport time (acceptable for MVP).

### 6.5 Partial Acceptance
In the “Review Phrases” sheet/list:
- Each phrase row shows summary `+ / - / ~`
- Accept / reject per phrase
- “Apply Selected” commits accepted phrase IDs

### 6.6 Exit
- Accept → applies to project, pushes one undo group, exits review mode
- Discard → exits review mode without changes

---

## 7) Variation vs “Generation Summary” (Two UX Layers)

You will have two related but distinct UIs:

### 7.1 Generation Summary (Project Creation)
Shown at the end of a multi-tool “create song” flow:
- What was created
- Quick audition buttons
- Optional “Make a Variation…” CTA (e.g., “Create a darker variation”)

### 7.2 Variation Review (Transformations)
Shown when something that exists is being transformed:
- Visual diff
- A/B + Delta Solo
- Accept / Discard / Partial accept

> This prevents the UX from being spammy during initial build, while still delivering the “Cursor of DAWs” magic where it matters most.

---

## 8) Failure Modes & UX Rules

### 8.1 If streaming fails mid-way
- Keep received hunks
- Show a “Retry stream” button
- Allow Discard

### 8.2 If commit fails due to `base_state_id` mismatch
- Offer: “Rebase Variation” (future)
- MVP: show message: “Project changed while reviewing; regenerate variation.”

### 8.3 If the user edits while reviewing
MVP rule:
- Block destructive edits to affected regions, or
- Allow edits but invalidate Variation (recommended: invalidate with clear toast)

---

## 9) MVP Cut (What to Ship First)

1. **Variation propose + stream hunks (SSE)**
2. **Piano roll overlay rendering**
3. **A/B audition (pause/swap/resume acceptable)**
4. **Accept all / Discard**
5. **Per-phrase accept (optional but high value)**

Score view diff + controller diffs can come after the demo.

---

## 10) Demo Script (Suggested)

1. Generate a major piano riff.
2. Ask: “Make that minor and more mysterious.”
3. Variation Review appears:
   - green/red note overlay
   - A/B toggle + Delta Solo
4. Accept only bars 5–8, discard rest.
5. Undo to prove it’s safe.

---

## 11) Appendix: Implementation Checklist

### Backend (✅ Complete & Deployed)
- [x] `POST /variation/propose` returns `variation_id` + `stream_url`
- [x] `POST /variation/commit` accepts `accepted_phrase_ids` 
- [x] `POST /variation/discard` returns `{"ok": true}`
- [x] SSE stream emits `meta`, `phrase*`, `done` (via `/compose/stream`)
- [x] Phrase grouping by bars (4 bars per phrase default)
- [x] Commit applies accepted phrases only, returns `new_state_id`
- [x] No mutation in variation mode
- [x] All data uses beats as canonical unit (not seconds/milliseconds)
- [x] Optimistic concurrency via `base_state_id` checks
- [x] Zero Git terminology - pristine musical language
- [x] `VariationService` computes variations (not "diffs")
- [x] `Phrase` model for independently reviewable changes
- [x] `NoteChange` model for note transformations
- [x] Beat-based fields: `start_beat`, `duration_beats`, `beat_range`

**Deployment Status:**
- ✅ Deployed to `stage.stori.audio`
- ✅ All endpoints tested and working
- ✅ Comprehensive test script: `test_muse_comprehensive.sh`

### Frontend (Not Yet Started)
- [ ] Variation Review Mode overlay chrome
- [ ] Render note states (added/removed/modified)
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
