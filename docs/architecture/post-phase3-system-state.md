# Post-Phase-3 System State Report

Produced: 2026-02-24
Scope: Maestro backend after Phase 3 boundary hardening
Audience: Senior backend engineers, future OSS contributors

---

## 1. Current Layer Map

```
┌─────────────────────────────────────────────────────────────────┐
│  API Routes                                                     │
│  app/api/routes/variation/{commit,propose,stream,retrieve}.py   │
│  app/api/routes/stream.py                                       │
│                        ↓                                        │
├─────────────────────────────────────────────────────────────────┤
│  Maestro Orchestration                                          │
│  app/core/maestro_handlers.py ← entry point                    │
│  app/core/maestro_editing/{handler,tool_execution,continuation} │
│  app/core/maestro_composing/{composing,storage,fallback}        │
│  app/core/maestro_agent_teams/{coordinator,agent,section_agent} │
│                        ↓                                        │
├─────────────────────────────────────────────────────────────────┤
│  Executor                                                       │
│  app/core/executor/variation.py   ← execute_tools_for_variation │
│  app/core/executor/execution.py   ← _execute_single_call       │
│  app/core/executor/apply.py       ← apply_variation_phrases     │
│  app/core/executor/snapshots.py   ← capture_*_snapshot          │
│  app/core/executor/models.py      ← VariationContext et al.     │
│                        ↓                                        │
├─────────────────────────────────────────────────────────────────┤
│  Muse (Variation Computation)                                   │
│  app/core/executor/variation.py   ← compute_variation_from_ctx  │
│  app/services/variation/service.py  ← VariationService          │
│  app/services/variation/note_matching.py                        │
│  app/services/variation/labels.py                               │
│  app/variation/storage/             ← VariationStore            │
│  app/variation/core/state_machine.py                            │
│                        ↓                                        │
├─────────────────────────────────────────────────────────────────┤
│  StateStore (Working Tree)                                      │
│  app/core/state_store.py          ← mutable per-session state   │
│  app/core/entity_registry.py      ← name→ID resolution          │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Services                                                       │
│  app/services/orpheus.py          ← Orpheus HTTP client + adapter│
│  app/services/backends/orpheus.py ← OrpheusBackend.generate     │
│  app/services/music_generator.py  ← facade over backends        │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  MCP Adapters                                                   │
│  app/mcp/tools/{registry,generation,...}.py ← tool definitions  │
│  app/mcp/stdio_server.py          ← MCP stdio transport         │
│  app/mcp/server.py                ← MCP HTTP transport           │
└─────────────────────────────────────────────────────────────────┘
```

### Allowed dependencies

| Source | May depend on |
|--------|---------------|
| API Routes | Core (handlers, executor, state_store), Models, Variation storage |
| Maestro Orchestration | Executor, StateStore, LLM client, MCP tools (read-only) |
| Executor | StateStore, Models, Services (music_generator), Tracing |
| Muse (compute_variation_from_context) | VariationService only — no StateStore, no EntityRegistry |
| VariationService | Models (Variation, Phrase, NoteChange) only |
| StateStore | EntityRegistry only |
| Orpheus client | Config only |

### Forbidden dependencies (enforced by Phase 3)

| Source | Must NOT depend on |
|--------|--------------------|
| `compute_variation_from_context` | StateStore, EntityRegistry, get_or_create_store |
| `VariationService` | Executor, StateStore, EntityRegistry |
| `_store_variation` | StateStore, EntityRegistry (receives data as params) |
| `apply_variation_phrases` | get_or_create_store (receives store as param) |
| StateStore | VariationStore, VariationService |

---

## 2. Maestro ↔ Muse Contract — As Implemented

### Snapshot capture points

Snapshots are captured at two locations, both in `app/core/maestro_composing/composing.py`:

1. **Before agent-team execution** (line ~413):
   `capture_base_snapshot(store)` → freezes all region data (notes, CC, pitch bends, aftertouch) via `deepcopy`.

2. **After agent-team execution** (line ~449):
   `capture_proposed_snapshot(store)` → freezes post-execution state.

For the single-instrument composing path, `execute_tools_for_variation()` internally captures base notes in `_extract_notes_from_project()` and proposed notes via `_process_call_for_variation()` into a `VariationContext`. The `VariationContext` itself is the snapshot boundary.

### Functions that receive snapshots

```python
# Muse computation — keyword-only, no store access
def compute_variation_from_context(
    *,
    base_notes: dict[str, list[dict[str, Any]]],
    proposed_notes: dict[str, list[dict[str, Any]]],
    track_regions: dict[str, str],
    proposed_cc: dict[str, list[dict[str, Any]]],
    proposed_pitch_bends: dict[str, list[dict[str, Any]]],
    proposed_aftertouch: dict[str, list[dict[str, Any]]],
    region_start_beats: dict[str, float],
    intent: str,
    explanation: Optional[str] = None,
) -> Variation
```

```python
# Variation storage — receives metadata as params, no store access
def _store_variation(
    variation: Any,
    project_context: dict[str, Any],
    base_state_id: str,
    conversation_id: str,
    region_metadata: dict[str, RegionMeta],
) -> None
```

### Parameters that cross the boundary

| Parameter | Type | Source | Consumer |
|-----------|------|--------|----------|
| `base_notes` | `dict[str, list[dict]]` | `VariationContext.base_notes` | `compute_variation_from_context` |
| `proposed_notes` | `dict[str, list[dict]]` | `VariationContext.proposed_notes` | `compute_variation_from_context` |
| `track_regions` | `dict[str, str]` | `VariationContext.track_regions` | `compute_variation_from_context` |
| `proposed_cc/pb/at` | `dict[str, list[dict]]` | `VariationContext` | `compute_variation_from_context` |
| `region_start_beats` | `dict[str, float]` | `_collect_region_start_beats(var_ctx)` | `compute_variation_from_context` |
| `base_state_id` | `str` | `store.get_state_id()` | `_store_variation` |
| `conversation_id` | `str` | `store.conversation_id` | `_store_variation` |
| `region_metadata` | `dict[str, RegionMeta]` | Built from `store.registry` by caller | `_store_variation` |

### Does Muse still depend on StateStore?

**`compute_variation_from_context`**: No. Structurally impossible — keyword-only signature accepts only plain data.

**`VariationService`**: No. Imports only from `app.models.variation` and internal `note_matching`/`labels` modules.

**`_store_variation`**: No. Receives `base_state_id`, `conversation_id`, and `region_metadata` as explicit parameters. No StateStore or EntityRegistry imports.

**`apply_variation_phrases`**: Receives `store` as an explicit parameter. Does not call `get_or_create_store()`. Receives `region_metadata` as an explicit parameter — never accesses `store.registry`.

---

## 3. Snapshot Boundary Verification

### `capture_base_snapshot` call sites

| Caller module | Function | Purpose | Data frozen | Flows to |
|---------------|----------|---------|-------------|----------|
| `app/core/maestro_composing/composing.py:413` | `_handle_composing_with_agent_teams` | Freeze region data before agent-team execution | `_region_notes`, `_region_cc`, `_region_pitch_bends`, `_region_aftertouch` (deepcopy) | `_base_notes` dict → `VariationService.compute_*` |

### `capture_proposed_snapshot` call sites

| Caller module | Function | Purpose | Data frozen | Flows to |
|---------------|----------|---------|-------------|----------|
| `app/core/maestro_composing/composing.py:449` | `_handle_composing_with_agent_teams` | Freeze region data after agent-team execution | Same four dicts (deepcopy) | `_proposed_notes` dict → `VariationService.compute_*` |

### Remaining live StateStore data crossings into Muse

**None.** `apply_variation_phrases` now receives `region_metadata` as an explicit parameter and no longer accesses `store.registry`. The commit route (`commit.py`) extracts region metadata from the registry before calling apply, keeping the boundary clean.

---

## 4. Commit Flow — End-to-End Trace

```
DAW accepts variation
  → POST /api/v1/variation/commit (CommitVariationRequest)
      Layer: API Routes (app/api/routes/variation/commit.py)
      Data: immutable request body

  → Load VariationRecord from VariationStore
      Layer: Variation Storage (app/variation/storage/)
      Data: immutable record lookup

  → Validate state machine transition (can_commit)
      Layer: Variation Core (app/variation/core/state_machine.py)

  → get_or_create_store(conversation_id=project_id)
      Layer: StateStore (app/core/state_store.py)
      Data: mutable working tree retrieved/created

  → store.check_state_id(base_state_id)
      Concurrency check: rejects if DAW and store are out of sync

  → apply_variation_phrases(variation, accepted_ids, project_state={}, store=store, region_metadata=...)
      Layer: Executor (app/core/executor/apply.py)
      Receives: explicit store param (no get_or_create_store call)

      → store.begin_transaction("Accept Variation")
          Mutations begin (mutable)

      → store.remove_notes() / store.add_notes()
          Per-region note mutations within transaction

      → store.add_cc() / add_pitch_bends() / add_aftertouch()
          Controller data mutations (no transaction — state_store quirk)

      → store.commit(tx)
          Transaction committed atomically

      → Build updated_regions from store.get_region_notes() (deepcopy)
          Post-mutation read — immutable copies returned

      → Return VariationApplyResult
          Data: immutable result with updated_regions

  → Transition VariationRecord to COMMITTED
      Layer: Variation Core (state machine)

  → Build CommitVariationResponse(new_state_id, applied_phrase_ids, updated_regions)
      Layer: API Routes
      Data: immutable HTTP response

  → HTTP 200 JSON response to DAW
      Transport: Direct HTTP (not SSE)
```

### Does commit logic still rely on conversation_id?

**Yes, in one place:** `app/api/routes/variation/commit.py:91-92` calls `get_or_create_store(conversation_id=commit_request.project_id)`. This is used to locate the StateStore instance for mutation. The `conversation_id` here is effectively `project_id` (they're the same value in commit requests).

`apply_variation_phrases` itself no longer calls `get_or_create_store`. The `conversation_id` parameter has been removed. Region metadata is passed explicitly via `region_metadata`.

---

## 5. Execution Layer After Refactor

### `_apply_single_tool_call` — current contract

**Location:** `app/core/maestro_editing/tool_execution.py`

**Inputs:**
```
tc_id: str                              — tool call ID
tc_name: str                            — tool name (e.g. "stori_add_notes")
resolved_args: dict[str, Any]           — pre-validated parameters
allowed_tool_names: set[str]            — allowlist for this execution
store: StateStore                       — mutable working tree
trace: TraceContext                     — distributed tracing
add_notes_failures: dict[str, int]      — circuit-breaker counter (mutable)
emit_sse: bool                          — whether to build SSE event dicts
composition_context: Optional[dict]     — composition metadata
pre_emit_callback: Optional[Callable]   — early SSE flush for generators
```

**Output:** `_ToolCallOutcome` dataclass:
```
enriched_params: dict[str, Any]         — params after entity resolution
tool_result: dict[str, Any]             — execution result
sse_events: list[dict[str, Any]]        — SSE event dicts (toolStart, toolCall, toolError)
msg_call: dict[str, Any]                — assistant message with tool call
msg_result: dict[str, Any]              — tool response message
skipped: bool                           — True if circuit-breaker/validation rejected
extra_tool_calls: list[dict[str, Any]]  — synthetic calls (e.g., icon assignment)
```

**What it no longer does:**
- Does not emit SSE directly — returns events in `sse_events` for the caller
- Does not call `VariationService` or `VariationStore`
- Does not import from `app/core/executor/variation.py`

**What it still does:**
- Entity resolution via `store.registry` (resolve_track, get_latest_region_for_track, find_overlapping_region)
- State mutations via `store` (create_track, create_region, add_notes, add_cc, etc.)
- Music generation via `get_music_generator()` for generator tools

### All callers

| Caller | File | SSE handling |
|--------|------|-------------|
| `_handle_editing` | `app/core/maestro_editing/handler.py:225` | Yields `outcome.sse_events` directly via `yield await sse_event(evt)` |
| `_handle_composing_with_agent_teams` | `app/core/maestro_agent_teams/coordinator.py:123,151,717` | Yields directly |
| `_execute_section_contract` | `app/core/maestro_agent_teams/section_agent.py:285,360,813` | Queues to `sse_queue.put(evt)` with agent tags |
| `_execute_track_setup` / `_execute_content` | `app/core/maestro_agent_teams/agent.py:804,980,1058,1118,1505,1537` | Queues to `sse_queue.put(evt)` with agent tags |

No caller assumes SSE is emitted internally. All consume `outcome.sse_events`.

---

## 6. MCP Tool Boundary (Server vs DAW)

### Tool classification

Tools are classified via the `server_side` flag in their definition dicts:

```python
# app/mcp/tools/generation.py
{"name": "stori_generate_midi", "server_side": True, ...}
{"name": "stori_generate_drums", "server_side": True, ...}
# ... all 5 generation tools
```

The registry (`app/mcp/tools/registry.py`) dynamically partitions:

```python
SERVER_SIDE_TOOLS: set[str] = {
    tool["name"] for tool in MCP_TOOLS if tool.get("server_side", False)
}
DAW_TOOLS: set[str] = {
    tool["name"] for tool in MCP_TOOLS if not tool.get("server_side", False)
}
```

### Where normalization occurs

Orpheus response normalization happens in `app/services/orpheus.py:normalize_orpheus_tool_calls()`. This is the adapter boundary: Orpheus returns DAW-style tool names (`addNotes`, `addMidiCC`, `addPitchBend`, `addAftertouch`), and the normalizer translates them to flat data lists (`notes`, `cc_events`, `pitch_bends`, `aftertouch`).

`app/services/backends/orpheus.py:OrpheusBackend.generate()` calls `normalize_orpheus_tool_calls()` and returns a `GenerationResult` with the normalized data.

### Tool-call format transitions

```
Orpheus HF Space → {"tool_calls": [{"tool": "addNotes", "params": {...}}]}
                          ↓
normalize_orpheus_tool_calls() → {"notes": [...], "cc_events": [...], ...}
                          ↓
GenerationResult → flat lists (notes, cc_events, pitch_bends, aftertouch)
                          ↓
_apply_single_tool_call or _process_call_for_variation → StateStore mutations + SSE events
                          ↓
SSE to DAW → {"type": "toolCall", "tool": "stori_add_notes", "params": {...}}
```

### Does Orpheus still leak DAW schema knowledge into Maestro?

**Yes, in one place:** The `normalize_orpheus_tool_calls()` function in `app/services/orpheus.py` hardcodes the DAW tool names (`addNotes`, `addMidiCC`, `addPitchBend`, `addAftertouch`) as string constants. These are Orpheus-internal names (not Stori MCP tool names), but they mirror the DAW's schema.

This is contained — only `normalize_orpheus_tool_calls` knows about these names, and it outputs plain data dicts. No downstream code sees Orpheus tool names.

---

## 7. StateStore — True Current Role

### Allowed responsibilities (code-backed)

- **Mutable working tree**: Tracks, regions, notes, buses, CC, pitch bends, aftertouch — mutated during tool execution.
- **Sync from DAW**: `sync_from_client(project_state)` — hydrates from client-provided project state.
- **Entity resolution**: Via `EntityRegistry` — name→ID lookup for tracks, regions, buses.
- **Versioned state**: `get_state_id()` returns monotonic integer for optimistic concurrency.
- **Transactions**: `begin_transaction()` / `commit()` / rollback for atomic plan execution.
- **Snapshot source**: `get_region_notes()`, `get_region_cc()`, etc. return `deepcopy` — safe for consumers.
- **Event log**: Append-only mutation history for debugging.

### Removed responsibilities (Phase 3)

- **Direct access by Muse commit**: `apply_variation_phrases` no longer calls `get_or_create_store()` — receives store as explicit param. No longer accesses `store.registry` — receives `region_metadata` as explicit param. `conversation_id` parameter removed.
- **Direct access by `_store_variation`**: No longer reads `store.registry` or `store.conversation_id` — receives them as params.
- **Muse variation computation**: `compute_variation_from_context` has zero store access.

### Still ambiguous responsibilities

- **Source of truth for commit**: `app/api/routes/variation/commit.py` calls `get_or_create_store()` and uses the store as the authority for applying mutations. This is correct for the working tree model but means the commit route owns the Maestro→store lifecycle.
- **Post-commit region reads**: `apply_variation_phrases` reads `store.get_region_notes()` after mutations to build `updated_regions`. This reads post-mutation state from the working tree (the store IS the authority during commit).
- **Session persistence**: StateStore instances are kept in a module-level `_stores` dict keyed by `conversation_id`. The lifecycle (creation, eviction, sharing across requests) is implicit.

### Where StateStore still acts like a source of truth

1. **Commit route** (`commit.py:91`): `get_or_create_store(conversation_id=project_id)` — if no store exists, one is created from scratch. The commit then applies mutations to a potentially empty store. In practice, the store was populated during the preceding propose/compose step.

2. **`apply_variation_phrases`** (`apply.py`): After committing mutations, reads `store.get_region_notes(rid)` to build the response payload. The store is the authority for post-mutation note state. Region metadata (start_beat, duration_beats, name) is provided externally via `region_metadata`.

3. **Entity resolution during execution**: `_apply_single_tool_call` and `_process_call_for_variation` use `store.registry` to resolve names → IDs and to find/create entities. The registry IS the authority during execution.

---

## 8. Remaining Coupling — Critical Section

### 1. ~~`apply_variation_phrases` → `store.registry.get_region()`~~ — RESOLVED

Region metadata is now passed as an explicit `region_metadata` parameter. The `conversation_id` parameter has been removed. `apply_variation_phrases` no longer accesses `store.registry`. Enforced by `scripts/check_boundaries.py` and `tests/test_boundary_seal.py`.

### 2. `_process_call_for_variation` → `var_ctx.store.registry.*`

**Coupling:** 15+ calls to `store.registry` for track/region resolution, entity creation, and fallback lookups during variation mode execution.
**Why it exists:** Variation execution simulates the same tool dispatch as streaming execution, which inherently requires entity resolution against the working tree.
**Risk level:** MEDIUM — this is Maestro orchestration (not Muse), so it's architecturally correct. But it means `VariationContext` carries a live `store` reference.
**Blocks future persistence:** No — this is tool execution, not diff computation.
**Blocks Swift extraction:** No.

### 3. `VariationContext.store` field

**Coupling:** `VariationContext` dataclass holds a `StateStore` reference. Any function receiving a `VariationContext` can reach into the store.
**Why it exists:** Tool dispatch during `execute_tools_for_variation` needs the store for entity resolution and note recording.
**Risk level:** MEDIUM — the field is used correctly (only during tool dispatch), but the type system doesn't prevent misuse.
**Blocks future persistence:** No.
**Blocks Swift extraction:** No.

### 4. `conversation_id` as StateStore key

**Coupling:** Module-level `_stores: dict[str, StateStore]` in `state_store.py` uses `conversation_id` as the lookup key. `get_or_create_store()` creates or retrieves by this key.
**Why it exists:** Multiple requests in the same conversation share state (DAW sync persists across LLM turns).
**Risk level:** LOW — standard session pattern.
**Blocks future persistence:** Yes — a persistent Muse would need its own identity model, not conversation-scoped sessions.
**Blocks Swift extraction:** No.

### 5. `VariationStore` → `conversation_id` in `VariationRecord`

**Coupling:** `_store_variation` writes `conversation_id` into the `VariationRecord`. The commit route uses `project_id` (passed as `conversation_id`) to look up the StateStore.
**Why it exists:** The commit route needs to find the right StateStore to apply mutations. `conversation_id` is the implicit join key between VariationStore and StateStore.
**Risk level:** MEDIUM — this couples variation lifecycle to session lifecycle.
**Blocks future persistence:** Yes — persistent variations would need explicit references, not session keys.
**Blocks Swift extraction:** No.

### 6. Hardcoded tool name strings

**Coupling:** 200+ occurrences of hardcoded tool names (`"stori_add_notes"`, `"stori_add_midi_track"`, `"stori_add_midi_region"`, etc.) across executor, handler, and agent-team modules.
**Why it exists:** Conditional logic for entity resolution, circuit breakers, and phase classification requires knowing which tool is being processed.
**Risk level:** LOW — tool names are stable (API contract with DAW). But refactoring tool names would be painful.
**Blocks future persistence:** No.
**Blocks Swift extraction:** No.

### 7. SSE event shape assumptions in handlers

**Coupling:** Callers of `_apply_single_tool_call` know the structure of `_ToolCallOutcome.sse_events` (dict with `type` key). Agent-team callers tag events with `agentId` and `sectionName` by mutating the returned dicts.
**Why it exists:** Agent teams need to annotate SSE events for the DAW to associate tool calls with specific agents/sections.
**Risk level:** LOW — the tagging pattern is consistent across all callers.
**Blocks future persistence:** No.
**Blocks Swift extraction:** No.

### 8. Execution mode branching in `_handle_editing`

**Coupling:** `_handle_editing` contains two divergent code paths: `execution_mode == "apply"` (streaming tool execution) and `execution_mode == "variation"` (variation proposal). These share the same LLM loop but diverge at execution.
**Why it exists:** The editing handler was built for streaming execution first; variation mode was bolted on with an `if` branch.
**Risk level:** MEDIUM — the branching makes the handler harder to reason about. The variation branch imports `execute_plan_variation` and `_store_variation` lazily.
**Blocks future persistence:** No.
**Blocks Swift extraction:** No.

---

## 9. Architectural Friction Map

### 1. Dual snapshot mechanisms

**Where:** `capture_base_snapshot`/`capture_proposed_snapshot` (agent-team path) vs `_extract_notes_from_project`/`VariationContext` (single-instrument path).
**Why awkward:** Two different mechanisms for the same conceptual operation. The agent-team path uses explicit snapshot functions; the single-instrument path accumulates snapshots incrementally in `VariationContext`.
**Why it exists:** The agent-team path was added later and needed explicit snapshot boundaries because execution happens outside `execute_tools_for_variation`.

### 2. `_collect_region_start_beats` as boundary translation

**Where:** `app/core/executor/variation.py`
**Why awkward:** A small standalone function exists solely to read region metadata from the store and translate it into a dict. This is a seam — correct architecturally, but feels like ceremony.
**Why it exists:** `compute_variation_from_context` must not touch the store, so the metadata extraction had to be pulled out.

### 3. `apply_variation_phrases` dual role

**Where:** `app/core/executor/apply.py`
**Why awkward:** The function both applies mutations (add/remove notes) AND queries post-mutation state (get_region_notes) to build the response. These are conceptually different operations.
**Why it exists:** The commit response needs `updated_regions` with full note state. Reading after write is the simplest approach.

### 4. ~~`conversation_id` parameter in `apply_variation_phrases`~~ — RESOLVED

The `conversation_id` parameter has been removed from `apply_variation_phrases`. This friction point no longer exists.

**Where:** Function signature accepts `conversation_id` but only uses it for logging.
**Why awkward:** The parameter existed for `get_or_create_store()` lookups. Now that the store is passed explicitly, the parameter is vestigial.
### 5. `VariationContext` carries live store

**Where:** `app/core/executor/models.py` — `VariationContext.store: StateStore`
**Why awkward:** The Muse computation boundary is structurally enforced on `compute_variation_from_context` (keyword-only params), but `VariationContext` itself can leak store access to any function that receives it.
**Why it exists:** Tool dispatch needs the store. The context object was designed for the executor, not for Muse.

### 6. Orpheus tool name constants

**Where:** `normalize_orpheus_tool_calls()` hardcodes `"addNotes"`, `"addMidiCC"`, `"addPitchBend"`, `"addAftertouch"`.
**Why awkward:** These are Orpheus-internal names. If Orpheus changes its output format, this function breaks.
**Why it exists:** Orpheus exposes a Gradio API that returns tool-call-shaped dicts. The normalizer is the adapter boundary.

---

## 10. OSS Contributor Mental Model

### What Maestro is (today)

Maestro is the AI orchestration backend for the Stori DAW. It receives natural-language prompts from the DAW (via `POST /api/v1/maestro/stream`) or from Cursor/Claude (via MCP tools), classifies them into intents (REASONING, EDITING, COMPOSING), and dispatches them through an LLM-driven pipeline.

In code: `app/core/maestro_handlers.py` is the entry point. It delegates to `_handle_editing` (tool-call execution) or `_handle_composing` / `_handle_composing_with_agent_teams` (plan→execute→variation).

Maestro owns the mutable working tree (`StateStore`), the LLM conversation, and the SSE stream to the DAW.

### What Muse is (today)

Muse is the variation computation subsystem. It takes a before/after snapshot of musical data and produces a `Variation` — a structured diff organized into `Phrase` objects containing `NoteChange` entries.

In code: `compute_variation_from_context()` in `app/core/executor/variation.py` is the Muse entry point. It delegates to `VariationService` (`app/services/variation/service.py`) which performs note matching and phrase generation. Muse receives only plain data (dicts of notes, CC, pitch bends, etc.) — it has no access to `StateStore` or `EntityRegistry`.

Muse also includes the commit path: `apply_variation_phrases()` applies accepted phrases to the working tree after human approval.

Muse is NOT a separate service or process. It is a set of functions within Maestro with enforced data boundaries.

### What MCP is (today)

MCP (Model Context Protocol) defines the tool vocabulary that LLMs use to describe DAW mutations. Tool definitions live in `app/mcp/tools/` as plain dicts. The `server_side` flag distinguishes tools executed on the Maestro backend (generation tools → Orpheus) from tools forwarded to the DAW (notes, tracks, regions, effects, mixing).

In code: `app/mcp/tools/registry.py` combines all tool category lists into `MCP_TOOLS`, `SERVER_SIDE_TOOLS`, and `DAW_TOOLS`. The MCP server (`app/mcp/stdio_server.py`) uses `SERVER_SIDE_TOOLS` to decide whether to proxy a call to the DAW or handle it locally.

### What StateStore is (today)

StateStore is a per-session, mutable, in-memory working tree that shadows the DAW's project state. It is populated via `sync_from_client(project_state)` at the start of each request, mutated during tool execution, and queried for entity resolution and post-mutation state.

In code: `app/core/state_store.py` contains the `StateStore` class with `EntityRegistry` for name→ID resolution. Instances are keyed by `conversation_id` in a module-level dict. The `get_or_create_store()` function is the primary access point.

StateStore is NOT persistent, NOT a database, and NOT a source of musical history. It is a scratchpad that exists only for the duration of a conversation.

---

## 11. System Confidence Assessment

| Dimension | Score | Notes |
|-----------|-------|-------|
| Boundary clarity | **7/10** | Muse computation boundary is structurally enforced. Commit path and agent-team snapshot boundary are clear. Tool execution still has implicit store coupling via `VariationContext.store`. |
| Layer isolation | **7/10** | VariationService is fully isolated. Executor does not emit SSE. But handler layer has execution-mode branching and lazy imports that blur the editing/composing boundary. |
| Future persistence readiness | **4/10** | `compute_variation_from_context` is persistence-ready (pure data in, Variation out). `apply_variation_phrases` receives all external data as explicit params. But `conversation_id`-keyed sessions, in-memory VariationStore, and implicit store lifecycle would all need replacement. |
| Swift extraction readiness | **6/10** | SSE event shapes are documented. MCP tool names are stable. `_ToolCallOutcome` provides a clean extraction point. But 200+ hardcoded tool name strings and entity resolution embedded in execution make extraction non-trivial. |
| Hidden technical debt | **Medium** | Dual snapshot mechanisms, vestigial `conversation_id` parameter, execution-mode branching in `_handle_editing`, and `VariationContext.store` leak risk are manageable but accumulating. |

### If we froze development today, what architectural risks would remain?

1. **Session-scoped identity**: StateStore and VariationStore are both keyed by `conversation_id`. There is no persistent identity for variations, commits, or musical history. If the process restarts, all state is lost.

2. **Implicit store lifecycle**: `get_or_create_store()` silently creates stores. There is no explicit lifecycle (creation, eviction, serialization). The commit route assumes a store exists from a previous propose/compose step — if it doesn't, it creates an empty one and the commit applies mutations to a blank slate.

3. **`VariationContext.store` reference**: Any code receiving a `VariationContext` can access the live store. The Muse boundary is enforced on `compute_variation_from_context` specifically, but the `VariationContext` dataclass does not enforce it.

4. **Execution-mode branching**: `_handle_editing` serves both streaming execution and variation proposal via an `execution_mode` parameter. This dual-mode design makes the function harder to test, extend, and reason about.

5. **~~No regression boundary for Muse contract~~** — RESOLVED. The "Muse must not access StateStore" rule is now enforced by `scripts/check_boundaries.py` (AST-based CI check) and `tests/test_boundary_seal.py` (unit tests). Adding a `store` parameter to `compute_variation_from_context` or importing `get_or_create_store` in `apply.py` would fail both the boundary checker and the test suite.
