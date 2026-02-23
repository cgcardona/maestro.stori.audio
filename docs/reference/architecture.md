# Architecture

How the backend works: one engine, two entry points; request flow; intent; execution mode; music.

---

## One backend, two entry points

- **Stori app:** User types in the DAW -> app POSTs to `POST /api/v1/maestro/stream` -> SSE stream -> app reacts based on intent state.
- **MCP client (Cursor, Claude, etc.):** User or script calls MCP tools -> Maestro runs or forwards to the **same** Stori instance (the one connected at `GET /api/v1/mcp/daw`). Same tool set; only the client changes. Human stays in the loop. Later: headless Stori for swarms.

---

## Request flow

1. **Frontend** sends `POST /api/v1/maestro/stream` with `prompt`, optional `project` (app state), `conversationId`.
2. **Intent** is classified (pattern + LLM fallback) -> REASONING / EDITING / COMPOSING.
3. **Backend forces execution mode** based on intent (frontend does not choose):
   - COMPOSING -> `execution_mode="variation"` (Variation proposal, no mutation)
   - EDITING -> `execution_mode="apply"` (tool calls applied directly)
   - REASONING -> no tools
4. **REASONING:** Chat only; no tools; stream `reasoning` + `content` events.
5. **EDITING:** LLM gets a tool allowlist; emits tool calls; server validates and resolves entity IDs. Emits a structured `plan` event (checklist of steps) before the first tool call, then `planStepUpdate` events bracketing each step, and `toolStart` + `toolCall` events for each tool.
6. **COMPOSING:** Planner produces a plan (JSON); executor simulates it without mutation; server streams Variation events (`meta`, `phrase*`, `done`). Frontend enters Variation Review Mode. User accepts or discards. The planner is **project-context-aware**: it checks existing tracks by name and instrument type before proposing new ones, reuses existing track UUIDs in region and generator calls, and maps abstract roles (e.g. "melody") to matching existing instruments (e.g. an "Organ" track).
7. **Stream:** Events include `state`, `reasoning`, `plan`, `planStepUpdate`, `toolStart`, `toolCall`, `toolError`, `meta`, `phrase`, `done`, `budgetUpdate`, `complete`, `error`. Variable refs (`$0.trackId`) resolved server-side. `complete` is **always the final event**, even on errors (`success: false`).

---

## Execution mode policy

The backend owns the execution mode decision.

| Intent state | Execution mode | Key SSE events | Frontend behavior |
|---|---|---|---|
| COMPOSING | `variation` | `state`, `planSummary`, `progress`, `meta`, `phrase*`, `done`, `complete` | Variation Review Mode (accept/discard) |
| EDITING | `apply` | `state`, `reasoning`, `plan`, `planStepUpdate`, `toolStart`, `toolCall*`, `budgetUpdate`, `complete` | Apply tool calls directly |
| REASONING | n/a | `state`, `reasoning`, `content`, `complete` | Show chat response |

This enforces the "Cursor of DAWs" paradigm: all AI-generated musical content (COMPOSING) requires human review before becoming canonical state. Structural operations (EDITING) apply immediately because they are low-risk and reversible.

See [muse-variation-spec.md](../protocol/muse-variation-spec.md) for the full Variation protocol.  
See [api.md](api.md) for the full SSE event reference and wire format.

---

## Stori Protocol (wire contract)

All SSE events across every streaming endpoint are validated through the Stori Protocol layer (`app/protocol/`). The protocol enforces:

- **One registry:** Every event type is registered in `app/protocol/registry.py`. Unknown types cannot be emitted.
- **One emitter:** `serialize_event()` in `app/protocol/emitter.py` is the single serialization path. It validates every dict against its Pydantic model before serialization. Raw `json.dumps` in streaming code is forbidden.
- **One guard:** `ProtocolGuard` is instantiated in every streaming route (maestro, conversations, MCP, variation). It checks event ordering invariants (first event = `state`, nothing after `complete`, etc.).
- **Fail loudly:** If validation fails, the emitter raises `ProtocolSerializationError`. The stream emits `error` + `complete(success: false)` and terminates. There is no silent fallback.

Introspection endpoints: `GET /api/v1/protocol` (version + hash), `GET /api/v1/protocol/events.json` (event schemas), `GET /api/v1/protocol/schema.json` (unified schema).

---

## Structured plan events (EDITING)

When an EDITING request produces two or more tool calls, the backend emits a structured checklist before executing tools. This gives the frontend a persistent progress view and reduces redundant reasoning on subsequent LLM iterations.

```
state → reasoning* → plan → planStepUpdate(active) → toolStart → toolCall → planStepUpdate(completed) → ... → complete
```

- **`plan`** — emitted once after initial reasoning, before the first tool call. Contains `planId`, `title` (musically descriptive, e.g. "Building Funk Groove", "Composing Lo-Fi Hip Hop", "Setting Up 6-Track Jazz"), and `steps[]` (each with `stepId`, `label`, `status: "pending"`, `toolName`, optional `detail`).
- **Step labels** follow canonical patterns that the frontend uses for per-instrument timeline grouping: `"Create <TrackName> track"`, `"Add content to <TrackName>"`, `"Add effects to <TrackName>"`, `"Add MIDI CC to <TrackName>"`, `"Add pitch bend to <TrackName>"`, `"Write automation for <TrackName>"`. Project-level steps use patterns without a track target: `"Set tempo to 120 BPM"`, `"Set key signature to A minor"`, `"Set up shared Reverb bus"`.
- **Step ordering** is track-contiguous: all steps for one instrument appear together (create → content → effects → expressive) before the next instrument's steps begin.
- **`toolName`** is present when the step maps to a specific tool, containing the canonical MCP tool name (e.g. `"stori_add_midi_track"`, `"stori_add_notes"`). Omitted (not empty string) when no tool applies, so Swift decodes it as `nil`. The frontend uses this for icon and color resolution independently of the label text.
- **`planStepUpdate`** — emitted per step: `status: "active"` when starting, `status: "completed"` (or `"failed"`) when done, with an optional `result` summary string. Steps that are never activated are emitted as `status: "skipped"` at plan completion — no step remains in `"pending"` after the stream ends.
- For composition mode (multi-iteration), the plan summary is also injected into the system prompt for subsequent LLM batches, reducing redundant chain-of-thought.

---

## Intent engine

Classify first, then execute. **REASONING** = questions, no tools. **EDITING** = direct DAW actions (transport, track, region, effects); LLM constrained by allowlist; validation + entity resolution before execution. **COMPOSING** = "make music" / high-level; planner -> executor -> Variation proposal; Orpheus for generation. Tool allowlisting and server-side entity IDs prevent bad or fabricated IDs.

---

## Structured prompts

Power users can bypass NL classification entirely with a structured prompt format. See [stori-prompt-spec.md](../protocol/stori-prompt-spec.md).

```
User prompt arrives
        │
        ▼
┌──────────────────┐
│  parse_prompt()  │ ← detect "STORI PROMPT" header
└────────┬─────────┘
         │
    returns ParsedPrompt?
    ┌─────┴──────┐
    │ Yes        │ No (returns None)
    ▼            ▼
 Hard-route   Existing NL pipeline
 from Mode    (normalize → patterns → idioms → LLM fallback)
 field        ← COMPLETELY UNCHANGED
```

- `Mode: compose` → COMPOSING (planner path). When Style, Tempo, Roles, and Bars are all specified, the planner builds a deterministic plan without an LLM call.
- `Mode: edit` → EDITING. Vibes are matched against the producer idiom lexicon to pick the most appropriate edit intent.
- `Mode: ask` → REASONING. No tools.

Parsed fields (Style, Key, Tempo, Roles, Constraints, Vibes, Target) are injected into the LLM system prompt as structured context, reducing inference overhead and increasing determinism.

Implementation: `app/core/prompt_parser.py` (parser), `app/core/intent/` (routing gate).

---

## Effects and mix routing

Effects are added through two complementary mechanisms that both produce real tool calls.

### 1. Deterministic planner inference

When a structured prompt specifies `Style` and `Role`, the planner infers effects before any LLM call via `_infer_mix_steps`. This runs regardless of whether an `Effects:` block is present.

**Role-based defaults (always applied):**
- Drums → `stori_add_insert_effect(type="compressor")`
- Bass → `stori_add_insert_effect(type="compressor")`
- Pads / melody / lead → `stori_add_send` to shared "Reverb" bus

**Style overrides (additive):**
- Rock / metal / shoegaze → distortion on lead; high-compression on drums
- Lo-fi / chill → filter on drums; chorus on lead/pads
- Jazz → reverb on chords; mild compression on drums
- Shoegaze → distortion + chorus + heavy reverb on lead

**Routing rule:** Reverb is always routed via `stori_add_send` to a shared bus, never as a direct insert. `stori_ensure_bus` is guaranteed to precede any `stori_add_send` for the same bus name.

**Suppression:** `Constraints: no_effects: true` disables all effect inference.

Implementation: `app/core/planner._infer_mix_steps`, wired into `plan_from_parsed_prompt` and `_schema_to_tool_calls`.

### 2. STORI PROMPT block translation

When a STORI PROMPT includes `Effects:`, `MidiExpressiveness:`, or `Automation:` blocks, every entry is translated into the corresponding tool call. These are **mandatory** — the system prompt explicitly instructs the LLM to treat them as an execution checklist, not decorative prose.

| STORI PROMPT block | Translated to |
|---|---|
| `Effects.drums.compression` | `stori_add_insert_effect(type="compressor")` |
| `Effects.drums.room/reverb` | `stori_add_insert_effect(type="reverb")` or reverb bus send |
| `Effects.bass.saturation/tube` | `stori_add_insert_effect(type="overdrive")` |
| `Effects.lead.overdrive` | `stori_add_insert_effect(type="overdrive")` |
| `Effects.lead.distortion` | `stori_add_insert_effect(type="distortion")` |
| `Effects.*.chorus/tremolo/delay/filter` | `stori_add_insert_effect(type=…)` |
| `MidiExpressiveness.cc_curves[cc: N]` | `stori_add_midi_cc(cc=N, events=[{beat,value},…])` |
| `MidiExpressiveness.pitch_bend` | `stori_add_pitch_bend(events=[{beat,value},…])` |
| `MidiExpressiveness.sustain_pedal` | `stori_add_midi_cc(cc=64, events=[…])` — 127=down, 0=up |
| `Automation[track, param, events]` | `stori_add_automation(target=trackId, points=[…])` |

The plan tracker surfaces each expressive block as a visible plan step with canonical per-track labels (`"Add effects to Drums"`, `"Add MIDI CC to Bass"`, `"Add pitch bend to Guitar Lead"`, `"Write automation for Strings"`) so the frontend's `ExecutionTimelineView` can group them into the correct instrument sections.

Implementation: `app/core/prompts.structured_prompt_context` (translation mandate injection), `app/core/prompts.editing_composition_prompt` (step-by-step guide), `app/core/maestro_handlers._PlanTracker.build_from_prompt` (plan steps).

---

## Agent Teams — three-level parallel composition

Multi-instrument STORI PROMPT compositions (2+ roles) use **Agent Teams**: a three-level agent hierarchy that enables both instrument-level and section-level parallelism.

### Architecture levels

```
Level 1 — COORDINATOR (coordinator.py)
  └── Deterministic setup, spawns instrument parents, optional mixing pass

Level 2 — INSTRUMENT PARENT (agent.py) — one per instrument
  └── One LLM call plans the entire instrument: track + [region, generate]* + effect
      Brief high-level reasoning (1-2 sentences about sonic character)
      Groups tool calls into section pairs, spawns section children

Level 3 — SECTION CHILD (section_agent.py) — one per section per instrument
  └── Section reasoning LLM call (streamed, with sectionName) → region → generate
      Optional refinement LLM call for CC curves / pitch bend / automation
```

### Contract-based handoffs (protocol, not conversation)

Each level boundary is formalized with a **frozen dataclass contract** — no free-form dict passing between layers.

```
CompositionContract  (global lineage anchor, built first by L1)
  ├── composition_id                       ← trace_id of the request
  ├── sections: tuple[SectionSpec, ...]   ← canonical section layout
  ├── style, tempo, key
  ├── contract_version: int = 2
  └── contract_hash: str                  ← SHA-256 of structural fields
                                             (sections serialized as sorted hashes, not full objects)

InstrumentContract  (L1 → L2)
  ├── instrument_name, role, style, bars, tempo, key
  ├── sections: tuple[SectionSpec, ...]   ← immutable beat layout
  ├── existing_track_id, assigned_color
  ├── gm_guidance                         ← genre-specific GM voice block (advisory, excluded from hash)
  ├── contract_version: int = 1
  ├── contract_hash: str                  ← SHA-256 short hash of structural fields
  └── parent_contract_hash: str           ← CompositionContract.contract_hash

SectionContract  (L2 → L3)
  ├── section: SectionSpec                ← section_id, name, index, start_beat, duration_beats, bars
  ├── track_id, instrument_name, role
  ├── style, tempo, key, region_name
  ├── l2_generate_prompt                  ← ADVISORY — L3 may refine via CoT (excluded from hash)
  ├── contract_version: int = 1
  ├── contract_hash: str                  ← hash of structural fields above
  └── parent_contract_hash: str           ← instrument_contract.contract_hash

SectionSpec  (embedded in both contracts)
  ├── section_id, name, index, start_beat, duration_beats, bars
  ├── character, role_brief               ← canonical descriptions (structural)
  ├── contract_version: int = 1
  └── contract_hash: str                  ← set by coordinator after construction

RuntimeContext  (travels alongside contracts — pure data, frozen)
  ├── raw_prompt, quality_preset
  ├── emotion_vector: tuple[tuple[str, float], ...] | None   ← frozen tuple-of-pairs, never a dict
  └── drum_telemetry: tuple[tuple[str, Any], ...] | None     ← immutable via with_drum_telemetry()

ExecutionServices  (mutable coordination — NOT frozen, passed separately)
  ├── section_signals: SectionSignals     ← asyncio.Event per "{section_id}:{contract_hash}"
  └── section_state: SectionState         ← write-once telemetry store
```

**Design rules:**
- `frozen=True` on all contracts and `RuntimeContext` — structural fields and data are immutable once built by L1.
- L3 may only reason about HOW to describe the music (Orpheus prompt). It must not reinterpret beat ranges, section names, roles, or track IDs — those come from the frozen contract.
- `l2_generate_prompt` is explicitly marked advisory; the contract's `section.character` and `section.role_brief` are authoritative when they conflict.
- `RuntimeContext` is genuinely frozen — no nested mutable objects. `emotion_vector` is stored as `tuple[tuple[str, float], ...]` (never a dict). `drum_telemetry` is a `tuple[tuple[str, Any], ...]`. Immutable updates use `with_emotion_vector()` / `with_drum_telemetry()`, each returning a new instance. `to_composition_context()` returns a `types.MappingProxyType` (read-only).
- Mutable coordination primitives (`SectionSignals`, `SectionState`) live in `ExecutionServices`, which is explicitly NOT frozen and passed separately from contracts and `RuntimeContext`. This prevents the frozen data boundary from wrapping live mutable synchronization state.
- `to_composition_context()` returns a `types.MappingProxyType` (read-only mapping) — downstream code cannot mutate shared state through the bridge dict. Services are excluded from the bridge.
- At the `_apply_single_tool_call` boundary (tool execution layer), a new dict is constructed by spreading the read-only bridge with local additions (`style`, `tempo`, etc.). No dict flows through the agent teams layer itself.
- L2 fallback SectionSpec reconstruction from LLM output is a hard error (`ValueError`). The contract is authoritative; if `instrument_contract` is missing or section index exceeds contract sections, the system fails fast rather than silently degrading.

### Contract lineage protocol (cryptographic execution identity)

All contracts carry a **self-verifying hash identity** implemented in `app/contracts/hash_utils.py`. This makes the system swarm-safe: any orchestration layer can verify a contract or result without trusting the agent that delivered it.

**Hash rules:**

- Only **structural fields** participate in hashes: beat ranges, section names, role, tempo, key, style, track layout.
- **Advisory and meta fields are always excluded**: `l2_generate_prompt`, `region_name`, `gm_guidance`, `assigned_color`, `existing_track_id`, `contract_hash`, `parent_contract_hash`, `execution_hash`, `contract_version`. Changing advisory text never alters structural identity.
- Serialization is canonical: `json.dumps(..., separators=(",", ":"), sort_keys=True)` applied to a recursively sorted dict. No repr(), no pickle, no runtime randomness.
- Hash function: SHA-256, truncated to the first 16 hex characters (64-bit short hash).
- Parent hashing uses `hash_list_canonical(items)` — sorts hashes lexicographically, JSON-encodes the list, then SHA-256. This is collision-proof (no delimiter attack) and order-independent.

**Lineage construction (four levels):**

```
L1 Coordinator:
  for each SectionSpec:
      seal_contract(spec)                          # sets spec.contract_hash

  seal_contract(composition_contract)              # sections serialized as sorted hash list
  # sets cc.contract_hash = hash of {composition_id, style, tempo, key, sorted(section_hashes)}

  seal_contract(instrument_contract,
      parent_hash=composition_contract.contract_hash)
  # sets ic.parent_contract_hash = cc.contract_hash
  # sets ic.contract_hash = hash of structural ic fields

L2 Instrument parent (dispatch):
  verify section_spec.section_id and section_spec.contract_hash  # hard fail if missing
  seal_contract(section_contract, parent_hash=instrument_contract.contract_hash)
  # sets sc.parent_contract_hash = ic.contract_hash
  # sets sc.contract_hash = hash of structural sc fields

L3 Section child (_run_section_child):
  assert verify_contract_hash(contract)            # recompute and compare
  # → ValueError("Protocol violation: SectionContract hash mismatch") if tampered
  # → ValueError("Protocol violation: … has no contract_hash") if unsealed

  execution_hash = SHA256(contract.contract_hash + trace.trace_id)[:16]
  result.execution_hash = execution_hash           # session-bound, prevents replay
```

**Verified lineage chain:**

```
CompositionContract.contract_hash
    ↓ (copied into parent_contract_hash)
InstrumentContract.parent_contract_hash
    ↓ (structural hash)
InstrumentContract.contract_hash
    ↓ (copied into)
SectionContract.parent_contract_hash
    ↓ (structural hash)
SectionContract.contract_hash
    ↓ (copied into)
SectionResult.contract_hash + SectionResult.parent_contract_hash
    +
SectionResult.execution_hash = SHA256(contract_hash + trace_id)
```

**Execution attestation:** `SectionResult` carries three lineage fields populated at L3 completion:
- `contract_hash` — the `SectionContract` that produced this result.
- `parent_contract_hash` — the `InstrumentContract` that spawned it.
- `execution_hash` — `SHA256(contract_hash + trace_id)`, binding the result to a specific composition session. The same contract re-run in a different session produces a different `execution_hash`, preventing replay attacks.

**Implementation:** `app/contracts/hash_utils.py` (`canonical_contract_dict`, `compute_contract_hash`, `hash_list_canonical`, `compute_execution_hash`, `seal_contract`, `verify_contract_hash`). Verified by `tests/test_protocol_proof.py` (existing proofs) and `tests/test_protocol_god_mode.py` (composition root, canonical parent hash, execution attestation, signal lineage, replay prevention, hash scope).

### Three-phase execution

```
Phase 1 — SETUP (sequential, coordinator, no LLM)
  └── Set tempo, key (deterministic from ParsedPrompt)

Phase 2 — INSTRUMENTS (all parents launched simultaneously)
  ├── Drums parent   → LLM → create track → spawn section children:
  │     ├── Intro child  → region → generate → signal bass
  │     ├── Verse child  → region → generate → signal bass
  │     └── Chorus child → region → generate → signal bass
  ├── Bass parent    → LLM → create track → spawn section children:
  │     ├── Intro child  → wait for drum intro → region → generate
  │     ├── Verse child  → wait for drum verse → region → generate
  │     └── Chorus child → wait for drum chorus → region → generate
  ├── Keys parent    → LLM → create track → spawn section children (all parallel)
  ├── Melody parent  → LLM → create track → spawn section children (all parallel)
  └── Guitar parent  → LLM → create track → spawn section children (all parallel)

Phase 3 — MIXING (sequential, one coordinator LLM call, after all agents complete)
  └── Ensure buses, add sends, volume, pan
```

### Drum-to-bass section-level pipelining

Bass section children no longer wait for ALL drum sections to finish. Instead, each bass section waits only for its corresponding drum section via `SectionSignals`:

```
Drums:  [intro ██████|verse █████████████|chorus ████████]
Bass:         [intro ██████|verse █████████████|chorus ████████]
Keys:   [intro ████|verse ██████████|chorus ██████]
Melody: [intro ███|verse █████████|chorus █████]
```

`SectionSignals` is a shared dataclass containing one `asyncio.Event` per lineage-bound key `"{section_id}:{contract_hash}"`. After a drum section child generates, it calls `signal_complete(section_id, contract_hash=..., success=True, drum_notes=...)` which stores a typed `SectionSignalResult` and fires the event. The matching bass section child calls `wait_for(section_id, contract_hash=..., timeout=...)` and receives either a success result with drum notes, a failure result (drums failed), or `asyncio.TimeoutError`.

**Safety guarantees:**
- `signal_complete` is **idempotent** — calling it twice for the same key is a no-op (first write wins).
- Store-before-signal ordering: drum data is stored in `_results` before the event is set.
- **Failure signaling:** `signal_complete(sid, contract_hash=..., success=False)` lets dependents distinguish "drums failed" from "drums never ran" — bass proceeds without the rhythm spine instead of deadlocking.
- **Signal lineage binding (swarm safety):** keys are `"{section_id}:{contract_hash}"`, not bare `section_id`. A drum signal from a different composition (different `contract_hash`) is invisible to the consumer waiting on the correct hash. `ProtocolViolationError` is raised if a stored result's `contract_hash` doesn't match what the waiter expected.

Independent instruments (keys, melody, guitar, pads, etc.) ignore `SectionSignals` entirely — their section children all run in parallel from the start.

### Instrument parent flow (Level 2)

1. One LLM call produces tool calls: `create_track` + `[region, generate_midi]` per section + `effect`
   - Reasoning is constrained to 1-2 sentences about instrument character — no per-section reasoning
   - Section-specific musical decisions are delegated to Level 3
2. Parent executes `create_track` immediately, captures real `trackId`
3. Groups remaining calls into section pairs `(region, generate_midi)`
4. Spawns section children — parallel for all instruments (bass children self-gate via signals)
5. Waits for all children
6. Executes `effect` call at the end
7. If children failed, checks whether the Orpheus circuit breaker is open — if so, aborts immediately instead of making a retry LLM turn (prevents wasting tokens on retries that will also fail)
8. Otherwise, optionally makes a retry LLM turn with `_missing_stages()`

For single-section compositions, the parent uses the sequential execution path (same as before the three-level refactor). Section children are only spawned for multi-section compositions.

### Section child flow (Level 3)

1. **Protocol guard:** recompute `contract_hash` from structural fields and compare to stored value. Raises `ValueError("Protocol violation: SectionContract hash mismatch")` on tamper or `ValueError("… has no contract_hash")` if L2 forgot to seal. Execution halts before any DAW mutation.
2. Emit `status` SSE event: `"Starting {instrument} / {section}"`
3. If bass: `await section_signals.wait_for(section_id, contract_hash=..., timeout=...)` — blocks until drum section completes or fails. On timeout or failure, proceeds without rhythm spine.
4. If bass: read `SectionState["Drums: {section_id}"]` — inject drum telemetry (groove, density, kick hash) into `RuntimeContext` via `with_drum_telemetry()`, which is bridged to the generate call at the tool-execution boundary
5. Execute `stori_add_midi_region` — all structural params (`trackId`, `startBeat`, `durationBeats`) come **exclusively from the frozen contract**, never from LLM-proposed values. LLM drift is silently corrected. **Idempotent:** if a region already exists at the same (trackId, startBeat, durationBeats) location, the existing region's ID is returned with `skipped: true` and no `toolCall` event is emitted to the frontend — preventing duplicate-region errors when agents retry after context truncation.
6. Capture `regionId` from result (also injected into retry reminders so agents can proceed without re-calling)
7. **Section reasoning**: brief streamed LLM call (`_reason_before_generate`) — reasons about section-specific musical approach (density, register, rhythmic choices). Emits `type: "reasoning"` events tagged with `agentId` + `sectionName` so the frontend can nest section-specific thinking under the correct section header. Returns a refined prompt for the generate call, or falls back to the parent's prompt on failure.
8. Execute `stori_generate_midi` — structural params (`trackId`, `regionId`, `role`, `bars`, `key`, `start_beat`, `tempo`) also come from the frozen contract.
9. Extract generated notes from SSE events
10. Compute `SectionTelemetry` from notes and write to `SectionState` (all instruments, not just drums)
11. If drums: call `section_signals.signal_complete(section_id, contract_hash=..., success=True, drum_notes=...)`
12. Emit `status` SSE event: `"{instrument} / {section}: N notes generated"`
13. **Execution attestation:** compute `execution_hash = SHA256(contract_hash + trace_id)`, store on `result.execution_hash`. This binds the result to the specific session — the same contract re-run in a different composition produces a different `execution_hash`, preventing replay attacks.
14. Optional: if the STORI PROMPT specifies `MidiExpressiveness` or `Automation`, run a **streamed** refinement LLM call (see below)
15. Return `SectionResult` to parent

Drum children always signal — even on failure (`signal_complete(section_id, contract_hash=..., success=False)`) — to prevent bass children from hanging indefinitely.

### Expression refinement (Level 3 streamed LLM call)

When the STORI PROMPT contains `MidiExpressiveness:` or `Automation:` blocks, section children make one small streamed LLM call after generation to add CC curves and pitch bends. This call:

- **Extracts** the relevant `MidiExpressiveness:` and `Automation:` YAML blocks from the raw prompt and includes them verbatim in the system message — the LLM sees the exact CC numbers, value ranges, and sweep descriptions the composer specified.
- **Streams reasoning** via `chat_completion_stream` with `reasoning_fraction` matching the parent's setting. Reasoning events are emitted as SSE `type: "reasoning"` tagged with both `agentId` and `sectionName`, so the GUI can display per-section musical thinking in real time alongside the parent's instrument-level CoT.
- **Emits** a `status` event (`"Adding expression to {instrument} / {section}"`) before the LLM call.
- **Executes** 1-3 tool calls (`stori_add_midi_cc`, `stori_add_pitch_bend`) with trackId/regionId auto-injected.
- **Cost:** ~$0.005 per section (tiny prompt, ~1000 max tokens). Skipped entirely when the prompt has no expressiveness blocks.

### Edge cases

- **Single-section compositions**: parent spawns one child, effectively same as before
- **No drums**: all instruments are independent, all sections run in parallel, no signals needed
- **No bass**: drum signals fire but nobody listens, no overhead
- **Section child failure**: parent marks that section's steps as failed, other sections continue; parent optionally retries the failed section in a follow-up turn
- **LLM produces incomplete plan**: multi-turn retry loop (existing) catches missing sections and prompts the LLM again

### Agent safety nets

Defense-in-depth against the failure modes that occur in nested, GPU-bound agent pipelines. All timeouts are configurable via `STORI_*` env vars.

**Orphaned subagent prevention (§2):**
- Section children are wrapped in `asyncio.wait_for(timeout=section_child_timeout)` (default 300s). If a child hangs on Orpheus or an LLM call, it is killed and the parent reports it as timed out.
- Instrument parents are wrapped in `asyncio.wait_for(timeout=instrument_agent_timeout)` (default 600s). A stuck parent cannot block the entire composition indefinitely.
- Bass signal waits use `asyncio.wait_for(timeout=bass_signal_wait_timeout)` (default 240s). If drums fail silently, bass proceeds without the rhythm spine rather than deadlocking.

**Track color pre-allocation:**
- Before Phase 2 starts, the coordinator calls `allocate_colors(instrument_names)` (`app/core/track_styling.py`) to assign one distinct hex color per instrument from a fixed 12-entry perceptually-spaced palette (`COMPOSITION_PALETTE`). Colors are chosen in role order so adjacent tracks always contrast. Each instrument agent receives its pre-assigned color and is instructed to pass it verbatim in `stori_add_midi_track` — the agent cannot override it. This prevents the LLM from hallucinating repeated colors (the original bug was all tracks receiving amber/orange).

**Orpheus pre-flight health check (hard gate):**
- Before Phase 2 starts, the coordinator calls `OrpheusClient.health_check()`. When `STORI_ORPHEUS_REQUIRED=true` (default), an unhealthy probe aborts the composition immediately with `complete(success=false)` instead of wasting 45+ seconds of LLM reasoning that would inevitably fail at generation time. When `STORI_ORPHEUS_REQUIRED=false` (development/testing), it falls back to a soft warning and continues with retry logic active.

**Orpheus transient-error retry:**
- `OrpheusClient.generate()` retries up to 4 attempts (delays: 2 s / 5 s / 10 s / 20 s) on *any* transient error — GPU cold-start phrases (`"No GPU was available"`, etc.), Gradio-level transient errors (`"'NoneType' object is not a mapping"`, `"Queue is full"`, `"upstream Gradio app has raised an exception"`, `"timed out"`), `httpx.ReadTimeout` exceptions, and **any 5xx HTTP status code** (including 503 Service Unavailable). The retry label in logs distinguishes the two (`"GPU cold-start"` vs `"Gradio transient error"`).

**Orpheus circuit breaker:**
- After `orpheus_cb_threshold` (default 3) consecutive failed `generate()` calls (across requests — not retries within a single call), the circuit breaker trips. While tripped, all subsequent `generate()` calls fail immediately with `error: "orpheus_circuit_open"` — no HTTP request is made, no tokens are wasted on LLM reasoning for retries that will also fail. The circuit automatically allows one probe request after `orpheus_cb_cooldown` seconds (default 60). If the probe succeeds, the circuit closes and normal operation resumes; if it fails, the circuit re-opens for another cooldown period. Instrument parents check `circuit_breaker_open` after dispatching section children — if the breaker is open and generates are still pending, the agent breaks out of its retry loop instead of making another LLM call.

**SSE keepalive (heartbeat monitoring):**
- The coordinator emits an SSE comment (`: heartbeat\n\n`) every 8 seconds when no real events are flowing. This prevents proxies and HTTP clients from interpreting GPU-bound silence as a dead connection.

**Frozen progress detection (§5):**
- The coordinator tracks `_last_event_time`. If no SSE events arrive for 30 seconds, it emits escalating warnings to the server log with the names of still-running agents. This makes "frozen but not crashed" states immediately visible in `docker compose logs`.

**Client disconnect propagation:**
- `is_cancelled` (from `request.is_disconnected`) is threaded from the SSE route through `orchestrate()` to the coordinator. The coordinator checks it on every 50ms poll cycle. If the client has disconnected, all pending agent tasks are cancelled, preventing wasted GPU and LLM tokens on compositions nobody will receive.

**Lifecycle logging:**
- Every level emits structured log messages with emoji prefixes for at-a-glance diagnosis: `🎬` start, `✅` success, `❌` error, `⏰` timeout, `💥` crash, `⏳` waiting, `🔄` retry, `🏁` completion. Timing is logged for LLM calls, Orpheus generation, signal waits, and full section/instrument durations.

| Config key | Default | Controls |
|---|---|---|
| `STORI_SECTION_CHILD_TIMEOUT` | 300 | Per-section child watchdog (seconds) |
| `STORI_INSTRUMENT_AGENT_TIMEOUT` | 600 | Per-instrument parent watchdog (seconds) |
| `STORI_BASS_SIGNAL_WAIT_TIMEOUT` | 240 | Bass waiting for drum signal (seconds) |
| `STORI_ORPHEUS_MAX_CONCURRENT` | 4 | GPU inference semaphore slots |
| `STORI_ORPHEUS_TIMEOUT` | 180 | Max read timeout per Orpheus HTTP call (seconds) |
| `STORI_ORPHEUS_TIMEOUT_BASE` | 60 | Fixed overhead per request (connect + cold-start buffer) |
| `STORI_ORPHEUS_TIMEOUT_PER_BAR` | 15 | Added per bar of requested music (A100: ~1-2 bars/s) |
| `STORI_ORPHEUS_CB_THRESHOLD` | 3 | Consecutive failures before circuit breaker trips |
| `STORI_ORPHEUS_CB_COOLDOWN` | 60 | Seconds before tripped circuit allows a probe |
| `STORI_ORPHEUS_REQUIRED` | true | Abort composition if pre-flight health check fails |

### SectionState — deterministic musical telemetry

`SectionState` is a shared, write-once telemetry store that runs alongside `SectionSignals`. Every section child computes a `SectionTelemetry` snapshot from its generated MIDI notes — pure math, no LLM calls, <2ms per section.

```
@dataclass(frozen=True)
class SectionTelemetry:
    section_name: str           # "verse"
    instrument: str             # "Drums"
    tempo: float                # 120.0
    energy_level: float         # normalized(velocity × density), 0–1
    density_score: float        # notes / beats
    groove_vector: tuple        # 16-bin onset histogram (16th-note resolution)
    kick_pattern_hash: str      # MD5 fingerprint of kick drum positions
    rhythmic_complexity: float  # stddev of inter-onset intervals
    velocity_mean: float        # mean MIDI velocity
    velocity_variance: float    # variance of MIDI velocity
```

Keys follow `"Instrument: section_id"` format (e.g. `"Drums: 0:verse"`, `"Bass: 1:chorus"`). All writes go through an `asyncio.Lock` for thread safety across concurrent section children. `snapshot()` is also async and locked to prevent races during execution. Values are frozen dataclasses — immutable after write.

**Bass enrichment:** Before generating, each bass section child reads `SectionState["Drums: {section_id}"]`. If available, the drum telemetry (groove vector, density, kick pattern hash, rhythmic complexity) is injected into a new `RuntimeContext` instance via `with_drum_telemetry()` (immutable update — stores as `tuple[tuple[str, Any], ...]`). The updated context is bridged to the Orpheus generate call at the tool-execution boundary via a read-only `MappingProxyType`. This enables deterministic cross-instrument awareness without expanding LLM prompts or adding token cost.

**Diagnostic value:** The coordinator can call `await section_state.snapshot()` after composition completes for orchestration diagnostics, quality scoring, and future mixing decisions.

Implementation: `app/core/telemetry.py` (computation), `app/core/maestro_agent_teams/signals.py` (SectionState store).

### Routing, SSE, isolation, safety

**Routing:** `orchestrate()` intercepts `Intent.GENERATE_MUSIC` + `execution_mode="apply"` + multi-role `ParsedPrompt` (2+ roles) before the standard EDITING path. Single-instrument requests and all non-STORI-PROMPT requests fall through to `_handle_editing` unchanged.

**SSE contract:** Plan steps for instruments carry `parallelGroup: "instruments"`. Phase 1 and Phase 3 steps have no `parallelGroup`. Multiple `planStepUpdate(active)` events fire simultaneously during Phase 2 as parents start. Every `planStepUpdate(active)` for an instrument content step is followed by exactly one `planStepUpdate(completed)` (or `planStepUpdate(failed)`) when the instrument agent finishes — no step remains in "active" after the stream ends. Section children tag their SSE events with both `agentId` and `sectionName` for frontend grouping — this includes `status`, `reasoning`, `toolCall`, `toolStart`, `toolError`, `generatorStart`, `generatorComplete`, and `content` events. `generatorStart` and `generatorComplete` additionally carry `agentId` baked in at source (in `_execute_agent_generator`) so the field is present regardless of the execution path. The `reasoning` events from Level 3 expression refinement carry `sectionName` to distinguish them from the parent's instrument-level reasoning. Tool calls from different instruments and sections interleave in the SSE stream — the frontend groups by `stepId`, so interleaving is handled naturally.

**Agent isolation:** Each instrument parent runs with a focused system prompt naming only its instrument, uses a restricted tool allowlist (`_INSTRUMENT_AGENT_TOOLS`), and makes one primary LLM call. Section children are further isolated — a failing child marks only its section's steps as failed and does not propagate to sibling sections or sibling instruments.

**Event multiplexing:** Parents and children write SSE event dicts into a shared `asyncio.Queue`. The coordinator drains the queue with a 50ms polling loop (`asyncio.wait(pending, timeout=0.05)`) and forwards events to the client as they arrive.

**Thread safety:** asyncio's single-threaded event loop serialises all `StateStore` and `_PlanTracker` mutations — no locks needed. UUID-based entity IDs are collision-free across agents and sections.

**Performance:** Wall-clock time for Phase 2 is `max(per-instrument time)` instead of `sum`, and within each instrument `max(per-section time)` instead of `sum(section times)`. For a 5-instrument, 3-section composition, bass sections start ~1 section behind drums rather than waiting for all 3 drum sections. Expected speedup: 3–5× for the instrument phase, with additional gains from section-level pipelining.

Implementation: `app/core/maestro_agent_teams/coordinator.py` (Level 1), `app/core/maestro_agent_teams/agent.py` (Level 2), `app/core/maestro_agent_teams/section_agent.py` (Level 3), `app/core/maestro_agent_teams/contracts.py` (CompositionContract, SectionSpec, SectionContract, InstrumentContract, RuntimeContext, ExecutionServices, ProtocolViolationError), `app/core/maestro_agent_teams/signals.py` (SectionSignals, SectionSignalResult, SectionState — lineage-bound keying), `app/core/telemetry.py` (SectionTelemetry computation), `app/contracts/hash_utils.py` (`canonical_contract_dict`, `compute_contract_hash`, `hash_list_canonical`, `compute_execution_hash`, `seal_contract`, `verify_contract_hash`).

---

## Execution safety

**Circuit breaker — `stori_add_notes`:** If the LLM makes three consecutive failed `stori_add_notes` calls for the same region (e.g. submitting shorthand placeholder params like `_noteCount` instead of a real `notes` array), the backend stops retrying for that region and emits a `toolError` event with a clear message. This prevents infinite retry loops. Tracked per `regionId` in `_handle_editing`.

**Tool validation — fake params:** `stori_add_notes` validation rejects known shorthand parameters (`_noteCount`, `_beatRange`, `_placeholder`, `_notes`, `_count`, `_summary`) with a detailed error message explaining the required format. An empty `notes: []` array is also rejected.

**No-op step elimination:** `_PlanTracker.build_from_prompt` compares requested tempo and key against the current project state and skips the corresponding plan step if the value already matches. Similarly, `_try_deterministic_plan` and the planner's track-reuse logic query `infer_track_role` to avoid creating duplicate tracks when existing tracks already fulfil the requested role.

---

## Music generation

**Orpheus required** for composing. No pattern fallback; if Orpheus is down, generation fails with a clear error. Config: `STORI_ORPHEUS_BASE_URL` (default `http://localhost:10002`). Full health requires Orpheus. See [setup.md](../guides/setup.md) for config.

### Expressive MIDI pipeline

The generation pipeline carries the **complete set** of musically relevant MIDI messages — not just notes:

| Data type | Pipeline field | MIDI message | Examples |
|-----------|---------------|-------------|----------|
| Notes | `notes` | Note On/Off (0x9n/0x8n) | pitch, velocity, duration, channel |
| Control Change | `cc_events` | CC (0xBn) | sustain (64), expression (11), mod (1), volume (7), pan (10), filter (74), reverb (91), etc. — all 128 CCs |
| Pitch Bend | `pitch_bends` | PB (0xEn) | 14-bit signed (−8192 to 8191) |
| Aftertouch | `aftertouch` | Channel Pressure (0xDn) / Poly Key Pressure (0xAn) | Channel-wide or per-note pressure |
| Program Change | track-level | PC (0xCn) | `stori_set_midi_program` |
| Automation | track-level | n/a (DAW param curves) | `stori_add_automation` (volume, pan, FX) |

**Data flow:** Orpheus generates notes + CC + pitch bend + aftertouch → `GenerationResult` → executor records into `VariationContext` → variation service groups into `Phrase.controller_changes` → commit materialises into `updated_regions` (cc_events, pitch_bends, aftertouch arrays) → frontend replaces region data.

In non-variation mode (EDITING), expressive data is written to `StateStore` directly and returned in `toolCall` results.

### Emotion vector conditioning

Every Orpheus generation call is conditioned by a 5-axis **EmotionVector** derived from the request's creative brief:

| Axis | Range | Musical meaning |
|---|---|---|
| `energy` | 0–1 | Stillness → explosive |
| `valence` | −1 → +1 | Dark/sad → bright/joyful |
| `tension` | 0–1 | Resolved → unresolved/anxious |
| `intimacy` | 0–1 | Epic/distant → close/personal |
| `motion` | 0–1 | Static/sustained → driving/rhythmic |

**Derivation pipeline:**

```
STORI PROMPT
    │
    ▼
emotion_vector_from_stori_prompt()          ← app/core/emotion_vector.py
    │  parses: Vibe keywords, Energy level,
    │          Section preset, Style/genre
    │  blends contributions by weighted average
    ▼
EmotionVector(energy, valence, tension, intimacy, motion)
    │
    ▼
OrpheusBackend.generate()                   ← app/services/backends/orpheus.py
    │  maps:  valence → tone_brightness
    │         energy → energy_intensity
    │         salient axes → musical_goals list
    ▼
OrpheusClient.generate()                    ← app/services/orpheus.py
    │  includes: tone_brightness, energy_intensity,
    │            musical_goals, quality_preset
    ▼
Orpheus HTTP API /generate
```

For **STORI PROMPTs**: `Vibe`, `Section`, `Style`, and `Energy` fields contribute. Everything in `Expression`, `Dynamics`, `Orchestration`, etc. continues to reach the LLM Maestro context unchanged — those dimensions inform the *plan*, while the EmotionVector conditions the *generator*.

For **natural language** prompts: the EmotionVector is not derived (no structured fields to parse). The LLM's plan and tool parameters carry the full expressive brief.

### Orpheus connection pool

`OrpheusClient` is a process-wide singleton (see `app/services/orpheus.get_orpheus_client()`). The `httpx.AsyncClient` is created once at startup with explicit connection limits and keepalive settings, and `warmup()` is called in the FastAPI lifespan to pre-establish the TCP connection before the first user request.

**Dynamic timeout scaling:** The read timeout per request is computed as `base + (per_bar * bars)`, clamped to `[30, orpheus_timeout]`. A 4-bar request gets ~120s; a 32-bar request gets ~540s. This replaces the previous flat 360s timeout.

**Orpheus-music service (container):** The Orpheus container wraps the HuggingFace Space Gradio API. Key reliability features: (1) `asyncio.wait_for()` around each Gradio `predict()` call (default 120s per call, configurable via `ORPHEUS_PREDICT_TIMEOUT`), (2) periodic keepalive pings to prevent the HF Space GPU from going to sleep (`ORPHEUS_KEEPALIVE_INTERVAL`, default 600s), (3) automatic Gradio client recreation on connection failures, (4) `/diagnostics` endpoint for operational visibility (Gradio client status, HF Space status, active generation count, cache stats).

---

## LLM cost optimisation — prompt caching

For Claude / Anthropic models (via OpenRouter), Maestro applies **Anthropic's prompt cache** breakpoints to:

1. **System prompt** — the full Maestro system prompt (~1,500–2,000 tokens), cached on every request.
2. **Tools array** — the full DAW tool definitions (~3,000–4,000 tokens), cached as a single block by marking the last tool with `cache_control: ephemeral`.

On a **cache hit**, input token cost drops to ~10% of the uncached price (Anthropic charges ~0.1× for cached reads). The cache TTL is 5 minutes, refreshed on each hit during an active session. Cache hits/misses are logged at `INFO` level with `🗃️ Prompt cache:` prefix, making them easy to spot in production logs.

The implementation is in `app/core/llm_client._enable_prompt_caching()`. Non-Anthropic models receive the payload unchanged.
