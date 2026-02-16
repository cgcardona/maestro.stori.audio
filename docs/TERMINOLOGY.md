# Muse / Variations — Canonical Terminology

> **This vocabulary is normative.** Use these exact words in code, UI, docs, and agent prompts.
> If any document, variable name, or SSE event uses different terminology, it is wrong and should be updated.

---

| Software Analogy | Stori Term | Definition | Used In |
|------------------|------------|------------|---------|
| Git | **Muse** | The creative intelligence / subsystem that proposes musical ideas | Branding, docs |
| Diff | **Variation** | A proposed musical interpretation expressed as a semantic, audible change set | API, models, events, tests, DB |
| Hunk | **Phrase** | An independently reviewable/applicable musical phrase (bars/region slice) | API, models, events, tests, DB |
| Line change | **NoteChange** | A single note-level diff: added, removed, or modified | Models, diff engine |
| Commit | **Accept Variation** | Apply selected phrases to canonical state; creates a single undo boundary | Commit endpoint, UI |
| Reject | **Discard Variation** | Close the proposal without mutating canonical state | Discard endpoint, UI |
| Revert | **Undo Variation** | Uses DAW undo/redo; engine-aware and audio-safe | Future |
| Branch | **Alternate Interpretation** | Parallel musical directions (future) | Future |
| Merge | **Blend Variations** | Combine harmony from A + rhythm from B (future) | Future |
| Version | **base_state_id** | Monotonic project version for optimistic concurrency control | All variation endpoints |

---

## Key Concepts

- **A diff is read. A Variation is heard.**
- **Canonical Time Unit:** All Muse data uses **beats** (not seconds). Seconds are a derived, playback-only representation.
- **Canonical State:** The DAW's real project state (undoable, playable, saved). Never mutated during proposal.
- **Proposed State:** An ephemeral, derived state computed by the backend to generate a Variation.

---

## State Machine States

| State | Description |
|-------|-------------|
| `CREATED` | Variation record exists; generation not started |
| `STREAMING` | Generation in progress; events flowing |
| `READY` | Generation complete; all phrases emitted; safe to commit |
| `COMMITTED` | Accepted phrases applied; canonical state advanced |
| `DISCARDED` | Variation canceled; no canonical mutation |
| `FAILED` | Terminal error; no canonical mutation |
| `EXPIRED` | TTL cleanup; no canonical mutation |

Terminal states: COMMITTED, DISCARDED, FAILED, EXPIRED.

---

## Event Types

| Event | Description |
|-------|-------------|
| `meta` | Variation summary, always sequence=1 |
| `phrase` | One musical phrase (atomic review unit), sequence=2..N |
| `done` | Terminal event (status: ready, failed, discarded), always last |
| `error` | Error during generation, followed by done(status=failed) |
| `heartbeat` | Keep-alive for long-running streams (optional) |

---

## Banned Terminology

Do **not** use these words in Stori code or docs:

| Wrong | Correct |
|-------|---------|
| diff | Variation |
| hunk | Phrase |
| commit (as noun) | Accept Variation |
| reject | Discard Variation |
| seconds / milliseconds (in Muse data) | beats |
| tool_calls (in variation response) | updated_regions or phrases |
