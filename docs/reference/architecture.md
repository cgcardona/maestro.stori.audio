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
      Groups tool calls into section pairs, spawns section children

Level 3 — SECTION CHILD (section_agent.py) — one per section per instrument
  └── Lightweight executor: region → generate (no LLM needed for core pipeline)
      Optional refinement LLM call for CC curves / pitch bend / automation
```

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

`SectionSignals` is a shared dataclass containing one `asyncio.Event` per parsed section. After a drum section child generates, it stores the drum notes in `SectionSignals.drum_data` and fires the event. The matching bass section child awaits the event, reads the drum data for per-section RhythmSpine coupling, then generates.

Independent instruments (keys, melody, guitar, pads, etc.) ignore `SectionSignals` entirely — their section children all run in parallel from the start.

### Instrument parent flow (Level 2)

1. One LLM call produces tool calls: `create_track` + `[region, generate_midi]` per section + `effect`
2. Parent executes `create_track` immediately, captures real `trackId`
3. Groups remaining calls into section pairs `(region, generate_midi)`
4. Spawns section children — parallel for all instruments (bass children self-gate via signals)
5. Waits for all children
6. Executes `effect` call at the end
7. If children failed, optionally makes a retry LLM turn with `_missing_stages()`

For single-section compositions, the parent uses the sequential execution path (same as before the three-level refactor). Section children are only spawned for multi-section compositions.

### Section child flow (Level 3)

1. If bass: `await section_signals.wait_for(section_name)` — blocks until drum section completes
2. Execute `stori_add_midi_region` with parent's pre-resolved params (trackId injected)
3. Capture `regionId` from result
4. Execute `stori_generate_midi` with `regionId` and `trackId` injected
5. If drums: extract generated notes from outcome, call `section_signals.signal_complete(section_name, drum_notes)`
6. Optional: if the STORI PROMPT specifies `MidiExpressiveness` or `Automation`, make one small focused LLM call to add CC curves / pitch bend (tiny prompt, ~$0.005)
7. Return `SectionResult` to parent

Drum children always signal — even on failure — to prevent bass children from hanging indefinitely.

### Edge cases

- **Single-section compositions**: parent spawns one child, effectively same as before
- **No drums**: all instruments are independent, all sections run in parallel, no signals needed
- **No bass**: drum signals fire but nobody listens, no overhead
- **Section child failure**: parent marks that section's steps as failed, other sections continue; parent optionally retries the failed section in a follow-up turn
- **LLM produces incomplete plan**: multi-turn retry loop (existing) catches missing sections and prompts the LLM again

### Routing, SSE, isolation, safety

**Routing:** `orchestrate()` intercepts `Intent.GENERATE_MUSIC` + `execution_mode="apply"` + multi-role `ParsedPrompt` (2+ roles) before the standard EDITING path. Single-instrument requests and all non-STORI-PROMPT requests fall through to `_handle_editing` unchanged.

**SSE contract:** Plan steps for instruments carry `parallelGroup: "instruments"`. Phase 1 and Phase 3 steps have no `parallelGroup`. Multiple `planStepUpdate(active)` events fire simultaneously during Phase 2 as parents start. Section children tag their SSE events with both `agentId` and `sectionName` for frontend grouping. Tool calls from different instruments and sections interleave in the SSE stream — the frontend groups by `stepId`, so interleaving is handled naturally.

**Agent isolation:** Each instrument parent runs with a focused system prompt naming only its instrument, uses a restricted tool allowlist (`_INSTRUMENT_AGENT_TOOLS`), and makes one primary LLM call. Section children are further isolated — a failing child marks only its section's steps as failed and does not propagate to sibling sections or sibling instruments.

**Event multiplexing:** Parents and children write SSE event dicts into a shared `asyncio.Queue`. The coordinator drains the queue with a 50ms polling loop (`asyncio.wait(pending, timeout=0.05)`) and forwards events to the client as they arrive.

**Thread safety:** asyncio's single-threaded event loop serialises all `StateStore` and `_PlanTracker` mutations — no locks needed. UUID-based entity IDs are collision-free across agents and sections.

**Performance:** Wall-clock time for Phase 2 is `max(per-instrument time)` instead of `sum`, and within each instrument `max(per-section time)` instead of `sum(section times)`. For a 5-instrument, 3-section composition, bass sections start ~1 section behind drums rather than waiting for all 3 drum sections. Expected speedup: 3–5× for the instrument phase, with additional gains from section-level pipelining.

Implementation: `app/core/maestro_agent_teams/coordinator.py` (Level 1), `app/core/maestro_agent_teams/agent.py` (Level 2), `app/core/maestro_agent_teams/section_agent.py` (Level 3), `app/core/maestro_agent_teams/signals.py` (drum-to-bass coupling).

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

---

## LLM cost optimisation — prompt caching

For Claude / Anthropic models (via OpenRouter), Maestro applies **Anthropic's prompt cache** breakpoints to:

1. **System prompt** — the full Maestro system prompt (~1,500–2,000 tokens), cached on every request.
2. **Tools array** — the full DAW tool definitions (~3,000–4,000 tokens), cached as a single block by marking the last tool with `cache_control: ephemeral`.

On a **cache hit**, input token cost drops to ~10% of the uncached price (Anthropic charges ~0.1× for cached reads). The cache TTL is 5 minutes, refreshed on each hit during an active session. Cache hits/misses are logged at `INFO` level with `🗃️ Prompt cache:` prefix, making them easy to spot in production logs.

The implementation is in `app/core/llm_client._enable_prompt_caching()`. Non-Anthropic models receive the payload unchanged.
