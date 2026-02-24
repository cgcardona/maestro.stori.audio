# Phase 3 — Maestro / Muse / MCP Boundary Hardening

> **Status:** Implementation plan — ready to execute
> **Prerequisite reading:** [`maestro-muse-evolution.md`](maestro-muse-evolution.md), [`muse-persistent-history-bridge.md`](muse-persistent-history-bridge.md)
> **Scope:** Internal refactors only. Zero API changes. Zero SSE changes. Zero frontend changes.
> **Timeline:** 1–2 weeks of focused work
> **Guiding rule:** Every change must leave all tests green and all runtime behavior identical.

---

## 1. Current Violations and Concrete Refactors

### Violation 1 — Muse commit reaches into StateStore via `conversation_id`

**Problem:**
`_store_variation()` in `app/core/maestro_composing/storage.py` saves `store.conversation_id` into `VariationRecord.conversation_id`. Later, `apply_variation_phrases()` in `app/core/executor/apply.py` calls `get_or_create_store(conversation_id=record.conversation_id)` to look up the same StateStore and read/write region notes. The commit endpoint in `app/api/routes/variation/commit.py` does the same with `get_or_create_store(conversation_id=commit_request.project_id)`.

**Why it causes tight coupling:**
Muse's commit logic is structurally dependent on Maestro's state management keying strategy. Muse cannot apply phrases without reaching into `StateStore` by key. If Muse were ever backed by its own persistence, this lookup would produce stale or empty data.

**Minimal change:**
Instead of `apply_variation_phrases` looking up a StateStore by key, make the caller provide the region data that apply needs. The commit endpoint already has access to `project_store` — it should pass the relevant region snapshots into the apply function rather than having apply reach out for them.

Concretely:
1. Add a `region_notes: dict[str, list[dict]]` parameter to `apply_variation_phrases()` — a snapshot of region notes at commit time.
2. The commit endpoint builds this snapshot from the `project_store` it already holds (lines 91–94 of `commit.py`).
3. `apply_variation_phrases()` uses the provided snapshot instead of calling `get_or_create_store()`.
4. The `conversation_id` parameter on `apply_variation_phrases()` becomes optional / unused (deprecate, remove later).
5. `VariationRecord.conversation_id` stays for now (removing it is a separate, smaller follow-up).

**Scope:**
- `app/core/executor/apply.py` — change signature, use provided data
- `app/api/routes/variation/commit.py` — build snapshot, pass it in
- `app/core/maestro_composing/composing.py` — update call sites (if any call `apply_variation_phrases` directly)

**Risk level:** LOW — `apply_variation_phrases` is called from exactly two places (commit endpoint and composing handler). Both already have a StateStore reference.

**Migration strategy:**
1. Add `region_notes` parameter with default `None` (backwards compatible).
2. When `region_notes` is provided, use it. When `None`, fall back to current `get_or_create_store()` lookup.
3. Update callers to pass snapshots.
4. Remove fallback path once all callers are migrated.
5. Run full test suite after each step.

---

### Violation 2 — `_store_variation()` reads StateStore registry for region metadata

**Problem:**
`_store_variation()` in `app/core/maestro_composing/storage.py` (lines 50–54) calls `store.registry.get_region(phrase.region_id)` to look up region metadata (`startBeat`, `durationBeats`, name) and bakes it into `PhraseRecord`. This is Maestro's composing handler reaching into L4 state to enrich L5 records.

**Why it causes tight coupling:**
The variation storage helper depends on `EntityRegistry`'s internal metadata structure (`metadata.get("startBeat")`). This couples the phrase storage format to the registry's camelCase metadata convention.

**Minimal change:**
Capture region metadata at variation *computation* time, not at storage time. `VariationService.compute_variation()` already receives `region_start_beat` as a parameter. The caller (the composing handler) knows the region metadata. Pass a `region_metadata` dict alongside the `Variation` when calling `_store_variation()` instead of having `_store_variation()` reach into the registry.

Concretely:
1. Add a `region_metadata: dict[str, dict]` parameter to `_store_variation()` — mapping `region_id` to `{start_beat, duration_beats, name}`.
2. The composing handler builds this from the StateStore *before* calling `_store_variation()`.
3. `_store_variation()` no longer imports or accesses `store.registry`.

**Scope:**
- `app/core/maestro_composing/storage.py` — change signature, remove registry access
- `app/core/maestro_composing/composing.py` — build region_metadata, pass it

**Risk level:** LOW — purely a parameter injection. Same data, different source.

**Migration strategy:**
1. Add `region_metadata` parameter with default `None`.
2. When provided, use it. When `None`, fall back to current registry lookup.
3. Update caller.
4. Remove fallback.

---

### Violation 3 — Orchestration layer transitively depends on Muse domain models

**Problem:**
`maestro_handlers.py` imports `_handle_composing_with_agent_teams` from `maestro_composing`, which internally constructs `Variation` objects, calls `VariationService`, manages `VariationStore` records, and calls `_store_variation()`. The orchestration layer (L1) is transitively coupled to the entire Muse model hierarchy (`Variation`, `Phrase`, `NoteChange`, `VariationStore`, `VariationStatus`).

**Why it causes tight coupling:**
L1 cannot be tested or reasoned about without the full Muse subsystem. Changing a `Phrase` field requires understanding its impact on orchestration, even though orchestration does not use phrases directly.

**Minimal change:**
This is already partially solved by the sub-module split (`maestro_handlers.py` delegates to `maestro_composing/`). The remaining issue is that `maestro_handlers.py` re-exports symbols from `maestro_composing/` and that the composing module is tightly interleaved with Muse internals.

The actionable step: ensure `maestro_handlers.py` only calls top-level handler functions (`_handle_composing`, `_handle_composing_with_agent_teams`, `_handle_reasoning`) and never imports Muse-specific types (`Variation`, `VariationStore`, `VariationService`).

Concretely: audit `maestro_handlers.py` imports. If any Muse types appear there, they are violations. Today the file is clean — the coupling is in `maestro_composing/`. Mark `maestro_composing/` as the Muse integration boundary and document that constraint.

**Scope:**
- `app/core/maestro_handlers.py` — audit, no changes expected
- `app/core/maestro_composing/` — document as integration boundary

**Risk level:** LOW — audit and documentation, minimal code change.

**Migration strategy:**
1. Add a module-level docstring to `maestro_composing/__init__.py` declaring it as the Maestro↔Muse integration boundary.
2. Add a lint rule or comment convention: "No Muse imports above this layer."

---

### Violation 4 — StateStore serves as both working tree and base state

**Problem:**
During a COMPOSING request, Maestro mutates `StateStore` (adds notes from Orpheus generation) and then reads from the same `StateStore` to compute base state for variation diffing. The object is simultaneously the mutable workspace and the immutable reference.

**Why it causes tight coupling:**
The variation diff depends on temporal ordering within a single request — base notes must be read before mutations begin. This is fragile and prevents future scenarios where base state should come from a persistent source.

**Minimal change:**
Do not change StateStore. Instead, capture the base snapshot *before* execution begins, at the point where `sync_from_client()` completes. The composing handler already operates in a sequence: sync → execute → diff. The change is to explicitly capture the snapshot at the sync→execute boundary and pass it to the diff computation.

Today, `execute_plan_variation()` in `app/core/executor/variation.py` calls `_extract_notes_from_project()` to capture base notes before executing tool calls. This is already the right pattern — it just needs to be treated as an explicit snapshot boundary rather than an implementation detail.

Concretely:
1. Rename `_extract_notes_from_project()` to `capture_base_snapshot()` and make it public.
2. Return a frozen snapshot structure (a simple `dict[str, list[dict]]` mapping `region_id → notes`), not a live reference.
3. Pass this snapshot to `VariationService.compute_variation()` as `base_notes` instead of having the service read from StateStore.

**Scope:**
- `app/core/executor/variation.py` — rename, return frozen snapshot
- Callers of `execute_plan_variation()` — no change (internal refactor)

**Risk level:** LOW — `_extract_notes_from_project` is already called at the right time. This formalizes the boundary.

**Migration strategy:**
1. Create `capture_base_snapshot()` as a wrapper that calls existing `_extract_notes_from_project()` and returns a `deepcopy`.
2. Verify existing tests still pass.
3. Update callers one at a time.

---

### Violation 5 — Orpheus emits DAW tool calls

**Problem:**
`generate_tool_calls()` in `orpheus-music/music_service.py` produces tool calls formatted with DAW tool names (`addMidiTrack`, `addMidiRegion`, `addNotes`, `addMidiCC`, `addPitchBend`). Orpheus embeds knowledge of L6's tool schema.

**Why it causes tight coupling:**
Changing MCP tool names, adding parameters, or restructuring the tool vocabulary requires coordinating with the Orpheus service. Orpheus should return musical content, not DAW commands.

**Minimal change:**
This violation is real but the fix has a larger blast radius than the others (Orpheus is a separate service with its own test suite). The *immediate* boundary hardening step is not to change Orpheus, but to isolate the tool-call-shaping logic on the Maestro side.

Concretely:
1. In the Maestro-side Orpheus client (`app/services/orpheus.py` or a new adapter module), add a `normalize_orpheus_response()` function that translates Orpheus's tool-call format into Maestro's internal format.
2. Today this translation already happens implicitly (Orpheus tool names like `addMidiTrack` are translated to `stori_add_midi_track` somewhere in the pipeline). Consolidate this into a single, explicit adapter function.
3. Document that Orpheus tool-call format is considered internal to the Orpheus service, and Maestro consumes via the adapter.

**Scope:**
- `app/services/orpheus.py` or new `app/services/orpheus_adapter.py`
- No changes to Orpheus itself

**Risk level:** LOW — adding an adapter that formalizes an already-happening translation.

**Migration strategy:**
1. Identify all places where Orpheus response `tool_calls` are consumed.
2. Route them through a single adapter function.
3. Add tests for the adapter.

---

## 2. Maestro ↔ Muse Contract (Implementation Form)

This is the interface that Maestro should call to interact with Muse. Today these operations are scattered across `maestro_composing/`, `executor/apply.py`, `variation/`, and `api/routes/variation/`. The contract defines what should be a clean boundary.

### Interface Definition

```
MuseInterface:

    propose_variation(
        base_snapshot:     dict[str, RegionSnapshot]   # region_id → {notes, cc, pitch_bends, aftertouch}
        proposed_snapshot: dict[str, RegionSnapshot]    # region_id → same shape, after changes
        track_regions:     dict[str, str]               # region_id → track_id
        region_offsets:    dict[str, float]             # region_id → absolute start_beat
        intent:            str                          # user prompt
        explanation:       str | None                   # LLM explanation
        project_id:        str
        base_state_id:     str
    ) → VariationProposal
        # Contains: variation_id, stream_url, phrases (for SSE emission)

    commit_variation(
        variation_id:        str
        accepted_phrase_ids: list[str]
        base_state_id:       str
        region_notes:        dict[str, list[dict]]     # current notes per region (snapshot)
        region_cc:           dict[str, list[dict]]      # current CC per region
        region_pitch_bends:  dict[str, list[dict]]
        region_aftertouch:   dict[str, list[dict]]
    ) → CommitResult
        # Contains: new_state_id, applied_phrase_ids, updated_regions, undo_label

    discard_variation(
        variation_id: str
    ) → None

    get_variation_status(
        variation_id: str
    ) → VariationStatus
```

### What Maestro Provides

Maestro is responsible for:
- Capturing base snapshots from StateStore before execution
- Executing tool calls and collecting proposed state after execution
- Providing region notes to the commit function (from its own StateStore)
- Providing `project_id` and `base_state_id`

### What Muse Returns

Muse returns:
- `VariationProposal` with variation ID, phrases, and SSE stream data
- `CommitResult` with new state ID, applied phrases, and `updated_regions` payloads
- Status information for polling

### What Muse Must NOT Access

After this contract is enforced, Muse must not:
- Call `get_or_create_store()` — StateStore is Maestro's domain
- Read `StateStore._region_notes` — base/proposed notes are provided as parameters
- Access `EntityRegistry` — region metadata is provided by the caller
- Know about `conversation_id` — it receives `project_id` and `base_state_id`

---

## 3. Snapshot Boundary (Immediate Change)

### Rule

> Muse must only receive immutable snapshots — never live StateStore references.

### Where Snapshots Are Captured Today

1. **`_extract_notes_from_project()`** in `app/core/executor/variation.py` — captures base notes before execution. This is the right place.

2. **Post-execution note reading** — after tool calls complete, the composing handler reads `StateStore._region_notes` for the proposed state. This is the right time but the data should be captured as a frozen copy, not a live reference.

3. **Commit-time note reading** — `apply_variation_phrases()` calls `store.get_region_notes(rid)` after applying changes to build `updated_regions`. This reads from StateStore at commit time — acceptable for now because the commit has a transaction, but should eventually receive a snapshot.

### Where Snapshots Should Be Captured Instead

The boundary is at the point where Maestro calls into Muse:

```
sync_from_client()
    │
    ▼
capture_base_snapshot()  ← SNAPSHOT POINT: freeze region notes here
    │
    ▼
execute tool calls (mutate StateStore)
    │
    ▼
capture_proposed_snapshot()  ← SNAPSHOT POINT: freeze proposed notes here
    │
    ▼
MuseInterface.propose_variation(base_snapshot, proposed_snapshot, ...)
```

### How to Pass Snapshots Without Changing Runtime Flow

The flow remains identical. The only change: at the two snapshot points above, call `deepcopy(store._region_notes)` (or `store.get_region_notes(rid)` per region, which already returns a deepcopy) and pass the result forward as a plain dict.

No new storage. No new serialization. Just `deepcopy` at the boundary.

The composing handler in `maestro_composing/composing.py` already follows this sequence. The refactor is to make the snapshot capture explicit rather than implicit.

---

## 4. MCP Separation Tasks

### Task 4.1 — Classify tools as server-side vs DAW-side in registry

**Goal:** Make the server-side vs DAW-side distinction explicit in tool metadata, not just in code comments.

**Files affected:**
- `app/mcp/tools/registry.py` — add a `server_side: bool` field to tool metadata (or a `target` enum: `"server"` | `"daw"`)
- `app/mcp/tools/generation.py` — mark all generation tools as `server_side=True`

**Expected diff size:** Small (add one field to each tool definition in `generation.py`, add filtering logic in registry).

**Why it reduces coupling:** Makes it impossible to accidentally forward a server-side tool to the DAW. Today, `SERVER_SIDE_TOOLS` is a set of strings. A new tool could be added without being classified, defaulting to DAW forwarding.

### Task 4.2 — Consolidate Orpheus response normalization

**Goal:** All Orpheus `tool_calls` responses pass through a single adapter function that translates Orpheus format to Maestro internal format.

**Files affected:**
- `app/services/orpheus.py` — add `normalize_tool_calls()` function
- `app/core/maestro_editing/tool_execution.py` — route Orpheus responses through normalizer
- `app/core/maestro_agent_teams/section_agent.py` — same

**Expected diff size:** Medium (new function + updating 2-3 call sites).

**Why it reduces coupling:** Orpheus's internal tool-call naming becomes an implementation detail. If Orpheus changes `addMidiTrack` to `add_midi_track`, only the adapter changes. Today, the translation is scattered.

### Task 4.3 — Document the tool-call shape boundary

**Goal:** Add a docstring or comment at the boundary where Orpheus responses enter Maestro, declaring the expected format and noting that Orpheus's format is internal.

**Files affected:**
- `app/services/orpheus.py` — docstring on the client method

**Expected diff size:** Small (documentation only).

**Why it reduces coupling:** Makes the implicit contract explicit. A future contributor changing Orpheus tool output will know to update the adapter.

---

## 5. Execution Layer Hardening Tasks

### Task 5.1 — Extract entity resolution into a standalone function

**Goal:** Entity resolution (name → ID) is currently interleaved with tool validation and execution in `_apply_single_tool_call()`. Extract it into a pure function: `resolve_tool_entities(tool_call, registry) → ResolvedToolCall`.

**Files affected:**
- `app/core/maestro_editing/tool_execution.py` — extract function
- `app/core/tool_validation/` — may already have some resolution; consolidate

**Why it reduces coupling:** Entity resolution is a pure operation (input: tool call + registry, output: resolved tool call). Embedding it in the execution function forces execution to know about registry internals. Extraction makes execution testable with pre-resolved tool calls.

### Task 5.2 — Isolate VariationService calls behind the composing handler

**Goal:** Ensure that `VariationService.compute_variation()` and `VariationService.compute_multi_region_variation()` are only called from within `app/core/maestro_composing/`, never from the execution layer directly.

**Files affected:**
- Search for all imports of `VariationService` outside of `maestro_composing/`
- If found in `executor/variation.py`, move the call to the composing handler and pass the result down

**Why it reduces coupling:** The execution layer should execute tool calls and return results. Computing variations is a Muse concern, not an execution concern. The composing handler is the boundary.

### Task 5.3 — Separate SSE emission from execution core

**Goal:** `_apply_single_tool_call()` currently validates, resolves, executes, records to StateStore, *and* emits SSE events. The SSE emission should be the caller's responsibility, not the tool executor's.

This is a medium-sized refactor. The approach:
1. Have `_apply_single_tool_call()` return a result object (tool call result + state mutations) without emitting SSE.
2. The calling handler (editing handler or agent) emits SSE based on the result.

**Files affected:**
- `app/core/maestro_editing/tool_execution.py`
- `app/core/maestro_editing/` handler code that calls it

**Expected diff size:** Medium — the function is central and called from several paths.

**Why it reduces coupling:** Makes the execution function testable without SSE infrastructure. Makes it reusable from contexts that don't use SSE (e.g., headless mode, batch processing).

**Note:** This is the largest task in the plan. Consider splitting it:
- 5.3a: Have `_apply_single_tool_call` return the SSE event data as a dict, and have the caller emit it.
- 5.3b: Remove direct SSE emission from `_apply_single_tool_call`.

---

## 6. StateStore Role Clarification

### Immediate Rules

Engineers should enforce these rules starting now. No code changes required — these are constraints on future code.

**StateStore MAY:**
- Maintain the in-memory working tree (tracks, regions, notes, buses)
- Accept mutations from tool call execution (create_track, add_notes, etc.)
- Resolve entity names to IDs via EntityRegistry
- Provide versioned state via `get_state_id()`
- Support transactions with rollback for plan execution
- Sync from the DAW via `sync_from_client()`
- Provide snapshots of region notes via `get_region_notes()` (returns deepcopy)

**StateStore MUST NOT:**
- Be accessed directly by Muse commit logic (Muse receives snapshots, not store references)
- Be treated as an immutable base state (it is mutable — callers must snapshot before mutation)
- Be the sole owner of `base_state_id` semantics (the concurrency token should eventually be Muse's concern)
- Store variation or phrase data (that belongs to VariationStore)
- Be shared across requests via conversation_id for Muse's benefit (if sharing is needed, the caller provides the data)

**StateStore IS:**
- A per-request working tree
- A mutable scratchpad for tool call execution
- A derived view of the DAW's state (via sync)
- A source of snapshots that Muse consumes (via explicit capture)

**StateStore IS NOT:**
- A persistent store (the name is misleading — accepted)
- An authority on musical history
- A replacement for a Muse repository
- A shared state bus between Maestro and Muse

---

## 7. Phase 3 Implementation Checklist

Ordered by dependency (top tasks unblock bottom tasks). Each task is independently shippable and testable.

### Week 1 — Snapshot boundary and Muse data flow

- [ ] **Rename `_extract_notes_from_project` to `capture_base_snapshot`** and make it a public function in `app/core/executor/variation.py`. Ensure it returns a `deepcopy`. Add a docstring marking it as the snapshot boundary. Tests: existing executor tests must pass unchanged.

- [ ] **Add `region_metadata` parameter to `_store_variation()`** in `app/core/maestro_composing/storage.py`. Default to `None` with fallback to current registry lookup. Update the caller in `composing.py` to pass metadata from the StateStore. Remove the registry lookup once the caller is migrated. Tests: variation storage tests must pass.

- [ ] **Add `region_notes` parameter to `apply_variation_phrases()`** in `app/core/executor/apply.py`. Default to `None` with fallback to current `get_or_create_store()` lookup. Update the commit endpoint in `app/api/routes/variation/commit.py` to pass a snapshot from the `project_store` it already holds. Tests: commit endpoint tests, apply tests.

- [ ] **Remove `conversation_id` usage from `apply_variation_phrases`** once the snapshot parameter is in use. The function should not call `get_or_create_store()` at all. Tests: same as above.

- [ ] **Add `StateStore` role rules** as a module-level docstring update to `app/core/state_store.py` (the MAY/MUST NOT rules from Section 6). No behavior change.

### Week 1 — MCP classification

- [ ] **Add `server_side` field to tool definitions** in `app/mcp/tools/generation.py`. Update `app/mcp/tools/registry.py` to use this field for `SERVER_SIDE_TOOLS` set construction instead of a hardcoded list. Tests: MCP tool tests.

- [ ] **Consolidate Orpheus response normalization** into a single `normalize_orpheus_tool_calls()` function in `app/services/orpheus.py`. Route all Orpheus response consumption through it. Tests: Orpheus client tests.

### Week 2 — Execution layer hardening

- [ ] **Extract entity resolution** from `_apply_single_tool_call()` into a standalone `resolve_tool_entities()` function. The execution function calls the resolver first, then executes with pre-resolved IDs. Tests: tool validation tests, execution tests.

- [ ] **Audit VariationService imports** — ensure `VariationService` is only imported within `app/core/maestro_composing/` and `app/services/variation/` and `app/api/routes/variation/`. If it appears in `executor/` or `maestro_editing/`, move the call to the composing handler boundary. Tests: import audit (can be a CI check or manual review).

- [ ] **Add `MuseInterface` boundary documentation** as a docstring on `app/core/maestro_composing/__init__.py` defining the contract from Section 2. No new code — this is the contract that the above refactors enforce. The docstring declares what crosses the boundary and what does not.

- [ ] **Separate SSE emission from `_apply_single_tool_call` (phase 1)** — have the function return SSE event data as a dict instead of emitting directly. The caller emits. Tests: all editing handler tests, agent team tests.

### Verification

After all tasks are complete:

- [ ] `docker compose exec maestro mypy app/ tests/` — clean
- [ ] `docker compose exec maestro pytest tests/ -v` — all pass
- [ ] `docker compose exec orpheus mypy .` — clean
- [ ] `docker compose exec orpheus pytest . -v` — all pass
- [ ] No new imports of `StateStore` in `app/variation/` or `app/models/variation.py`
- [ ] No imports of `VariationService`, `VariationStore`, `Variation`, `Phrase`, or `NoteChange` in `app/core/maestro_handlers.py`
- [ ] `apply_variation_phrases()` does not call `get_or_create_store()`
- [ ] `_store_variation()` does not access `store.registry`
