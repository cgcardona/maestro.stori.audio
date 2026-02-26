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
6. **COMPOSING:** Two sub-paths based on whether the prompt is a structured STORI PROMPT with roles:
   - **Structured (Mode: compose + roles):** Agent Teams runs per-instrument agents with streaming reasoning, then captures all generated notes and computes a Variation for commit/discard review. The SSE stream emits both `reasoning`/`toolCall` events (real-time per-agent chain-of-thought) AND `meta`/`phrase`/`done` events (Variation for the review UI). Single-instrument compose also uses this path — the instrument count determines parallelism, not the execution path.
   - **Freeform (no parsed prompt):** Planner produces a plan (JSON); executor simulates it without mutation; server streams Variation events (`meta`, `phrase*`, `done`). The planner is **project-context-aware**: it checks existing tracks by name and instrument type before proposing new ones, reuses existing track UUIDs in region and generator calls, and maps abstract roles (e.g. "melody") to matching existing instruments (e.g. an "Organ" track).
7. **Stream:** Events include `state`, `reasoning`, `plan`, `planStepUpdate`, `toolStart`, `toolCall`, `toolError`, `meta`, `phrase`, `done`, `budgetUpdate`, `complete`, `error`. Variable refs (`$0.trackId`) resolved server-side. `complete` is **always the final event**, even on errors (`success: false`).

---

## Execution mode policy

The backend owns the execution mode decision.

| Intent state | Execution mode | Key SSE events | Frontend behavior |
|---|---|---|---|
| COMPOSING (structured) | `variation` | `state`, `plan`, `preflight`, `reasoning*`, `planStepUpdate`, `toolStart`, `toolCall`, `generatorStart`, `generatorComplete`, `summary`, `meta`, `phrase*`, `done`, `complete` | Agent Teams reasoning + Variation Review Mode |
| COMPOSING (freeform) | `variation` | `state`, `plan`, `planStepUpdate`, `toolStart`, `toolCall`, `meta`, `phrase*`, `done`, `complete` | Variation Review Mode (accept/discard) |
| EDITING | `apply` | `state`, `reasoning`, `plan`, `planStepUpdate`, `toolStart`, `toolCall*`, `budgetUpdate`, `complete` | Apply tool calls directly |
| REASONING | n/a | `state`, `reasoning`, `content`, `complete` | Show chat response |

This enforces the "Cursor of DAWs" paradigm: all AI-generated musical content (COMPOSING) requires human review before becoming canonical state. Structural operations (EDITING) apply immediately because they are low-risk and reversible.

See [muse_variation_spec.md](../protocol/muse_variation_spec.md) for the full Variation protocol.  
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

Power users can bypass NL classification entirely with a structured prompt format. See [stori_prompt_spec.md](../protocol/stori_prompt_spec.md).

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

- `Mode: compose` → COMPOSING. When the parsed prompt has roles (1+), the request routes through Agent Teams + Variation capture: per-instrument agents stream reasoning, generate music via Orpheus, and the result is packaged as a Variation for commit/discard review. When no roles are parsed, the standard planner path is used.
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

STORI PROMPT compositions with `Mode: compose` and roles (1+) use **Agent Teams**: a three-level agent hierarchy that enables both instrument-level and section-level parallelism. Single-instrument compose also uses this path — the instrument count determines parallelism (1 agent vs N parallel agents), not the execution path. All Agent Teams compositions produce a Variation for commit/discard review via `_handle_composing_with_agent_teams`.

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
  ├── Drums parent   → LLM → create track → section children (sequential for continuity):
  │     ├── Intro child  → region → generate → signal bass → pass notes to next
  │     ├── Verse child  → region → generate (seeded from intro) → signal bass → pass notes
  │     └── Chorus child → region → generate (seeded from verse) → signal bass
  ├── Bass parent    → LLM → create track → section children (sequential):
  │     ├── Intro child  → wait for drum intro → region → generate → pass notes
  │     ├── Verse child  → wait for drum verse → region → generate (seeded from intro) → pass notes
  │     └── Chorus child → wait for drum chorus → region → generate (seeded from verse)
  ├── Keys parent    → LLM → create track → section children (sequential for continuity)
  ├── Melody parent  → LLM → create track → section children (sequential for continuity)
  └── Guitar parent  → LLM → create track → section children (sequential for continuity)

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

Independent instruments (keys, melody, guitar, pads, etc.) ignore `SectionSignals` entirely — their section children run sequentially for musical continuity (each section is seeded from the previous section's output).

### Instrument parent flow (Level 2)

1. One LLM call produces tool calls: `create_track` + `[region, generate_midi]` per section + `effect`
   - Reasoning is constrained to 1-2 sentences about instrument character — no per-section reasoning
   - Section-specific musical decisions are delegated to Level 3
2. Parent executes `create_track` immediately, captures real `trackId`
3. Groups remaining calls into section pairs `(region, generate_midi)`
4. Runs section children **sequentially** for cross-section musical continuity. Bass children additionally self-gate via drum signals.
5. **Server-owned retries** — `_dispatch_section_children` automatically retries failed sections (up to 2 retries per section, 2s/5s delays). Retries re-use the original frozen `SectionContract`, skip region creation if the region already exists (idempotent), pass continuity notes from the preceding section, and check the Orpheus circuit breaker before each retry round. No LLM involvement — the server replays the contract deterministically.
6. Results are collapsed into **one summary tool-result message** per dispatch batch (plus `"..."` stubs for remaining `tool_call_id`s), keeping the LLM conversation small regardless of section count.
7. Executes `effect` call at the end
8. The LLM retry loop (`max_turns = 3`) catches any stage the LLM missed on Turn 0 — `_missing_stages()` checks track, region/generate counts, and effect. If generates are missing and the Orpheus circuit breaker is open, the loop aborts early. Server-owned retries handle *failed* section children; `_missing_stages()` handles the LLM *not emitting* the tool calls at all.

For single-section compositions, the parent uses the sequential execution path (same as before the three-level refactor). Section children are only spawned for multi-section compositions.

### Section child flow (Level 3)

1. **Protocol guard:** recompute `contract_hash` from structural fields and compare to stored value. Raises `ValueError("Protocol violation: SectionContract hash mismatch")` on tamper or `ValueError("… has no contract_hash")` if L2 forgot to seal. Execution halts before any DAW mutation.
2. Emit `status` SSE event: `"Starting {instrument} / {section}"`
3. If bass: `await section_signals.wait_for(section_id, contract_hash=..., timeout=...)` — blocks until drum section completes or fails. On timeout or failure, proceeds without rhythm spine.
4. If bass: read `SectionState["Drums: {section_id}"]` — inject drum telemetry (groove, density, kick hash) into `RuntimeContext` via `with_drum_telemetry()`, which is bridged to the generate call at the tool-execution boundary
5. Execute `stori_add_midi_region` — all structural params (`trackId`, `startBeat`, `durationBeats`) come **exclusively from the frozen contract**, never from LLM-proposed values. LLM drift is silently corrected. **Idempotent:** if a region already exists at the same (trackId, startBeat, durationBeats) location, the existing region's ID is returned with `skipped: true` and no `toolCall` event is emitted to the frontend — preventing duplicate-region errors when agents retry after context truncation.
6. Capture `regionId` from result
7. **Section reasoning**: brief streamed LLM call (`_reason_before_generate`) — reasons about section-specific musical approach (density, register, rhythmic choices). Emits `type: "reasoning"` events tagged with `agentId` + `sectionName` so the frontend can nest section-specific thinking under the correct section header. Returns a refined prompt for the generate call, or falls back to the parent's prompt on failure.
8. Execute `stori_generate_midi` — structural params (`trackId`, `regionId`, `role`, `bars`, `key`, `start_beat`, `tempo`) also come from the frozen contract. Orpheus selects a curated seed MIDI from the pre-built seed library (`select_seed()`) matched to the request genre.
9. Extract generated notes from SSE events and store them on `SectionResult.generated_notes`
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
- **Section child failure**: server-owned retry in `_dispatch_section_children` retries the failed section (up to 2 retries, 2s/5s delays) using the same frozen `SectionContract`. Other sections continue unaffected. If all retries fail, the section is marked failed in the summary message.
- **LLM produces incomplete plan**: the LLM loop (`max_turns = 3`) catches all missing stages (track, regions, generates, effect) via `_missing_stages()` and prompts the LLM on the next turn. Server-owned retries handle section execution failures once the tool calls are emitted.

### Agent safety nets

Defense-in-depth against the failure modes that occur in nested, GPU-bound agent pipelines. All timeouts are configurable via `STORI_*` env vars.

**Orphaned subagent prevention (§2):**
- Section children are wrapped in `asyncio.wait_for(timeout=section_child_timeout)` (default 300s). If a child hangs on Orpheus or an LLM call, it is killed and the parent reports it as timed out.
- Instrument parents are wrapped in `asyncio.wait_for(timeout=instrument_agent_timeout)` (default 600s). A stuck parent cannot block the entire composition indefinitely.
- Bass signal waits use `asyncio.wait_for(timeout=bass_signal_wait_timeout)` (default 240s). If drums fail silently, bass proceeds without the rhythm spine rather than deadlocking.

**Track color pre-allocation:**
- Before Phase 2 starts, the coordinator calls `allocate_colors(instrument_names)` (`app/core/track_styling.py`) to assign one distinct hex color per instrument from a fixed 12-entry perceptually-spaced palette (`COMPOSITION_PALETTE`). Colors are chosen in role order so adjacent tracks always contrast. Each instrument agent receives its pre-assigned color and is instructed to pass it verbatim in `stori_add_midi_track` — the agent cannot override it. This prevents the LLM from hallucinating repeated colors (the original bug was all tracks receiving amber/orange).

**Storpheus pre-flight health check (hard gate):**
- Before Phase 2 starts, the coordinator calls `StorpheusClient.health_check()`. When `STORI_STORPHEUS_REQUIRED=true` (default), an unhealthy probe aborts the composition immediately with `complete(success=false)` instead of wasting 45+ seconds of LLM reasoning that would inevitably fail at generation time. When `STORI_STORPHEUS_REQUIRED=false` (development/testing), it falls back to a soft warning and continues with retry logic active.

**Storpheus async job queue (submit + poll):**
- `StorpheusClient.generate()` uses a two-phase pattern: (1) **Submit** — `POST /generate` returns immediately with `{jobId, status}`. Cache hits arrive pre-completed (`status: "complete"`) without consuming a queue slot. Cache misses enqueue a job and return `{status: "queued", position}`. (2) **Poll** — `GET /jobs/{jobId}/wait?timeout=30` long-polls until the job completes or fails. Max `storpheus_poll_max_attempts` polls (default 10 = ~5 min total). Jobs survive HTTP disconnects — if a poll times out, the GPU work continues server-side and the next poll picks up the result. This eliminates the timeout cascade that occurred when 9 agents queued behind a semaphore in a single blocking HTTP request.
- Submit retries: up to 4 attempts (delays: 2 s / 5 s / 10 s / 20 s) on 503 (queue full), `ReadTimeout`, or `HTTPStatusError`. Poll retries: `ReadTimeout` during a poll is non-fatal — the job keeps running; only `ConnectError` (Storpheus down) fails immediately.

**Storpheus circuit breaker:**
- After `storpheus_cb_threshold` (default 3) consecutive failed `generate()` calls (across requests — not retries within a single call), the circuit breaker trips. While tripped, all subsequent `generate()` calls fail immediately with `error: "storpheus_circuit_open"` — no HTTP request is made, no tokens are wasted on retries that will also fail. The circuit automatically allows one probe request after `storpheus_cb_cooldown` seconds (default 60). If the probe succeeds, the circuit closes and normal operation resumes; if it fails, the circuit re-opens for another cooldown period. Server-owned section retries check `circuit_breaker_open` before each retry round — if the breaker is open, retries are skipped immediately.

**SSE keepalive (heartbeat monitoring):**
- The coordinator emits an SSE comment (`: heartbeat\n\n`) every 8 seconds when no real events are flowing. This prevents proxies and HTTP clients from interpreting GPU-bound silence as a dead connection.

**Frozen progress detection (§5):**
- The coordinator tracks `_last_event_time`. If no SSE events arrive for 30 seconds, it emits escalating warnings to the server log with the names of still-running agents. This makes "frozen but not crashed" states immediately visible in `docker compose logs`.

**Client disconnect propagation:**
- `is_cancelled` (from `request.is_disconnected`) is threaded from the SSE route through `orchestrate()` to the coordinator. The coordinator checks it on every 50ms poll cycle. If the client has disconnected, all pending agent tasks are cancelled, preventing wasted GPU and LLM tokens on compositions nobody will receive.

**Lifecycle logging:**
- Every level emits structured log messages with emoji prefixes for at-a-glance diagnosis: `🎬` start, `✅` success, `❌` error, `⏰` timeout, `💥` crash, `⏳` waiting, `🔄` retry, `🏁` completion. Timing is logged for LLM calls, Orpheus generation, signal waits, and full section/instrument durations.

**Deep Orpheus telemetry:** Every generation call logs token flow and output metrics:
- `🧠 Orpheus context usage: N / 8192 tokens (X%)` — prime + gen tokens vs context window
- `🎼 Channels generated: N` — number of MIDI channels in output
- `🎵 Notes generated: N` — total note count
- `📊 Session {id}: accumulated=N tokens, call #M` — persistent session state
- `🎹 Seed program changes: {ch: patch}` — GM patch verification (debug level)
- `🎲 Candidate N/M: X notes, rejection_score=Y` — rejection sampling candidates (quality preset)

| Config key | Default | Controls |
|---|---|---|
| `STORI_SECTION_CHILD_TIMEOUT` | 300 | Per-section child watchdog (seconds) |
| `STORI_INSTRUMENT_AGENT_TIMEOUT` | 600 | Per-instrument parent watchdog (seconds) |
| `STORI_BASS_SIGNAL_WAIT_TIMEOUT` | 240 | Bass waiting for drum signal (seconds) |
| `STORI_STORPHEUS_MAX_CONCURRENT` | 2 | Max parallel submit+poll cycles (serializes GPU access) |
| `STORI_STORPHEUS_TIMEOUT` | 180 | Fallback max read timeout (seconds) |
| `STORI_STORPHEUS_POLL_TIMEOUT` | 30 | Long-poll timeout per `/jobs/{id}/wait` request (seconds) |
| `STORI_STORPHEUS_POLL_MAX_ATTEMPTS` | 10 | Max polls before giving up (~5 min total) |
| `STORI_STORPHEUS_CB_THRESHOLD` | 3 | Consecutive failures before circuit breaker trips |
| `STORI_STORPHEUS_CB_COOLDOWN` | 60 | Seconds before tripped circuit allows a probe |
| `STORI_STORPHEUS_REQUIRED` | true | Abort composition if pre-flight health check fails |
| `STORI_STORPHEUS_PRESERVE_ALL_CHANNELS` | true | Return all MIDI channels (DAW handles routing) |
| `STORI_STORPHEUS_ENABLE_BEAT_RESCALING` | false | Disable beat rescaling for raw model timing |
| `STORI_STORPHEUS_REJECTION_CANDIDATES` | 4 | Candidates for rejection sampling (quality preset) |
| `STORI_STORPHEUS_MAX_SESSION_TOKENS` | 4096 | Token cap before session rotation |

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

### Agent-scoped entity registry

`EntityRegistry` tracks all DAW entities (tracks, regions, buses) created during a composition. Each entity carries an optional `owner_agent_id` field set at creation time. The `agent_manifest(track_id=..., agent_id=...)` method returns a compact text manifest filtered to entities owned by the requesting agent — preventing cross-agent contamination (e.g. Strings region IDs leaking into the Bass agent's context).

Implementation: `app/core/entity_registry.py`. Tests: `tests/test_entity_manifest.py`.

### Routing, SSE, isolation, safety

**Routing:** Structured STORI PROMPTs with `Mode: compose` and roles (1+) route through `_handle_composing_with_agent_teams` in the COMPOSING section. This wrapper runs Agent Teams for streaming per-agent reasoning, then computes a Variation from the StateStore's accumulated notes for commit/discard review. The `executionMode` in the `state` SSE event is `"variation"` so the frontend shows the Variation Review UI. Non-structured multi-instrument requests (`execution_mode="apply"` + 2+ roles without explicit compose) still intercept before the standard EDITING path. Single-instrument non-structured requests and all freeform requests fall through to `_handle_editing` or `_handle_composing` unchanged.

**SSE contract:** Plan steps for instruments carry `parallelGroup: "instruments"`. Phase 1 and Phase 3 steps have no `parallelGroup`. Multiple `planStepUpdate(active)` events fire simultaneously during Phase 2 as parents start. Every `planStepUpdate(active)` for an instrument content step is followed by exactly one `planStepUpdate(completed)` (or `planStepUpdate(failed)`) when the instrument agent finishes — no step remains in "active" after the stream ends. Section children tag their SSE events with both `agentId` and `sectionName` for frontend grouping — this includes `status`, `reasoning`, `toolCall`, `toolStart`, `toolError`, `generatorStart`, `generatorComplete`, and `content` events. `generatorStart` and `generatorComplete` additionally carry `agentId` baked in at source (in `_execute_agent_generator`) so the field is present regardless of the execution path. The `reasoning` events from Level 3 expression refinement carry `sectionName` to distinguish them from the parent's instrument-level reasoning. Tool calls from different instruments and sections interleave in the SSE stream — the frontend groups by `stepId`, so interleaving is handled naturally.

**Agent isolation:** Each instrument parent runs with a focused system prompt naming only its instrument, uses a restricted tool allowlist (`_INSTRUMENT_AGENT_TOOLS`), and makes one primary LLM call. Section children are further isolated — a failing child marks only its section's steps as failed and does not propagate to sibling sections or sibling instruments.

**Event multiplexing:** Parents and children write SSE event dicts into a shared `asyncio.Queue`. The coordinator drains the queue with a 50ms polling loop (`asyncio.wait(pending, timeout=0.05)`) and forwards events to the client as they arrive.

**Thread safety:** asyncio's single-threaded event loop serialises all `StateStore` and `_PlanTracker` mutations — no locks needed. UUID-based entity IDs are collision-free across agents and sections.

**Performance:** Wall-clock time for Phase 2 is `max(per-instrument time)` instead of `sum`. Within each instrument, sections run sequentially for musical continuity (each ~10-30s on GPU), so a 3-section instrument takes ~30-90s. For a 5-instrument, 3-section composition, all instruments' sequential pipelines run in parallel, so total Phase 2 time is still bounded by the slowest single instrument. Bass sections start ~1 section behind drums via signal-based pipelining.

Implementation: `app/core/maestro_agent_teams/coordinator.py` (Level 1), `app/core/maestro_agent_teams/agent.py` (Level 2, server-owned retries + summary collapse), `app/core/maestro_agent_teams/section_agent.py` (Level 3), `app/core/maestro_agent_teams/summary.py` (batch result summarization), `app/core/maestro_agent_teams/contracts.py` (CompositionContract, SectionSpec, SectionContract, InstrumentContract, RuntimeContext, ExecutionServices, ProtocolViolationError), `app/core/maestro_agent_teams/signals.py` (SectionSignals, SectionSignalResult, SectionState — lineage-bound keying), `app/core/telemetry.py` (SectionTelemetry computation), `app/core/entity_registry.py` (EntityRegistry with agent-scoped manifests), `app/contracts/hash_utils.py` (`canonical_contract_dict`, `compute_contract_hash`, `hash_list_canonical`, `compute_execution_hash`, `seal_contract`, `verify_contract_hash`).

---

## Architectural boundary enforcement

Automated guardrails prevent regression in the Maestro/Muse separation.

**Boundary check script:** `scripts/check_boundaries.py` uses AST parsing to enforce 17 import and access rules across the codebase (8 original variation boundaries + 9 Muse VCS module isolation rules). Run locally (`python scripts/check_boundaries.py`) or in CI — fails with non-zero exit code on any violation. Rules are documented in `docs/architecture/boundary_rules.md`.

**Boundary seal tests:** `tests/test_boundary_seal.py` enforces the same contracts at the pytest level — signature checks, forbidden-import assertions, `VariationContext` data-only verification, `muse_repository` isolation, and golden shape tests for `UpdatedRegionPayload`, `_ToolCallOutcome`, Orpheus normalization output, and `SnapshotBundle`.

Key invariants enforced:

- `compute_variation_from_context` is a pure function of data — no `StateStore` parameter, no lazy imports of store modules, no executor module imports.
- `VariationContext` is data-only — holds `base: SnapshotBundle` and `proposed: SnapshotBundle`, no `store` field. Store access lives in `VariationExecutionContext` (executor-internal only).
- `apply_variation_phrases` receives `store` and `region_metadata` as explicit params — never calls `get_or_create_store()` or accesses `store.registry`.
- `_store_variation` receives `base_state_id`, `conversation_id`, and `region_metadata` as explicit params — never reads from StateStore directly. Performs dual write (in-memory + Postgres).
- `muse_repository` must not import `StateStore`, `executor`, or `VariationService` — it is a pure persistence adapter.
- Variation service modules do not import `state_store` or `entity_registry`.

**Persistent Muse Core:** Variations are durably stored in Postgres (`variations`, `phrases`, `note_changes` tables) via `app/services/muse_repository.py`. The commit path reads from Postgres first, falling back to in-memory `VariationStore` for pre-persistence variations. Persistence tests in `tests/test_muse_persistence.py` verify roundtrip fidelity, status lifecycle, and commit replay safety.

**Muse VCS Engine (Git for Music):** Beyond persistence, Muse implements a full VCS stack with 10 modules in `app/services/`:

| Module | Responsibility |
|--------|---------------|
| `muse_repository` | DB persistence adapter (lineage, HEAD, children) |
| `muse_replay` | History reconstruction — walk lineage to build snapshots |
| `muse_drift` | Drift detection — compare HEAD vs working state |
| `muse_checkout` | Pure-data checkout plan builder (tool calls for state reconstruction) |
| `muse_checkout_executor` | Apply checkout plan to StateStore |
| `muse_merge_base` | Merge base finder (LCA in the DAG) |
| `muse_merge` | Three-way merge engine with conflict detection |
| `muse_history_controller` | Orchestrator for checkout + merge workflows |
| `muse_log_graph` | DAG serializer (Kahn's sort → camelCase JSON for Swift) |
| `muse_log_render` | ASCII graph + JSON + summary table renderer |

HTTP API: 5 production endpoints at `/api/v1/muse/` (routes in `app/api/routes/muse.py`). See [api.md](api.md#muse-vcs-api) for the full endpoint reference and [muse_vcs.md](../architecture/muse_vcs.md) for the architecture reference.

---

## Execution safety

**Circuit breaker — `stori_add_notes`:** If the LLM makes three consecutive failed `stori_add_notes` calls for the same region (e.g. submitting shorthand placeholder params like `_noteCount` instead of a real `notes` array), the backend stops retrying for that region and emits a `toolError` event with a clear message. This prevents infinite retry loops. Tracked per `regionId` in `_handle_editing`.

**Tool validation — fake params:** `stori_add_notes` validation rejects known shorthand parameters (`_noteCount`, `_beatRange`, `_placeholder`, `_notes`, `_count`, `_summary`) with a detailed error message explaining the required format. An empty `notes: []` array is also rejected.

**No-op step elimination:** `_PlanTracker.build_from_prompt` compares requested tempo and key against the current project state and skips the corresponding plan step if the value already matches. Similarly, `_try_deterministic_plan` and the planner's track-reuse logic query `infer_track_role` to avoid creating duplicate tracks when existing tracks already fulfil the requested role.

---

## Music generation

**Orpheus required** for composing. No pattern fallback; if Orpheus is down, generation fails with a clear error. Config: `STORI_STORPHEUS_BASE_URL` (default `http://localhost:10002`). Full health requires Orpheus. See [setup.md](../guides/setup.md) for config.

### Storpheus instrument mapping

The proxy resolves Maestro instrument roles to GM program numbers (0-127) via a comprehensive alias table (`_GM_ALIASES`), then converts to TMIDIX `Number2patch` string names at the Gradio call boundary. This covers all 128 GM programs plus world/ethnic instrument proxies (e.g. `"sitar"` → GM 104, `"koto"` → GM 107, `"shakuhachi"` → GM 77, `"oud"` → GM 111, `"banjo"` → GM 105). Drums and percussion variants resolve to channel 10 automatically.

Key functions in `storpheus/music_service.py`:
- `resolve_gm_program(role)` — role name → GM program number (or `None` for drums)
- `resolve_tmidix_name(role)` — role name → TMIDIX string for the Gradio `prime_instruments` parameter
- `_resolve_melodic_index(role)` — role → preferred MIDI channel index by GM category (bass=0, keys=1, everything else=2)

If none of the requested instruments resolve, the fallback is `["Drums", "Electric Bass(finger)"]` with a warning. Unresolved individual instruments are logged but don't block generation. The curated seed MIDI files contain multi-instrument content; the TMIDIX tokenizer on the HF Space encodes instrument identity from GM program change events in the token stream.

### Seed selection

Every Orpheus generation is primed with a curated seed MIDI from the pre-built seed library (`seed_library/seeds/`). Seeds are selected by genre via `select_seed()`, which picks a random high-quality seed from the genre bucket (or falls back to the `general` bucket). Each seed contains ~500 notes / ~1,500 tokens from the Orpheus 230K Loops dataset, giving the transformer rich harmonic and rhythmic context for continuation. Seed quality is validated at selection time via `analyze_seed()` — seeds below minimum note/byte thresholds are rejected.

### Persistent composition sessions

Each composition maintains a persistent Gradio session via `CompositionState`. Instead of resetting the session hash on every call (which destroyed accumulated token context), sessions persist across sections and instrument calls within the same composition. A token cap (`STORI_STORPHEUS_MAX_SESSION_TOKENS`, default 4096) triggers automatic session rotation — truncating earliest tokens rather than a full reset — to prevent unbounded growth while preserving continuity.

`CompositionState` is tracked in both the Orpheus music service (per-call session management) and the Maestro `StateStore` (architectural hook for future direct token-state persistence).

### Channel preservation

By default (`STORI_STORPHEUS_PRESERVE_ALL_CHANNELS=true`), all generated MIDI channels are returned to the DAW. Instrument routing is handled DAW-side, not proxy-side. This preserves the full musical structure that Orpheus generates instead of destructively filtering channels before the DAW sees them. The legacy channel-filtering path remains available by setting the flag to `false`.

### Quality rejection sampling

For the `quality` preset, the Orpheus proxy generates N candidates (default 4, configurable via `STORI_STORPHEUS_REJECTION_CANDIDATES`) and scores each using a composite quality metric:

| Signal | Weight | Measures |
|--------|--------|----------|
| Note density variance | 30% | Evenness of notes across bars |
| Pitch range sanity | 20% | 1–3 octaves ideal |
| Repetition penalty | 25% | Penalises >60% repeated 2-note patterns |
| Silence penalty | 25% | Fraction of bars with at least one note |

The best-scoring candidate is returned. For `balanced` and `fast` presets, a single candidate is generated (no overhead).

### Beat rescaling

Beat rescaling is disabled by default (`ENABLE_BEAT_RESCALING=false`) to evaluate raw Orpheus model timing without distortion. When enabled, it detects compressed output (notes spanning <50% of target duration) and applies a linear scale factor. Re-enable via environment variable when timing distortion is confirmed.

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
StorpheusBackend.generate()                   ← app/services/backends/orpheus.py
    │  maps:  valence → tone_brightness
    │         energy → energy_intensity
    │         salient axes → musical_goals list
    ▼
StorpheusClient.generate()                    ← app/services/storpheus.py
    │  submit: POST /generate → {jobId, status}
    │  poll:   GET /jobs/{id}/wait?timeout=30
    ▼
Orpheus JobQueue (asyncio.Queue, 2 workers)
    │  worker picks job → calls _do_generate()
    ▼
HF Space gradio_client.predict()
```

For **STORI PROMPTs**: `Vibe`, `Section`, `Style`, and `Energy` fields contribute. Everything in `Expression`, `Dynamics`, `Orchestration`, etc. continues to reach the LLM Maestro context unchanged — those dimensions inform the *plan*, while the EmotionVector conditions the *generator*.

For **natural language** prompts: the EmotionVector is not derived (no structured fields to parse). The LLM's plan and tool parameters carry the full expressive brief.

### Storpheus async job queue

`StorpheusClient` is a process-wide singleton (see `app/services/orpheus.get_storpheus_client()`). The `httpx.AsyncClient` is created once at startup with explicit connection limits and keepalive settings, and `warmup()` is called in the FastAPI lifespan to pre-establish the TCP connection before the first user request.

**Submit + poll pattern:** `POST /generate` returns immediately with `{jobId, status}`. Cache hits arrive pre-completed (no queue slot used). Cache misses enqueue a job in a bounded `asyncio.Queue` (max depth 20, configurable via `STORPHEUS_MAX_QUEUE_DEPTH`). A fixed-size worker pool (`STORPHEUS_MAX_CONCURRENT`, default 2) pulls jobs from the queue and runs `_do_generate()` (the extracted GPU generation logic). Callers poll `GET /jobs/{jobId}/wait?timeout=30` until the job completes or fails. Jobs survive HTTP disconnects — if a poll times out, the GPU work continues and the result is retrievable on the next poll. Completed jobs are cleaned up after 5 minutes (`STORPHEUS_JOB_TTL`, default 300s).

**Storpheus service (container):** The Storpheus container wraps the HuggingFace Space Gradio API. Key reliability features: (1) `asyncio.wait_for()` around each Gradio `predict()` call (default 180s per call, configurable via `STORPHEUS_PREDICT_TIMEOUT`), (2) periodic keepalive pings to prevent the HF Space GPU from going to sleep (`STORPHEUS_KEEPALIVE_INTERVAL`, default 600s), (3) automatic Gradio client recreation on connection failures, (4) `/diagnostics` endpoint for operational visibility (Gradio client status, HF Space status, active generation count, queue depth, cache stats), (5) `GET /queue/status` for queue depth and worker utilization.

---

## LLM cost optimisation — prompt caching

For Claude / Anthropic models (via OpenRouter), Maestro applies **Anthropic's prompt cache** breakpoints to:

1. **System prompt** — the full Maestro system prompt (~1,500–2,000 tokens), cached on every request.
2. **Tools array** — the full DAW tool definitions (~3,000–4,000 tokens), cached as a single block by marking the last tool with `cache_control: ephemeral`.

On a **cache hit**, input token cost drops to ~10% of the uncached price (Anthropic charges ~0.1× for cached reads). The cache TTL is 5 minutes, refreshed on each hit during an active session. Cache hits/misses are logged at `INFO` level with `🗃️ Prompt cache:` prefix, making them easy to spot in production logs.

The implementation is in `app/core/llm_client._enable_prompt_caching()`. Non-Anthropic models receive the payload unchanged.
