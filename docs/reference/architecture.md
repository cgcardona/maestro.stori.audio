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
6. **COMPOSING:** Planner produces a plan (JSON); executor simulates it without mutation; server streams Variation events (`meta`, `phrase*`, `done`). Frontend enters Variation Review Mode. User accepts or discards.
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

- **`plan`** — emitted once after initial reasoning, before the first tool call. Contains `planId`, `title` (with musical params like key and tempo), and `steps[]` (each with `stepId`, `label`, `status: "pending"`, optional `detail`).
- **`planStepUpdate`** — emitted twice per step: `status: "active"` when starting, `status: "completed"` (or `"failed"` / `"skipped"`) when done, with an optional `result` summary string.
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

Implementation: `app/core/prompt_parser.py` (parser), `app/core/intent.py` (routing gate).

---

## Music generation

**Orpheus required** for composing. No pattern fallback; if Orpheus is down, generation fails with a clear error. Config: `STORI_ORPHEUS_BASE_URL` (default `http://localhost:10002`). Full health requires Orpheus. See [setup.md](../guides/setup.md) for config.
