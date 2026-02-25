# Stori — Maestro Architectural Decomposition & Muse Contract Elevation

> **Status:** Historical Analysis (Phase 1) — many items described as "does not exist" have since been implemented.
> **Superseded by:** [`muse-vcs.md`](muse-vcs.md) — the canonical Muse VCS implementation reference.
> **Date:** February 2026
> **Audience:** Distributed systems engineers, music technology researchers, AI infrastructure teams, open-source contributors
> **Scope:** Analysis and contract definition only — no implementation proposals
>
> **Implementation note (Feb 2026):** Since this analysis was written, Phases 5–13 have been completed.
> The persistent commit lineage, drift detection, checkout engine, merge engine, log graph serializer,
> and production HTTP API all now exist. See `muse-vcs.md` for the current state.

---

## Table of Contents

1. [Layered Architecture Decomposition](#1-layered-architecture-decomposition)
2. [Authority & State Ownership Analysis](#2-authority--state-ownership-analysis)
3. [Contract Surface Definitions](#3-contract-surface-definitions)
4. [Temporal Model (Time, Identity, Lineage)](#4-temporal-model)
5. [Variation → Commit Semantics Mapping](#5-variation--commit-semantics-mapping)
6. [Grammar & Semantic Change Model](#6-grammar--semantic-change-model)
7. [Drift & Working Tree Analysis](#7-drift--working-tree-analysis)
8. [Coupling Matrix](#8-coupling-matrix)
9. [Open Architecture Questions](#9-open-architecture-questions)
10. [Risks to Open-Source Evolution](#10-risks-to-open-source-evolution)

---

## Preface: What the System Is

Stori is an AI-assisted music composition system. It comprises four conceptual subsystems:

| Subsystem | Role | Runtime |
|-----------|------|---------|
| **Maestro** | AI orchestration: prompt interpretation, execution planning, tool-call generation | FastAPI process (`app/`) |
| **Muse** | Change-proposal protocol: variation computation, phrase grouping, commit lifecycle | Library within the Maestro process (`app/variation/`, `app/services/variation/`, `app/models/variation.py`) |
| **Story** (DAW) | Canonical project state, audio rendering, user interface | macOS application (Swift, separate repository) |
| **Orpheus** | Neural MIDI generation via Orpheus Music Transformer | Separate FastAPI process (`orpheus-music/`) |

The system's core loop:

```
User prompt → Maestro (intent → plan → tool calls) → Orpheus (MIDI generation)
                                                    → Story (DAW mutation via MCP/SSE)
                                                    → Muse (variation proposal → human review → commit)
```

Maestro and Muse share a process. Story and Orpheus are separate processes. This document maps the conceptual boundaries as they exist in code today and identifies the seams along which the system can evolve.

---

## 1. Layered Architecture Decomposition

The Maestro codebase, examined by what the code *does* rather than how it is named, decomposes into seven layers. Each layer has a distinct responsibility, a defined set of inputs and outputs, and a set of implicit assumptions.

### Layer Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ENTRY POINTS                                    │
│  POST /maestro/stream (SSE)  │  MCP stdio  │  MCP WebSocket        │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│  L1  ORCHESTRATION LAYER                                            │
│  maestro_handlers.orchestrate()                                     │
│  Routes: REASONING → L1 only  │  EDITING → L3  │  COMPOSING → L2+L3│
└──────────────────┬──────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│  L2  PLANNING LAYER                                                 │
│  planner/  │  maestro_agent_teams/coordinator  │  contracts.py       │
│  Builds ExecutionPlan or CompositionContract                        │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│  L3  EXECUTION LAYER                                                │
│  maestro_editing/  │  section_agent  │  executor/                    │
│  Resolves entities, validates tools, executes calls                  │
└──────────┬────────────────────────────────┬─────────────────────────┘
           │                                │
┌──────────▼────────────┐   ┌───────────────▼─────────────────────────┐
│  L4  STATE PROJECTION │   │  L5  VARIATION / MUSE LAYER             │
│  StateStore            │   │  VariationService  │  VariationStore    │
│  EntityRegistry        │   │  state_machine  │  SSEBroadcaster      │
│  CompositionState      │   │  event_envelope │  stream_router       │
└────────────────────────┘   └─────────────────────────────────────────┘
                   │                                │
┌──────────────────▼────────────────────────────────▼─────────────────┐
│  L6  MCP / DAW INTEGRATION LAYER                                    │
│  mcp/server  │  mcp/tools/*  │  protocol/events  │  protocol/emitter│
│  SSE event emission  │  WebSocket tool forwarding                    │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│  L7  GENERATION LAYER (ORPHEUS BOUNDARY)                            │
│  services/orpheus.py (HTTP client)                                   │
│  music_generator.py  │  backends/*                                   │
│  ──── process boundary ────                                          │
│  orpheus-music/music_service.py (separate FastAPI)                   │
└─────────────────────────────────────────────────────────────────────┘
```

### Layer Details

#### L1 — Orchestration Layer

| Attribute | Value |
|-----------|-------|
| **Code** | `app/core/maestro_handlers.py`, `app/core/intent/` |
| **Responsibility** | Receive user prompt. Classify intent (REASONING / EDITING / COMPOSING). Determine execution mode (`variation` / `apply` / `reasoning`). Route to appropriate handler. Manage trace context. |
| **Inputs** | Prompt string, project context (JSON), conversation history, model preference |
| **Outputs** | AsyncIterator of SSE event strings |
| **Hidden assumptions** | `StateStore` is available and synced *before* intent classification. `project_context.id` is a stable project identity. Empty projects override COMPOSING to EDITING. |
| **Direct imports** | L2 (planner, agent teams), L3 (editing handlers), L4 (StateStore), L6 (SSE utils) |
| **Boundary blur** | Directly constructs `StateStore` and calls `sync_from_client()`. Intent classification happens here but conceptually belongs to a separate routing layer. Execution mode policy (variation vs apply overrides) is embedded in orchestration logic rather than declared as rules. |

#### L2 — Planning Layer

| Attribute | Value |
|-----------|-------|
| **Code** | `app/core/planner/`, `app/core/maestro_agent_teams/coordinator.py`, `app/core/maestro_agent_teams/contracts.py` |
| **Responsibility** | Convert classified intent into a structured execution plan. For Agent Teams: parse sections, build frozen `CompositionContract` → `InstrumentContract` → `SectionContract` chain with cryptographic lineage hashing. For planner path: build `ExecutionPlanSchema` with steps. |
| **Inputs** | Parsed prompt (`ParsedPrompt`), intent result, project context |
| **Outputs** | `ExecutionPlan` or `CompositionContract` hierarchy |
| **Hidden assumptions** | Section parsing assumes 4/4 time signature for beat math. Contract hashes assume fields are set before sealing. Instrument ordering (drums first, then bass) is hardcoded for signal coordination. |
| **Direct imports** | L3 (dispatches to agents/executor), L4 (StateStore for track existence checks) |
| **Boundary blur** | The coordinator both plans *and* orchestrates execution of instrument agents, collapsing the L2/L3 distinction for Agent Teams. Deterministic setup steps (tempo, key) are applied immediately during planning, bypassing the execution layer. |

#### L3 — Execution Layer

| Attribute | Value |
|-----------|-------|
| **Code** | `app/core/maestro_editing/`, `app/core/maestro_agent_teams/section_agent.py`, `app/core/maestro_agent_teams/agent.py`, `app/core/executor/` |
| **Responsibility** | Execute tool calls against state. Resolve entity references (name → ID). Validate tool parameters. Handle LLM tool-use loops. Retry failed sections. |
| **Inputs** | Execution plan or section contract, LLM client, StateStore |
| **Outputs** | Tool call results, SSE events, state mutations |
| **Hidden assumptions** | Entity resolution assumes names are unique within type (track names, region names). LLM will produce valid JSON tool arguments. Server-side generation tools (`stori_generate_*`) return notes that can be added to the current StateStore. |
| **Direct imports** | L4 (StateStore for mutations), L5 (VariationService for diff computation), L6 (SSE event emission), L7 (Orpheus client) |
| **Boundary blur** | `_apply_single_tool_call()` in `maestro_editing` simultaneously validates, resolves entities, executes, records to StateStore, and emits SSE — it is the densest coupling point in the system. Section agents reach through to Orpheus (L7) and StateStore (L4) in the same function. |

#### L4 — State Projection Layer

| Attribute | Value |
|-----------|-------|
| **Code** | `app/core/state_store.py`, `app/core/entity_registry.py` |
| **Responsibility** | Maintain an in-memory shadow of the DAW's project state. Version mutations monotonically. Provide transaction/rollback via snapshots. Offer fast name→ID entity resolution. |
| **Inputs** | Client project state (via `sync_from_client`), tool call mutations (via `create_track`, `add_notes`, etc.) |
| **Outputs** | Current state version (`state_id`), materialized note lists, entity lookups |
| **Hidden assumptions** | `sync_from_client` is called at the start of every request. Notes are stored as untyped dicts (no schema validation at ingress). The registry stores both server-generated and client-reported IDs in the same namespace. |
| **Direct imports** | None — this is a leaf layer for state. EntityRegistry is purely internal. |
| **Boundary blur** | `StateStore` serves two masters: it is both the "working tree" for the current request and the "base state" for variation diffing. These are conceptually different states (mutable workspace vs immutable snapshot) but are physically the same object. |

#### L5 — Variation / Muse Layer

| Attribute | Value |
|-----------|-------|
| **Code** | `app/variation/` (core state machine, event envelopes, store, streaming), `app/services/variation/` (diff engine, note matching, labels), `app/models/variation.py` (Pydantic models), `app/api/routes/variation/` (HTTP endpoints) |
| **Responsibility** | Compute structured change proposals (Variations) by diffing base and proposed note states. Group changes into independently reviewable Phrases by bar range. Manage variation lifecycle (CREATED → STREAMING → READY → COMMITTED / DISCARDED). Stream phrases via SSE. Apply accepted phrases to StateStore on commit. |
| **Inputs** | Base notes (from StateStore), proposed notes (from execution), intent string, metadata |
| **Outputs** | `Variation` with `Phrase` objects, SSE events (`meta`, `phrase`, `done`), `CommitVariationResponse` with `updated_regions` |
| **Hidden assumptions** | Notes are matched by `(pitch, start_beat)` proximity. Bar grouping uses fixed 4-bar windows with 4 beats per bar. Phrase identity is ephemeral (UUID per variation, not stable across variations). `VariationStore` is in-memory (no persistence). Commit assumes `StateStore` still contains the base notes used during proposal. |
| **Direct imports** | L4 (StateStore for reading/writing notes on commit) |
| **Boundary blur** | The commit endpoint reaches directly into `StateStore` to read region notes and apply changes. `VariationRecord` stores `conversation_id` to enable this cross-layer lookup. The Muse layer has no independent state — it parasitizes L4's `StateStore` for both base state and commit target. |

#### L6 — MCP / DAW Integration Layer

| Attribute | Value |
|-----------|-------|
| **Code** | `app/mcp/` (server, stdio, tools), `app/protocol/` (events, emitter, validation, schemas) |
| **Responsibility** | Define the tool vocabulary for DAW manipulation (~50 tools across 10 categories). Serialize tool calls as SSE events (for the Stori DAW) or JSON-RPC messages (for MCP clients). Validate event shapes. Forward tool calls to connected DAW via WebSocket. Cache project state from DAW. |
| **Inputs** | Tool call objects from L3, DAW WebSocket messages |
| **Outputs** | SSE events, WebSocket messages, tool responses |
| **Hidden assumptions** | Tool names use `snake_case`, parameters use `camelCase`. DAW will respond within 30 seconds. Project state cache is eventually consistent with the actual DAW. SSE is the primary transport (WebSocket for MCP only). |
| **Direct imports** | L4 (StateStore for cached project state), L7 (Orpheus for server-side generation tools) |
| **Boundary blur** | Server-side generation tools (`stori_generate_midi` etc.) are defined alongside DAW tools but execute entirely server-side via Orpheus — they never reach the DAW. This conflates "tools the DAW executes" with "tools the server executes on behalf of the DAW." |

#### L7 — Generation Layer (Orpheus Boundary)

| Attribute | Value |
|-----------|-------|
| **Code** | `app/services/orpheus.py` (HTTP client), `app/services/music_generator.py` (abstraction), `app/services/backends/` (pluggable backends). Across process boundary: `orpheus-music/music_service.py` |
| **Responsibility** | Generate MIDI content from musical parameters (genre, tempo, key, instruments, bars, emotion vector). Manage Gradio client connections to HuggingFace Space. Cache results (LRU + TTL). Parse generated MIDI into note dicts. Filter channels by instrument. Convert to tool calls. |
| **Inputs** | `GenerateRequest` (genre, tempo, instruments, bars, key, emotion vector, quality preset, composition continuity state) |
| **Outputs** | `GenerateResponse` (tool calls containing track/region/note creation commands, or flat note list) |
| **Hidden assumptions** | HuggingFace Space is available (GPU cold-start tolerance via circuit breaker + retries). One Gradio session per worker. Batch selection is non-deterministic (`random.randint`). Session tokens accumulate and require rotation at cap. Seed MIDI encodes genre-specific patterns. |
| **Direct imports** | None upward — Orpheus is called via HTTP from L3/L6. |
| **Boundary blur** | Orpheus returns tool calls (`createProject`, `addMidiTrack`, `addMidiRegion`, `addNotes`) — it knows about the DAW tool vocabulary. This means L7 has implicit knowledge of L6's tool schema. The Maestro-side Orpheus client (`app/services/orpheus.py`) includes circuit breaker and retry logic that is interleaved with business logic in `_execute_agent_generator`. |

---

## 2. Authority & State Ownership Analysis

### Current Authority Map

| State Type | Current Owner | Persistence | Implied Future Owner | Notes |
|------------|---------------|-------------|---------------------|-------|
| **Project canonical state** (tracks, regions, notes, effects, automation) | **Story (DAW)** | DAW project file | Story (DAW) | The DAW is the only durable representation of a composition. Everything else is derived or ephemeral. |
| **Project shadow state** (entity registry, notes, CC, tempo, key) | **StateStore** (L4) | In-memory only | Muse (as persistent repo) | Recreated from scratch each request via `sync_from_client()`. Lost on process restart. |
| **State version** (`base_state_id`) | **StateStore** (L4) | In-memory monotonic int | Muse (as commit counter) | Resets to 0 on process restart. Not correlated with any DAW concept. |
| **Variations** (proposals, phrases, note changes) | **VariationStore** (L5) | In-memory dict | Muse (as staging area) | Ephemeral by design. No history — discarded variations are deleted. |
| **Prompts** | **PostgreSQL** (ConversationMessage) | Persistent | Maestro | Stored with token counts and costs. The only durable record of user intent. |
| **Tool call transcript** | **PostgreSQL** (ConversationMessage.tool_calls) | Persistent (JSON column) | Maestro | Stored as JSON blobs per message. No structured query capability. |
| **Tempo** | Story (DAW), shadowed by StateStore | DAW: persistent; StateStore: ephemeral | Story, with Muse tracking changes | Single value — no tempo map. |
| **Key** | Story (DAW), shadowed by StateStore | DAW: persistent; StateStore: ephemeral | Story, with Muse tracking changes | Single value — no key changes at positions. |
| **Time signature** | Story (DAW), shadowed by StateStore | DAW: persistent; StateStore: ephemeral | Story, with Muse tracking changes | Single value — no meter changes. |
| **Track definitions** | Story (DAW), shadowed by EntityRegistry | DAW: persistent; EntityRegistry: ephemeral | Story, with Muse tracking lineage | Names, GM programs, drum kits, colors, icons. |
| **Region notes** | Story (DAW), shadowed by StateStore._region_notes | DAW: persistent; StateStore: ephemeral | Muse (as versioned content) | Notes exist in two places during a request. |
| **Intent classification** | **Maestro** (L1) | SSE event only (not persisted) | Maestro | Intent is computed per-request, emitted as SSE, not stored in any queryable form. |
| **Orpheus generation results** | **Orpheus** disk cache | `/data/cache/result_cache.json` (24h TTL) | Orpheus (generation lineage could move to Muse) | Cached by quantized request hash. Survives container restarts. |
| **Composition session state** | **StateStore.CompositionState** | In-memory per composition_id | Muse (as session lineage) | Tracks Gradio session_id and accumulated tokens for Orpheus continuity. |
| **User identity** | **PostgreSQL** (User table) | Persistent | Maestro | Device UUID as primary key. Budget tracking. |
| **Arrangement structure** (sections, markers) | **Nobody** | Not persisted | Muse | Sections are parsed from Stori prompts at generation time and discarded. No durable section model. |
| **Contract lineage** (CompositionContract → InstrumentContract → SectionContract) | **Maestro** (L2) | In-memory per request | Muse (as execution provenance) | SHA-256 hashes form a lineage chain. Verified at execution time but not stored. |

### Why Muse Cannot Yet Be a Persistent History Engine

For Muse to become persistent, it needs to own the authoritative version of at least:

1. **State snapshots** — full project state at each commit point. Today StateStore is ephemeral and rebuilds from the DAW each request.

2. **Commit lineage** — a chain of `(base_state, variation, accepted_phrases) → new_state`. Today `base_state_id` is a monotonic in-memory integer that resets on restart. There is no lineage graph.

3. **Stable phrase identity** — phrases that survive across variations, enabling "this phrase was modified in variation A and again in variation B." Today phrase IDs are ephemeral UUIDs.

4. **Complete change grammar** — all musical mutations must flow through Muse. Today, EDITING-mode operations (tempo, key, track creation, quantization, region edits) bypass Muse entirely and mutate the DAW directly.

5. **Durable storage** — a persistence backend (PostgreSQL, SQLite, or custom format). Today `VariationStore` is an in-memory dict.

The fundamental blocker: **authority is split**. The DAW owns canonical state, StateStore owns the working shadow, and Muse owns ephemeral proposals. For Muse to be authoritative, it must either (a) replace StateStore as the state owner, or (b) be the exclusive path through which all state mutations flow — including EDITING-mode operations.

---

## 3. Contract Surface Definitions

### 3.1 Maestro → Muse Contract (Implicit)

Today, Maestro invokes Muse via direct Python function calls. The implicit contract:

```
VariationService.compute_variation(
    base_notes:       list[dict]       # Notes from StateStore (base state)
    proposed_notes:   list[dict]       # Notes after Maestro's transformation
    region_id:        str              # DAW region UUID
    track_id:         str              # DAW track UUID
    intent:           str              # User's natural language intent
    explanation:      str | None       # LLM-generated explanation
    variation_id:     str | None       # Pre-generated UUID
    region_start_beat: float           # Absolute position in project
    cc_events:        list[dict] | None
    pitch_bends:      list[dict] | None
    aftertouch:       list[dict] | None
) → Variation
```

**If this were a service boundary, the contract would be:**

| Aspect | Specification |
|--------|---------------|
| **Payload** | `(base_state: RegionSnapshot, proposed_state: RegionSnapshot, metadata: VariationMetadata)` |
| **Invariant** | `base_state` must be an immutable snapshot, not a live reference to mutable StateStore data |
| **Identity rule** | `region_id` and `track_id` must be server-assigned UUIDs that the DAW recognizes |
| **Assumption** | Notes are in snake_case internal format (`start_beat`, `duration_beats`), already normalized |
| **Ordering** | Single call per variation; for multi-region, use `compute_multi_region_variation` |
| **Idempotency** | Not idempotent — each call generates new phrase UUIDs. Passing the same `variation_id` would create a new variation with the same ID. |
| **Error model** | Raises `ValueError` on malformed `NoteMatch`. Returns empty `Variation` on no changes. |

**Missing contract properties:**
- No schema version on the note dict format
- No validation that `base_notes` actually corresponds to the current StateStore state
- No mechanism to detect if `proposed_notes` were generated against stale base state

### 3.2 Muse → Maestro Contract (Implicit)

What Muse returns to Maestro (via commit endpoint → `CommitVariationResponse`):

```python
CommitVariationResponse(
    project_id:        str
    new_state_id:      str                    # Monotonic version after commit
    applied_phrase_ids: list[str]             # Which phrases were accepted
    undo_label:        str                    # DAW undo group label
    updated_regions:   list[UpdatedRegionPayload]  # Full post-commit MIDI state
)
```

| Aspect | Specification |
|--------|---------------|
| **Invariant** | `updated_regions` contains the *complete* note list for each affected region after commit — not a delta |
| **Identity rule** | `region_id` and `track_id` in `UpdatedRegionPayload` must match IDs the DAW already knows |
| **Assumption** | The DAW will *replace* (not merge) notes in the identified regions |
| **Assumption** | The DAW will create a single undo group labeled with `undo_label` |
| **Ordering** | Commit is atomic — all accepted phrases apply in one operation |

**Missing contract properties:**
- No mechanism for the DAW to report commit application success/failure back to Muse
- No way to correlate `new_state_id` with the DAW's internal version
- New regions (created during generation) include `start_beat`, `duration_beats`, `name` in `UpdatedRegionPayload`, but there is no explicit signal distinguishing "update existing region" from "create new region" — the frontend infers this from whether `region_id` is already known

### 3.3 Maestro → Story (DAW) Contract

The MCP tool vocabulary defines the boundary. Formalized:

| Aspect | Specification |
|--------|---------------|
| **Transport** | SSE events (compose stream) or WebSocket JSON-RPC (MCP session) |
| **Tool vocabulary** | ~50 tools across 10 categories (project, track, region, notes, generation, playback, effects, automation, MIDI control, UI) |
| **Naming convention** | Tool names: `snake_case` (`stori_add_notes`). Parameters: `camelCase` (`regionId`, `startBeat`) |
| **Side effects** | Each tool call is a mutation of the DAW's canonical state. No tool is read-only except `stori_read_project`. |
| **Ordering guarantee** | SSE events are ordered per-stream (monotonic `sequence`). The DAW applies them in order. |
| **Idempotency** | Region creation: idempotent (checks for overlap). Track creation: not idempotent (always creates new). Note addition: not idempotent (appends; calling twice doubles notes). |
| **Error model** | `toolError` SSE event. Non-fatal — stream continues. 30-second timeout for WebSocket-forwarded tools. |

**Allowed side effects by category:**

| Category | Creates | Modifies | Deletes |
|----------|---------|----------|---------|
| Project | project | tempo, key | — |
| Track | track | volume, pan, name, program, mute, solo, color, icon | — |
| Region | region | position (move) | region (delete) |
| Notes | notes (append) | quantization, swing | notes (clear) |
| Effects | insert effect, send, bus | — | — |
| Automation | automation points | — | — |
| MIDI Control | CC events, pitch bends, aftertouch | — | — |
| Playback | — | playhead position, play/stop state | — |
| UI | — | panel visibility, zoom | — |

### 3.4 Maestro → Orpheus Contract

| Aspect | Specification |
|--------|---------------|
| **Transport** | HTTP POST to `{ORPHEUS_BASE_URL}/generate` |
| **Request** | `GenerateRequest(genre, tempo, instruments, bars, key, emotion_vector, role_profile_summary, generation_constraints, intent_goals, quality_preset, temperature, top_p, composition_id, seed, trace_id, intent_hash)` |
| **Response** | `GenerateResponse(success, tool_calls, notes, error, metadata)` |
| **Stochasticity sources** | (1) Gradio model inference (temperature/top_p), (2) random batch selection, (3) curated seed MIDI selection (randomized within genre bucket) |
| **Determinism** | Optional `seed` parameter for reproducible generation. Cache lookup provides determinism on cache hit. `trace_id` + `intent_hash` for full observability. |
| **Error model** | Circuit breaker (3 failures → 60s cooldown → half-open probe). Retries: 4 attempts with exponential backoff (2s, 5s, 10s, 20s) for GPU cold-start errors. |
| **Latency** | 10–180 seconds depending on GPU cold-start state and generation length. |

**Orpheus knows about DAW tool schemas.** The `generate_tool_calls()` function in `music_service.py` produces `createProject`, `addMidiTrack`, `addMidiRegion`, `addNotes`, `addMidiCC`, `addPitchBend` — it constructs DAW tool calls directly. This means the Orpheus → Maestro response implicitly contains L6 knowledge.

---

## 4. Temporal Model

### Identity Analysis

| ID | Code location | Lifespan | Persistence | Stability across sessions | Musical or UI identity | Lineage potential |
|----|---------------|----------|-------------|--------------------------|----------------------|-------------------|
| `project_id` | `StateStore.project_id`, DAW project | Per-project | DAW: persistent. StateStore: ephemeral (re-synced each request). | Stable if DAW sends same project ID. | Musical (represents a composition) | **High** — natural root of a lineage tree |
| `conversation_id` | `StateStore` key, `Conversation` DB table | Per-conversation | PostgreSQL: persistent. StateStore key: ephemeral. | Stable within a conversation. A project may span multiple conversations. | UI (session grouping) | **Low** — a conversation is an interaction session, not a musical entity |
| `variation_id` | `VariationRecord.variation_id` | Per-variation proposal | In-memory only | Not stable — lost on restart, new UUID each proposal | Musical (represents a change proposal) | **Medium** — could anchor commit entries if persisted |
| `phrase_id` | `PhraseRecord.phrase_id`, `Phrase.phrase_id` | Per-phrase within a variation | In-memory only | Not stable — new UUID each variation, even for "the same" musical region | Musical (represents a reviewable change group) | **Low under current scheme** — ephemeral identity. Would need content-addressing or positional stability for lineage. |
| `region_id` | `EntityRegistry`, DAW region | Per-region | DAW: persistent. EntityRegistry: ephemeral (re-synced). | Stable if DAW preserves region IDs. Server-generated IDs persist via DAW round-trip (see `fe-project-state-sync.md`). | Musical (container for notes at a position) | **High** — regions are the granularity at which Muse computes diffs |
| `track_id` | `EntityRegistry`, DAW track | Per-track | DAW: persistent. EntityRegistry: ephemeral. | Stable if DAW preserves track IDs. | Musical (instrument/voice identity) | **High** — tracks are stable containers |
| `state_version` / `base_state_id` | `StateStore._version` | Per-process lifetime | In-memory monotonic int | Resets to 0 on process restart | Neither — it is a concurrency token | **Medium** — would need to become a persistent, monotonic counter or a content hash to serve as commit identity |
| `composition_id` | `CompositionState.composition_id` | Per-composition session | In-memory | Stable within a multi-section generation session | Musical (groups sections of one generation) | **Medium** — could link to Orpheus generation provenance |
| `trace_id` | `TraceContext.trace_id` | Per-request | Logged only | Not stable | Neither — observability only | **None** |
| `contract_hash` | `SectionContract.contract_hash` etc. | Per-generation | In-memory (verified at execution) | Deterministic given same inputs | Neither — integrity verification | **High** — already forms a lineage chain (CompositionContract → InstrumentContract → SectionContract), but is not persisted |

### Lineage Graph Potential

The IDs that could form a future commit lineage graph:

```
project_id (root)
  └── base_state_id (commit sequence — needs persistence)
        └── variation_id (proposal — needs persistence)
              └── phrase_id (change unit — needs stability)
                    └── region_id + track_id (content location — already stable via DAW)
```

The missing link: `base_state_id` is ephemeral. A persistent Muse would need either:
- A content-addressed state hash (hash of all region notes at a point in time), or
- A persistent monotonic counter that survives restarts and is shared between Maestro and Muse

The contract lineage chain (`contract_hash`) is a second, parallel lineage system that tracks *how* content was generated (which agent, which section, which contract parameters). If persisted, it would provide generation provenance distinct from content history.

---

## 5. Variation → Commit Semantics Mapping

### Lifecycle

The current variation lifecycle maps to version-control concepts as follows:

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  1. COMPOSING intent classified                                  │
│     → Equivalent to: `git checkout -b proposal-branch`           │
│     → But: no branch is created. No base snapshot is persisted.  │
│                                                                 │
│  2. Maestro generates proposed notes into StateStore             │
│     → Equivalent to: editing files in working tree               │
│     → But: edits happen in the same StateStore as the base,      │
│       not a separate working copy.                               │
│                                                                 │
│  3. VariationService.compute_variation(base, proposed)           │
│     → Equivalent to: `git diff HEAD`                             │
│     → Produces: Variation with Phrases (hunks)                   │
│                                                                 │
│  4. SSE streams meta → phrase* → done                            │
│     → Equivalent to: showing a diff in a code review UI          │
│                                                                 │
│  5. User reviews and selects phrases to accept                   │
│     → Equivalent to: interactive staging (`git add -p`)          │
│                                                                 │
│  6. POST /variation/commit with accepted_phrase_ids              │
│     → Equivalent to: `git commit` with selected hunks            │
│     → But: there is no persistent commit object.                 │
│       base_state_id increments but the old state is gone.        │
│     → Returns: updated_regions (full snapshot of affected        │
│       regions after commit — equivalent to the new HEAD)         │
│                                                                 │
│  7. DAW applies updated_regions                                  │
│     → Equivalent to: deploying the committed change              │
│     → The DAW becomes the new canonical state.                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### What a Variation Really Is Today

A Variation is an **ephemeral, squash-merged pull request** that:
- Exists only in memory
- Contains complete before/after snapshots of changed notes (not true deltas — `NoteChange` stores full `MidiNoteSnapshot` objects)
- Is organized into independently reviewable hunks (Phrases)
- Can be partially accepted (individual phrases)
- Creates no persistent record after acceptance
- Is deleted after discard
- Has no parent-child relationship with other variations

### Why It Is Not Yet a Commit

| Property of a commit | Variation today |
|---------------------|-----------------|
| Persistent | No — in-memory only |
| Has a unique, stable hash | No — UUID, not content-addressed |
| Points to parent commit | No — `base_state_id` is an ephemeral counter |
| Contains a complete snapshot or delta | Contains both (NoteChange has before/after), but only for changed notes, not full project state |
| Can be replayed | No — no mechanism to reconstruct state from variation history |
| Has metadata (author, timestamp, message) | Partially — has `intent`, `ai_explanation`, `created_at`, but no author concept |
| Supports merge | No — `base_state_id` mismatch rejects; no rebase or merge |

### What Would Make It a Commit

A Variation becomes a commit when:
1. It is persisted to durable storage with a content-addressed or sequence-stable ID.
2. It points to a parent state (the `base_state_id` it was computed against).
3. The base state is also persisted (either as a snapshot or as a chain of deltas from genesis).
4. Discarded variations can optionally be retained (for history, not just accepted ones).
5. The commit graph forms a DAG (supporting future branching/merging).

---

## 6. Grammar & Semantic Change Model

### Change Taxonomy

#### Versioned Changes (Flow Through Muse)

These changes are computed as `NoteChange` objects and reviewed as Phrases within Variations:

| Change Type | `NoteChange` representation | Example |
|-------------|---------------------------|---------|
| Note insertion | `change_type="added"`, `before=None`, `after=snapshot` | Adding a bass note at beat 4 |
| Note deletion | `change_type="removed"`, `before=snapshot`, `after=None` | Removing a passing tone |
| Note modification (pitch) | `change_type="modified"`, both snapshots, `after.pitch != before.pitch` | Transposing E to Eb (major → minor) |
| Note modification (timing) | `change_type="modified"`, `after.start_beat` or `after.duration_beats` differs | Syncopation, quantization |
| Note modification (velocity) | `change_type="modified"`, `after.velocity != before.velocity` | Dynamic changes |
| CC insertion | `controller_changes` with `kind="cc"` | Adding sustain pedal events |
| Pitch bend insertion | `controller_changes` with `kind="pitch_bend"` | Adding pitch bend expression |
| Aftertouch insertion | `controller_changes` with `kind="aftertouch"` | Adding pressure expression |

Tags computed from changes: `pitchChange`, `rhythmChange`, `velocityChange`, `harmonyChange`, `scaleChange`, `densityChange`, `registerChange`, `articulationChange`.

#### Non-Versioned Changes (Bypass Muse)

These changes are executed as EDITING-mode tool calls, applied directly to the DAW:

| Change Type | Tool call | Why it bypasses Muse |
|-------------|-----------|---------------------|
| Tempo change | `stori_set_tempo` | Project-level metadata, not note content |
| Key change | `stori_set_key` | Project-level metadata |
| Track creation | `stori_add_midi_track` | Structural, no diff target |
| Track property edits | `stori_set_track_volume`, `_pan`, `_name`, `_color`, `_icon` | Mixing parameters, not musical content |
| Track program change | `stori_set_midi_program` | Instrumentation, not note data |
| Mute/solo | `stori_mute_track`, `stori_solo_track` | Playback state |
| Region creation | `stori_add_midi_region` | Structural container, not content |
| Region move | `stori_move_region` | Arrangement, not note content |
| Region delete | `stori_delete_region` | Destructive structural edit |
| Region duplicate | `stori_duplicate_region` | Structural |
| Note quantization | `stori_quantize_notes` | DAW-side operation, not diffable |
| Swing application | `stori_apply_swing` | DAW-side operation |
| Effect insertion | `stori_add_insert_effect` | Mix/routing, not musical content |
| Send/bus creation | `stori_add_send`, `stori_ensure_bus` | Routing |
| Automation | `stori_add_automation` | Continuous parameter curves, not discrete note events |
| Playback control | `stori_play`, `stori_stop`, `stori_set_playhead` | Transport, ephemeral |
| UI operations | `stori_show_panel`, `stori_set_zoom` | View state |

### Architectural Risk: Two Change Grammars

The system has a **split grammar problem**: musical mutations take two fundamentally different paths depending on intent classification.

**Path A (COMPOSING → Muse):** Notes and expressive MIDI data are proposed, reviewed, and committed through the variation protocol. Changes are structured, diffable, and partially reversible.

**Path B (EDITING → Direct):** Everything else is applied immediately, with no proposal, no review, and no structured record. The DAW's native undo stack is the only safety net.

**Consequences:**

1. **Incomplete history.** Even if Muse becomes persistent, the commit log would only capture Path A changes. A composition's tempo changes, track additions, arrangement edits, and effect chain configuration would have no lineage.

2. **Inconsistent safety model.** A destructive edit like `stori_delete_region` bypasses Muse entirely — it is applied immediately with no confirmation UI. Meanwhile, an additive note insertion ("add a pad") goes through full variation review.

3. **Grammar mismatch.** When a user says "make that more jazzy," the resulting changes might include both Path A operations (note pitch modifications → Muse) and Path B operations (tempo change → direct apply, swing quantization → direct apply). The intent is singular but the execution is bifurcated.

4. **Future branching complexity.** If Muse supports branches/variations, Path B changes would not participate. Switching between "interpretations" would only affect notes, not tempo/key/instrumentation — which are often the defining characteristics of a musical variation.

---

## 7. Drift & Working Tree Analysis

### The DAW as Working Tree

In the version-control analogy:

| Concept | Git equivalent | Stori equivalent |
|---------|---------------|------------------|
| Repository | `.git/` directory | No equivalent (Muse is ephemeral) |
| Working tree | Files on disk | DAW project state |
| Index / staging area | `git add` staging | `VariationStore` (for pending proposals) |
| HEAD | Latest commit | `StateStore._version` (ephemeral counter) |
| `git status` | Diff between HEAD and working tree | No equivalent |

### How Drift Occurs

Drift is the divergence between Maestro's `StateStore` (its understanding of the DAW's state) and the DAW's actual canonical state.

```
Timeline:
  t₀  Request arrives. sync_from_client() aligns StateStore with DAW.
      StateStore == DAW ✓

  t₁  Maestro generates notes, adds to StateStore.
      StateStore contains proposed notes that DAW does not have.
      StateStore ≠ DAW (intentional — proposal in progress)

  t₂  Variation committed. updated_regions sent to DAW.
      DAW applies changes.
      StateStore == DAW ✓ (assuming DAW applied correctly)

  t₃  User manually edits notes in the DAW GUI.
      StateStore still reflects t₂ state.
      StateStore ≠ DAW (drift)

  t₄  Next request arrives. sync_from_client() re-aligns.
      StateStore == DAW ✓ (drift resolved by full replacement)
```

Drift is invisible between `t₃` and `t₄`. If a variation was in-flight during `t₃`, it was computed against stale state.

### Why `sync_from_client()` Is Not `git status`

| `git status` | `sync_from_client()` |
|-------------|---------------------|
| Compares HEAD (immutable) against working tree (mutable) | Replaces the entire shadow with the client's snapshot |
| Detects additions, modifications, deletions | No detection — pure overwrite |
| Preserves HEAD — you can always compare | Destroys previous state — no before/after |
| Non-destructive (read-only comparison) | Destructive (clears and rebuilds registry and notes) |
| Runs on demand | Runs once at request start |

`sync_from_client()` is closer to `git checkout --force` than `git status` — it discards local state and replaces it with the remote.

### Invariants That Break Under Drift

1. **Variation base state consistency.** If a variation was computed at `t₂` but the user edited notes at `t₃`, the variation's `NoteChange.before` snapshots no longer match the DAW's actual notes. Committing the variation would produce `updated_regions` based on stale data.

2. **Entity registry coherence.** If the user deletes a track in the DAW between requests, `StateStore` still references it until the next `sync_from_client()`. Tool calls targeting that track would produce `toolError` events.

3. **`base_state_id` semantics.** The optimistic concurrency check (`base_state_id` must match) protects against concurrent Maestro changes, but not against DAW-side changes. A user could edit notes in the DAW, then commit a Muse variation, and the `base_state_id` check would pass because Maestro's counter didn't change.

---

## 8. Coupling Matrix

### Layer Coupling Table

| Layer | Knows About | Should Not Know About | Violation Severity | Why It Matters |
|-------|-------------|----------------------|-------------------|----------------|
| **L1 Orchestration** | L2 (planner), L3 (editing), L4 (StateStore), L5 (variation concepts), L6 (SSE) | L5 internal models, L7 Orpheus details | Medium | Orchestration should route, not construct variation models or configure state stores |
| **L2 Planning** | L3 (agents), L4 (StateStore for track checks), section parsing | L5 (variations), L6 (SSE details), L7 (Orpheus internals) | Low | Planning is currently well-bounded via frozen contracts |
| **L3 Execution** | L4 (StateStore), L5 (VariationService), L6 (SSE/tools), L7 (Orpheus) | L1 (orchestration decisions), L2 (planning internals) | High | Execution is the densest coupling point — it touches every layer |
| **L4 State Projection** | EntityRegistry (internal) | L5 (variations), L6 (DAW specifics), L7 (Orpheus) | Low | State projection is relatively clean — it is imported by others, not the importer |
| **L5 Muse** | L4 (StateStore — for base notes and commit writes) | L1, L2, L3 implementation, L6 (DAW tool schemas), L7 (Orpheus) | High | Muse should receive structured inputs and produce structured outputs without reaching into StateStore |
| **L6 MCP/DAW** | L4 (StateStore for project cache), L7 (Orpheus for server-side tools) | L1, L2, L3, L5 internal logic | Medium | MCP tools should be a pure schema layer — but server-side generation tools create implicit coupling to L7 |
| **L7 Orpheus** | DAW tool call schema (via `generate_tool_calls()`) | L1–L5 internal logic, StateStore, VariationStore | Medium | Orpheus produces DAW tool calls directly, embedding L6 schema knowledge in the generation service |

### Three Strongest Coupling Violations

**1. `VariationStore` ↔ `StateStore` via `conversation_id`**

```
app/variation/storage/variation_store.py:
    VariationRecord.conversation_id: str = ""
    # "The StateStore conversation_id from the compose phase. Stored so that
    # commit can look up the same store..."

app/api/routes/variation/commit.py:
    store = get_or_create_store(record.conversation_id)
    # Reaches directly into StateStore to read region notes
```

The Muse layer (L5) stores a key into Maestro's state management layer (L4) so it can reach across the boundary at commit time. This makes `VariationStore` structurally dependent on `StateStore`'s keying strategy. If Muse were extracted to a separate service, this lookup would break.

**2. `maestro_handlers.py` importing Variation domain models**

```python
from app.core.maestro_composing import (
    _handle_composing,
    _handle_composing_with_agent_teams,
)
```

The orchestration layer (L1) delegates to `_handle_composing_with_agent_teams`, which internally constructs `Variation` objects, calls `VariationService`, and manages `VariationStore` records. This means L1 transitively depends on the entire Muse domain model. The orchestration layer should route to Muse through a boundary, not embed it.

**3. Orpheus producing DAW tool calls**

```python
# orpheus-music/music_service.py
def generate_tool_calls(notes_by_channel, ...):
    tool_calls.append({"tool": "addMidiTrack", ...})
    tool_calls.append({"tool": "addMidiRegion", ...})
    tool_calls.append({"tool": "addNotes", ...})
```

The generation layer (L7) produces output formatted as DAW integration layer (L6) tool calls. This means changing the MCP tool schema requires coordinating with Orpheus. If Orpheus returned a neutral format (e.g., a note list with instrument metadata), the tool call generation could happen in L3 or L6, keeping Orpheus schema-agnostic.

---

## 9. Open Architecture Questions

These questions must be answered before Muse can become a persistent history engine. They are listed without answers.

### State Authority

1. Should Muse own `state_version` / `base_state_id`, replacing StateStore as the authority?
2. Should StateStore be downgraded to a read-through cache of Muse's persisted state?
3. If Muse owns state, does the DAW become a renderer rather than the canonical source?
4. What is the source of truth during a request: the DAW's project snapshot, Muse's last commit, or a merge of both?

### Identity

5. Should phrase IDs be content-addressed (hash of `track_id + region_id + start_beat + end_beat + note_hashes`) or identity-based (UUID with history)?
6. Should `region_id` be the anchor for content lineage, or should there be a separate "content address" below region?
7. Should variations that are discarded be retained in the history graph (for "why did Muse suggest this" analytics)?
8. Should `composition_id` (currently scoped to Orpheus session) be elevated to a first-class Muse concept?

### Change Grammar

9. Should EDITING-mode operations (tempo, key, track creation) flow through Muse as "structural changes" alongside note changes?
10. If yes, what is the grammar for non-note changes? (A tempo change is not a `NoteChange` — it needs a new change type.)
11. Should `stori_quantize_notes` and `stori_apply_swing` produce Muse-visible diffs instead of being opaque DAW-side operations?
12. Should automation curves be versioned? They are continuous, not discrete — different diffing semantics.

### Persistence

13. What storage backend for Muse's commit history? (PostgreSQL JSONB, SQLite per-project, custom binary format, content-addressed object store?)
14. Should full project snapshots be stored at each commit, or should the system be delta-only with snapshot checkpoints?
15. What is the maximum acceptable commit history size before garbage collection / compaction?
16. Should the commit history be exportable (e.g., as a `.stori-history` file alongside the DAW project)?

### Concurrency

17. Should Muse support concurrent variations on the same project? (Requires branching or locking.)
18. If multiple variations are in flight, what is the merge strategy?
19. Should optimistic concurrency (`base_state_id` check) be replaced with or augmented by content-hash-based conflict detection?

### DAW Boundary

20. Should the DAW report state changes back to Maestro (push model) instead of Maestro polling via `sync_from_client()` (pull model)?
21. If Muse becomes persistent, should the DAW be required to apply changes through Muse's commit interface (making Muse the write path), or should the DAW remain independently mutable with Muse reconciling drift?
22. Should there be a formal "working tree status" API that compares the DAW's current state against Muse's last commit?

---

## 10. Risks to Open-Source Evolution

### Hidden Architectural Assumptions

1. **Single-process assumption.** Muse exists as a library within Maestro's process. All "contracts" are Python function calls. An external contributor might assume Muse is a service from the documentation, then discover it shares memory with Maestro.

2. **DAW-is-truth assumption.** The system assumes the DAW is always right. `sync_from_client()` overwrites Maestro's state unconditionally. This works for a single-user desktop app but fails for collaborative editing, server-side rendering, or headless operation.

3. **Single-tempo assumption.** The entire system models tempo, key, and time signature as single values. Music commonly involves tempo changes, key modulations, and meter changes. The beat-based time model is correct, but the data model only supports one tempo/key/time signature per project.

4. **In-memory-everything assumption.** StateStore, VariationStore, EntityRegistry, and CompositionState are all in-memory. A contributor adding a feature that depends on cross-request state (e.g., "show me the last three variations") would find no persistence layer to query.

### Naming Divergences

| Name in code | What it actually does | Potential confusion |
|-------------|----------------------|-------------------|
| `StateStore` | Ephemeral in-memory shadow cache of DAW state | Sounds persistent. "Store" implies durability. |
| `VariationStore` | In-memory dict of active variation proposals | Same — "Store" implies persistence. |
| `EntityRegistry` | Name→ID lookup table rebuilt each request | "Registry" implies a durable service registry. |
| `base_state_id` | Monotonic in-memory counter | Sounds like a commit hash or version identifier. Resets on restart. |
| `commit` (endpoint) | Apply accepted phrases to StateStore + return updated regions | "Commit" implies persistence. Nothing is durably committed. |
| `conversation_id` as StateStore key | Identifies a state session | A "conversation" and a "state session" are conceptually different. Multiple conversations can share a project. |
| `stori_generate_midi` (MCP tool) | Server-side generation via Orpheus | Listed alongside DAW tools but never reaches the DAW. |

### Implicit Contracts

1. **Note dict format.** Notes flow as untyped `dict[str, Any]` through StateStore, VariationService, Orpheus, and tool calls. The schema is implicit: `{pitch, start_beat, duration_beats, velocity, channel}`. There is no shared schema definition that all layers import. Muse's `MidiNoteSnapshot` Pydantic model is the closest, but StateStore stores raw dicts.

2. **Region-relative vs absolute beats.** Phrase `start_beat`/`end_beat` are absolute project positions. Note `start_beat` inside `NoteChange` is region-relative. This convention is documented in the spec but not enforced by types. A contributor unfamiliar with the convention could easily produce absolute note positions.

3. **Wire format casing.** Python internals use `snake_case`. Wire format uses `camelCase`. `CamelModel` handles serialization, but internal code must use `snake_case` field names. The `_normalize_note()` function in StateStore handles `startBeat` → `start_beat` conversion, but only for two specific fields — it is not a general normalizer.

4. **Tool call parameter naming.** MCP tool parameters use `camelCase` (`regionId`, `trackId`). The entity resolution layer expects these specific keys. But Orpheus produces tool calls with different key names (`addMidiTrack` vs `stori_add_midi_track`). The boundary between Orpheus tool call format and Maestro tool call format is undocumented.

### Potential Confusion for External Teams

1. **"Where is Muse?"** A team reading the terminology doc would expect a standalone subsystem called Muse. They would find: `app/variation/` (infrastructure), `app/services/variation/` (diff engine), `app/models/variation.py` (data models), and scattered references in `app/core/maestro_composing/`. There is no `app/muse/` package, no `MuseService` class, and no clear entry point.

2. **"How do I add a new tool?"** Adding a new MCP tool requires: defining it in `app/mcp/tools/`, adding it to the tool registry, handling it in the execution layer (L3), potentially adding StateStore mutations (L4), and updating documentation. There is no generator or checklist. The tool categories (project, track, region, notes, etc.) are implicit groupings, not enforced by an architecture.

3. **"How do I run Muse without a DAW?"** The answer is: you can compute variations and commit them, but the commit just updates StateStore. There is no way to persist the result or export it. The system assumes a DAW is consuming the `updated_regions` response. A headless mode would require Muse to be self-sufficient as a state owner.

4. **"What is the API contract?"** The OpenAPI schema is auto-generated and only available in debug mode. There is no committed API spec file. The closest thing is `docs/contracts/stori-maestro-contract.md`, which describes the contract in prose. A team integrating with Stori would need to read the code to understand exact payload shapes.

5. **"What happens if I change a Pydantic model?"** There is no schema migration strategy for in-memory models. Changing `Phrase` or `NoteChange` fields is a breaking change for any connected frontend, but there is no versioning mechanism on these models. The protocol hash (`compute_protocol_hash()`) detects SSE event schema drift but does not cover variation model changes.

---

## Appendix: File Map

For reference, the key files in each layer:

```
L1 Orchestration
  app/core/maestro_handlers.py
  app/core/intent/routing.py, detection.py, models.py, patterns.py, structured.py

L2 Planning
  app/core/planner/
  app/core/maestro_agent_teams/coordinator.py
  app/core/maestro_agent_teams/contracts.py
  app/core/maestro_agent_teams/sections.py

L3 Execution
  app/core/maestro_editing/tool_execution.py
  app/core/maestro_agent_teams/agent.py
  app/core/maestro_agent_teams/section_agent.py
  app/core/executor/
  app/core/tool_validation/

L4 State Projection
  app/core/state_store.py
  app/core/entity_registry.py

L5 Variation / Muse
  app/models/variation.py
  app/services/variation/service.py
  app/services/variation/note_matching.py
  app/services/variation/labels.py
  app/variation/core/state_machine.py
  app/variation/core/event_envelope.py
  app/variation/storage/variation_store.py
  app/variation/streaming/sse_broadcaster.py
  app/variation/streaming/stream_router.py
  app/api/routes/variation/

L6 MCP / DAW Integration
  app/mcp/server.py
  app/mcp/stdio_server.py
  app/mcp/tools/*.py
  app/protocol/events.py
  app/protocol/emitter.py
  app/protocol/version.py
  app/protocol/validation.py

L7 Generation (Orpheus)
  app/services/orpheus.py
  app/services/music_generator.py
  app/services/backends/
  orpheus-music/music_service.py (separate process)
```
