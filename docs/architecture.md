# Architecture

How the backend works: one engine, two entry points; request flow; intent; music.

---

## One backend, two entry points

- **Stori app:** User types in the DAW → app POSTs to `POST /api/v1/compose/stream` → SSE stream of tool calls → app applies them.
- **MCP client (Cursor, Claude, etc.):** User or script calls MCP tools → Composer runs or forwards to the **same** Stori instance (the one connected at `GET /api/v1/mcp/daw`). Same tool set; only the client changes. Human stays in the loop. Later: headless Stori for swarms.

---

## Request flow

1. **Frontend** sends `POST /api/v1/compose/stream` with `prompt`, optional `project` (app state), `conversation_id`.
2. **Intent** is classified (pattern + LLM fallback) → REASONING / EDITING / COMPOSING.
3. **REASONING:** Chat only; no tools; stream `reasoning` + `content` events.
4. **EDITING:** LLM gets a tool allowlist; emits tool calls; server validates and resolves entity IDs; stream `tool_call` events for client to apply.
5. **COMPOSING:** Planner produces a plan (JSON); executor runs it (tools + Orpheus); server streams progress and applies tools; client gets state updates.
6. **Stream:** Events include `status`, `reasoning`, `tool_start`, `tool_call`, `tool_complete`, `complete`, `error`. Variable refs (`$0.trackId`) resolved server-side.

---

## Intent engine

Classify first, then execute. **REASONING** = questions, no tools. **EDITING** = direct DAW actions (transport, track, region, effects); LLM constrained by allowlist; validation + entity resolution before execution. **COMPOSING** = “make music” / high-level; planner → executor; Orpheus for generation. Tool allowlisting and server-side entity IDs prevent bad or fabricated IDs.

---

## Music generation

**Orpheus required** for composing. No pattern fallback; if Orpheus is down, generation fails with a clear error. Config: `STORI_ORPHEUS_BASE_URL` (default `http://localhost:10002`). Full health requires Orpheus. See [setup.md](setup.md) for config.
