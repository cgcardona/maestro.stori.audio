# Stori — Muse Persistent History Bridge

> **Status:** Historical Conceptual Specification (Phase 2) — most concepts defined here have been implemented.
> **Superseded by:** [`muse-vcs.md`](muse-vcs.md) — the canonical Muse VCS implementation reference.
> **Date:** February 2026
> **Companion to:** [`maestro-muse-evolution.md`](maestro-muse-evolution.md) (Phase 1 — Current State Analysis)
> **Audience:** Distributed systems engineers, music technology researchers, AI infrastructure teams, open-source contributors
> **Scope:** Conceptual contracts and authority model only — no implementation proposals, no code, no storage schemas
>
> **Implementation note (Feb 2026):** Since this spec was written, the persistent commit lineage (commit objects
> in Postgres with parent references), drift detection (working tree projection), checkout (state reconstruction
> from any commit), three-way merge with conflict detection, and log graph serialization have all been
> implemented. See `muse-vcs.md` for the current implementation reference.

---

## Purpose

The Phase 1 document mapped the system as it exists. This document defines the **minimal conceptual bridge** that allows Muse to evolve into a persistent musical history engine while preserving Maestro's runtime model.

It answers one question:

> What is the smallest set of conceptual changes required so that Muse could become authoritative over musical history without breaking the existing Stori runtime?

Nothing in this document requires changing current behavior. It defines the **seams** along which the system can evolve, the **contracts** that would need to exist at those seams, and the **invariants** that must survive the transition.

---

## Table of Contents

1. [Muse as Repository — Conceptual Model](#1-muse-as-repository--conceptual-model)
2. [Commit Object Specification (Conceptual Only)](#2-commit-object-specification)
3. [Authority Transition Model](#3-authority-transition-model)
4. [Working Tree vs Repository Projection](#4-working-tree-vs-repository-projection)
5. [Grammar Unification Boundary](#5-grammar-unification-boundary)
6. [Temporal Authority Model](#6-temporal-authority-model)
7. [Backwards Compatibility Guarantees](#7-backwards-compatibility-guarantees)
8. [Minimal New Abstractions](#8-minimal-new-abstractions)
9. [External Contributor Mental Model](#9-external-contributor-mental-model)

---

## 1. Muse as Repository — Conceptual Model

### The Analogy, Made Precise

Muse is a **repository** for musical material. Not metaphorically — structurally. It holds the same conceptual primitives as any content-versioning system:

| Repository concept | Muse equivalent | Current status |
|--------------------|-----------------|----------------|
| **Repository** | A project's complete musical history | Does not exist. No persistent store. |
| **Object store** | Immutable snapshots of musical content (notes, CC, pitch bends, aftertouch per region) | Does not exist. `StateStore._region_notes` is mutable and ephemeral. |
| **Commit** | A recorded transition from one project state to another, with metadata and parent reference | Does not exist. `Accept Variation` mutates in-memory state and forgets. |
| **HEAD** | The most recent committed state for a project | `StateStore._version` — but ephemeral and resets on restart. |
| **Working tree** | The DAW's current live state, which may diverge from HEAD | The DAW project state, received via `sync_from_client()`. |
| **Index / staging area** | Proposed changes awaiting human review | `VariationStore` — holds active variation proposals. Already exists, already ephemeral. |
| **Diff / patch** | A structured description of how one state differs from another | `Variation` with `Phrase` and `NoteChange` objects. Already exists. |
| **Branch** | A named pointer to a commit, enabling parallel evolution | Does not exist. Reserved as "Alternate Interpretation" in terminology. |
| **Tag** | An immutable label on a specific commit | Does not exist. |

### What a Muse Repository Contains

A Muse repository, if it existed, would contain:

**Content objects** — immutable snapshots of musical regions. Each object represents the complete state of one region at one point in time: its notes, CC events, pitch bends, aftertouch, and position metadata. Content objects are identified by a deterministic hash of their contents.

**Commit objects** — immutable records of state transitions. Each commit points to its parent (or parents, if merge is supported), contains the set of content objects that represent the project at that commit, and carries metadata: intent, explanation, timestamp, accepted phrase IDs.

**A lineage graph** — a directed acyclic graph of commits. The root is the project's initial state (which may be empty). Each subsequent commit adds or modifies content objects. The graph is append-only: commits are never modified or deleted.

**A HEAD pointer** — the most recent commit for the project. Advances on each `Accept Variation`. This is what `base_state_id` would become if persistent.

**A working tree projection** — a read-only function that, given a commit, produces the complete project state at that point. Today this is the DAW's job. In the future, Muse could reconstruct project state from any commit without the DAW.

### What a Muse Repository Does Not Contain

- Audio data. Muse versions MIDI-level musical material, not rendered audio.
- Plugin state. Effect chains, synth presets, and routing are DAW concerns.
- View state. Zoom, panel layout, scroll position are UI concerns.
- Transport state. Playhead position, play/stop, loop markers are ephemeral.

### Relationship to Current Code

| Future concept | Current artifact | Gap |
|----------------|-----------------|-----|
| Repository | `_stores: dict[str, StateStore]` (module-level dict) | Not persistent, keyed by conversation_id not project_id |
| Content object | `StateStore._region_notes[region_id]` | Mutable, not hashed, not snapshotted |
| Commit | `Accept Variation` endpoint response | No persistent record created |
| HEAD | `StateStore._version` | Ephemeral integer, resets on restart |
| Working tree | DAW project state (via `sync_from_client`) | Already exists, already the right abstraction |
| Staging area | `VariationStore` (active proposals) | Already exists, already ephemeral (correct for staging) |
| Diff | `Variation` + `Phrase` + `NoteChange` | Already exists, already well-structured |

The core observation: **Muse already has the diff/staging layer**. What it lacks is the commit/repository layer beneath it. The bridge is a persistent layer below the existing variation protocol, not a replacement of it.

---

## 2. Commit Object Specification

### Mapping Current Concepts to Commit Semantics

A Muse commit is derived from what already happens during `Accept Variation`. Today, the commit endpoint:

1. Receives `accepted_phrase_ids` and `base_state_id`
2. Validates that `base_state_id` matches `StateStore._version`
3. Applies note additions/removals from accepted phrases to `StateStore`
4. Returns `updated_regions` (full post-commit state of affected regions)
5. Increments `StateStore._version`
6. Discards all variation data

A persistent commit would **record** steps 1–5 instead of discarding them.

### Commit Object — Conceptual Shape

A commit captures a transition. It contains:

| Field | Source in current system | Exists today? |
|-------|------------------------|---------------|
| **commit_id** | New — derived from content hash or persistent sequence | No |
| **project_id** | `StateStore.project_id` / DAW `project.id` | Yes |
| **parent_id** | Previous commit's ID (would replace `base_state_id`) | No — `base_state_id` is ephemeral |
| **variation_id** | `VariationRecord.variation_id` | Yes — but ephemeral |
| **intent** | `VariationRecord.intent` | Yes |
| **explanation** | `VariationRecord.ai_explanation` | Yes |
| **accepted_phrase_ids** | `CommitVariationRequest.accepted_phrase_ids` | Yes — but not stored after commit |
| **phrase_summaries** | Derived from each `Phrase`: `(phrase_id, track_id, region_id, start_beat, end_beat, label, tags, note_counts)` | Yes — all fields exist in `Phrase` model |
| **affected_regions** | `Variation.affected_regions` | Yes |
| **affected_tracks** | `Variation.affected_tracks` | Yes |
| **region_snapshots** | Post-commit region state — the `updated_regions` payload | Yes — computed and returned but not stored |
| **timestamp** | `VariationRecord.updated_at` | Yes |
| **author** | Would need to be `user_id` or `"muse"` | Partially — `user_id` exists in auth context but not in variation records |
| **contract_lineage** | `CompositionContract.contract_hash` chain | Yes — computed but not stored |

### Properties a Commit Has That a Variation Does Not

| Property | Variation | Commit |
|----------|-----------|--------|
| Persistent | No | Yes |
| Immutable | No (status transitions) | Yes (append-only) |
| Has parent reference | No (`base_state_id` is ephemeral) | Yes (points to parent commit) |
| Contains post-commit state | Computed transiently (`updated_regions`) then forgotten | Stored as `region_snapshots` |
| Survives process restart | No | Yes |
| Queryable after creation | Only while variation is active | Always |
| Forms a lineage graph | No (isolated proposals) | Yes (DAG via parent_id) |

### What Already Exists and What Is Missing

**Already exists (reusable without change):**

- `Variation`, `Phrase`, `NoteChange` models — the complete diff representation
- `VariationService.compute_variation()` — the diff computation engine
- `VariationStore` lifecycle (CREATED → STREAMING → READY → COMMITTED) — the staging protocol
- `EventEnvelope`, `SSEBroadcaster` — the streaming infrastructure
- `CommitVariationResponse` with `updated_regions` — the commit result shape
- `base_state_id` optimistic concurrency check — the conflict detection mechanism
- `contract_hash` lineage chain — the generation provenance system

**Missing (would need to be introduced):**

- A persistent commit record that survives process lifetime
- A `parent_id` reference linking commits into a DAG
- A `commit_id` that is stable and deterministic (content hash or persistent sequence)
- Durable storage of `region_snapshots` (the `updated_regions` payload at commit time)
- A query interface: "give me the project state at commit N" or "show me the history for this region"
- An `author` field on commits (human user vs Muse vs specific agent)

---

## 3. Authority Transition Model

### Current Authority Stack

```
┌──────────────────────────────────────────────────┐
│  Story (DAW)                                      │
│  ═══════════                                      │
│  CANONICAL AUTHORITY                               │
│  Owns: project state, notes, effects, audio        │
│  Persistence: DAW project file                     │
│  Survives: everything                              │
├──────────────────────────────────────────────────┤
│  StateStore (L4)                                   │
│  ════════════                                      │
│  SHADOW / WORKING COPY                             │
│  Owns: nothing (derived from DAW via sync)         │
│  Persistence: none (in-memory)                     │
│  Survives: nothing (rebuilt each request)           │
├──────────────────────────────────────────────────┤
│  VariationStore (L5)                               │
│  ════════════════                                  │
│  STAGING AREA                                      │
│  Owns: active proposals                            │
│  Persistence: none (in-memory)                     │
│  Survives: nothing (lost on restart)               │
├──────────────────────────────────────────────────┤
│  Maestro (L1–L3)                                   │
│  ═══════════════                                   │
│  CHANGE GENERATOR                                  │
│  Owns: intent, prompts, tool call generation       │
│  Persistence: PostgreSQL (prompts, costs)          │
│  Survives: restarts                                │
└──────────────────────────────────────────────────┘
```

### Future Authority Stack

```
┌──────────────────────────────────────────────────┐
│  Story (DAW)                                      │
│  ═══════════                                      │
│  WORKING TREE / RENDERER                           │
│  Owns: live project state, audio rendering         │
│  Relationship to Muse: like files-on-disk to .git/ │
│  May diverge from HEAD (drift is normal)           │
├──────────────────────────────────────────────────┤
│  Muse                                              │
│  ════                                              │
│  HISTORY AUTHORITY                                 │
│  Owns: commit lineage, content snapshots           │
│  Persistence: durable storage                      │
│  Relationship to Story: canonical reference,       │
│    not real-time mirror                            │
├──────────────────────────────────────────────────┤
│  StateStore                                        │
│  ══════════                                        │
│  READ-THROUGH PROJECTION                           │
│  Owns: nothing (derived from Muse HEAD + DAW sync) │
│  Purpose: fast entity lookups during a request     │
├──────────────────────────────────────────────────┤
│  Maestro                                           │
│  ═══════                                           │
│  CHANGE GENERATOR                                  │
│  Owns: intent, prompts, orchestration              │
│  Relationship to Muse: produces change proposals   │
│    that Muse records                               │
└──────────────────────────────────────────────────┘
```

### What Stays the Same

1. **Maestro's role does not change.** It still classifies intent, orchestrates agents, generates tool calls, and delegates to Orpheus. It produces *proposed* changes — it never owns history.

2. **The SSE streaming protocol does not change.** `meta → phrase* → done` remains the variation delivery mechanism. The frontend's Variation Review Mode is unaffected.

3. **The MCP tool vocabulary does not change.** All ~50 tools remain. The DAW applies them the same way. EDITING-mode direct application is preserved.

4. **Orpheus is unaffected.** It generates MIDI on demand. It does not know about commits, history, or persistence.

5. **The variation lifecycle does not change.** CREATED → STREAMING → READY → COMMITTED / DISCARDED. The staging protocol is already correct.

### What Conceptually Shifts

1. **Authority over "what is the current state" moves from the DAW to Muse.** Today, `sync_from_client()` overwrites Maestro's state with the DAW's snapshot. In the future, Muse's HEAD commit is the reference, and the DAW is a working tree that may have diverged.

2. **`base_state_id` becomes a commit reference.** Instead of an ephemeral in-memory counter, it points to a persistent commit. This enables variation computation against a known, immutable base — even across process restarts.

3. **Commit becomes a durable event.** The `Accept Variation` endpoint, which today mutates in-memory state and returns, would additionally persist a commit record with the full `updated_regions` payload and lineage metadata.

4. **StateStore becomes derived.** Instead of being the shadow of the DAW, it becomes a projection of Muse's HEAD commit, optionally merged with the DAW's latest working tree snapshot. It remains in-memory, remains fast, but is no longer the closest thing to truth — Muse is.

### What Must Never Change for Compatibility

1. **The DAW must remain independently operable.** A user must be able to edit the DAW without Maestro or Muse running. Muse is not a lock manager — it is a history recorder.

2. **`sync_from_client()` must remain functional.** Even with Muse persistent, the DAW must be able to push its current state to Maestro. The difference: Maestro could now *compare* the DAW state against Muse's HEAD, rather than blindly accepting it.

3. **The variation protocol must remain ephemeral.** Variations are proposals, not commits. They should remain in-memory and discardable. Only *accepted* variations produce commits.

4. **EDITING-mode must remain immediate.** Structural operations (add track, set tempo) must apply directly without going through Muse review. The future grammar unification (Section 5) addresses how these can be *recorded* without being *reviewed*.

---

## 4. Working Tree vs Repository Projection

### Mapping Current Architecture to Version-Control Primitives

Using only current code concepts, the mapping is:

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  Working Tree  =  DAW project state                              │
│                   (what the user sees, plays, and edits)          │
│                                                                 │
│  Index         =  VariationStore                                 │
│                   (proposals staged for review, not yet committed)│
│                                                                 │
│  HEAD          =  StateStore._version                            │
│                   (the last known-good state Maestro has seen)    │
│                   NOTE: Today this is "last sync from DAW,"       │
│                   not "last Muse commit." The semantics shift.    │
│                                                                 │
│  Repository    =  (does not exist)                               │
│                   Would be: Muse commit history                   │
│                                                                 │
│  Diff          =  Variation (with Phrases and NoteChanges)       │
│                   Already well-formed, already structured         │
│                                                                 │
│  Commit msg    =  Variation.intent + Variation.ai_explanation     │
│                   Already captured, already surfaced in UI         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### The StateStore's Dual Identity Problem

Today, `StateStore` serves two incompatible roles simultaneously:

**Role A — Working tree.** During a request, `StateStore` is the mutable workspace where Maestro applies tool calls. Notes are added, regions are created, tracks are registered. This is the "working tree" — it contains uncommitted, in-progress changes.

**Role B — Base state.** When computing a variation, `VariationService` reads `StateStore._region_notes` as the *base* notes (the "before" in the diff). This requires `StateStore` to represent the immutable, pre-change state — but it is the same mutable object that Role A is modifying.

This dual identity is manageable today because the variation is computed *after* all tool calls complete (the proposed notes are already in StateStore, and the base notes are whatever was there before). But it creates a fragile temporal dependency: the base state must be captured *before* mutations begin, even though the same object holds both.

### How This Resolves Under Persistent Muse

With a persistent Muse, the roles separate cleanly:

**StateStore remains the working tree.** It is mutable, in-memory, rebuilt each request. It is where Maestro stages proposed changes during execution. Nothing changes about its runtime behavior.

**Muse's HEAD commit becomes the base state.** Instead of reading base notes from `StateStore._region_notes` (which may already contain proposed changes), `VariationService` reads them from Muse's last committed snapshot. This is an immutable reference that cannot be corrupted by in-flight mutations.

**The projection function:** Given Muse HEAD + DAW working tree, the system can compute:

- What changed since the last commit (equivalent to `git diff HEAD`)
- Whether the DAW has drifted (equivalent to `git status`)
- What the base state is for a new variation (always: Muse HEAD, not StateStore)

---

## 5. Grammar Unification Boundary

### The Problem (from Phase 1)

Musical mutations take two different paths:

- **Path A (COMPOSING → Muse):** Notes and expressive MIDI data go through variation review.
- **Path B (EDITING → Direct):** Everything else (tempo, key, tracks, regions, effects, automation) applies immediately.

For Muse to be a complete history engine, Path B changes must eventually be recordable. But they should not require human review — `stori_set_tempo` at 120 BPM does not need a Variation Review Mode overlay.

### Conceptual Change Types

To unify the grammar, every mutation in the system can be classified into one of these conceptual change types. These are categories, not code — they define what Muse would need to *understand*, not how it would store them.

#### Content Changes (Already Versioned)

Changes to the musical material within a region. These already flow through Muse via `NoteChange` and `controller_changes`.

| Change type | Granularity | Example |
|-------------|-------------|---------|
| `note.add` | Single note | Adding a bass note at beat 4 |
| `note.remove` | Single note | Deleting a passing tone |
| `note.modify` | Single note (pitch, timing, velocity, channel) | Transposing E to Eb |
| `cc.add` | CC event | Adding sustain pedal |
| `pitch_bend.add` | Pitch bend event | Adding expression |
| `aftertouch.add` | Aftertouch event | Adding pressure dynamics |

#### Structural Changes (Currently Bypass Muse)

Changes to the project's structure — containers and their properties. These do not affect note content but define the scaffolding that content lives in.

| Change type | Granularity | Example |
|-------------|-------------|---------|
| `track.create` | Track | Adding a bass track |
| `track.delete` | Track | Removing unused track |
| `track.rename` | Track property | "Bass" → "Upright Bass" |
| `track.set_program` | Track property | GM program 0 → 32 |
| `track.set_volume` | Track property | Volume 0.8 → 1.0 |
| `track.set_pan` | Track property | Pan center → left |
| `track.set_color` | Track property | Visual color |
| `track.set_icon` | Track property | SF Symbol icon |
| `track.mute` | Track property | Mute on/off |
| `track.solo` | Track property | Solo on/off |
| `region.create` | Region | Adding a container at beat 0–16 |
| `region.delete` | Region | Removing a container |
| `region.move` | Region | Shifting a region to a new position |
| `region.duplicate` | Region | Copying a region |

#### Global Changes (Currently Bypass Muse)

Changes to project-level properties that affect all content.

| Change type | Granularity | Example |
|-------------|-------------|---------|
| `project.set_tempo` | Project | 120 → 140 BPM |
| `project.set_key` | Project | C major → A minor |
| `project.set_time_signature` | Project | 4/4 → 3/4 |

#### Mix Changes (Currently Bypass Muse)

Changes to the signal routing and effects chain. These are non-musical (they affect sound, not composition) and have the weakest case for versioning.

| Change type | Granularity | Example |
|-------------|-------------|---------|
| `effect.insert` | Track | Adding a reverb insert |
| `send.create` | Track → Bus | Adding a send to a bus |
| `bus.create` | Project | Creating a reverb bus |
| `automation.add` | Track + parameter | Volume automation curve |

#### Ephemeral Changes (Never Versioned)

These are transient and should never be recorded in history.

| Change type | Granularity | Example |
|-------------|-------------|---------|
| `transport.play` | Session | Starting playback |
| `transport.stop` | Session | Stopping playback |
| `transport.set_playhead` | Session | Moving playhead |
| `ui.show_panel` | Session | Opening mixer |
| `ui.set_zoom` | Session | Changing zoom level |

### How This Relates to Current Execution Modes

The grammar unification does not change execution mode semantics. The distinction is:

- **Content changes** go through COMPOSING mode → Variation review → human accept/discard. This remains unchanged.
- **Structural, global, and mix changes** go through EDITING mode → immediate apply. This remains unchanged.
- **The only new concept:** after immediate application, EDITING-mode changes could be *recorded* as lightweight commit entries. No review, no variation protocol — just a log entry: "tempo changed from 120 to 140 at this point in the lineage graph."

This is analogous to the difference between a commit that requires code review (COMPOSING) and a commit that is auto-merged (EDITING). Both produce history. Only one requires human approval.

---

## 6. Temporal Authority Model

### Current Temporal Model

```
StateStore._version:  0 → 1 → 2 → 3 → 4 → 5 → ...
                      ↑                            ↑
               process start                  last mutation
               (resets to 0)                  (lost on restart)
```

`StateStore._version` is a monotonic in-memory integer. It increments on every mutation (track created, notes added, tempo changed, transaction events). It serves as `base_state_id` for optimistic concurrency in variation commits.

**Limitations:**
- Resets to 0 on process restart
- Does not survive across requests (unless the same `StateStore` instance is reused via the module-level `_stores` dict, keyed by `conversation_id`)
- No correlation with the DAW's internal version
- No correlation with any persistent concept
- Not content-addressed — two different states can have the same version number across restarts

### Transition to Muse Lineage

The transition preserves the optimistic concurrency mechanism while grounding it in persistent identity:

```
Muse lineage:  C₀ ← C₁ ← C₂ ← C₃ ← C₄ ← HEAD
               │    │    │    │    │
               ∅   V₁   V₂   V₃   V₄   (accepted variations)
                         │
                         V₂'  (discarded — not in lineage)
```

Where:
- `Cₙ` is a commit (persistent, immutable, has parent)
- `Vₙ` is the variation that produced the commit (may or may not be retained)
- `HEAD` points to the latest commit
- `base_state_id` in variation proposals refers to a `Cₙ` commit ID instead of an ephemeral integer

### How Optimistic Concurrency Survives

The mechanism is identical. Today:

```
1. Variation proposed with base_state_id = "42"
2. User edits DAW or another variation commits
3. StateStore._version is now 43
4. Commit attempt: base_state_id "42" ≠ current "43" → rejected
```

Under persistent Muse:

```
1. Variation proposed with base_state_id = "C₄₂" (commit ID)
2. Another variation commits, advancing HEAD to C₄₃
3. Commit attempt: base_state_id "C₄₂" ≠ HEAD "C₄₃" → rejected
```

The only change: the concurrency token is a persistent commit reference instead of an ephemeral counter. The check logic, the rejection behavior, the frontend's "regenerate variation" flow — all remain identical.

### The `_version` Counter During Transition

During a transition period, both systems can coexist:

- `StateStore._version` continues to increment as it does today (unchanged runtime behavior)
- Muse commit IDs exist independently and are recorded on each `Accept Variation`
- `base_state_id` in variation requests could accept either format (integer for legacy, commit ID for persistent mode)
- The frontend is unaffected — it receives `base_state_id` as an opaque string in both cases

The key insight: `base_state_id` is already a string (`str`) in the protocol. The frontend does not parse it — it stores it and sends it back. Changing its contents from `"42"` to `"C_a3f8b2..."` requires zero frontend changes.

---

## 7. Backwards Compatibility Guarantees

These invariants must remain true throughout and after any evolution toward persistent Muse. They are listed as hard constraints — not aspirations.

### SSE Protocol

| Invariant | Reason |
|-----------|--------|
| The `state` event remains the first event in every stream | Frontend mode detection depends on it |
| Event types remain: `state`, `reasoning`, `toolCall`, `toolError`, `meta`, `phrase`, `done`, `complete`, `error` | Frontend parsers are keyed on these types |
| `meta → phrase* → done` ordering remains enforced for COMPOSING streams | Frontend accumulates phrases and enables review controls on `done` |
| `sequence` numbers remain monotonically increasing within a variation | Frontend ordering depends on this |
| All SSE payloads remain camelCase JSON | `CamelModel` serialization is baked into both frontend and backend |
| `executionMode` in the `state` event remains `"variation"`, `"apply"`, or `"reasoning"` | Frontend uses this to choose between Variation Review and direct apply |

### MCP Tools

| Invariant | Reason |
|-----------|--------|
| All ~50 tool names and schemas remain unchanged | MCP clients (Cursor, Claude Desktop) depend on stable tool names |
| Tool names remain `snake_case`, parameters remain `camelCase` | MCP convention |
| `stori_generate_*` tools remain server-side executed | The DAW does not implement generation |
| Tool call events in SSE remain the same shape | Frontend applies them directly |
| Entity IDs (track, region, bus) remain server-generated UUIDs | Frontend stores and round-trips them |

### Orpheus API

| Invariant | Reason |
|-----------|--------|
| `POST /generate` request/response shapes remain unchanged | Orpheus is a separate service with its own release cadence |
| `GenerateResponse.tool_calls` format remains unchanged | Maestro's Orpheus client parses this format |
| Circuit breaker and retry behavior remain unchanged | Operational reliability |
| Cache key computation remains unchanged | Cache hit rates must not regress |

### DAW Integration

| Invariant | Reason |
|-----------|--------|
| `sync_from_client()` continues to accept the same project JSON shape | Frontend serialization is stable |
| `updated_regions` in `CommitVariationResponse` remains a full region snapshot | Frontend replaces region content in bulk |
| `undo_label` continues to be provided on commit | DAW uses this for its native undo group |
| `base_state_id` remains an opaque string (frontend does not parse it) | Enables the transition from integer to commit ID without frontend changes |
| WebSocket MCP connection protocol remains unchanged | DAW connection handshake is stable |

### Variation Protocol

| Invariant | Reason |
|-----------|--------|
| `POST /variation/propose` returns `variation_id` + `stream_url` | Frontend initiates streaming from this |
| `POST /variation/commit` accepts `accepted_phrase_ids` | Partial acceptance is a core UX feature |
| `POST /variation/discard` remains idempotent | Frontend may call discard multiple times |
| The variation state machine (CREATED → STREAMING → READY → COMMITTED / DISCARDED / FAILED / EXPIRED) remains unchanged | Backend and frontend both depend on these states |
| Optimistic concurrency via `base_state_id` mismatch rejection remains unchanged | The error handling flow is already implemented |

---

## 8. Minimal New Abstractions

The following are **conceptual interfaces** — names and responsibilities, not code. They represent the smallest set of abstractions needed to bridge current Muse to persistent Muse. Each is defined in terms of its role, not its implementation.

### ChangeSet

A `ChangeSet` is a unified container for any mutation, regardless of execution mode.

| Aspect | Definition |
|--------|-----------|
| **What it is** | A structured record of one or more changes to a project |
| **What it contains** | A list of typed changes (content changes, structural changes, global changes, mix changes) with before/after states where applicable |
| **What it replaces** | Nothing — it is a new abstraction that wraps existing concepts. A `Variation` is a reviewed `ChangeSet`. An EDITING-mode tool call sequence is an auto-committed `ChangeSet`. |
| **Why it is needed** | Today, content changes (notes) and structural changes (tempo, tracks) have no common representation. A `ChangeSet` provides a uniform envelope for both. |
| **Relationship to existing code** | A `Variation` would *contain* a `ChangeSet` of type "content." An EDITING-mode execution would produce a `ChangeSet` of type "structural" or "global." |

### CommitCandidate

A `CommitCandidate` is the object produced when a `ChangeSet` is ready to be recorded.

| Aspect | Definition |
|--------|-----------|
| **What it is** | A fully resolved, validated mutation with all IDs resolved and all changes computed, ready to be persisted as a commit |
| **How it is produced** | For COMPOSING: `Accept Variation` → `CommitCandidate`. For EDITING: immediate tool call execution → `CommitCandidate` (auto-accepted). |
| **What it contains** | Parent commit reference, change set, region snapshots (post-commit state), metadata (intent, explanation, author, timestamp) |
| **What it replaces** | The transient state between "variation accepted" and "state version incremented" — which today happens in a single synchronous call with no intermediate representation |
| **Why it is needed** | To decouple "deciding what to commit" from "recording the commit." Today these are the same operation. |

### RepositoryState

A `RepositoryState` is an immutable snapshot of a project at a specific commit.

| Aspect | Definition |
|--------|-----------|
| **What it is** | The complete musical state of a project at one point in history |
| **What it contains** | All region content objects (notes, CC, pitch bends, aftertouch), track definitions, project metadata (tempo, key, time signature), and the commit ID this snapshot corresponds to |
| **What it replaces** | The role that `StateStore` currently plays as "the known state" — but immutable and persistent, not mutable and ephemeral |
| **Why it is needed** | To answer "what did the project look like at commit N?" without the DAW running |
| **Relationship to existing code** | `StateStore.to_dict()` already serializes a representation that is close to a `RepositoryState`. The gap is persistence and immutability. |

### WorkingTreeProjection

A `WorkingTreeProjection` is the computed difference between the DAW's current state and Muse's HEAD commit.

| Aspect | Definition |
|--------|-----------|
| **What it is** | A diff between the repository's HEAD and the DAW's live state — the musical equivalent of `git status` |
| **What it contains** | Lists of: regions with changed notes, tracks with changed properties, global properties that differ (tempo, key) |
| **How it is computed** | DAW sends its project state (as it does today via `sync_from_client`). Instead of blindly replacing StateStore, the system compares it against Muse's HEAD. The delta is the `WorkingTreeProjection`. |
| **What it replaces** | Nothing directly — this is a new capability. Today there is no comparison, only overwrite. |
| **Why it is needed** | To detect drift. To answer "has the user changed anything in the DAW since the last commit?" To enable future features like "commit DAW changes" or "revert to last commit." |
| **Relationship to existing code** | `sync_from_client()` receives the DAW state that would be compared. `VariationService.compute_variation()` already has the diff algorithm. The difference: the "base" would be Muse's HEAD commit, not StateStore's previous state. |

### How These Abstractions Relate

```
                        ┌──────────────┐
                        │   Maestro    │
                        │  (generates) │
                        └──────┬───────┘
                               │
                         ChangeSet
                       (content or structural)
                               │
                    ┌──────────┴──────────┐
                    │                     │
              COMPOSING               EDITING
              (human review)          (auto-accept)
                    │                     │
              ┌─────▼─────┐        ┌──────▼──────┐
              │ Variation  │        │  Immediate   │
              │ (staging)  │        │  apply       │
              └─────┬──────┘        └──────┬──────┘
                    │                      │
                    │    CommitCandidate    │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Muse Repository    │
                    │  (persistent)       │
                    │                     │
                    │  HEAD → Cₙ → ...    │
                    │  RepositoryState    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Story (DAW)        │
                    │  (working tree)     │
                    │                     │
                    │  WorkingTree        │
                    │  Projection         │
                    └─────────────────────┘
```

---

## 9. External Contributor Mental Model

### The Three-Word Summary

> **Maestro** = composer. **Muse** = memory. **Story** = instrument.

### The Longer Version

**Maestro is the composer.** It receives creative intent from the user (natural language, structured prompts, direct tool invocations). It interprets that intent, plans a course of action, delegates to specialist generators (Orpheus), and produces a structured set of musical changes. Maestro is stateless across requests — it generates, it does not remember. It is the AI.

**Muse is the memory.** It records what Maestro proposes and what the human accepts. It organizes musical history into a lineage of commits — not as a log of actions taken, but as a sequence of musical states the composition passed through. Muse does not generate music. It does not decide what to play. It remembers what was chosen. Today, Muse's memory is short-lived (in-memory, per-process). The evolution is to make it persistent.

**Story is the instrument.** It is the DAW — the surface on which music is played, heard, and edited. It is the working tree. It may diverge from what Muse remembers (the user can edit notes by hand). It is the renderer, the performer, the workspace. Story does not know about AI, about prompts, or about commit history. It knows about tracks, regions, notes, effects, and playback.

### How They Interact

```
User                          Maestro                    Muse                Story (DAW)
  │                              │                        │                      │
  │  "Make a chill lo-fi beat"   │                        │                      │
  │─────────────────────────────▶│                        │                      │
  │                              │ classify intent        │                      │
  │                              │ plan composition       │                      │
  │                              │ generate via Orpheus   │                      │
  │                              │                        │                      │
  │                              │ compute variation      │                      │
  │                              │───────────────────────▶│                      │
  │                              │                        │ stage proposal       │
  │                              │                        │                      │
  │  ◀──────── SSE: meta, phrases, done ─────────────────│                      │
  │                              │                        │                      │
  │  review, accept phrases      │                        │                      │
  │─────────────────────────────▶│                        │                      │
  │                              │ commit variation       │                      │
  │                              │───────────────────────▶│                      │
  │                              │                        │ record commit        │
  │                              │                        │ advance HEAD         │
  │                              │                        │                      │
  │                              │                        │ updated_regions      │
  │                              │◀───────────────────────│                      │
  │                              │                        │                      │
  │                              │ emit toolCall SSE ─────│──────────────────────▶│
  │                              │                        │                      │ apply to DAW
  │                              │                        │                      │
```

### What This Means for Contributors

**If you are adding a new MCP tool:** You are working in the Story boundary (L6). Define the tool schema, add it to the registry. If the tool creates entities, add StateStore mutations (L4). Muse does not need to know about new tools — they flow through the existing EDITING path.

**If you are changing how variations work:** You are working in Muse (L5). The variation models, the diff engine, the state machine, the streaming infrastructure — these are Muse's domain. Changes here affect the proposal/review/commit protocol.

**If you are adding a new generation capability:** You are working in Maestro (L1–L3) and possibly Orpheus (L7). The orchestration, planning, and execution layers decide *how* to generate. Muse only sees the result.

**If you are working on persistence:** You are building Muse's repository layer — the layer that does not exist yet. Your work sits below the existing variation protocol. You should not need to change `VariationService`, `VariationStore`, or the SSE streaming infrastructure. You are adding a commit/storage layer beneath them.

**If you are working on drift detection:** You are building the `WorkingTreeProjection` concept — comparing the DAW's live state against Muse's HEAD. The comparison algorithm already exists (`VariationService.compute_variation`). The new part is the persistent reference point to compare against.

### The North Star

Muse should eventually be able to answer questions that no part of the current system can:

- "What did this composition sound like three edits ago?"
- "Which AI suggestion introduced this bass line?"
- "Show me every version of the chorus, in order."
- "Undo the last four AI changes but keep the manual edits."

These questions require persistent history. That is what this bridge prepares.

---

## Appendix: Relationship to Phase 1 Open Questions

This document addresses the open questions from Phase 1 (Section 9 of `maestro-muse-evolution.md`) by providing conceptual frameworks, not definitive answers:

| Phase 1 Question | Where Addressed |
|------------------|-----------------|
| Q1: Should Muse own `state_version`? | Section 6 — Temporal Authority Model |
| Q2: Should StateStore become a cache? | Section 3 — Authority Transition (StateStore becomes "read-through projection") |
| Q3: Does the DAW become a renderer? | Section 3 — Authority Transition ("working tree / renderer") |
| Q4: Source of truth during a request? | Section 4 — Working Tree vs Repository (dual identity resolved) |
| Q5–8: Identity questions | Section 2 — Commit Object (which IDs exist, which are missing) |
| Q9–12: Grammar unification | Section 5 — Grammar Unification Boundary (full change taxonomy) |
| Q13–16: Persistence details | Deliberately deferred — this document defines *what* is persisted, not *how* |
| Q17–19: Concurrency | Section 6 — optimistic concurrency preserved via commit references |
| Q20–22: DAW boundary | Section 4 — WorkingTreeProjection as the `git status` equivalent |
